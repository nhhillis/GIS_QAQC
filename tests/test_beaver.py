"""
Tests using Beaver JP 31835(04).

Known values (verified against actual reports and shapefiles):
  WW: S1 (Kiowa Creek, Perennial, Likely, 0.12 ac), DF1 (Drainage, Unlikely, 0.12 ac).
      No wetlands. Footprint 9 ac (report rounds to integer).
  BA: footprint 9 ac, bat forest within 100ft = 1.45 ac (Indiana Bat / NLEB / TCB),
      100-300ft = 0.0 ac, >300ft = 0.0 ac. Bridge B1 NBI 18599.
  Shapefiles: S1 (Perennial Stream, Likely, 0.1217 ac, 607.9 lf),
              D1 (Drainage, Unlikely, 0.1156 ac, 535.5 lf — note: report uses "DF1"),
              TCB habitat 1.4583 ac, footprint 9.0537 ac (from GDB), bridge B1 NBI 18599.

Note: The shapefile uses label "D1" while the WW report uses "DF1". This is a real
data inconsistency that the QC tool correctly flags as a FAIL.
"""

import os
import zipfile
import tempfile
import pytest

BEAVER_BASE = (
    r"B:\Code\BioTestData\ODOT Examples-20260615"
    r"\Beaver JP 31835(04) -20260615\Beaver JP 31835(04)"
)
WW_REPORT = os.path.join(BEAVER_BASE, "Beaver County JP 31835(04) -  Waters and Wetlands Report.docx")
BA_REPORT = os.path.join(BEAVER_BASE, "Beaver County JP 31835(04) - Biological Assessment Report.docx")
SHP_ZIP   = os.path.join(BEAVER_BASE, "Beaver JP 31835(04) - Shapefiles.zip")


# ---------------------------------------------------------------------------
# WW parser tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ww():
    from gis_qaqc.report_parser.ww_parser import parse_ww_report
    return parse_ww_report(WW_REPORT)


def test_ww_jp_number(ww):
    assert ww.header.jp_number is not None
    assert "31835" in ww.header.jp_number


def test_ww_county(ww):
    assert ww.header.county is not None
    assert "beaver" in ww.header.county.lower()


def test_ww_water_body(ww):
    assert ww.header.water_body_name is not None
    assert "kiowa" in ww.header.water_body_name.lower()


def test_ww_footprint_acres(ww):
    assert ww.header.footprint_acres is not None
    assert abs(ww.header.footprint_acres - 9.0) <= 0.5  # report rounds to integer


def test_ww_stream_count(ww):
    assert len(ww.streams) == 2, f"Expected 2 streams, got {[s.label for s in ww.streams]}"


def test_ww_stream_s1(ww):
    s1 = next((s for s in ww.streams if s.label == "S1"), None)
    assert s1 is not None, "S1 not found in WW streams"
    assert s1.feature_type is not None and "perennial" in s1.feature_type.lower()
    assert s1.jurisdictional_status == "Likely"
    assert s1.acres is not None and abs(s1.acres - 0.12) <= 0.05


def test_ww_stream_df1(ww):
    df1 = next((s for s in ww.streams if s.label == "DF1"), None)
    assert df1 is not None, "DF1 not found in WW streams"
    assert df1.jurisdictional_status == "Unlikely"


def test_ww_no_wetlands(ww):
    assert len(ww.wetlands) == 0


# ---------------------------------------------------------------------------
# BA parser tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ba():
    from gis_qaqc.report_parser.ba_parser import parse_ba_report
    return parse_ba_report(BA_REPORT)


def test_ba_jp_number(ba):
    assert ba.header.jp_number is not None
    assert "31835" in ba.header.jp_number


def test_ba_footprint_acres(ba):
    assert ba.footprint_acres is not None
    assert abs(ba.footprint_acres - 9.0) <= 0.5


def test_ba_bat_forest_within_100ft(ba):
    feat = next((h for h in ba.habitat_features if "within_100ft" in h.label), None)
    assert feat is not None, "bat_forest_within_100ft not found"
    assert feat.acres is not None
    assert abs(feat.acres - 1.45) <= 0.05


def test_ba_bat_forest_100_300ft(ba):
    feat = next((h for h in ba.habitat_features if "100_300" in h.label), None)
    assert feat is not None, "bat_forest_100_300ft not found"
    assert feat.acres == 0.0


def test_ba_bridge_b1(ba):
    assert len(ba.bridges) >= 1
    b1 = next((b for b in ba.bridges if b.label == "B1"), None)
    assert b1 is not None, "Bridge B1 not found"
    assert b1.nbi_number == "18599"


# ---------------------------------------------------------------------------
# Shapefile tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def shp():
    from gis_qaqc.shapefile_reader import load_shapefiles
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(SHP_ZIP) as z:
            z.extractall(tmp)
        shp_dir = os.path.join(tmp, "Beaver JP 31835(04) - Shapefiles")
        return load_shapefiles(shp_dir)


def test_shp_footprint(shp):
    assert shp.footprint_acres is not None
    assert abs(shp.footprint_acres - 9.05) <= 0.10


def test_shp_stream_count(shp):
    assert len(shp.streams) == 2


def test_shp_stream_s1(shp):
    s1 = next((s for s in shp.streams if s.label == "S1"), None)
    assert s1 is not None
    assert s1.jurisdictional_status == "Likely"
    assert s1.acres is not None and abs(s1.acres - 0.1217) <= 0.01
    assert s1.linear_feet is not None and abs(s1.linear_feet - 607.9) <= 10


def test_shp_stream_d1(shp):
    d1 = next((s for s in shp.streams if s.label == "D1"), None)
    assert d1 is not None
    assert d1.jurisdictional_status == "Unlikely"
    assert d1.acres is not None and abs(d1.acres - 0.1156) <= 0.01


def test_shp_no_wetlands(shp):
    assert len(shp.wetlands) == 0


def test_shp_tcb_habitat(shp):
    tcb = next((h for h in shp.habitat_features if "tcb" in h.label.lower()), None)
    assert tcb is not None, "TCB habitat shapefile not found"
    assert tcb.acres is not None and abs(tcb.acres - 1.4583) <= 0.01


def test_shp_bridge_b1(shp):
    b1 = next((b for b in shp.bridges if b.label == "B1"), None)
    assert b1 is not None
    assert b1.nbi_number == "18599"


# ---------------------------------------------------------------------------
# Comparator tests
# ---------------------------------------------------------------------------

def test_cross_report_acreage(ww, ba):
    from gis_qaqc.comparator import _check_cross_report_acreage
    findings = _check_cross_report_acreage(ww, ba)
    assert findings
    assert all(f.severity != "FAIL" for f in findings)


def test_habitat_acreage_passes(shp, ba):
    from gis_qaqc.comparator import _check_habitat_acreage
    findings = _check_habitat_acreage(shp, ba)
    bat = next((f for f in findings if "bat_forest" in f.feature_id), None)
    assert bat is not None, "No bat_forest habitat finding"
    assert bat.severity == "PASS", f"Expected PASS, got {bat.severity}: {bat.message}"


def test_label_mismatch_d1_df1(shp, ww):
    """D1 in shapefile vs DF1 in WW report — tool must flag this."""
    from gis_qaqc.comparator import _check_label_set
    findings = _check_label_set(shp.streams, ww.streams, "Stream/DF")
    fail_msgs = [f.message for f in findings if f.severity == "FAIL"]
    assert any("D1" in m or "DF1" in m for m in fail_msgs), (
        "Expected label mismatch FAIL for D1/DF1"
    )
