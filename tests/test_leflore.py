"""
Tests using LeFlore JP 28617(04) — the known-good reference project.

Known values (from project handoff doc and verified against actual reports):
  WW: S1 (Cedar Creek, perennial, Likely, 0.09 ac). No wetlands. No open water.
  BA: footprint 1.71 ac, bat forest 0.57 ac (all within 100 ft), ABB native veg 0.29 ac.
  Both reports: JP 28617(04), LeFlore, NS 193 MC 4084C, Cedar Creek, ROW Nov 2025,
                Let Oct 2026, 0.19 mi, 34.802761/-94.523580, S24 T4N R26E.
  Bridge: B1, NBI 6415, Cedar Creek.
"""

import os
import pytest

EXAMPLE_BASE = r"B:\Code\BioTestData\ODOT Examples-20260615\ODOT Examples"
WW_REPORT = os.path.join(EXAMPLE_BASE, "Leflore 28617(04) Waters and Wetlands Report.docx")
BA_REPORT = os.path.join(EXAMPLE_BASE, "LeFlore JP 28617(04) - Biological Assessment Report_Revised.docx")


# ---------------------------------------------------------------------------
# WW parser tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ww():
    from gis_qaqc.report_parser.ww_parser import parse_ww_report
    return parse_ww_report(WW_REPORT)


def test_ww_header_jp_number(ww):
    assert ww.header.jp_number is not None
    assert "28617" in ww.header.jp_number


def test_ww_header_county(ww):
    assert ww.header.county is not None
    assert "leflore" in ww.header.county.lower() or "le flore" in ww.header.county.lower()


def test_ww_header_water_body(ww):
    assert ww.header.water_body_name is not None
    assert "cedar" in ww.header.water_body_name.lower()


def test_ww_header_footprint_acres(ww):
    assert ww.header.footprint_acres is not None
    assert abs(ww.header.footprint_acres - 1.71) <= 0.05


def test_ww_stream_count(ww):
    assert len(ww.streams) == 1, f"Expected 1 stream, got {len(ww.streams)}: {[s.label for s in ww.streams]}"


def test_ww_stream_s1_label(ww):
    labels = [s.label for s in ww.streams]
    assert "S1" in labels


def test_ww_stream_s1_type(ww):
    s1 = next(s for s in ww.streams if s.label == "S1")
    assert s1.feature_type is not None
    assert "perennial" in s1.feature_type.lower()


def test_ww_stream_s1_jurisdictional(ww):
    s1 = next(s for s in ww.streams if s.label == "S1")
    assert s1.jurisdictional_status == "Likely"


def test_ww_stream_s1_acres(ww):
    s1 = next(s for s in ww.streams if s.label == "S1")
    assert s1.acres is not None
    assert abs(s1.acres - 0.09) <= 0.05


def test_ww_no_wetlands(ww):
    assert len(ww.wetlands) == 0, f"Expected 0 wetlands, got {len(ww.wetlands)}"


# ---------------------------------------------------------------------------
# BA parser tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ba():
    from gis_qaqc.report_parser.ba_parser import parse_ba_report
    return parse_ba_report(BA_REPORT)


def test_ba_header_jp_number(ba):
    assert ba.header.jp_number is not None
    assert "28617" in ba.header.jp_number


def test_ba_footprint_acres(ba):
    assert ba.footprint_acres is not None
    assert abs(ba.footprint_acres - 1.71) <= 0.05


def test_ba_bat_forest_acres(ba):
    bat = next((h for h in ba.habitat_features if "bat_forest" in h.label), None)
    assert bat is not None, "No bat forest habitat feature found"
    assert bat.acres is not None
    assert abs(bat.acres - 0.57) <= 0.05


def test_ba_abb_native_veg_acres(ba):
    abb = next((h for h in ba.habitat_features if "abb" in h.label), None)
    assert abb is not None, "No ABB native veg feature found"
    assert abb.acres is not None
    assert abs(abb.acres - 0.29) <= 0.05


def test_ba_bridge_b1(ba):
    assert len(ba.bridges) >= 1
    b1 = next((b for b in ba.bridges if b.label == "B1"), None)
    assert b1 is not None, "Bridge B1 not found"
    assert b1.nbi_number == "6415"


# ---------------------------------------------------------------------------
# Comparator tests (report-only, no shapefile needed)
# ---------------------------------------------------------------------------

def test_cross_report_acreage(ww, ba):
    from gis_qaqc.comparator import _check_cross_report_acreage
    findings = _check_cross_report_acreage(ww, ba)
    assert findings, "No findings returned"
    statuses = [f.severity for f in findings]
    assert "FAIL" not in statuses, f"Cross-report acreage check failed: {[f.message for f in findings]}"
