"""
Parse ODOT Biological Assessment Reports (.docx or .pdf).

Key extraction challenges:
  - Habitat acreages in Section 3.2 are stored in Word SDT (content controls),
    which cell.text skips. We use raw XML text extraction instead.
  - Footprint acreage is in a table (Section 1.3) where 'Acreage' is a column
    header and the value is in the data row.
  - Bridge info uses a 'Label : Bx' / 'NBI - XXXX' pattern in Section 3.4.
"""

import re
from pathlib import Path
from typing import Optional

from ..models import BAReport, BridgeFeature, HabitatFeature, ProjectHeader
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
    h = ProjectHeader()
    kv: dict[str, str] = {}

    for table in all_tables:
        for row in table:
            # Deduplicate merged cells
            deduped = []
            seen = None
            for cell in row:
                if cell and cell != seen:
                    deduped.append(cell)
                    seen = cell
            i = 0
            while i + 1 < len(deduped):
                key = deduped[i].lower().strip().rstrip(":")
                val = deduped[i + 1].strip()
                for attr, aliases in _KV_KEYS.items():
                    if any(alias in key for alias in aliases):
                        if not kv.get(attr):
                            kv[attr] = val
                i += 2

    for attr, val in kv.items():
        setattr(h, attr, val or None)
    return h


def _parse_footprint_acres_from_tables(all_tables: list[list[list[str]]]) -> Optional[float]:
    """
    Section 1.3 table has 'Acreage' as a column header; value is in the data row
    of the same column. Works for both WW and BA templates.
    """
    for table in all_tables:
        for ri, row in enumerate(table):
            for ci, cell in enumerate(row):
                if cell.lower().strip() == "acreage":
                    if ri + 1 < len(table) and ci < len(table[ri + 1]):
                        val = _parse_float(table[ri + 1][ci])
                        if val and 0 < val < 10000:
                            return val
    return None


# ---------------------------------------------------------------------------
# Section 3.2: Species Habitat Analysis
# Acreages live in SDT content controls — we use the raw XML text which
# includes SDT values via tag-stripping.
# ---------------------------------------------------------------------------

def _extract_habitat_features(full_text: str) -> list[HabitatFeature]:
    """
    Extract habitat acreages from the full document text (SDT-inclusive).
    The values appear as digits immediately after the description label
    in the raw-stripped XML text.
    """
    features = []

    # Bat forested area total (Indiana Bat / NLEB / TCB)
    # In raw XML text: "...within 1000 feet of other forested/wooded habitat. 0.57 TOTAL"
    m_total = re.search(
        r"within\s+1000\s+feet\s+of\s+other\s+forested/wooded\s+habitat\s*\.?\s*([\d.]+)\s+TOTAL",
        full_text, re.IGNORECASE
    )
    if m_total:
        features.append(HabitatFeature(
            label="bat_forest_total",
            species="Indiana Bat / NLEB / TCB",
            acres=_parse_float(m_total.group(1)),
        ))

    # Trees within 100 ft — appears in SDT adjacent to "Acres of trees within 100 feet"
    # In raw text: "Acres of trees within 100 feet from pavement 0.57"
    m_100 = re.search(
        r"Acres\s+of\s+trees\s+within\s+100\s+feet?\s+from\s+pavement\s+([\d.]+)",
        full_text, re.IGNORECASE
    )
    if m_100:
        features.append(HabitatFeature(
            label="bat_forest_within_100ft",
            species="Indiana Bat / NLEB / TCB",
            acres=_parse_float(m_100.group(1)),
            distance_band="within 100 ft",
        ))

    # Trees 100-300 ft
    m_100_300 = re.search(
        r"Acres\s+of\s+trees\s+between\s+100[-\s–]+300\s+feet?\s+from\s+pavement\s+([\d.NA]+)",
        full_text, re.IGNORECASE
    )
    if m_100_300:
        raw = m_100_300.group(1)
        features.append(HabitatFeature(
            label="bat_forest_100_300ft",
            species="Indiana Bat / NLEB / TCB",
            acres=_parse_float(raw) if "NA" not in raw.upper() else None,
            distance_band="100-300 ft",
        ))

    # Trees >300 ft
    m_300 = re.search(
        r"Acres\s+of\s+trees\s+greater\s+than\s+300\s+feet?\s+from\s+pavement\s+([\d.NA]+)",
        full_text, re.IGNORECASE
    )
    if m_300:
        raw = m_300.group(1)
        features.append(HabitatFeature(
            label="bat_forest_over_300ft",
            species="Indiana Bat / NLEB / TCB",
            acres=_parse_float(raw) if "NA" not in raw.upper() else None,
            distance_band=">300 ft",
        ))

    # ABB native perennial plant vegetation
    # "Number of acres of native perennial plant vegetation ... within the Project Footprint
    #  (include shapefiles). 0.29"
    m_abb = re.search(
        r"Number\s+of\s+acres\s+of\s+native\s+perennial\s+plant\s+vegetation[^.]+\.\s*([\d.]+)",
        full_text, re.IGNORECASE
    )
    if m_abb:
        features.append(HabitatFeature(
            label="abb_native_veg",
            species="American Burying Beetle",
            acres=_parse_float(m_abb.group(1)),
        ))

    return features


