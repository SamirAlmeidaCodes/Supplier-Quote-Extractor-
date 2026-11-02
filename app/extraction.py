"""Run extraction and merge multi-page results.

Page images are sent to the selected provider. The JSON text is validated
against :class:`app.schema.SupplierQuote` before it reaches the UI. When a
document is split across requests, header fields are merged by confidence
and line items are concatenated, then exact duplicates are dropped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import MAX_PAGES_PER_REQUEST
from app.documents import PageImage, PreparedDocument
from app.errors import AppError
from app.prompts import SYSTEM_PROMPT, build_user_prompt
from app.providers import ImagePayload, VisionProvider
from app.schema import HEADER_FIELDS, QUOTE_JSON_SCHEMA, LineItem, SupplierQuote, parse_quote_json


@dataclass
class ExtractionResult:
    quote: SupplierQuote
    provider_id: str
    provider_label: str
    model: str
    page_count: int
    request_count: int
    mode: str
    raw_json: str
    model_warnings: list[str] = field(default_factory=list)


def extract_document(
    document: PreparedDocument,
    provider: VisionProvider,
    *,
    api_key: str,
    model: str,
    mode: str,
    on_progress: Callable[[str], None] | None = None,
) -> ExtractionResult:
    """Extract one prepared document.

    ``mode`` is ``combined`` or ``per_page``. Combined mode still batches
    documents that are longer than ``MAX_PAGES_PER_REQUEST``.
    """

    pages = list(document.pages)
    if not pages:
        raise AppError("There are no pages to extract.")
    if mode not in {"combined", "per_page"}:
        raise AppError("Choose combined extraction or page-by-page extraction.")

    batches = _batches(pages, mode)
    effective = mode if len(batches) == 1 and mode == "combined" else (
        "per_page" if mode == "per_page" else "batched"
    )
    quotes: list[SupplierQuote] = []
    for batch_index, batch in enumerate(batches, start=1):
        numbers = [page.page_number for page in batch]
        if on_progress:
            listed = ", ".join(str(number) for number in numbers)
            on_progress(
                f"Request {batch_index} of {len(batches)}: reading page {listed} with {provider.label}."
            )
        partial = not (mode == "combined" and len(batches) == 1)
        prompt = build_user_prompt(
            page_numbers=numbers,
            page_count=len(pages),
            partial=partial,
        )
        try:
            raw = provider.extract(
                api_key=api_key,
                model=model,
                images=[ImagePayload("image/jpeg", page.jpeg_bytes) for page in batch],
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                schema=QUOTE_JSON_SCHEMA,
            )
        except AppError as exc:
            raise AppError(f"Page {_page_label(numbers)} failed. {exc}") from None
        quotes.append(parse_quote_json(raw))

    merged = quotes[0] if len(quotes) == 1 else merge_quotes(quotes)
    return ExtractionResult(
        quote=merged,
        provider_id=provider.id,
        provider_label=provider.label,
        model=model,
        page_count=len(pages),
        request_count=len(batches),
        mode=effective,
        raw_json=merged.model_dump_json(indent=2),
        model_warnings=list(merged.model_warnings),
    )


def _page_label(numbers: list[int]) -> str:
    if len(numbers) == 1:
        return str(numbers[0])
    return f"{numbers[0]}–{numbers[-1]}"


def _batches(pages: list[PageImage], mode: str) -> list[list[PageImage]]:
    if mode == "per_page":
        return [[page] for page in pages]
    size = MAX_PAGES_PER_REQUEST
    return [pages[index : index + size] for index in range(0, len(pages), size)]


def merge_quotes(quotes: list[SupplierQuote]) -> SupplierQuote:
    """Combine page-level quotes into one document."""

    if not quotes:
        raise AppError("Nothing was extracted to merge.")
    if len(quotes) == 1:
        return quotes[0]

    merged = quotes[0].model_copy(deep=True)
    for incoming in quotes[1:]:
        for value_name, score_name, _label in HEADER_FIELDS:
            current_value = getattr(merged, value_name)
            current_score = float(getattr(merged, score_name))
            new_value = getattr(incoming, value_name)
            new_score = float(getattr(incoming, score_name))
            chosen_value, chosen_score = _prefer(current_value, current_score, new_value, new_score)
            setattr(merged, value_name, chosen_value)
            setattr(merged, score_name, chosen_score)
        merged.line_items = _merge_lines(merged.line_items, incoming.line_items)
        merged.model_warnings = _unique(merged.model_warnings + incoming.model_warnings)
    return merged


def _prefer(current: str, current_score: float, new: str, new_score: float) -> tuple[str, float]:
    current_blank = not current.strip()
    new_blank = not new.strip()
    if current_blank and not new_blank:
        return new, new_score
    if new_blank and not current_blank:
        return current, current_score
    if new_score > current_score:
        return new, new_score
    return current, current_score


def _merge_lines(current: list[LineItem], incoming: list[LineItem]) -> list[LineItem]:
    merged = list(current)
    seen = {_fingerprint(item): index for index, item in enumerate(merged) if _fingerprint(item)}
    for item in incoming:
        fingerprint = _fingerprint(item)
        if fingerprint and fingerprint in seen:
            kept = merged[seen[fingerprint]]
            if _line_score(item) > _line_score(kept):
                merged[seen[fingerprint]] = item
            continue
        if fingerprint:
            seen[fingerprint] = len(merged)
        merged.append(item)
    return merged


def _fingerprint(item: LineItem) -> tuple[str, str, str, str] | None:
    sku = _norm(item.sku)
    description = _norm(item.description)
    if not sku and not description:
        return None
    return (sku, description, _norm(item.quantity), _norm(item.unit_price))


def _line_score(item: LineItem) -> float:
    """Average confidence of the fields that actually have text.

    ``min`` would let one unscored field throw away a clearer duplicate.
    """

    pairs = (
        (item.sku, item.sku_confidence),
        (item.description, item.description_confidence),
        (item.quantity, item.quantity_confidence),
        (item.unit, item.unit_confidence),
        (item.unit_price, item.unit_price_confidence),
        (item.line_total, item.line_total_confidence),
    )
    scores = [score for value, score in pairs if str(value).strip()]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        text = item.strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered
