"""Quality flags, spreadsheet export, and multi-page merge."""

import io
import json
from pathlib import Path

from openpyxl import load_workbook

from app.documents import PageImage, PreparedDocument
from app.export import build_csv, build_excel
from app.extraction import extract_document, merge_quotes
from app.amounts import parse_decimal
from app.providers import VisionProvider
from app.quality import header_frame, line_frame, review_issues
from app.schema import LineItem, SupplierQuote

ROOT = Path(__file__).resolve().parent.parent


def expected_quote() -> SupplierQuote:
    payload = json.loads((ROOT / "samples" / "expected.json").read_text(encoding="utf-8"))
    return SupplierQuote.model_validate(payload)


def test_expected_quote_has_no_review_flags_and_balanced_totals() -> None:
    quote = expected_quote()
    issues = review_issues(header_frame(quote), line_frame(quote), threshold=0.7)
    assert issues == []
    total = sum(parse_decimal(item.line_total) or 0 for item in quote.line_items)
    assert total == parse_decimal(quote.subtotal)
    assert total + (parse_decimal(quote.tax) or 0) == parse_decimal(quote.grand_total)


def test_math_mismatch_duplicate_and_missing_supplier_are_flagged() -> None:
    quote = expected_quote()
    quote.supplier_name = ""
    quote.line_items[0].line_total = "999.00"
    quote.line_items.append(quote.line_items[1].model_copy(deep=True))
    issues = review_issues(header_frame(quote), line_frame(quote), threshold=0.7)
    messages = " ".join(issue.message for issue in issues)
    assert any(issue.severity == "error" and "Supplier" in issue.message for issue in issues)
    assert "does not match" in messages
    assert "duplicates" in messages
    assert "subtotal" in messages.lower()


def test_low_confidence_is_flagged_without_a_second_model_call() -> None:
    quote = expected_quote()
    quote.quote_number_confidence = 0.4
    issues = review_issues(header_frame(quote), line_frame(quote), threshold=0.7)
    assert any("Quote number confidence" in issue.message for issue in issues)


def test_decimal_parser_accepts_both_separators() -> None:
    assert parse_decimal("1,234.50") == 1234.50
    assert parse_decimal("1.234,50") == 1234.50
    assert parse_decimal("USD 18.40") == 18.40
    assert parse_decimal("(45.00)") == -45.0
    assert parse_decimal("n/a") is None
    assert parse_decimal("") is None


def test_exports_neutralize_formula_cells() -> None:
    header = header_frame(expected_quote())
    header.loc[header["Field"] == "Supplier", "Value"] = "=HYPERLINK(\"http://example.test\")"
    lines = line_frame(expected_quote())
    lines.loc[0, "SKU"] = "+1+1"
    lines.loc[0, "Description"] = "@SUM(A1)"
    csv_text = build_csv(header, lines).decode("utf-8")
    assert "'=HYPERLINK" in csv_text
    assert "'+1+1" in csv_text
    assert "'@SUM(A1)" in csv_text

    workbook = load_workbook(io.BytesIO(build_excel(header, lines, [])))
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    assert not cell.value.lstrip(" \t'\r\n").startswith(("=", "+", "-", "@")) or cell.value.startswith("'")


def test_csv_and_excel_round_trip() -> None:
    quote = expected_quote()
    header = header_frame(quote)
    lines = line_frame(quote)
    csv_text = build_csv(header, lines).decode("utf-8")
    assert "Northwind Industrial Supply" in csv_text
    assert "HXB-M8-40" in csv_text

    workbook = load_workbook(io.BytesIO(build_excel(header, lines, [])))
    assert workbook.sheetnames == ["Header", "Line items", "Review flags"]
    header_sheet = workbook["Header"]
    assert header_sheet["A2"].value == "Supplier"
    assert header_sheet["B2"].value == "Northwind Industrial Supply"
    # Subtotal is numeric so a spreadsheet can sum it.
    subtotal_row = next(row for row in header_sheet.iter_rows(values_only=True) if row[0] == "Subtotal")
    assert float(subtotal_row[1]) == 797.0


def test_merge_keeps_the_clearer_header_and_drops_duplicate_lines() -> None:
    first = SupplierQuote(
        supplier_name="Northwind Industrial Supply",
        supplier_name_confidence=0.4,
        line_items=[
            LineItem(description="Hex bolt", quantity="12", unit_price="18.40", line_total="220.80", description_confidence=0.5)
        ],
    )
    second = SupplierQuote(
        supplier_name="Northwind Industrial Supply",
        supplier_name_confidence=0.95,
        grand_total="797.00",
        grand_total_confidence=0.9,
        line_items=[
            LineItem(description="Hex bolt", quantity="12", unit_price="18.40", line_total="220.80", description_confidence=0.9),
            LineItem(description="Mist coolant", quantity="2", unit_price="32.00", line_total="64.00", description_confidence=0.9),
        ],
        model_warnings=["Page 2 was faint"],
    )
    merged = merge_quotes([first, second])
    assert merged.supplier_name_confidence == 0.95
    assert merged.grand_total == "797.00"
    assert [item.description for item in merged.line_items] == ["Hex bolt", "Mist coolant"]
    assert merged.line_items[0].description_confidence == 0.9
    assert merged.model_warnings == ["Page 2 was faint"]


class _RecordingProvider(VisionProvider):
    id = "fake"
    label = "Fake"

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def extract(self, **kwargs: object) -> str:  # type: ignore[override]
        images = kwargs["images"]
        assert isinstance(images, list)
        self.batch_sizes.append(len(images))
        index = len(self.batch_sizes)
        quote = SupplierQuote(
            supplier_name="Northwind Industrial Supply" if index == 1 else "",
            supplier_name_confidence=0.9 if index == 1 else 0,
            line_items=[
                LineItem(
                    description=f"Item {index}",
                    quantity="1",
                    unit_price="5.00",
                    line_total="5.00",
                    description_confidence=0.9,
                    quantity_confidence=0.9,
                    unit_price_confidence=0.9,
                    line_total_confidence=0.9,
                )
            ],
        )
        return quote.model_dump_json()


def _document(pages: int) -> PreparedDocument:
    images = tuple(
        PageImage(page_number=index, jpeg_bytes=b"\xff\xd8\xff", width=8, height=8)
        for index in range(1, pages + 1)
    )
    return PreparedDocument(filename="quote.pdf", kind="pdf", pages=images)


def test_combined_mode_batches_long_documents_and_merges_them() -> None:
    provider = _RecordingProvider()
    result = extract_document(
        _document(5),
        provider,
        api_key="not-sent-by-the-fake",
        model="fake-model",
        mode="combined",
    )
    assert provider.batch_sizes == [4, 1]
    assert result.mode == "batched"
    assert result.request_count == 2
    assert [item.description for item in result.quote.line_items] == ["Item 1", "Item 2"]
    assert result.quote.supplier_name == "Northwind Industrial Supply"


def test_per_page_mode_sends_one_page_at_a_time() -> None:
    provider = _RecordingProvider()
    result = extract_document(
        _document(2),
        provider,
        api_key="not-sent-by-the-fake",
        model="fake-model",
        mode="per_page",
    )
    assert provider.batch_sizes == [1, 1]
    assert result.mode == "per_page"
    assert len(result.quote.line_items) == 2
