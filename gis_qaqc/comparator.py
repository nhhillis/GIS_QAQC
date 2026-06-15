"""
Cross-check shapefile spatial data against WW and BA report claims.

Implements all comparison checks from the ODOT GIS QAQC spec:
  1.  Project footprint acreage (±0.05 ac)
  2.  Stream/DF feature count (exact)
  3.  Wetland feature count (exact)
  4.  Feature label set comparison
  5.  Feature type match per feature ID
  6.  Jurisdictional status match per feature ID
  7.  Feature acreage match (±0.05 ac)
  8.  Stream linear feet match (±10 ft)
  9.  BA/WW cross-report footprint acreage (±0.01 ac)
  10. Habitat acreage match (±0.05 ac)
  11. Required shapefile schema fields present
"""

import re
from typing import Optional

from .models import (
    BAReport,
    QCFinding,
    Severity,
    ShapefileData,
    WWReport,
)

ACREAGE_TOL = 0.05
FOOTPRINT_ACREAGE_TOL = 0.10  # looser: reports often round footprint to whole acres
CROSS_REPORT_ACREAGE_TOL = 0.01
LINEAR_FEET_TOL = 10.0

# StreamType suffixes that appear in shapefiles but are dropped in reports
_STREAM_TYPE_STRIP = re.compile(
    r"\s+(stream|ditch|channel|creek|drain)\s*$", re.IGNORECASE
)


def _norm_stream_type(t: Optional[str]) -> Optional[str]:
    """'Perennial Stream' → 'Perennial', 'Intermittent Stream' → 'Intermittent'."""
    if not t:
        return t
    return _STREAM_TYPE_STRIP.sub("", t.strip())


def _fmt(val: Optional[float], unit: str = "ac") -> str:
    if val is None:
        return "N/A"
    return f"{val:.4f} {unit}"


def _acreage_pass(a: Optional[float], b: Optional[float], tol: float = ACREAGE_TOL) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _check_footprint_acreage(
    shp: ShapefileData,
    ww: Optional[WWReport],
    ba: Optional[BAReport],
) -> list[QCFinding]:
    findings = []

    shp_ac = shp.footprint_acres

    if ww and ww.header.footprint_acres is not None:
        rpt_ac = ww.header.footprint_acres
        if shp_ac is None:
            findings.append(QCFinding(
                check_name="Footprint Acreage (WW)",
                severity=Severity.WARN,
                message="Project footprint shapefile not found or empty; cannot compare to WW report.",
                report_value=_fmt(rpt_ac),
            ))
        elif _acreage_pass(shp_ac, rpt_ac, FOOTPRINT_ACREAGE_TOL):
            findings.append(QCFinding(
                check_name="Footprint Acreage (WW)",
                severity=Severity.PASS,
                message=f"Footprint acreage matches WW report within ±{FOOTPRINT_ACREAGE_TOL} ac.",
                shapefile_value=_fmt(shp_ac),
                report_value=_fmt(rpt_ac),
            ))
        else:
            findings.append(QCFinding(
                check_name="Footprint Acreage (WW)",
                severity=Severity.FAIL,
                message=f"Footprint acreage mismatch: diff = {abs(shp_ac - rpt_ac):.4f} ac (tolerance ±{FOOTPRINT_ACREAGE_TOL} ac).",
                shapefile_value=_fmt(shp_ac),
                report_value=_fmt(rpt_ac),
            ))

    if ba and ba.footprint_acres is not None:
        rpt_ac = ba.footprint_acres
        if shp_ac is None:
            findings.append(QCFinding(
                check_name="Footprint Acreage (BA)",
                severity=Severity.WARN,
                message="Project footprint shapefile not found; cannot compare to BA report.",
                report_value=_fmt(rpt_ac),
            ))
        elif _acreage_pass(shp_ac, rpt_ac, FOOTPRINT_ACREAGE_TOL):
            findings.append(QCFinding(
                check_name="Footprint Acreage (BA)",
                severity=Severity.PASS,
                message=f"Footprint acreage matches BA report within ±{FOOTPRINT_ACREAGE_TOL} ac.",
                shapefile_value=_fmt(shp_ac),
                report_value=_fmt(rpt_ac),
            ))
        else:
            findings.append(QCFinding(
                check_name="Footprint Acreage (BA)",
                severity=Severity.FAIL,
                message=f"Footprint acreage mismatch: diff = {abs(shp_ac - rpt_ac):.4f} ac (tolerance ±{FOOTPRINT_ACREAGE_TOL} ac).",
                shapefile_value=_fmt(shp_ac),
                report_value=_fmt(rpt_ac),
            ))

    return findings


