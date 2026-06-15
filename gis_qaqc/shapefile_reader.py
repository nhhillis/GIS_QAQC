"""
Load and validate ODOT field GIS shapefiles.

Shapefile type is detected by filename keywords. Required attribute field names
follow ODOT WW guidance (Jan 2026). Field name lookup is case-insensitive and
accepts common ArcGIS truncations.
"""

import os
from pathlib import Path
from typing import Optional

import geopandas as gpd
from pyproj import Transformer

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
# Field name aliases  (lowercase → canonical model attribute)
# ---------------------------------------------------------------------------

STREAM_FIELD_ALIASES = {
    "label":              ["label", "feat_label", "feature_lab", "feat_id", "name"],
    "feature_type":       ["type", "feat_type", "feature_typ", "stream_typ", "strm_type"],
    "jurisdictional_status": ["juris_stat", "jurisdictio", "jur_status", "jurisdicti", "status"],
    "acres":              ["acres", "area_ac", "area_acres", "acreage"],
    "linear_feet":        ["lin_ft", "lin_feet", "length_ft", "length", "lin_length"],
    "mapped_on_usgs":     ["usgs_topo", "on_usgs", "usgs_map", "mapped_usg", "usgs"],
    "stream_name":        ["stream_nam", "stream_name", "waterway", "water_body", "waterbody"],
}

WETLAND_FIELD_ALIASES = {
    "label":              ["label", "feat_label", "feature_lab", "feat_id", "name"],
    "wetland_type":       ["type", "wet_type", "wetland_ty", "feature_ty", "wl_type"],
    "cowardin":           ["cowardin", "cowardin_cl", "cowardin_c", "class"],
    "jurisdictional_status": ["juris_stat", "jurisdictio", "jur_status", "jurisdicti", "status"],
    "acres":              ["acres", "area_ac", "area_acres", "acreage"],
}

HABITAT_FIELD_ALIASES = {
    "label":   ["label", "hab_label", "feature_la", "feat_id", "name"],
    "species": ["species", "species_na", "sp_name"],
}

BRIDGE_FIELD_ALIASES = {
    "label":       ["label", "bridge_lab", "feat_label", "bridge_id", "name"],
    "nbi_number":  ["nbi", "nbi_number", "nbi_num", "nbi_no"],
    "water_body":  ["water_body", "waterway", "waterbody", "stream"],
    "road_number": ["road_num", "road_no", "roadway"],
}

# Shapefile type keywords (checked against lowercase filename)
TYPE_KEYWORDS = {
    "stream":     ["stream", "ohwm", "drainage"],
    "wetland":    ["wetland", "wet_"],
    "open_water": ["open_water", "openwater", "pond", "lake"],
    "habitat":    ["habitat", "abb", "bat", "tcb", "pollinator"],
    "footprint":  ["footprint", "project_foot", "proj_foot", "action_area", "boundary"],
    "bridge":     ["bridge", "culvert", "structure"],
    "photo":      ["photo", "sample"],
}


def _find_field(columns: list[str], aliases: list[str]) -> Optional[str]:
    """Return the first column name that matches any alias (case-insensitive)."""
    lower = {c.lower(): c for c in columns}
    for alias in aliases:
        if alias in lower:
            return lower[alias]
    return None


def _get(row, field_map: dict, key: str):
    """Safely get a value from a GeoDataFrame row using the resolved field name."""
    col = field_map.get(key)
    if col is None:
        return None
    val = row.get(col)
    if val is None:
        return None
    return str(val).strip() if not isinstance(val, (int, float)) else val


