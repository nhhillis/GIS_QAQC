"""
Load and validate ODOT field GIS shapefiles.

Shapefile type is detected by filename keywords. Field name lookup is
case-insensitive and covers both the ODOT guidance spec names and the
real field names observed in production shapefiles (e.g. Name, StreamType,
JDStatus, LF, Mapped, NBI, FeatureCro).

When a stream is split into segments (e.g. "S1 - North", "S1 - South"),
features are grouped by their base label and geometry is summed.
"""

import os
import re
from pathlib import Path
from typing import Optional

import geopandas as gpd

from .models import (
    BridgeFeature,
    HabitatFeature,
    QCFinding,
    Severity,
    ShapefileData,
    StreamFeature,
    WetlandFeature,
)

# ---------------------------------------------------------------------------
# Field name aliases  (canonical key → list of accepted column names, lowercase)
# ---------------------------------------------------------------------------

STREAM_FIELD_ALIASES = {
    "label":                 ["name", "label", "feat_label", "feature_lab", "feat_id"],
    "feature_type":          ["streamtype", "type", "feat_type", "feature_typ", "stream_typ", "strm_type"],
    "jurisdictional_status": ["jdstatus", "juris_stat", "jurisdictio", "jur_status", "jurisdicti", "status"],
    "acres":                 ["area", "acres", "area_ac", "area_acres", "acreage"],
    "linear_feet":           ["lf", "lin_ft", "lin_feet", "length_ft", "length"],
    "mapped_on_usgs":        ["mapped", "usgs_topo", "on_usgs", "usgs_map", "usgs"],
    "stream_name":           ["stream_nam", "stream_name", "waterway", "water_body", "waterbody", "featurecro"],
}

WETLAND_FIELD_ALIASES = {
    "label":                 ["name", "label", "feat_label", "feature_lab", "feat_id"],
    "wetland_type":          ["type", "wet_type", "wetland_ty", "feature_ty", "wl_type"],
    "cowardin":              ["cowardin", "cowardin_cl", "cowardin_c", "class"],
    "jurisdictional_status": ["jdstatus", "juris_stat", "jurisdictio", "jur_status", "jurisdicti", "status"],
    "acres":                 ["area", "acres", "area_ac", "area_acres", "acreage"],
}

HABITAT_FIELD_ALIASES = {
    "label":   ["name", "label", "hab_label", "feature_la", "feat_id"],
    "species": ["species", "species_na", "sp_name"],
    "acres":   ["acres", "area", "area_ac"],
}

BRIDGE_FIELD_ALIASES = {
    "label":       ["name", "label", "bridge_lab", "feat_label", "bridge_id"],
    "nbi_number":  ["nbi", "nbi_number", "nbi_num", "nbi_no"],
    "water_body":  ["featurecro", "water_body", "waterway", "waterbody", "stream"],
    "road_number": ["roadway", "road_num", "road_no"],
}