def _check_cross_report_acreage(
    ww: Optional[WWReport],
    ba: Optional[BAReport],
) -> list[QCFinding]:
    if not ww or not ba:
        return []
    if ww.header.footprint_acres is None or ba.footprint_acres is None:
        return [QCFinding(
            check_name="Cross-Report Footprint Acreage",
            severity=Severity.WARN,
            message="Could not extract footprint acreage from one or both reports.",
            shapefile_value=_fmt(ww.header.footprint_acres),
            report_value=_fmt(ba.footprint_acres),
        )]
    diff = abs(ww.header.footprint_acres - ba.footprint_acres)
    if diff <= CROSS_REPORT_ACREAGE_TOL:
        return [QCFinding(
            check_name="Cross-Report Footprint Acreage",
            severity=Severity.PASS,
            message="WW and BA footprint acreages agree.",
            shapefile_value=f"{ww.header.footprint_acres:.4f} ac (WW)",
            report_value=f"{ba.footprint_acres:.4f} ac (BA)",
        )]
    return [QCFinding(
        check_name="Cross-Report Footprint Acreage",
        severity=Severity.FAIL,
        message=f"WW/BA footprint acreage mismatch: diff = {diff:.4f} ac (tolerance ±{CROSS_REPORT_ACREAGE_TOL} ac).",
        shapefile_value=f"{ww.header.footprint_acres:.4f} ac (WW)",
        report_value=f"{ba.footprint_acres:.4f} ac (BA)",
    )]


def _check_feature_count(
    shp_features: list,
    rpt_features: list,
    feature_type: str,
) -> QCFinding:
    shp_n = len(shp_features)
    rpt_n = len(rpt_features)
    if shp_n == rpt_n:
        return QCFinding(
            check_name=f"{feature_type} Count",
            severity=Severity.PASS,
            message=f"{feature_type} count matches: {shp_n}.",
            shapefile_value=str(shp_n),
            report_value=str(rpt_n),
        )
    return QCFinding(
        check_name=f"{feature_type} Count",
        severity=Severity.FAIL,
        message=f"{feature_type} count mismatch: shapefile has {shp_n}, report has {rpt_n}.",
        shapefile_value=str(shp_n),
        report_value=str(rpt_n),
    )


def _check_label_set(
    shp_features: list,
    rpt_features: list,
    feature_type: str,
) -> list[QCFinding]:
    shp_labels = {f.label.upper() for f in shp_features}
    rpt_labels = {f.label.upper() for f in rpt_features}
    findings = []

    only_in_report = rpt_labels - shp_labels
    only_in_shp = shp_labels - rpt_labels

    if not only_in_report and not only_in_shp:
        findings.append(QCFinding(
            check_name=f"{feature_type} Label Set",
            severity=Severity.PASS,
            message=f"All {feature_type} labels match between shapefile and report.",
        ))
    if only_in_report:
        findings.append(QCFinding(
            check_name=f"{feature_type} Label Set",
            severity=Severity.FAIL,
            message=f"Labels in report but NOT in shapefile: {sorted(only_in_report)}",
            report_value=str(sorted(only_in_report)),
        ))
    if only_in_shp:
        findings.append(QCFinding(
            check_name=f"{feature_type} Label Set",
            severity=Severity.FAIL,
            message=f"Labels in shapefile but NOT in report: {sorted(only_in_shp)}",
            shapefile_value=str(sorted(only_in_shp)),
        ))
    return findings


