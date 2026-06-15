"""
GIS_QAQC Streamlit App

Upload a WW report, BA report, and shapefile folder (as a .zip),
then run all QC checks and view results in-browser.
"""

import io
import os
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from gis_qaqc.comparator import run_checks
from gis_qaqc.models import Severity
from gis_qaqc.reporter import write_html_report

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="GIS QAQC",
    page_icon="🗺️",
    layout="wide",
)

st.title("GIS QAQC")
st.caption("Cross-check ODOT field shapefiles against Biological Assessment and Waters & Wetlands reports.")

# ---------------------------------------------------------------------------
# Sidebar — file uploads
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Upload Files")

    ww_file = st.file_uploader(
        "Waters & Wetlands Report",
        type=["docx", "pdf"],
        help="ODOT WW report (.docx or .pdf)",
    )
    ba_file = st.file_uploader(
        "Biological Assessment",
        type=["docx", "pdf"],
        help="ODOT BA report (.docx or .pdf)",
    )
    shp_zip = st.file_uploader(
        "Shapefiles (.zip)",
        type=["zip"],
        help="Zip the shapefile folder and upload it here.",
    )

    st.divider()
    project_label = st.text_input("Project label (optional)", placeholder="e.g. LeFlore JP 28617(04)")
    run_btn = st.button("▶  Run QC", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEV_ICON = {Severity.PASS: "✅", Severity.FAIL: "❌", Severity.WARN: "⚠️", Severity.INFO: "ℹ️"}
_SEV_ORDER = {Severity.FAIL: 0, Severity.WARN: 1, Severity.PASS: 2, Severity.INFO: 3}


def _save_upload(uploaded, suffix: str, tmp_dir: str) -> str:
    dest = os.path.join(tmp_dir, f"upload{suffix}")
    with open(dest, "wb") as f:
        f.write(uploaded.getbuffer())
    return dest


def _extract_zip(uploaded, tmp_dir: str) -> str:
    zip_path = os.path.join(tmp_dir, "shapefiles.zip")
    with open(zip_path, "wb") as f:
        f.write(uploaded.getbuffer())
    shp_dir = os.path.join(tmp_dir, "shapefiles")
    os.makedirs(shp_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(shp_dir)
    return shp_dir


# ---------------------------------------------------------------------------
# Run logic
# ---------------------------------------------------------------------------

if run_btn:
    if not ww_file and not ba_file and not shp_zip:
        st.warning("Upload at least one file to get started.")
        st.stop()

    label = project_label.strip() or (ww_file.name if ww_file else "GIS QAQC")

    with st.spinner("Processing…"):
        with tempfile.TemporaryDirectory() as tmp:

            # Parse WW
            ww_report = None
            if ww_file:
                ww_path = _save_upload(ww_file, Path(ww_file.name).suffix, tmp)
                from gis_qaqc.report_parser.ww_parser import parse_ww_report
                try:
                    ww_report = parse_ww_report(ww_path)
                except Exception as e:
                    st.error(f"Failed to parse WW report: {e}")

            # Parse BA
            ba_report = None
            if ba_file:
                ba_path = _save_upload(ba_file, Path(ba_file.name).suffix, tmp)
                from gis_qaqc.report_parser.ba_parser import parse_ba_report
                try:
                    ba_report = parse_ba_report(ba_path)
                except Exception as e:
                    st.error(f"Failed to parse BA report: {e}")

            # Load shapefiles
            from gis_qaqc.models import ShapefileData
            shp_data = ShapefileData()
            if shp_zip:
                shp_dir = _extract_zip(shp_zip, tmp)
                from gis_qaqc.shapefile_reader import load_shapefiles
                try:
                    shp_data = load_shapefiles(shp_dir)
                except Exception as e:
                    st.error(f"Failed to load shapefiles: {e}")

            # Run checks
            findings = run_checks(shp_data, ww=ww_report, ba=ba_report)

            # Sort: FAILs first, then WARNs, then PASSes
            findings.sort(key=lambda f: _SEV_ORDER.get(f.severity, 9))

            # Build HTML for download
            html_buf = io.StringIO()
            html_path = os.path.join(tmp, "qc_report.html")
            write_html_report(findings, html_path, project_label=label)
            html_bytes = Path(html_path).read_bytes()

    # ---------------------------------------------------------------------------
    # Results display
    # ---------------------------------------------------------------------------

    passes = sum(1 for f in findings if f.severity == Severity.PASS)
    fails  = sum(1 for f in findings if f.severity == Severity.FAIL)
    warns  = sum(1 for f in findings if f.severity == Severity.WARN)

    st.subheader(f"Results — {label}")

    col1, col2, col3 = st.columns(3)
    col1.metric("✅ Pass", passes)
    col2.metric("❌ Fail", fails, delta=f"-{fails}" if fails else None, delta_color="inverse")
    col3.metric("⚠️ Warn", warns)

    st.download_button(
        "⬇️  Download HTML Report",
        data=html_bytes,
        file_name=f"{label.replace(' ', '_')}_QC_Report.html",
        mime="text/html",
    )

    st.divider()

    # Parsed data summary
    with st.expander("📋 Parsed data summary", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            if ww_report:
                st.markdown("**WW Report**")
                st.write(f"- Streams: {len(ww_report.streams)}")
                st.write(f"- Wetlands: {len(ww_report.wetlands)}")
                st.write(f"- Footprint: {ww_report.header.footprint_acres} ac")
                st.write(f"- JP: {ww_report.header.jp_number}")
                st.write(f"- County: {ww_report.header.county}")
            if ba_report:
                st.markdown("**BA Report**")
                st.write(f"- Footprint: {ba_report.footprint_acres} ac")
                st.write(f"- Habitat features: {len(ba_report.habitat_features)}")
                st.write(f"- Bridges: {len(ba_report.bridges)}")
        with c2:
            if shp_zip:
                st.markdown("**Shapefiles**")
                st.write(f"- Streams: {len(shp_data.streams)}")
                st.write(f"- Wetlands: {len(shp_data.wetlands)}")
                st.write(f"- Open waters: {len(shp_data.open_waters)}")
                st.write(f"- Habitat: {len(shp_data.habitat_features)}")
                st.write(f"- Bridges: {len(shp_data.bridges)}")
                st.write(f"- Footprint: {shp_data.footprint_acres} ac")
                if shp_data.schema_issues:
                    st.markdown("**Schema issues:**")
                    for issue in shp_data.schema_issues:
                        st.warning(issue)

    st.divider()

    # Findings table
    if fails > 0:
        st.error(f"{fails} check(s) failed — review below.")
    elif warns > 0:
        st.warning("All checks passed with warnings.")
    else:
        st.success("All checks passed.")

    for f in findings:
        icon = _SEV_ICON.get(f.severity, "")
        feat_tag = f" — `{f.feature_id}`" if f.feature_id else ""
        header = f"{icon} **{f.check_name}**{feat_tag}"

        if f.severity == Severity.FAIL:
            with st.expander(header, expanded=True):
                st.markdown(f.message)
                if f.shapefile_value or f.report_value:
                    c1, c2 = st.columns(2)
                    c1.metric("Shapefile", f.shapefile_value or "—")
                    c2.metric("Report", f.report_value or "—")
        elif f.severity == Severity.WARN:
            with st.expander(header, expanded=False):
                st.markdown(f.message)
                if f.shapefile_value or f.report_value:
                    c1, c2 = st.columns(2)
                    c1.metric("Shapefile", f.shapefile_value or "—")
                    c2.metric("Report", f.report_value or "—")
        else:
            with st.expander(header, expanded=False):
                st.markdown(f.message)
                if f.shapefile_value or f.report_value:
                    c1, c2 = st.columns(2)
                    c1.metric("Shapefile", f.shapefile_value or "—")
                    c2.metric("Report", f.report_value or "—")

else:
    # Landing state
    st.info(
        "Upload files in the sidebar and click **▶ Run QC** to begin.\n\n"
        "- **WW Report** and/or **BA Report** are optional — upload what you have.\n"
        "- **Shapefiles**: zip your shapefile folder before uploading.\n"
        "- Running with only shapefiles performs schema validation only."
    )
