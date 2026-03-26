"""
attendance_extractor.py
=======================
Module for extracting and preprocessing college attendance data from
an HTML-disguised .xls file.

Responsibilities:
    1. Data extraction from HTML .xls file
    2. Parsing attendance entries per date cell
    3. Structuring subject-wise attendance data
    4. Classifying subject types (Core / Elective / Practical)

Does NOT perform: marks calculation, waiver optimisation, or any analytics.

Author : AntiGravity
"""

from __future__ import annotations

import re
import logging
from datetime import date
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
import pandas as pd
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("attendance_extractor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex: captures status (P/A) and class type (Th / tu / PR)
# Handles optional whitespace inside the parentheses.
ENTRY_PATTERN = re.compile(r"([AP])\(\s*(Th|tu|PR)\s*\)")

# Metadata columns that are never date columns
META_COLS = {"sno", "Student", "Roll No", "Total Present", "Total Absent"}

# Subject type classification rules
SUBJECT_TYPE_RULES: dict[str, set[str]] = {
    "Core":       {"Th", "tu"},        # must have BOTH theory AND tutorial
    "Elective":   {"Th"},              # only theory
    "Practical":  {"PR"},              # only practical
}


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class AttendanceCount:
    """Holds present / total for a single class type."""
    total: int = 0
    present: int = 0

    def __repr__(self) -> str:
        return f"{self.present}/{self.total}"


@dataclass
class SubjectRecord:
    """Full attendance record for one subject row."""
    sno: str
    subject: str
    roll_no: str
    subject_type: str = "Unknown"

    # Per-type attendance counts
    Th: AttendanceCount = field(default_factory=AttendanceCount)
    tu: AttendanceCount = field(default_factory=AttendanceCount)
    PR: AttendanceCount = field(default_factory=AttendanceCount)

    # Raw date-keyed data preserved for downstream use
    raw_date_entries: dict[str, list[tuple[str, str]]] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        """Flat dict ready for DataFrame construction."""
        return {
            "sno":          self.sno,
            "subject":      self.subject,
            "roll_no":      self.roll_no,
            "subject_type": self.subject_type,
            "Th_present":   self.Th.present,
            "Th_total":     self.Th.total,
            "tu_present":   self.tu.present,
            "tu_total":     self.tu.total,
            "PR_present":   self.PR.present,
            "PR_total":     self.PR.total,
        }


# ---------------------------------------------------------------------------
# Step 1 – File Loading & HTML Parsing
# ---------------------------------------------------------------------------

def load_html(filepath: str | Path) -> BeautifulSoup:
    """
    Read the .xls (HTML) file and return a BeautifulSoup object.

    Parameters
    ----------
    filepath : path-like
        Path to the HTML-as-xls attendance file.

    Returns
    -------
    BeautifulSoup
        Parsed document.

    Raises
    ------
    FileNotFoundError
        If the file path does not exist.
    ValueError
        If no HTML table is found in the file.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    logger.info("Loading file: %s", filepath)
    raw = filepath.read_text(encoding="utf-8", errors="replace")

    # ------------------------------------------------------------------ #
    # Sanitise known HTML quirks in this file format:
    #   • <th>Roll No</th>;  → stray semicolon breaks table parsing
    # ------------------------------------------------------------------ #
    raw = raw.replace("<th>Roll No</th>;", "<th>Roll No</th>")

    # Fix malformed nesting: </td> </table> → </table></td>
    # The attendance file puts </td> before </table> inside date cells,
    # which breaks BeautifulSoup's parse tree (especially for row 1/OM).
    raw = re.sub(r"</td>\s*</table>", "</table></td>", raw)

    soup = BeautifulSoup(raw, "html.parser")

    if not soup.find("table"):
        raise ValueError("No <table> found in the provided file.")

    logger.info("HTML loaded and sanitised successfully.")
    return soup


# ---------------------------------------------------------------------------
# Step 2 – Header Extraction & Deduplication
# ---------------------------------------------------------------------------

def _deduplicate_columns(columns: list[str]) -> list[str]:
    """
    Append `.1`, `.2`, … suffixes to duplicate column names.

    The original first occurrence keeps its name; every subsequent
    duplicate gets the next available integer suffix.

    Parameters
    ----------
    columns : list of str
        Raw column names, possibly containing duplicates.

    Returns
    -------
    list of str
        Column names guaranteed to be unique.
    """
    seen: dict[str, int] = {}
    unique: list[str] = []

    for col in columns:
        if col not in seen:
            seen[col] = 0
            unique.append(col)
        else:
            seen[col] += 1
            unique.append(f"{col}.{seen[col]}")

    return unique


def extract_headers(soup: BeautifulSoup) -> list[str]:
    """
    Locate the header row and return deduplicated column names.

    Identification rule: the first three cells of the row must be
    exactly ``sno``, ``Student``, ``Roll No`` (case-sensitive).

    Parameters
    ----------
    soup : BeautifulSoup
        Parsed HTML document.

    Returns
    -------
    list of str
        Deduplicated column names including metadata and date columns.

    Raises
    ------
    ValueError
        If no matching header row is found.
    """
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) >= 3:
            labels = [c.get_text(strip=True) for c in cells[:3]]
            if labels == ["sno", "Student", "Roll No"]:
                raw_cols = [c.get_text(strip=True) for c in cells]
                deduped = _deduplicate_columns(raw_cols)
                logger.info(
                    "Header row found: %d columns (%d date cols).",
                    len(deduped),
                    len(deduped) - len(META_COLS),
                )
                return deduped

    raise ValueError(
        "Header row not found. Expected first three cells to be "
        "'sno', 'Student', 'Roll No'."
    )


# ---------------------------------------------------------------------------
# Step 3 – Cell Text Extraction (handles nested tables)
# ---------------------------------------------------------------------------

def _extract_cell_text(td) -> str:
    """
    Return the combined text content of a <td> cell.

    If the cell contains a nested <table>, concatenate the text of every
    nested <td> with a space separator.  Otherwise return the direct text.

    Parameters
    ----------
    td : bs4.element.Tag
        A ``<td>`` or ``<th>`` element.

    Returns
    -------
    str
        Combined plain-text content, stripped of extra whitespace.
    """
    nested_table = td.find("table")
    if nested_table:
        parts = [
            inner_td.get_text(strip=True)
            for inner_td in nested_table.find_all("td")
        ]
        return " ".join(filter(None, parts))
    return td.get_text(strip=True)


# ---------------------------------------------------------------------------
# Step 4 – Subject Row Extraction → Raw DataFrame
# ---------------------------------------------------------------------------

def extract_raw_dataframe(
    soup: BeautifulSoup,
    headers: list[str],
) -> pd.DataFrame:
    """
    Extract all valid subject rows into a raw DataFrame.

    A row is considered a valid subject row when its first cell is a
    non-empty numeric string (the ``sno`` field).

    Rows with mismatched column counts are handled gracefully:
    * Extra cells are silently trimmed.
    * Missing cells are filled with empty strings.

    Parameters
    ----------
    soup : BeautifulSoup
        Parsed HTML document.
    headers : list of str
        Deduplicated column names from :func:`extract_headers`.

    Returns
    -------
    pd.DataFrame
        Raw DataFrame — one row per subject, raw string cell values.
    """
    n_cols = len(headers)
    rows: list[dict[str, str]] = []

    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue

        first_val = cells[0].get_text(strip=True)
        if not first_val.isdigit():
            continue  # skip header and non-subject rows

        cell_texts = [_extract_cell_text(c) for c in cells]

        # ---------------------------------------------------------------- #
        # Align to header length: pad with "" or trim as needed.
        # This handles sno=1 whose HTML is broken (4 cells instead of 43).
        # ---------------------------------------------------------------- #
        if len(cell_texts) < n_cols:
            logger.debug(
                "Row sno=%s has %d cells; expected %d — padding with empty strings.",
                first_val, len(cell_texts), n_cols,
            )
            cell_texts.extend([""] * (n_cols - len(cell_texts)))
        elif len(cell_texts) > n_cols:
            logger.debug(
                "Row sno=%s has %d cells; expected %d — trimming.",
                first_val, len(cell_texts), n_cols,
            )
            cell_texts = cell_texts[:n_cols]

        rows.append(dict(zip(headers, cell_texts)))

    logger.info("Extracted %d subject row(s) from HTML.", len(rows))
    df = pd.DataFrame(rows, columns=headers)
    return df


# ---------------------------------------------------------------------------
# Step 5 – Entry Parsing
# ---------------------------------------------------------------------------

def parse_cell_entries(cell_text: str) -> list[tuple[str, str]]:
    """
    Extract all ``(status, class_type)`` pairs from a single cell string.

    Pattern matched: ``[AP]( Th | tu | PR )``

    Parameters
    ----------
    cell_text : str
        Raw text from one attendance cell.

    Returns
    -------
    list of (str, str)
        E.g. ``[('P', 'Th'), ('A', 'tu')]``.
        Empty list if the cell is empty or contains no valid entries.
    """
    if not cell_text or not cell_text.strip():
        return []
    return ENTRY_PATTERN.findall(cell_text)


# ---------------------------------------------------------------------------
# Step 6 – Per-Subject Aggregation
# ---------------------------------------------------------------------------

def _identify_date_columns(headers: list[str]) -> list[str]:
    """Return only the date-column names (excludes META_COLS)."""
    return [h for h in headers if h not in META_COLS]


def _resolve_date_columns(
    date_cols: list[str],
    start_date: date,
) -> dict[str, str]:
    """
    Map deduplicated column names to full date strings (DD-Mon-YYYY).

    Month boundaries are inferred by detecting when the day number
    decreases (e.g. 30 → 02 means Jan → Feb).

    Parameters
    ----------
    date_cols : list of str
        Deduplicated date-column names (e.g. '02', '05', '02.1').
    start_date : date
        The date of the first column (used to anchor year and month).

    Returns
    -------
    dict[str, str]
        Mapping from column name → 'DD-Mon-YYYY' (e.g. '02-Jan-2026').
    """
    mapping: dict[str, str] = {}
    current_year = start_date.year
    current_month = start_date.month
    prev_day = 0

    for col in date_cols:
        base = col.split(".")[0]  # strip dedup suffix (.1, .2, …)
        try:
            day = int(base)
        except ValueError:
            continue

        # Day decreased → crossed into next month
        if day < prev_day:
            current_month += 1
            if current_month > 12:
                current_month = 1
                current_year += 1

        prev_day = day

        try:
            d = date(current_year, current_month, day)
            mapping[col] = d.strftime("%d %b %Y, %A")
        except ValueError:
            mapping[col] = col  # fallback for invalid dates

    logger.info(
        "Resolved %d date columns (spanning %s → %s).",
        len(mapping),
        list(mapping.values())[0] if mapping else "?",
        list(mapping.values())[-1] if mapping else "?",
    )
    return mapping


def _aggregate_subject(
    row: pd.Series,
    date_cols: list[str],
    date_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Aggregate attendance counts for a single subject row.

    Parameters
    ----------
    row : pd.Series
        One row of the raw DataFrame.
    date_cols : list of str
        Names of date columns to iterate over.

    Returns
    -------
    dict
        Keys: ``Th_present``, ``Th_total``, ``tu_present``,
        ``tu_total``, ``PR_present``, ``PR_total``,
        plus ``raw_date_entries`` (dict[date_col → entries]).
    """
    counts: dict[str, AttendanceCount] = {
        "Th": AttendanceCount(),
        "tu": AttendanceCount(),
        "PR": AttendanceCount(),
    }
    raw_entries: dict[str, list[tuple[str, str]]] = {}

    for col in date_cols:
        cell_text = str(row.get(col, "")).strip()
        entries = parse_cell_entries(cell_text)
        if entries:
            key = date_mapping.get(col, col) if date_mapping else col
            raw_entries[key] = entries
        for status, cls_type in entries:
            if cls_type in counts:
                counts[cls_type].total += 1
                if status == "P":
                    counts[cls_type].present += 1

    return {
        "Th_present":      counts["Th"].present,
        "Th_total":        counts["Th"].total,
        "tu_present":      counts["tu"].present,
        "tu_total":        counts["tu"].total,
        "PR_present":      counts["PR"].present,
        "PR_total":        counts["PR"].total,
        "raw_date_entries": raw_entries,
    }


# ---------------------------------------------------------------------------
# Step 7 – Subject Type Classification
# ---------------------------------------------------------------------------

def classify_subject(th_total: int, tu_total: int, pr_total: int) -> str:
    """
    Classify a subject based on which class types were observed.

    Rules
    -----
    * **Core**       — has both Theory (Th) and Tutorial (tu) classes.
    * **Elective**   — has only Theory (Th) classes.
    * **Practical**  — has only Practical (PR) classes.
    * **Unknown**    — does not fit any category above.

    Parameters
    ----------
    th_total : int
        Total theory classes recorded.
    tu_total : int
        Total tutorial classes recorded.
    pr_total : int
        Total practical classes recorded.

    Returns
    -------
    str
        One of ``"Core"``, ``"Elective"``, ``"Practical"``, ``"Unknown"``.
    """
    has_th = th_total > 0
    has_tu = tu_total > 0
    has_pr = pr_total > 0

    if has_th and has_tu:
        return "Core"
    if has_th and not has_tu:
        return "Elective"
    if has_pr and not has_th and not has_tu:
        return "Practical"
    return "Unknown"


# ---------------------------------------------------------------------------
# Step 8 – Build Structured Records
# ---------------------------------------------------------------------------

def build_subject_records(
    raw_df: pd.DataFrame,
    headers: list[str],
    start_date: date | None = None,
) -> list[SubjectRecord]:
    """
    Convert the raw DataFrame into a list of :class:`SubjectRecord` objects.

    Parameters
    ----------
    raw_df : pd.DataFrame
        Raw DataFrame produced by :func:`extract_raw_dataframe`.
    headers : list of str
        Full deduplicated header list.

    Returns
    -------
    list of SubjectRecord
        One record per subject row.
    """
    date_cols = _identify_date_columns(headers)
    date_mapping = _resolve_date_columns(date_cols, start_date) if start_date else None
    records: list[SubjectRecord] = []

    for _, row in raw_df.iterrows():
        agg = _aggregate_subject(row, date_cols, date_mapping)
        subject_type = classify_subject(
            agg["Th_total"], agg["tu_total"], agg["PR_total"]
        )

        rec = SubjectRecord(
            sno=str(row.get("sno", "")).strip(),
            subject=str(row.get("Student", "")).strip(),
            roll_no=str(row.get("Roll No", "")).strip(),
            subject_type=subject_type,
            Th=AttendanceCount(
                total=agg["Th_total"], present=agg["Th_present"]
            ),
            tu=AttendanceCount(
                total=agg["tu_total"], present=agg["tu_present"]
            ),
            PR=AttendanceCount(
                total=agg["PR_total"], present=agg["PR_present"]
            ),
            raw_date_entries=agg["raw_date_entries"],
        )
        records.append(rec)
        logger.debug(
            "Subject %s '%s' → type=%s | Th=%s | tu=%s | PR=%s",
            rec.sno, rec.subject, rec.subject_type,
            rec.Th, rec.tu, rec.PR,
        )

    logger.info("Built %d SubjectRecord(s).", len(records))
    return records


# ---------------------------------------------------------------------------
# Step 9 – Final Output DataFrame
# ---------------------------------------------------------------------------

def records_to_dataframe(records: list[SubjectRecord]) -> pd.DataFrame:
    """
    Convert a list of :class:`SubjectRecord` objects into a clean DataFrame.

    Columns
    -------
    sno, subject, roll_no, subject_type,
    Th_present, Th_total, tu_present, tu_total, PR_present, PR_total

    Parameters
    ----------
    records : list of SubjectRecord

    Returns
    -------
    pd.DataFrame
    """
    return pd.DataFrame([r.to_dict() for r in records])


# ---------------------------------------------------------------------------
# Public API – Single Entry Point
# ---------------------------------------------------------------------------

def extract_attendance(
    filepath: str | Path,
    start_date: date | None = None,
) -> tuple[pd.DataFrame, list[SubjectRecord]]:
    """
    Full extraction pipeline: load → parse → structure → classify.

    Parameters
    ----------
    filepath : path-like
        Path to the HTML-disguised .xls attendance file.

    Returns
    -------
    summary_df : pd.DataFrame
        Clean, flat DataFrame with one row per subject and columns:
        ``sno``, ``subject``, ``roll_no``, ``subject_type``,
        ``Th_present``, ``Th_total``, ``tu_present``, ``tu_total``,
        ``PR_present``, ``PR_total``.

    records : list of SubjectRecord
        Rich structured objects — useful for downstream pipelines that
        need access to per-date raw entries.

    Example
    -------
    >>> df, records = extract_attendance("Student_Attendance_test.xls")
    >>> print(df[["subject", "subject_type", "Th_present", "Th_total"]])
    """
    # ---- Stage 1: Load HTML ----
    soup = load_html(filepath)

    # ---- Stage 2: Extract and deduplicate headers ----
    headers = extract_headers(soup)

    # ---- Stage 3: Pull raw data rows ----
    raw_df = extract_raw_dataframe(soup, headers)

    # ---- Stage 4: Aggregate + classify ----
    records = build_subject_records(raw_df, headers, start_date)

    # ---- Stage 5: Export flat DataFrame ----
    summary_df = records_to_dataframe(records)

    logger.info("Extraction complete. Shape: %s", summary_df.shape)
    return summary_df, records


# ---------------------------------------------------------------------------
# CLI / Quick Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json

    target = sys.argv[1] if len(sys.argv) > 1 else "C:/Users/user/Documents/Waiver_Optimizer/Student_Attendance_Demo.xls"

    df, records = extract_attendance(target, start_date=date(2026, 1, 2))

    print("\n" + "=" * 70)
    print("ATTENDANCE SUMMARY")
    print("=" * 70)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)
    print(df.to_string(index=False))

    print("\n" + "=" * 70)
    print("PER-SUBJECT DETAIL")
    print("=" * 70)
    for rec in records:
        print(f"\n[{rec.sno}] {rec.subject}")
        print(f"    Roll No      : {rec.roll_no}")
        print(f"    Type         : {rec.subject_type}")
        print(f"    Theory       : {rec.Th.present}/{rec.Th.total} present")
        print(f"    Tutorial     : {rec.tu.present}/{rec.tu.total} present")
        print(f"    Practical    : {rec.PR.present}/{rec.PR.total} present")
        print(f"    Active dates : {list(rec.raw_date_entries.keys())}")