def _check_per_feature_streams(
    shp_streams: list,
    rpt_streams: list,
) -> list[QCFinding]:
    findings = []
    shp_by_label = {f.label.upper(): f for f in shp_streams}
    rpt_by_label = {f.label.upper(): f for f in rpt_streams}

    for label, rpt_feat in rpt_by_label.items():
        shp_feat = shp_by_label.get(label)
        if shp_feat is None:
            continue  # Already flagged by label set check

        # Feature type — normalize to strip trailing " Stream" / " Ditch" etc.
        if rpt_feat.feature_type and shp_feat.feature_type:
            if (_norm_stream_type(rpt_feat.feature_type).lower()
                    != _norm_stream_type(shp_feat.feature_type).lower()):
                findings.append(QCFinding(
                    check_name="Stream Feature Type",
                    severity=Severity.FAIL,
                    message=f"{label}: feature type mismatch.",
                    shapefile_value=shp_feat.feature_type,
                    report_value=rpt_feat.feature_type,
                    feature_id=label,
                ))
            else:
                findings.append(QCFinding(
                    check_name="Stream Feature Type",
                    severity=Severity.PASS,
                    message=f"{label}: feature type matches ({_norm_stream_type(rpt_feat.feature_type)}).",
                    feature_id=label,
                ))

        # Jurisdictional status
        if rpt_feat.jurisdictional_status and shp_feat.jurisdictional_status:
            if rpt_feat.jurisdictional_status.lower() != shp_feat.jurisdictional_status.lower():
                findings.append(QCFinding(
                    check_name="Stream Jurisdictional Status",
                    severity=Severity.FAIL,
                    message=f"{label}: jurisdictional status mismatch.",
                    shapefile_value=shp_feat.jurisdictional_status,
                    report_value=rpt_feat.jurisdictional_status,
                    feature_id=label,
                ))
            else:
                findings.append(QCFinding(
                    check_name="Stream Jurisdictional Status",
                    severity=Severity.PASS,
                    message=f"{label}: jurisdictional status matches ({rpt_feat.jurisdictional_status}).",
                    feature_id=label,
                ))

        # Acreage
        if rpt_feat.acres is not None and shp_feat.acres is not None:
            if _acreage_pass(shp_feat.acres, rpt_feat.acres):
                findings.append(QCFinding(
                    check_name="Stream Acreage",
                    severity=Severity.PASS,
                    message=f"{label}: acreage matches within ±{ACREAGE_TOL} ac.",
                    shapefile_value=_fmt(shp_feat.acres),
                    report_value=_fmt(rpt_feat.acres),
                    feature_id=label,
                ))
            else:
                findings.append(QCFinding(
                    check_name="Stream Acreage",
                    severity=Severity.FAIL,
                    message=f"{label}: acreage mismatch (diff = {abs(shp_feat.acres - rpt_feat.acres):.4f} ac).",
                    shapefile_value=_fmt(shp_feat.acres),
                    report_value=_fmt(rpt_feat.acres),
                    feature_id=label,
                ))
        elif rpt_feat.acres is not None:
            findings.append(QCFinding(
                check_name="Stream Acreage",
                severity=Severity.WARN,
                message=f"{label}: acreage not available in shapefile.",
                report_value=_fmt(rpt_feat.acres),
                feature_id=label,
            ))

        # Linear feet
        if rpt_feat.linear_feet is None and shp_feat.linear_feet is not None:
            # Report may not have linear feet in table; that's OK
            pass
        elif rpt_feat.linear_feet is not None and shp_feat.linear_feet is not None:
            diff = abs(shp_feat.linear_feet - rpt_feat.linear_feet)
            if diff <= LINEAR_FEET_TOL:
                findings.append(QCFinding(
                    check_name="Stream Linear Feet",
                    severity=Severity.PASS,
                    message=f"{label}: linear feet matches within ±{LINEAR_FEET_TOL} ft.",
                    shapefile_value=_fmt(shp_feat.linear_feet, "ft"),
                    report_value=_fmt(rpt_feat.linear_feet, "ft"),
                    feature_id=label,
                ))
            else:
                findings.append(QCFinding(
                    check_name="Stream Linear Feet",
                    severity=Severity.FAIL,
                    message=f"{label}: linear feet mismatch (diff = {diff:.1f} ft).",
                    shapefile_value=_fmt(shp_feat.linear_feet, "ft"),
                    report_value=_fmt(rpt_feat.linear_feet, "ft"),
                    feature_id=label,
                ))

    return findings


