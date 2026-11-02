"""CSV and Excel downloads built from the tables the user is looking at.

Numbers are written as numbers when they parse, so a spreadsheet can sum
them. Values that do not parse stay as text for the reviewer to fix.
"""

from __future__ import annotations

import io

import pandas as pd

from app.amounts import parse_decimal
from app.quality import QualityIssue

_NUMERIC_LINE_COLUMNS = ("Quantity", "Unit price", "Line total")
_NUMERIC_HEADER_FIELDS = {"Subtotal", "Tax", "Grand total"}


def build_csv(header: pd.DataFrame, lines: pd.DataFrame) -> bytes:
    """One row per line item, with the quote header repeated on each row."""

    flat = _flat_frame(header, lines)
    buffer = io.StringIO()
    flat.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8")


def build_excel(header: pd.DataFrame, lines: pd.DataFrame, issues: list[QualityIssue]) -> bytes:
    """A workbook with header, line items, and the current review flags."""

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        _typed_header(header).to_excel(writer, sheet_name="Header", index=False)
        _typed_lines(lines).to_excel(writer, sheet_name="Line items", index=False)
        _issues_frame(issues).to_excel(writer, sheet_name="Review flags", index=False)
        _autosize(writer)
    return buffer.getvalue()


def _flat_frame(header: pd.DataFrame, lines: pd.DataFrame) -> pd.DataFrame:
    header_values = {}
    if header is not None and not header.empty:
        for _, row in header.iterrows():
            label = str(row.get("Field", "")).strip()
            if label:
                header_values[label] = row.get("Value", "")
    body = lines.copy() if lines is not None else pd.DataFrame()
    if body.empty:
        body = pd.DataFrame([{"Line": ""}])
    for label, value in header_values.items():
        body[label] = value
    front = [column for column in header_values if column in body.columns]
    rest = [column for column in body.columns if column not in front]
    ordered = body[front + rest]
    return _coerce_numeric(ordered, _NUMERIC_LINE_COLUMNS + tuple(_NUMERIC_HEADER_FIELDS))


def _typed_header(header: pd.DataFrame) -> pd.DataFrame:
    frame = header.copy() if header is not None else pd.DataFrame()
    if frame.empty:
        return frame
    typed_values = []
    for _, row in frame.iterrows():
        label = str(row.get("Field", ""))
        raw = row.get("Value", "")
        if label in _NUMERIC_HEADER_FIELDS:
            parsed = parse_decimal(raw)
            typed_values.append(parsed if parsed is not None else raw)
        else:
            typed_values.append(raw)
    frame = frame.copy()
    frame["Value"] = typed_values
    return frame


def _typed_lines(lines: pd.DataFrame) -> pd.DataFrame:
    frame = lines.copy() if lines is not None else pd.DataFrame()
    return _coerce_numeric(frame, _NUMERIC_LINE_COLUMNS)


def _coerce_numeric(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    if frame.empty:
        return frame
    output = frame.copy()
    for column in columns:
        if column not in output.columns:
            continue
        output[column] = [
            parsed if (parsed := parse_decimal(value)) is not None else value
            for value in output[column].tolist()
        ]
    return output


def _issues_frame(issues: list[QualityIssue]) -> pd.DataFrame:
    if not issues:
        return pd.DataFrame([{"Severity": "", "Field": "", "Message": "No review flags."}])
    return pd.DataFrame(
        [{"Severity": issue.severity, "Field": issue.field, "Message": issue.message} for issue in issues]
    )


def _autosize(writer: pd.ExcelWriter) -> None:
    for worksheet in writer.book.worksheets:
        for column in worksheet.columns:
            letter = column[0].column_letter
            width = 12
            for cell in column[:40]:
                text = "" if cell.value is None else str(cell.value)
                width = max(width, min(len(text) + 2, 60))
            worksheet.column_dimensions[letter].width = width
