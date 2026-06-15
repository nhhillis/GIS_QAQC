"""
GIS_QAQC — ODOT shapefile vs. report QC checker.

Usage:
    python main.py --shapefiles ./data/LeFlore_28617/ --ww WW_Report.docx --ba BA_Report.docx
    python main.py --shapefiles ./data/LeFlore_28617/ --ww WW_Report.pdf --output ./output/qc.html
    python main.py --shapefiles ./data/LeFlore_28617/   # schema validation only
"""

import argparse
import sys
from pathlib import Path

from gis_qaqc.comparator import run_checks
from gis_qaqc.reporter import print_console_report, write_html_report
from gis_qaqc.shapefile_reader import load_shapefiles


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cross-check ODOT field GIS shapefiles against BA and/or WW reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--shapefiles", "-s",
        required=True,
        metavar="FOLDER",
        help="Folder containing project shapefiles (.shp).",
    )
    p.add_argument(
        "--ww",
        metavar="FILE",
        help="Waters and Wetlands report (.docx or .pdf).",
    )
    p.add_argument(
        "--ba",
        metavar="FILE",
        help="Biological Assessment report (.docx or .pdf).",
    )
    p.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Write HTML QC report to this path (optional).",
    )
    p.add_argument(
        "--label",
        metavar="TEXT",
        default=None,
        help="Project label shown in the report header (default: derived from shapefile folder name).",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    shp_folder = Path(args.shapefiles)
    if not shp_folder.is_dir():
        print(f"ERROR: Shapefile folder not found: {shp_folder}", file=sys.stderr)
        return 1

    label = args.label or shp_folder.name

    # --- Load shapefiles ---
    from rich.console import Console
    con = Console()
    con.print(f"[bold]Loading shapefiles from:[/bold] {shp_folder}")
    shp_data = load_shapefiles(str(shp_folder))
    con.print(
        f"  Streams: {len(shp_data.streams)}  "
        f"Wetlands: {len(shp_data.wetlands)}  "
        f"Open Waters: {len(shp_data.open_waters)}  "
        f"Habitat: {len(shp_data.habitat_features)}  "
        f"Bridges: {len(shp_data.bridges)}  "
        f"Footprint: {f'{shp_data.footprint_acres:.4f} ac' if shp_data.footprint_acres else 'N/A'}"
    )

    # --- Parse WW report ---
    ww_report = None
    if args.ww:
        ww_path = Path(args.ww)
        if not ww_path.exists():
            print(f"ERROR: WW report not found: {ww_path}", file=sys.stderr)
            return 1
        con.print(f"[bold]Parsing WW report:[/bold] {ww_path.name}")
        from gis_qaqc.report_parser.ww_parser import parse_ww_report
        ww_report = parse_ww_report(str(ww_path))
        con.print(
            f"  Streams: {len(ww_report.streams)}  "
            f"Wetlands: {len(ww_report.wetlands)}  "
            f"Footprint: {f'{ww_report.header.footprint_acres:.2f} ac' if ww_report.header.footprint_acres else 'N/A'}"
        )

    # --- Parse BA report ---
    ba_report = None
    if args.ba:
        ba_path = Path(args.ba)
        if not ba_path.exists():
            print(f"ERROR: BA report not found: {ba_path}", file=sys.stderr)
            return 1
        con.print(f"[bold]Parsing BA report:[/bold] {ba_path.name}")
        from gis_qaqc.report_parser.ba_parser import parse_ba_report
        ba_report = parse_ba_report(str(ba_path))
        con.print(
            f"  Footprint: {f'{ba_report.footprint_acres:.2f} ac' if ba_report.footprint_acres else 'N/A'}  "
            f"Habitat features: {len(ba_report.habitat_features)}  "
            f"Bridges: {len(ba_report.bridges)}"
        )

    # --- Run checks ---
    con.print()
    findings = run_checks(shp_data, ww=ww_report, ba=ba_report)

    # --- Output ---
    print_console_report(findings, project_label=label)

    if args.output:
        write_html_report(findings, args.output, project_label=label)

    # Exit code 1 if any FAILs
    has_failures = any(f.severity == "FAIL" for f in findings)
    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
