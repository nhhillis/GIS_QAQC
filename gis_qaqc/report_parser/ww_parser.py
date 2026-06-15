"""
Parse ODOT Waters and Wetlands Evaluation Reports (.docx or .pdf).

Extraction strategy:
  - Header: parse Table 0 directly by key-value cell pairs
  - Footprint acreage: table whose row 1 col contains 'Acreage', value in row 2
  - Streams: table whose header contains 'Feature Type' or 'Stream Name'
  - Wetlands: table whose header contains 'Cowardin' or 'Type of Wetland'
              AND whose data rows start with W/OW labels or 'N/A'
"""

import re
from pathlib import Path
from typing import Optional

from ..models import ProjectHeader, StreamFeature, WetlandFeature, WWReport
from ._docx_utils import _clean, _full_text_from_docx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_float(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    text = re.sub(r"[^\d.\-]", "", str(text))
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_juris(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.strip().lower()
    if t.startswith("likely"):
        return "Likely"
    if t.startswith("unlikely"):
        return "Unlikely"
    return _clean(text)


def _normalize_yn(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.strip().lower()
    if t.startswith("y"):
        return "Yes"
    if t.startswith("n") and "a" not in t[:2]:  # avoid "N/A"
        return "No"
    return _clean(text)


# ---------------------------------------------------------------------------
# Header table parsing
# ---------------------------------------------------------------------------

# The WW header table (Table 0) has rows like:
#   ['County', 'LeFlore', 'JP Number', '28617(04)', 'Project Number', 'J2-8617(004)CI']
#   ['Road Number', 'NS 193 MC 4084C', 'Water Body Name', ..., 'Cedar Creek', ...]
#   ['ROW Date', 'November 2025', 'Let Date', 'Oct 2026', 'Project Length', '0.19 miles']
# We scan all tables, flatten them into key→value pairs, then pull the fields we need.

_KV_KEYS = {
    "jp_number":       ["jp number"],
    "county":          ["county"],
    "road_number":     ["road number"],
    "water_body_name": ["water body name"],
    "row_date":        ["row date"],
    "let_date":        ["let date"],
    "project_length":  ["project length"],
}


def _parse_header_from_tables(all_tables: list[list[list[str]]]) -> ProjectHeader:
    """
    Scan all tables looking for a header table (contains 'County' and 'JP Number').
    Build a key→value map from adjacent cell pairs.
    """
    h = ProjectHeader()
    kv: dict[str, str] = {}

    for table in all_tables:
        flat_pairs = []
        for row in table:
            # Deduplicate merged cells (same value repeated)
            deduped = []
            seen = None
            for cell in row:
                if cell and cell != seen:
                    deduped.append(cell)
                    seen = cell
            # Walk as k→v pairs
            i = 0
            while i + 1 < len(deduped):
                key = deduped[i].lower().strip().rstrip(":")
                val = deduped[i + 1].strip()
                flat_pairs.append((key, val))
                i += 2

        for key, val in flat_pairs:
            for attr, aliases in _KV_KEYS.items():
                if any(alias in key for alias in aliases):
                    if not kv.get(attr):  # take first match
                        kv[attr] = val

    for attr, val in kv.items():
        setattr(h, attr, val or None)

    return h


def _parse_footprint_acres_from_tables(all_tables: list[list[list[str]]]) -> Optional[float]:
    """
    Find the Section 1.3-equivalent table (has 'Acreage' as a column header).
    Return the acreage value from the data row.
    """
    for table in all_tables:
        for ri, row in enumerate(table):
            if any("acreage" in cell.lower() for cell in row if cell):
                # Find which column index has 'Acreage'
                for ci, cell in enumerate(row):
                    if "acreage" in cell.lower():
                        # Value is in next row, same column
                        if ri + 1 < len(table):
                            val = table[ri + 1][ci] if ci < len(table[ri + 1]) else None
                            parsed = _parse_float(val)
                            if parsed and 0 < parsed < 1000:
                                return parsed
    return None


# ---------------------------------------------------------------------------
# Stream table detection and parsing
# ---------------------------------------------------------------------------

_STREAM_LABEL_RE = re.compile(r"^(S|DF|DR)\d+$", re.IGNORECASE)
_WETLAND_LABEL_RE = re.compile(r"^(W|OW)\d+$", re.IGNORECASE)


def _is_stream_table(header_row: list[str]) -> bool:
    joined = " ".join(header_row).lower()
    return (
        "feature type" in joined
        or "stream name" in joined
        or ("feature" in joined and "drainage" in joined)
    )


def _is_wetland_table(header_row: list[str], data_rows: list[list[str]]) -> bool:
    """
    Must match header AND have data rows where first cell is W/OW label or N/A.
    This prevents false-positive matching on the 'Data Sources' table which
    contains 'USACE Wetland Regional Supplement' in its header.
    """
    joined = " ".join(header_row).lower()
    if not ("cowardin" in joined or "type of wetland" in joined or "pond" in joined):
        return False
    # At least one data row must have a valid wetland label or explicit N/A
    for row in data_rows:
        if not row:
            continue
        label = row[0].strip() if row[0] else ""
        if _WETLAND_LABEL_RE.match(label) or label.upper() in ("N/A", "NA"):
            return True
    return False


def _map_stream_columns(header: list[str]) -> dict:
    m = {}
    for i, h in enumerate(header):
        hl = h.lower()
        if "individual" in hl or "feature #" in hl or (i == 0 and "label" not in m):
            m["label"] = i
        elif "stream name" in hl or ("name" in hl and "stream" in hl):
            m["stream_name"] = i
        elif "usgs" in hl or "mapped" in hl or "7.5" in hl:
            m["usgs"] = i
        elif "feature type" in hl or ("type" in hl and "feature" in hl):
            m["type"] = i
        elif "jurisdict" in hl or ("status" in hl and "potential" in hl):
            m["status"] = i
        elif "acre" in hl:
            m["acres"] = i
    return m


def _map_wetland_columns(header: list[str]) -> dict:
    m = {}
    for i, h in enumerate(header):
        hl = h.lower()
        if "individual" in hl or "feature #" in hl or (i == 0 and "label" not in m):
            m["label"] = i
        elif "cowardin" in hl:
            m["cowardin"] = i
        elif "type" in hl and ("wetland" in hl or "pond" in hl):
            m["type"] = i
        elif "jurisdict" in hl or ("status" in hl and "potential" in hl):
            m["status"] = i
        elif "acre" in hl:
            m["acres"] = i
    return m


def _parse_stream_row(cells: list[str], col_map: dict) -> Optional[StreamFeature]:
    label = cells[col_map["label"]] if "label" in col_map and col_map["label"] < len(cells) else None
    if not label:
        return None
    if label.strip().lower() in ("individual feature #", "n/a", "na"):
        return None
    return StreamFeature(
        label=label.strip(),
        stream_name=cells[col_map["stream_name"]].strip() if "stream_name" in col_map and col_map["stream_name"] < len(cells) else None,
        mapped_on_usgs=_normalize_yn(cells[col_map["usgs"]].strip() if "usgs" in col_map and col_map["usgs"] < len(cells) else None),
        feature_type=cells[col_map["type"]].strip() if "type" in col_map and col_map["type"] < len(cells) else None,
        jurisdictional_status=_normalize_juris(cells[col_map["status"]].strip() if "status" in col_map and col_map["status"] < len(cells) else None),
        acres=_parse_float(cells[col_map["acres"]].strip() if "acres" in col_map and col_map["acres"] < len(cells) else None),
    )


def _parse_wetland_row(cells: list[str], col_map: dict) -> Optional[WetlandFeature]:
    label = cells[col_map["label"]] if "label" in col_map and col_map["label"] < len(cells) else None
    if not label:
        return None
    if label.strip().upper() in ("INDIVIDUAL FEATURE #", "N/A", "NA"):
        return None
    return WetlandFeature(
        label=label.strip(),
        wetland_type=cells[col_map["type"]].strip() if "type" in col_map and col_map["type"] < len(cells) else None,
        cowardin=cells[col_map["cowardin"]].strip() if "cowardin" in col_map and col_map["cowardin"] < len(cells) else None,
        jurisdictional_status=_normalize_juris(cells[col_map["status"]].strip() if "status" in col_map and col_map["status"] < len(cells) else None),
        acres=_parse_float(cells[col_map["acres"]].strip() if "acres" in col_map and col_map["acres"] < len(cells) else None),
    )


# ---------------------------------------------------------------------------
# DOCX parser
# ---------------------------------------------------------------------------

def _parse_docx(path: str) -> WWReport:
    from docx import Document

    doc = Document(path)
    report = WWReport(source_path=path)

    all_tables = []
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [_clean(cell.text) or "" for cell in row.cells]
            rows.append(cells)
        all_tables.append(rows)

    report.header = _parse_header_from_tables(all_tables)
    report.header.footprint_acres = _parse_footprint_acres_from_tables(all_tables)

    for table_rows in all_tables:
        if not table_rows:
            continue
        header_row = table_rows[0]
        data_rows = table_rows[1:]

        if _is_stream_table(header_row):
            col_map = _map_stream_columns(header_row)
            for row in data_rows:
                feat = _parse_stream_row(row, col_map)
                if feat:
                    report.streams.append(feat)

        elif _is_wetland_table(header_row, data_rows):
            col_map = _map_wetland_columns(header_row)
            for row in data_rows:
                feat = _parse_wetland_row(row, col_map)
                if feat:
                    report.wetlands.append(feat)

    return report


# ---------------------------------------------------------------------------
# PDF parser
# ---------------------------------------------------------------------------

def _parse_pdf(path: str) -> WWReport:
    import pdfplumber

    report = WWReport(source_path=path)
    full_text = ""
    all_tables: list[list[list[str]]] = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"
            for table in (page.extract_tables() or []):
                if not table:
                    continue
                rows = [[_clean(str(c)) or "" for c in row] for row in table]
                all_tables.append(rows)

    report.header = _parse_header_from_tables(all_tables)
    report.header.footprint_acres = _parse_footprint_acres_from_tables(all_tables)

    for table_rows in all_tables:
        if not table_rows:
            continue
        header_row = table_rows[0]
        data_rows = table_rows[1:]

        if _is_stream_table(header_row):
            col_map = _map_stream_columns(header_row)
            for row in data_rows:
                feat = _parse_stream_row(row, col_map)
                if feat:
                    report.streams.append(feat)

        elif _is_wetland_table(header_row, data_rows):
            col_map = _map_wetland_columns(header_row)
            for row in data_rows:
                feat = _parse_wetland_row(row, col_map)
                if feat:
                    report.wetlands.append(feat)

    return report


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_ww_report(path: str) -> WWReport:
    """Parse a WW report from .docx or .pdf. Raises ValueError for unknown format."""
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        return _parse_docx(path)
    elif suffix == ".pdf":
        return _parse_pdf(path)
    else:
        raise ValueError(f"Unsupported WW report format: {suffix}")
