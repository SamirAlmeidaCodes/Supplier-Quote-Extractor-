"""Schema shape and JSON parsing."""

import json
from pathlib import Path

from app.schema import QUOTE_JSON_SCHEMA, SupplierQuote, clamp_unit_interval, parse_quote_json, strict_json_schema
from app.schema import LineItem


ROOT = Path(__file__).resolve().parent.parent


def _walk(node: dict) -> None:
    if node.get("type") == "object":
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])
        for child in node["properties"].values():
            _walk(child)
    elif node.get("type") == "array":
        _walk(node["items"])


def test_quote_schema_is_strict_and_portable() -> None:
    encoded = json.dumps(QUOTE_JSON_SCHEMA)
    _walk(QUOTE_JSON_SCHEMA)
    assert "minimum" not in encoded
    assert "maximum" not in encoded
    assert "anyOf" not in encoded
    assert QUOTE_JSON_SCHEMA["properties"]["line_items"]["items"]["type"] == "object"


def test_nested_line_schema_matches_the_model() -> None:
    schema = strict_json_schema(LineItem)
    assert set(schema["required"]) == set(LineItem.model_fields)


def test_expected_fixture_validates() -> None:
    payload = json.loads((ROOT / "samples" / "expected.json").read_text(encoding="utf-8"))
    quote = SupplierQuote.model_validate(payload)
    assert quote.supplier_name == "Northwind Industrial Supply"
    assert len(quote.line_items) == 7


def test_parse_strips_a_json_fence_and_clamps_scores() -> None:
    raw = '```json\n{"supplier_name": "Acme", "supplier_name_confidence": 95, "line_items": null}\n```'
    quote = parse_quote_json(raw)
    assert quote.supplier_name == "Acme"
    assert quote.supplier_name_confidence == 0.95
    assert quote.line_items == []


def test_confidence_clamps_out_of_range_values() -> None:
    assert clamp_unit_interval(None) == 0
    assert clamp_unit_interval("nope") == 0
    assert clamp_unit_interval(-0.2) == 0
    assert clamp_unit_interval(1.4) == 1
    assert clamp_unit_interval(80) == 0.8
