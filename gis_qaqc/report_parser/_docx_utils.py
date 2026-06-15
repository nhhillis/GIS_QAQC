"""
Shared DOCX utilities.

python-docx's cell.text skips Structured Document Tags (<w:sdt>) which ODOT
templates use for user-fill-in values (e.g. habitat acreages in Section 3.2).
_full_text_from_docx() bypasses this by stripping raw XML tags, giving a flat
string that includes SDT content as well as regular text.
"""

import re
import zipfile
from typing import Optional


def _full_text_from_docx(path: str) -> str:
    """
    Extract all text from a .docx file including content inside SDTs
    (Structured Document Tags / content controls), which cell.text misses.
    Returns a single space-joined string.
    """
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    # Strip all XML tags; what remains is text content + whitespace
    text = re.sub(r"<[^>]+>", " ", xml)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _table_rows_from_docx(path: str) -> list[list[list[str]]]:
    """
    Return all tables from a docx as a list of tables, each a list of rows,
    each row a list of cell text strings (using cell.text — no SDT).
    Use _full_text_from_docx when you need SDT values.
    """
    from docx import Document
    doc = Document(path)
    result = []
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [_clean(cell.text) or "" for cell in row.cells]
            rows.append(cells)
        result.append(rows)
    return result


def _clean(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return re.sub(r"\s+", " ", str(text)).strip() or None