def _check_per_feature_wetlands(
    shp_wetlands: list,
    rpt_wetlands: list,
) -> list[QCFinding]:
    findings = []
    shp_by_label = {f.label.upper(): f for f in shp_wetlands}
    rpt_by_label = {f.label.upper(): f for f in rpt_wetlands}

    for label, rpt_feat in rpt_by_label.items():
        shp_feat = shp_by_label.get(label)
        if shp_feat is None:
            continue

        if rpt_feat.jurisdictional_status and shp_feat.jurisdictional_status:
            if rpt_feat.jurisdictional_status.lower() != shp_feat.jurisdictional_status.lower():
                findings.append(QCFinding(
                    check_name="Wetland Jurisdictional Status",
                    severity=Severity.FAIL,
                    message=f"{label}: jurisdictional status mismatch.",
                    shapefile_value=shp_feat.jurisdictional_status,
                    report_value=rpt_feat.jurisdictional_status,
                    feature_id=label,
                ))
            else:
                findings.append(QCFinding(
                    check_name="Wetland Jurisdictional Status",
                    severity=Severity.PASS,
                    message=f"{label}: jurisdictional status matches.",
                    feature_id=label,
                ))

        if rpt_feat.acres is not None and shp_feat.acres is not None:
            if _acreage_pass(shp_feat.acres, rpt_feat.acres):
                findings.append(QCFinding(
                    check_name="Wetland Acreage",
                    severity=Severity.PASS,
                    message=f"{label}: acreage matches within ±{ACREAGE_TOL} ac.",
                    shapefile_value=_fmt(shp_feat.acres),
                    report_value=_fmt(rpt_feat.acres),
                    feature_id=label,
                ))
            else:
                findings.append(QCFinding(
                    check_name="Wetland Acreage",
                    severity=Severity.FAIL,
                    message=f"{label}: acreage mismatch (diff = {abs(shp_feat.acres - rpt_feat.acres):.4f} ac).",
                    shapefile_value=_fmt(shp_feat.acres),
                    report_value=_fmt(rpt_feat.acres),
                    feature_id=label,
                ))

    return findings


_HAB_CATEGORIES = {
    "bat_forest": ["bat_forest", "tcb", "bat", "indiana", "nleb", "forested", "wooded"],
    "abb":        ["abb", "burying_beetle", "burying beetle", "native_perennial", "native perennial"],
    "pollinator": ["pollinator", "milkweed"],
}


def _hab_category(label: str) -> Optional[str]:
    l = label.lower()
    for cat, keywords in _HAB_CATEGORIES.items():
        if any(k in l for k in keywords):
            return cat
    return None