def _to_acres(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject to an equal-area CRS and calculate area in acres."""
    if gdf.crs is None:
        return gdf
    projected = gdf.to_crs("EPSG:5070")  # Albers Equal Area CONUS
    gdf = gdf.copy()
    gdf["_calc_acres"] = projected.geometry.area / 4046.8564  # m² → acres
    return gdf


def _to_linear_feet(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reproject and calculate length in feet."""
    if gdf.crs is None:
        return gdf
    projected = gdf.to_crs("EPSG:5070")
    gdf = gdf.copy()
    gdf["_calc_feet"] = projected.geometry.length * 3.28084  # m → ft
    return gdf


def _detect_type(filename: str) -> Optional[str]:
    name = filename.lower()
    for shp_type, keywords in TYPE_KEYWORDS.items():
        if any(kw in name for kw in keywords):
            return shp_type
    return None


def _validate_fields(columns: list[str], aliases_map: dict, shp_name: str, issues: list[str]):
    """Warn about required fields that can't be resolved."""
    for field_key, aliases in aliases_map.items():
        if _find_field(columns, aliases) is None:
            issues.append(
                f"[{shp_name}] Required field '{field_key}' not found "
                f"(looked for: {', '.join(aliases[:3])}...)"
            )


def _load_streams(gdf: gpd.GeoDataFrame, name: str, issues: list[str]) -> list[StreamFeature]:
    gdf = _to_acres(_to_linear_feet(gdf))
    cols = list(gdf.columns)
    fm = {k: _find_field(cols, v) for k, v in STREAM_FIELD_ALIASES.items()}
    _validate_fields(cols, STREAM_FIELD_ALIASES, name, issues)
    features = []
    for _, row in gdf.iterrows():
        label = _get(row, fm, "label") or f"S{_ + 1}"
        # Prefer calculated geometry values; fall back to attribute fields
        acres = row.get("_calc_acres") or _parse_float(_get(row, fm, "acres"))
        lin_ft = row.get("_calc_feet") or _parse_float(_get(row, fm, "linear_feet"))
        features.append(StreamFeature(
            label=label,
            stream_name=_get(row, fm, "stream_name"),
            feature_type=_get(row, fm, "feature_type"),
            jurisdictional_status=_get(row, fm, "jurisdictional_status"),
            acres=round(acres, 4) if acres is not None else None,
            linear_feet=round(lin_ft, 1) if lin_ft is not None else None,
            mapped_on_usgs=_get(row, fm, "mapped_on_usgs"),
        ))
    return features


def _load_wetlands(gdf: gpd.GeoDataFrame, name: str, issues: list[str]) -> list[WetlandFeature]:
    gdf = _to_acres(gdf)
    cols = list(gdf.columns)
    fm = {k: _find_field(cols, v) for k, v in WETLAND_FIELD_ALIASES.items()}
    _validate_fields(cols, WETLAND_FIELD_ALIASES, name, issues)
    features = []
    for _, row in gdf.iterrows():
        label = _get(row, fm, "label") or f"W{_ + 1}"
        acres = row.get("_calc_acres") or _parse_float(_get(row, fm, "acres"))
        features.append(WetlandFeature(
            label=label,
            wetland_type=_get(row, fm, "wetland_type"),
            cowardin=_get(row, fm, "cowardin"),
            jurisdictional_status=_get(row, fm, "jurisdictional_status"),
            acres=round(acres, 4) if acres is not None else None,
        ))
    return features


def _load_habitat(gdf: gpd.GeoDataFrame, name: str, issues: list[str]) -> list[HabitatFeature]:
    gdf = _to_acres(gdf)
    cols = list(gdf.columns)
    fm = {k: _find_field(cols, v) for k, v in HABITAT_FIELD_ALIASES.items()}
    features = []
    for _, row in gdf.iterrows():
        label = _get(row, fm, "label") or Path(name).stem
        acres = row.get("_calc_acres") or _parse_float(_get(row, fm, "acres") if "acres" in fm else None)
        features.append(HabitatFeature(
            label=label,
            species=_get(row, fm, "species"),
            acres=round(acres, 4) if acres is not None else None,
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


def _parse_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def load_shapefiles(folder: str) -> ShapefileData:
    """
    Scan a folder for shapefiles, detect their type, load and validate each.
    Returns a ShapefileData with all extracted spatial information.
    """
    data = ShapefileData()
    folder_path = Path(folder)

    shp_files = list(folder_path.glob("*.shp"))
    if not shp_files:
        data.schema_issues.append(f"No .shp files found in {folder}")
        return data

    for shp_path in shp_files:
        shp_type = _detect_type(shp_path.name)
        if shp_type in ("photo", None):
            continue  # skip photo points and unrecognized files

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
        elif shp_type == "footprint":
            gdf_proj = gdf.to_crs("EPSG:5070") if gdf.crs else gdf
            total_acres = gdf_proj.geometry.area.sum() / 4046.8564
            data.footprint_acres = round(total_acres, 4)

    return data