# Shapefile type detection — checked against lowercase filename
TYPE_KEYWORDS = {
    "stream":     ["stream", "ohwm", "drainage"],
    "wetland":    ["wetland", "wet_"],
    "open_water": ["open_water", "openwater", "pond", "lake"],
    "habitat":    ["habitat", "abb", "bat", "tcb", "pollinator"],
    "footprint":  ["footprint", "project_foot", "proj_foot", "boundary"],
    "bridge":     ["bridge", "structure", "culvert"],
    "skip":       ["photo", "sample", "data_point", "data point", "action area",
                   "actionarea", "eagle", "location"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_field(columns: list[str], aliases: list[str]) -> Optional[str]:
    lower = {c.lower(): c for c in columns}
    for alias in aliases:
        if alias in lower:
            return lower[alias]
    return None


def _get(row, field_map: dict, key: str):
    col = field_map.get(key)
    if col is None:
        return None
    val = row.get(col)
    if val is None:
        return None
    return str(val).strip() if not isinstance(val, (int, float)) else val


def _parse_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _normalize_juris(text: Optional[str]) -> Optional[str]:
    """
    Normalize jurisdictional status to 'Likely' or 'Unlikely'.
    Handles full ODOT phrases: 'Likely Jurisdictional',
    'Not Likely Jurisdictional', 'Unlikely Jurisdictional'.
    """
    if not text:
        return None
    t = text.strip().lower()
    if t.startswith("not likely") or t.startswith("unlikely"):
        return "Unlikely"
    if t.startswith("likely"):
        return "Likely"
    return text.strip()


def _base_label(name: Optional[str]) -> Optional[str]:
    """
    Extract the base feature label from names like 'S1 - North', 'DF1 - South'.
    Returns 'S1', 'DF1', etc.
    """
    if not name:
        return None
    # Strip segment suffixes: " - North", " - South", "_N", "_S", etc.
    clean = re.split(r"\s*[-–_]\s*(north|south|east|west|upper|lower|n|s|e|w)\b",
                     name, flags=re.IGNORECASE)[0]
    return clean.strip()


def _to_acres_series(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        return gdf
    projected = gdf.to_crs("EPSG:5070")
    gdf = gdf.copy()
    gdf["_calc_acres"] = projected.geometry.area / 4046.8564
    return gdf


def _to_feet_series(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        return gdf
    projected = gdf.to_crs("EPSG:5070")
    gdf = gdf.copy()
    gdf["_calc_feet"] = projected.geometry.length * 3.28084
    return gdf


def _detect_type(filename: str) -> Optional[str]:
    name = filename.lower()
    # Check skip list first so e.g. "Eagle Habitat Buffer" doesn't match "habitat"
    if any(kw in name for kw in TYPE_KEYWORDS["skip"]):
        return "skip"
    for shp_type, keywords in TYPE_KEYWORDS.items():
        if shp_type == "skip":
            continue
        if any(kw in name for kw in keywords):
            return shp_type
    return None


def _validate_fields(columns: list[str], aliases_map: dict, shp_name: str, issues: list[str]):
    for field_key, aliases in aliases_map.items():
        if _find_field(columns, aliases) is None:
            issues.append(
                f"[{shp_name}] Required field '{field_key}' not found "
                f"(tried: {', '.join(aliases[:4])})"
            )


# ---------------------------------------------------------------------------
# Type-specific loaders
# ---------------------------------------------------------------------------

def _load_streams(gdf: gpd.GeoDataFrame, name: str, issues: list[str]) -> list[StreamFeature]:
    gdf = _to_acres_series(_to_feet_series(gdf))
    cols = list(gdf.columns)
    fm = {k: _find_field(cols, v) for k, v in STREAM_FIELD_ALIASES.items()}

    # Only validate fields that are core requirements
    required = {k: v for k, v in STREAM_FIELD_ALIASES.items()
                if k in ("label", "feature_type", "jurisdictional_status", "acres")}
    _validate_fields(cols, required, name, issues)

    # Group rows by base label to merge segments (S1-North + S1-South → S1)
    rows_by_label: dict[str, list] = {}
    for _, row in gdf.iterrows():
        raw_name = _get(row, fm, "label")
        label = _base_label(raw_name) or raw_name or f"S{_}"
        rows_by_label.setdefault(label, []).append(row)

    features = []
    for label, rows in rows_by_label.items():
        # Sum geometry-calculated acreage and linear feet across segments
        calc_acres = sum(r.get("_calc_acres") or 0 for r in rows) or None
        calc_feet  = sum(r.get("_calc_feet") or 0 for r in rows) or None

        # Attribute acreage: sum the 'Area' / 'Acres' field if present
        attr_acres = None
        if fm.get("acres"):
            vals = [_parse_float(_get(r, fm, "acres")) for r in rows]
            vals = [v for v in vals if v is not None]
            if vals:
                attr_acres = sum(vals)

        # Attribute linear feet
        attr_feet = None
        if fm.get("linear_feet"):
            vals = [_parse_float(_get(r, fm, "linear_feet")) for r in rows]
            vals = [v for v in vals if v is not None]
            if vals:
                attr_feet = sum(vals)

        # Use geometry-calculated values as primary; attribute as fallback
        acres   = round(calc_acres or attr_acres or 0, 4) if (calc_acres or attr_acres) else None
        lin_ft  = round(calc_feet or attr_feet or 0, 1) if (calc_feet or attr_feet) else None

        # Take first row for categorical fields
        r0 = rows[0]
        features.append(StreamFeature(
            label=label,
            stream_name=_get(r0, fm, "stream_name"),
            feature_type=_get(r0, fm, "feature_type"),
            jurisdictional_status=_normalize_juris(_get(r0, fm, "jurisdictional_status")),
            acres=acres,
            linear_feet=lin_ft,
            mapped_on_usgs=_get(r0, fm, "mapped_on_usgs"),
        ))

    return features


def _load_wetlands(gdf: gpd.GeoDataFrame, name: str, issues: list[str]) -> list[WetlandFeature]:
    gdf = _to_acres_series(gdf)
    cols = list(gdf.columns)
    fm = {k: _find_field(cols, v) for k, v in WETLAND_FIELD_ALIASES.items()}
    required = {k: v for k, v in WETLAND_FIELD_ALIASES.items()
                if k in ("label", "jurisdictional_status", "acres")}
    _validate_fields(cols, required, name, issues)

    rows_by_label: dict[str, list] = {}
    for _, row in gdf.iterrows():
        raw_name = _get(row, fm, "label")
        label = _base_label(raw_name) or raw_name or f"W{_}"
        rows_by_label.setdefault(label, []).append(row)

    features = []
    for label, rows in rows_by_label.items():
        calc_acres = sum(r.get("_calc_acres") or 0 for r in rows) or None
        attr_acres = None
        if fm.get("acres"):
            vals = [_parse_float(_get(r, fm, "acres")) for r in rows if _get(r, fm, "acres")]
            if vals:
                attr_acres = sum(vals)
        acres = round(calc_acres or attr_acres or 0, 4) if (calc_acres or attr_acres) else None
        r0 = rows[0]
        features.append(WetlandFeature(
            label=label,
            wetland_type=_get(r0, fm, "wetland_type"),
            cowardin=_get(r0, fm, "cowardin"),
            jurisdictional_status=_normalize_juris(_get(r0, fm, "jurisdictional_status")),
            acres=acres,
        ))
    return features


def _load_habitat(gdf: gpd.GeoDataFrame, name: str, issues: list[str]) -> list[HabitatFeature]:
    gdf = _to_acres_series(gdf)
    cols = list(gdf.columns)
    fm = {k: _find_field(cols, v) for k, v in HABITAT_FIELD_ALIASES.items()}

    # Derive a short label from filename by stripping county/JP prefix patterns
    # e.g. "Beaver JP 31835(04) - TCB.shp" → "tcb"
    stem = Path(name).stem
    # Remove leading "County JP XXXXX(XX) - " prefix if present
    stem = re.sub(r"^[\w\s]+JP\s+[\d\(\)]+\s*[-–]\s*", "", stem, flags=re.IGNORECASE)
    file_label = stem.strip().lower().replace(" ", "_").replace("-", "_")

    # Group by label if available, otherwise treat all rows as one feature
    if fm.get("label"):
        rows_by_label: dict[str, list] = {}
        for _, row in gdf.iterrows():
            label = _base_label(_get(row, fm, "label")) or file_label
            rows_by_label.setdefault(label, []).append(row)
    else:
        rows_by_label = {file_label: list(gdf.itertuples())}

    features = []
    for label, rows in rows_by_label.items():
        if fm.get("label"):
            calc_acres = sum(r.get("_calc_acres") or 0 for r in rows) or None
            attr_acres = None
            if fm.get("acres"):
                vals = [_parse_float(_get(r, fm, "acres")) for r in rows if _get(r, fm, "acres")]
                attr_acres = sum(vals) if vals else None
            acres = round(calc_acres or attr_acres or 0, 4) if (calc_acres or attr_acres) else None
        else:
            # Sum _calc_acres across all rows (e.g. TCB with no label)
            calc_acres = gdf["_calc_acres"].sum()
            acres = round(calc_acres, 4) if calc_acres else None

        r0 = rows[0]
        species = _get(r0, fm, "species") if fm.get("species") else None
        features.append(HabitatFeature(
            label=label,
            species=species,
            acres=acres,
        ))
    return features


def _load_bridges(gdf: gpd.GeoDataFrame, name: str, issues: list[str]) -> list[BridgeFeature]:
    cols = list(gdf.columns)
    fm = {k: _find_field(cols, v) for k, v in BRIDGE_FIELD_ALIASES.items()}
    features = []
    for _, row in gdf.iterrows():
        label = _get(row, fm, "label") or f"B{_ + 1}"
        features.append(BridgeFeature(
            label=label,
            nbi_number=_get(row, fm, "nbi_number"),
            water_body=_get(row, fm, "water_body"),
            road_number=_get(row, fm, "road_number"),
        ))
    return features


# ---------------------------------------------------------------------------
# GDB footprint loader
# ---------------------------------------------------------------------------

def _load_footprint_from_gdb(gdb_path: str) -> Optional[float]:
    try:
        import pyogrio
        layers = pyogrio.list_layers(gdb_path)
        for layer_name in layers[:, 0]:
            # Look for footprint / environmental study footprint layer
            if any(kw in layer_name.lower() for kw in ("footprint", "study", "boundary")):
                gdf = gpd.read_file(gdb_path, layer=layer_name)
                if not gdf.empty and gdf.crs:
                    projected = gdf.to_crs("EPSG:5070")
                    return round(projected.geometry.area.sum() / 4046.8564, 4)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def load_shapefiles(folder: str) -> ShapefileData:
    """
    Scan a folder for shapefiles (and .gdb), detect type by filename,
    load and validate each. Returns a ShapefileData with all spatial info.
    """
    data = ShapefileData()
    folder_path = Path(folder)

    # Try GDB for project footprint first
    for gdb_dir in folder_path.glob("*.gdb"):
        if gdb_dir.is_dir():
            ac = _load_footprint_from_gdb(str(gdb_dir))
            if ac:
                data.footprint_acres = ac
                break

    shp_files = list(folder_path.glob("*.shp"))
    if not shp_files and data.footprint_acres is None:
        data.schema_issues.append(f"No .shp files found in {folder}")
        return data

    for shp_path in sorted(shp_files):
        shp_type = _detect_type(shp_path.name)
        if shp_type in ("skip", None):
            continue

        try:
            gdf = gpd.read_file(str(shp_path))
        except Exception as e:
            data.schema_issues.append(f"Could not read {shp_path.name}: {e}")
            continue

        if gdf.empty:
            continue

        name = shp_path.name

        if shp_type == "stream":
            data.streams.extend(_load_streams(gdf, name, data.schema_issues))
        elif shp_type == "wetland":
            data.wetlands.extend(_load_wetlands(gdf, name, data.schema_issues))
        elif shp_type == "open_water":
            data.open_waters.extend(_load_wetlands(gdf, name, data.schema_issues))
        elif shp_type == "habitat":
            data.habitat_features.extend(_load_habitat(gdf, name, data.schema_issues))
        elif shp_type == "bridge":
            data.bridges.extend(_load_bridges(gdf, name, data.schema_issues))
        elif shp_type == "footprint" and data.footprint_acres is None:
            gdf_proj = gdf.to_crs("EPSG:5070") if gdf.crs else gdf
            data.footprint_acres = round(gdf_proj.geometry.area.sum() / 4046.8564, 4)

    return data