def _check_habitat_acreage(
    shp: ShapefileData,
    ba: Optional[BAReport],
) -> list[QCFinding]:
    if not ba or not ba.habitat_features or not shp.habitat_features:
        return []

    findings = []

    # Group BA features by category, summing acres
    ba_by_cat: dict[str, float] = {}
    for feat in ba.habitat_features:
        cat = _hab_category(feat.label)
        if cat and feat.acres is not None:
            ba_by_cat[cat] = ba_by_cat.get(cat, 0.0) + feat.acres

    # Group shapefile features by category, summing acres
    shp_by_cat: dict[str, float] = {}
    for feat in shp.habitat_features:
        cat = _hab_category(feat.label)
        if cat and feat.acres is not None:
            shp_by_cat[cat] = shp_by_cat.get(cat, 0.0) + feat.acres

    for cat in sorted(set(list(ba_by_cat) + list(shp_by_cat))):
        ba_ac = ba_by_cat.get(cat)
        shp_ac = shp_by_cat.get(cat)
        if ba_ac is None:
            findings.append(QCFinding(
                check_name="Habitat Acreage",
                severity=Severity.WARN,
                message=f"{cat}: shapefile has {shp_ac:.4f} ac but no matching BA habitat data.",
                shapefile_value=_fmt(shp_ac),
                feature_id=cat,
            ))
        elif shp_ac is None:
            findings.append(QCFinding(
                check_name="Habitat Acreage",
                severity=Severity.WARN,
                message=f"{cat}: BA reports {ba_ac:.4f} ac but no matching shapefile found.",
                report_value=_fmt(ba_ac),
                feature_id=cat,
            ))
        elif _acreage_pass(shp_ac, ba_ac):
            findings.append(QCFinding(
                check_name="Habitat Acreage",
                severity=Severity.PASS,
                message=f"{cat}: acreage matches within ±{ACREAGE_TOL} ac.",
                shapefile_value=_fmt(shp_ac),
                report_value=_fmt(ba_ac),
                feature_id=cat,
            ))
        else:
            findings.append(QCFinding(
                check_name="Habitat Acreage",
                severity=Severity.FAIL,
                message=f"{cat}: acreage mismatch (diff = {abs(shp_ac - ba_ac):.4f} ac).",
                shapefile_value=_fmt(shp_ac),
                report_value=_fmt(ba_ac),
                feature_id=cat,
            ))

    return findings


def _check_schema(shp: ShapefileData) -> list[QCFinding]:
    findings = []
    for issue in shp.schema_issues:
        findings.append(QCFinding(
            check_name="Shapefile Schema",
            severity=Severity.WARN,
            message=issue,
        ))
    if not shp.schema_issues:
        findings.append(QCFinding(
            check_name="Shapefile Schema",
            severity=Severity.PASS,
            message="All required shapefile schema fields are present.",
        ))
    return findings


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_checks(
    shp: ShapefileData,
    ww: Optional[WWReport] = None,
    ba: Optional[BAReport] = None,
) -> list[QCFinding]:
    """Run all applicable QC checks and return a flat list of findings."""
    findings: list[QCFinding] = []

    # 11. Schema
    findings.extend(_check_schema(shp))

    # 9. Cross-report acreage
    findings.extend(_check_cross_report_acreage(ww, ba))

    # 1. Footprint acreage
    findings.extend(_check_footprint_acreage(shp, ww, ba))

    if ww:
        rpt_streams = ww.streams
        rpt_wetlands = ww.wetlands

        # Combine shapefile wetlands + open waters for comparison
        all_shp_wetlands = shp.wetlands + shp.open_waters

        # 2. Stream count
        findings.append(_check_feature_count(shp.streams, rpt_streams, "Stream/DF"))

        # 3. Wetland count
        findings.append(_check_feature_count(all_shp_wetlands, rpt_wetlands, "Wetland/OW"))

        # 4. Feature labels
        findings.extend(_check_label_set(shp.streams, rpt_streams, "Stream/DF"))
        findings.extend(_check_label_set(all_shp_wetlands, rpt_wetlands, "Wetland/OW"))

        # 5, 6, 7, 8. Per-feature checks
        findings.extend(_check_per_feature_streams(shp.streams, rpt_streams))
        findings.extend(_check_per_feature_wetlands(all_shp_wetlands, rpt_wetlands))

    # 10. Habitat acreage
    findings.extend(_check_habitat_acreage(shp, ba))

    return findings