# ---------------------------------------------------------------------------
# Section 3.4: Bridge/Culvert/Structure Assessment
# ---------------------------------------------------------------------------

def _extract_bridges(text: str) -> list[BridgeFeature]:
    """
    Look for bridge labels (B1, B2, ...) and NBI numbers in the Section 3.4
    bridge inspection block.
    """
    bridges = []
    seen_labels: set[str] = set()

    # Pattern: "Label : B1" (in table cell text)
    label_nbi_re = re.compile(
        r"Label\s*[:\-]?\s*(B\d+).*?NBI\s*[-–]?\s*(\d{4,})",
        re.IGNORECASE | re.DOTALL
    )
    for m in label_nbi_re.finditer(text):
        label = m.group(1).strip()
        if label in seen_labels:
            continue
        seen_labels.add(label)
        water = re.search(
            r"Water\s+Body\s*\(?[^\)]*\)?\s*\n?\s*([A-Za-z][\w\s]+?)(?:\n|Multi|Material|$)",
            text[m.start():m.start() + 500], re.IGNORECASE
        )
        bridges.append(BridgeFeature(
            label=label,
            nbi_number=m.group(2).strip(),
            water_body=_clean(water.group(1)) if water else None,
        ))

    # Fallback: scan for "B1 ... NBI 6415" inline in narrative
    if not bridges:
        for m in re.finditer(r"\b(B\d+)\b[^.]*?NBI\s*[-–]?\s*(\d{4,})", text, re.IGNORECASE):
            label = m.group(1)
            if label not in seen_labels:
                seen_labels.add(label)
                bridges.append(BridgeFeature(label=label, nbi_number=m.group(2)))

    return bridges


# ---------------------------------------------------------------------------
# DOCX parser
# ---------------------------------------------------------------------------

def _parse_docx(path: str) -> BAReport:
    from docx import Document

    doc = Document(path)
    report = BAReport(source_path=path)

    # Build table list for header and footprint parsing
    all_tables: list[list[list[str]]] = []
    for table in doc.tables:
        rows = [[_clean(cell.text) or "" for cell in row.cells] for row in table.rows]
        all_tables.append(rows)

    report.header = _parse_header_from_tables(all_tables)
    report.footprint_acres = _parse_footprint_acres_from_tables(all_tables)

    # Use raw XML text (includes SDT content) for habitat and bridge extraction
    full_text = _full_text_from_docx(path)
    report.habitat_features = _extract_habitat_features(full_text)
    report.bridges = _extract_bridges(full_text)

    return report


# ---------------------------------------------------------------------------
# PDF parser
# ---------------------------------------------------------------------------

def _parse_pdf(path: str) -> BAReport:
    import pdfplumber

    report = BAReport(source_path=path)
    all_tables: list[list[list[str]]] = []
    full_text = ""

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"
            for table in (page.extract_tables() or []):
                if not table:
                    continue
                rows = [[_clean(str(c)) or "" for c in row] for row in table]
                all_tables.append(rows)

    report.header = _parse_header_from_tables(all_tables)
    report.footprint_acres = _parse_footprint_acres_from_tables(all_tables)
    report.habitat_features = _extract_habitat_features(full_text)
    report.bridges = _extract_bridges(full_text)

    return report


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_ba_report(path: str) -> BAReport:
    """Parse a BA report from .docx or .pdf. Raises ValueError for unknown format."""
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        return _parse_docx(path)
    elif suffix == ".pdf":
        return _parse_pdf(path)
    else:
        raise ValueError(f"Unsupported BA report format: {suffix}")
