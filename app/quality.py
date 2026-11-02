"""Data-quality checks that do not call a model.

These run on the editable tables, so a correction clears a math warning
without another API call. Confidence flags still use the scores from the
extraction. A human edit does not pretend to raise those scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd

from app.amounts import format_amount, parse_decimal
from app.schema import HEADER_FIELDS, LINE_VALUE_FIELDS, LineItem, SupplierQuote

Severity = Literal["error", "warning"]

HEADER_COLUMNS = ["Field", "Value", "Confidence %"]
LINE_COLUMNS = [
    "Line",
    "SKU",
    "Description",
    "Quantity",
    "Unit",
    "Unit price",
    "Line total",
    "Confidence %",
]


@dataclass(frozen=True)
class QualityIssue:
    severity: Severity
    field: str
    message: str


def header_frame(quote: SupplierQuote) -> pd.DataFrame:
    rows = []
    for value_name, score_name, label in HEADER_FIELDS:
        score = float(getattr(quote, score_name))
        rows.append(
            {
                "Field": label,
                "Value": getattr(quote, value_name),
                "Confidence %": _percent(score),
            }
        )
    return pd.DataFrame(rows, columns=HEADER_COLUMNS)


def line_frame(quote: SupplierQuote) -> pd.DataFrame:
    rows = []
    for index, item in enumerate(quote.line_items, start=1):
        rows.append(_line_row(index, item))
    if not rows:
        return pd.DataFrame(columns=LINE_COLUMNS)
    return pd.DataFrame(rows, columns=LINE_COLUMNS)


def _line_row(index: int, item: LineItem) -> dict[str, object]:
    scores = [
        item.description_confidence,
        item.quantity_confidence,
        item.unit_price_confidence,
        item.line_total_confidence,
    ]
    present = [
        score
        for score, value in (
            (item.description_confidence, item.description),
            (item.quantity_confidence, item.quantity),
            (item.unit_price_confidence, item.unit_price),
            (item.line_total_confidence, item.line_total),
        )
        if str(value).strip()
    ]
    confidence = min(present) if present else min(scores)
    return {
        "Line": index,
        "SKU": item.sku,
        "Description": item.description,
        "Quantity": item.quantity,
        "Unit": item.unit,
        "Unit price": item.unit_price,
        "Line total": item.line_total,
        "Confidence %": _percent(confidence),
    }


def review_issues(
    header: pd.DataFrame,
    lines: pd.DataFrame,
    *,
    threshold: float,
    model_warnings: list[str] | None = None,
) -> list[QualityIssue]:
    """Return review flags for the current header and line tables."""

    issues: list[QualityIssue] = []
    values = _header_map(header)

    def value_of(label: str) -> str:
        return str(values.get(label, {}).get("value", "") or "").strip()

    def score_of(label: str) -> float:
        raw = values.get(label, {}).get("score", 0)
        try:
            return float(raw) / 100.0
        except (TypeError, ValueError):
            return 0.0

    supplier = value_of("Supplier")
    if not supplier:
        issues.append(QualityIssue("error", "Supplier", "Supplier name is missing."))
    elif score_of("Supplier") < threshold:
        issues.append(_low_confidence("Supplier", score_of("Supplier"), threshold))

    if not value_of("Quote number"):
        issues.append(QualityIssue("warning", "Quote number", "Quote number is missing."))
    elif score_of("Quote number") < threshold:
        issues.append(_low_confidence("Quote number", score_of("Quote number"), threshold))

    _date_issue(issues, "Quote date", value_of("Quote date"), score_of("Quote date"), threshold)
    _date_issue(issues, "Valid until", value_of("Valid until"), score_of("Valid until"), threshold, required=False)

    currency = value_of("Currency")
    if not currency:
        issues.append(QualityIssue("warning", "Currency", "Currency is missing."))
    elif len(currency) != 3 or not currency.isalpha():
        issues.append(
            QualityIssue("warning", "Currency", f"Currency '{currency}' is not a 3-letter ISO code.")
        )
    elif score_of("Currency") < threshold:
        issues.append(_low_confidence("Currency", score_of("Currency"), threshold))

    for label in ("Payment terms", "Delivery / lead time", "Notes"):
        if value_of(label) and score_of(label) < threshold:
            issues.append(_low_confidence(label, score_of(label), threshold))

    if not value_of("Grand total"):
        issues.append(QualityIssue("warning", "Grand total", "Grand total is missing."))
    elif score_of("Grand total") < threshold:
        issues.append(_low_confidence("Grand total", score_of("Grand total"), threshold))

    for label in ("Subtotal", "Tax"):
        if value_of(label) and score_of(label) < threshold:
            issues.append(_low_confidence(label, score_of(label), threshold))

    line_rows = _line_records(lines)
    if not line_rows:
        issues.append(QualityIssue("error", "Line items", "No line items were extracted."))
    else:
        issues.extend(_line_issues(line_rows, threshold))
        issues.extend(_duplicate_issues(line_rows))
        issues.extend(_total_issues(line_rows, values))

    for warning in model_warnings or []:
        text = warning.strip()
        if text:
            issues.append(QualityIssue("warning", "Model", text))
    return issues


def _date_issue(
    issues: list[QualityIssue],
    label: str,
    raw: str,
    score: float,
    threshold: float,
    *,
    required: bool = True,
) -> None:
    if not raw:
        if required:
            issues.append(QualityIssue("warning", label, f"{label} is missing."))
        return
    if not _is_iso_date(raw):
        issues.append(
            QualityIssue(
                "warning",
                label,
                f"{label} '{raw}' is not YYYY-MM-DD, so the day and month may be swapped.",
            )
        )
        return
    if score < threshold:
        issues.append(_low_confidence(label, score, threshold))


def _is_iso_date(raw: str) -> bool:
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _low_confidence(label: str, score: float, threshold: float) -> QualityIssue:
    return QualityIssue(
        "warning",
        label,
        f"{label} confidence is {score:.0%}, below the {threshold:.0%} review threshold.",
    )


def _header_map(header: pd.DataFrame) -> dict[str, dict[str, object]]:
    if header is None or header.empty:
        return {}
    mapped: dict[str, dict[str, object]] = {}
    for _, row in header.iterrows():
        label = str(row.get("Field", "")).strip()
        if not label:
            continue
        mapped[label] = {"value": row.get("Value", ""), "score": row.get("Confidence %", 0)}
    return mapped


def _line_records(lines: pd.DataFrame) -> list[dict[str, object]]:
    if lines is None or lines.empty:
        return []
    records = []
    for index, row in lines.reset_index(drop=True).iterrows():
        description = str(row.get("Description", "") or "").strip()
        sku = str(row.get("SKU", "") or "").strip()
        content_columns = ("SKU", "Description", "Quantity", "Unit", "Unit price", "Line total")
        if not any(str(row.get(column, "") or "").strip() for column in content_columns):
            continue
        records.append(
            {
                "line": index + 1,
                "sku": sku,
                "description": description,
                "quantity": str(row.get("Quantity", "") or "").strip(),
                "unit": str(row.get("Unit", "") or "").strip(),
                "unit_price": str(row.get("Unit price", "") or "").strip(),
                "line_total": str(row.get("Line total", "") or "").strip(),
                "score": row.get("Confidence %", 0),
            }
        )
    return records


def _line_issues(rows: list[dict[str, object]], threshold: float) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for row in rows:
        line = int(row["line"])
        label = f"Line {line}"
        if not row["description"]:
            issues.append(QualityIssue("error", label, f"{label} has no description."))
        quantity = parse_decimal(row["quantity"])
        price = parse_decimal(row["unit_price"])
        total = parse_decimal(row["line_total"])
        if row["quantity"] and quantity is None:
            issues.append(QualityIssue("warning", label, f"{label} quantity '{row['quantity']}' is not a number."))
        if row["unit_price"] and price is None:
            issues.append(
                QualityIssue("warning", label, f"{label} unit price '{row['unit_price']}' is not a number.")
            )
        if not row["unit_price"]:
            issues.append(QualityIssue("warning", label, f"{label} unit price is missing."))
        if row["line_total"] and total is None:
            issues.append(QualityIssue("warning", label, f"{label} total '{row['line_total']}' is not a number."))
        if quantity is not None and price is not None and total is not None:
            expected = quantity * price
            tolerance = max(0.02, abs(total) * 0.005)
            if abs(expected - total) > tolerance:
                issues.append(
                    QualityIssue(
                        "warning",
                        label,
                        (
                            f"{label} total {format_amount(total)} does not match "
                            f"quantity × unit price ({format_amount(expected)})."
                        ),
                    )
                )
        score = _optional_score(row["score"])
        # A blank score means the reviewer added the row by hand.
        if row["description"] and score is not None and score < threshold:
            issues.append(_low_confidence(label, score, threshold))
    return issues


def _duplicate_issues(rows: list[dict[str, object]]) -> list[QualityIssue]:
    seen: dict[tuple[str, str, str, str], int] = {}
    issues: list[QualityIssue] = []
    for row in rows:
        if not row["description"] and not row["sku"]:
            continue
        fingerprint = (
            _norm(str(row["sku"])),
            _norm(str(row["description"])),
            _norm(str(row["quantity"])),
            _norm(str(row["unit_price"])),
        )
        if fingerprint in seen:
            issues.append(
                QualityIssue(
                    "warning",
                    f"Line {row['line']}",
                    f"Lines {seen[fingerprint]} and {row['line']} look like duplicates.",
                )
            )
        else:
            seen[fingerprint] = int(row["line"])
    return issues


def _total_issues(rows: list[dict[str, object]], header: dict[str, dict[str, object]]) -> list[QualityIssue]:
    totals = [parse_decimal(row["line_total"]) for row in rows]
    if any(value is None for value in totals):
        return []
    summed = float(sum(value or 0.0 for value in totals))
    subtotal = parse_decimal(header.get("Subtotal", {}).get("value"))
    tax = parse_decimal(header.get("Tax", {}).get("value"))
    grand = parse_decimal(header.get("Grand total", {}).get("value"))
    issues: list[QualityIssue] = []

    if subtotal is not None and abs(summed - subtotal) > max(0.05, abs(subtotal) * 0.005):
        issues.append(
            QualityIssue(
                "warning",
                "Subtotal",
                f"Line totals sum to {format_amount(summed)}, but the subtotal is {format_amount(subtotal)}.",
            )
        )
    if subtotal is not None and tax is not None and grand is not None:
        expected = subtotal + tax
        if abs(expected - grand) > max(0.05, abs(grand) * 0.005):
            issues.append(
                QualityIssue(
                    "warning",
                    "Grand total",
                    (
                        f"Subtotal plus tax is {format_amount(expected)}, "
                        f"but the grand total is {format_amount(grand)}."
                    ),
                )
            )
    elif subtotal is None and grand is not None and abs(summed - grand) > max(0.05, abs(grand) * 0.005):
        issues.append(
            QualityIssue(
                "warning",
                "Grand total",
                f"Line totals sum to {format_amount(summed)}, but the grand total is {format_amount(grand)}.",
            )
        )
    return issues


def _optional_score(raw: object) -> float | None:
    """Return a 0–1 score, or None when the cell is empty."""

    if raw is None or raw == "":
        return None
    try:
        if pd.isna(raw):
            return None
    except TypeError:
        pass
    try:
        return float(raw) / 100.0
    except (TypeError, ValueError):
        return None


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _percent(score: float) -> int:
    return int(round(max(0.0, min(1.0, score)) * 100))


def line_field_names() -> list[str]:
    """Labels of the line-item value columns, used by tests and export."""

    return [label for _, _, label in LINE_VALUE_FIELDS]
