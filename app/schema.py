"""Pydantic schema for a supplier quote and the JSON Schema sent to models.

The JSON Schema is strict on purpose:

* every object sets ``additionalProperties`` to false
* every property is required
* missing values are empty strings, not null

That shape is accepted by xAI structured outputs, OpenAI strict JSON schema,
Anthropic ``output_config.format``, and Gemini ``responseJsonSchema``. Numeric
bounds stay in the Python validators. Some providers reject ``minimum`` and
``maximum`` inside a strict schema.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import MAX_LINE_ITEMS, MAX_MODEL_WARNINGS, MAX_TEXT_CHARS, MAX_WARNING_CHARS
from app.errors import AppError

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def clamp_unit_interval(value: object) -> float:
    """Coerce a model score into the closed interval from 0 to 1.

    A model that returns a percentage such as ``95`` is scaled to ``0.95``.
    A score only slightly above 1, such as ``1.4``, is clamped to 1 rather
    than treated as 1.4 percent. Unreadable values become ``0`` so one bad
    score does not fail the document.
    """

    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if 2.0 <= number <= 100.0:
        number = number / 100.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def empty_string(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    """Coerce model text and drop control characters and oversized values."""

    if value is None:
        return ""
    text = str(value).strip()
    text = "".join(char for char in text if char in "\n\t" or char.isprintable())
    if len(text) > limit:
        return text[:limit]
    return text


class LineItem(BaseModel):
    """One priced row on the quote, including freight or a discount."""

    model_config = ConfigDict(extra="ignore")

    sku: str = Field("", description="Supplier part number or SKU. Empty string if none is printed.")
    description: str = Field(
        "",
        description="Product or service description as printed. Do not repeat the SKU here when it has its own field.",
    )
    quantity: str = Field("", description="Quantity as printed, without the unit. Empty string if blank.")
    unit: str = Field("", description="Unit of measure such as ea, box, kg, or hour. Empty string if not printed.")
    unit_price: str = Field(
        "",
        description="Price for one unit, without a currency symbol or thousands separator. Empty string if not printed.",
    )
    line_total: str = Field(
        "",
        description="Extended line total as printed, without a currency symbol. Empty string if not printed.",
    )
    sku_confidence: float = Field(0, description="Confidence from 0 to 1 that the SKU was read correctly.")
    description_confidence: float = Field(
        0, description="Confidence from 0 to 1 that the description was read correctly."
    )
    quantity_confidence: float = Field(0, description="Confidence from 0 to 1 that the quantity was read correctly.")
    unit_confidence: float = Field(0, description="Confidence from 0 to 1 that the unit was read correctly.")
    unit_price_confidence: float = Field(
        0, description="Confidence from 0 to 1 that the unit price was read correctly."
    )
    line_total_confidence: float = Field(
        0, description="Confidence from 0 to 1 that the line total was read correctly."
    )

    @field_validator("sku", "description", "quantity", "unit", "unit_price", "line_total", mode="before")
    @classmethod
    def _text(cls, value: object) -> str:
        return empty_string(value)

    @field_validator(
        "sku_confidence",
        "description_confidence",
        "quantity_confidence",
        "unit_confidence",
        "unit_price_confidence",
        "line_total_confidence",
        mode="before",
    )
    @classmethod
    def _score(cls, value: object) -> float:
        return clamp_unit_interval(value)


class SupplierQuote(BaseModel):
    """Header, commercial terms, and line items from one supplier quote."""

    model_config = ConfigDict(extra="ignore")

    supplier_name: str = Field("", description="Legal or trade name of the supplier that issued the quote.")
    supplier_name_confidence: float = Field(0, description="Confidence from 0 to 1 for supplier_name.")
    quote_number: str = Field("", description="Quote, offer, or proposal number. Empty string if not printed.")
    quote_number_confidence: float = Field(0, description="Confidence from 0 to 1 for quote_number.")
    quote_date: str = Field(
        "",
        description="Quote date. Use YYYY-MM-DD when the calendar date is unambiguous, otherwise the printed text.",
    )
    quote_date_confidence: float = Field(0, description="Confidence from 0 to 1 for quote_date.")
    valid_until: str = Field(
        "",
        description="Date the quote expires. Use YYYY-MM-DD when unambiguous, otherwise the printed text.",
    )
    valid_until_confidence: float = Field(0, description="Confidence from 0 to 1 for valid_until.")
    currency: str = Field("", description="ISO 4217 currency code such as USD, EUR, or GBP. Empty string if unknown.")
    currency_confidence: float = Field(0, description="Confidence from 0 to 1 for currency.")
    payment_terms: str = Field("", description="Payment terms in a short phrase copied from the document.")
    payment_terms_confidence: float = Field(0, description="Confidence from 0 to 1 for payment_terms.")
    delivery_lead_time: str = Field("", description="Delivery or lead time in a short phrase copied from the document.")
    delivery_lead_time_confidence: float = Field(0, description="Confidence from 0 to 1 for delivery_lead_time.")
    notes: str = Field(
        "",
        description="Exceptions, exclusions, freight conditions, and other remarks that do not fit a dedicated field.",
    )
    notes_confidence: float = Field(0, description="Confidence from 0 to 1 for notes.")
    subtotal: str = Field("", description="Printed subtotal before tax, without a currency symbol.")
    subtotal_confidence: float = Field(0, description="Confidence from 0 to 1 for subtotal.")
    tax: str = Field("", description="Printed tax amount, without a currency symbol. Empty string if tax is not shown.")
    tax_confidence: float = Field(0, description="Confidence from 0 to 1 for tax.")
    grand_total: str = Field("", description="Printed grand total, without a currency symbol.")
    grand_total_confidence: float = Field(0, description="Confidence from 0 to 1 for grand_total.")
    line_items: list[LineItem] = Field(
        default_factory=list,
        description="Every priced row, including freight, surcharges, and discounts. A discount is negative.",
    )
    model_warnings: list[str] = Field(
        default_factory=list,
        description="Short notes about illegible regions, conflicting totals, or missing pages. Empty array if none.",
    )

    @field_validator(
        "supplier_name",
        "quote_number",
        "quote_date",
        "valid_until",
        "currency",
        "payment_terms",
        "delivery_lead_time",
        "notes",
        "subtotal",
        "tax",
        "grand_total",
        mode="before",
    )
    @classmethod
    def _text(cls, value: object) -> str:
        return empty_string(value)

    @field_validator(
        "supplier_name_confidence",
        "quote_number_confidence",
        "quote_date_confidence",
        "valid_until_confidence",
        "currency_confidence",
        "payment_terms_confidence",
        "delivery_lead_time_confidence",
        "notes_confidence",
        "subtotal_confidence",
        "tax_confidence",
        "grand_total_confidence",
        mode="before",
    )
    @classmethod
    def _score(cls, value: object) -> float:
        return clamp_unit_interval(value)

    @field_validator("line_items", mode="before")
    @classmethod
    def _items(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, list) and len(value) > MAX_LINE_ITEMS:
            return value[:MAX_LINE_ITEMS]
        return value

    @field_validator("model_warnings", mode="before")
    @classmethod
    def _warnings(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items: list[object] = [value]
        elif isinstance(value, list):
            items = list(value)
        else:
            return []
        cleaned: list[str] = []
        for item in items[:MAX_MODEL_WARNINGS]:
            text = empty_string(item, MAX_WARNING_CHARS)
            if text:
                cleaned.append(text)
        return cleaned


# Header fields shown in the review table, in display order.
# The second item is the confidence attribute on SupplierQuote.
HEADER_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("supplier_name", "supplier_name_confidence", "Supplier"),
    ("quote_number", "quote_number_confidence", "Quote number"),
    ("quote_date", "quote_date_confidence", "Quote date"),
    ("valid_until", "valid_until_confidence", "Valid until"),
    ("currency", "currency_confidence", "Currency"),
    ("payment_terms", "payment_terms_confidence", "Payment terms"),
    ("delivery_lead_time", "delivery_lead_time_confidence", "Delivery / lead time"),
    ("subtotal", "subtotal_confidence", "Subtotal"),
    ("tax", "tax_confidence", "Tax"),
    ("grand_total", "grand_total_confidence", "Grand total"),
    ("notes", "notes_confidence", "Notes"),
)

LINE_VALUE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("sku", "sku_confidence", "SKU"),
    ("description", "description_confidence", "Description"),
    ("quantity", "quantity_confidence", "Quantity"),
    ("unit", "unit_confidence", "Unit"),
    ("unit_price", "unit_price_confidence", "Unit price"),
    ("line_total", "line_total_confidence", "Line total"),
)


def _schema_for(annotation: Any, description: str) -> dict[str, Any]:
    if annotation is str:
        return {"type": "string", "description": description}
    if annotation is float:
        return {"type": "number", "description": description}
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())
    if origin is list and args:
        inner = args[0]
        if inner is str:
            return {"type": "array", "description": description, "items": {"type": "string"}}
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return {"type": "array", "description": description, "items": strict_json_schema(inner)}
    raise TypeError(f"Unsupported schema annotation: {annotation!r}")


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Build a provider-safe JSON Schema for ``model``."""

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, field in model.model_fields.items():
        properties[name] = _schema_for(field.annotation, field.description or "")
        required.append(name)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


QUOTE_JSON_SCHEMA = strict_json_schema(SupplierQuote)


def strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = _FENCE.sub("", text).strip()
    return text


def parse_quote_json(raw: str) -> SupplierQuote:
    """Validate a model response. The error text is safe to show in the UI."""

    try:
        payload = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError:
        raise AppError(
            "The model did not return valid JSON. Try again, or switch the model."
        ) from None
    if not isinstance(payload, dict):
        raise AppError("The model returned JSON that is not a quote object. Try again.") from None
    try:
        return SupplierQuote.model_validate(payload)
    except Exception:
        raise AppError(
            "The model returned JSON that does not match the quote schema. Try again."
        ) from None
