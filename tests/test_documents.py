"""Rasterizing the bundled sample quote."""

from pathlib import Path

import pytest

from app.documents import load_document
from app.errors import AppError

ROOT = Path(__file__).resolve().parent.parent
PNG = ROOT / "samples" / "northwind_quote.png"
PDF = ROOT / "samples" / "northwind_quote.pdf"


def test_png_sample_becomes_one_jpeg_page() -> None:
    document = load_document(PNG.read_bytes(), PNG.name)
    assert document.kind == "image"
    assert len(document.pages) == 1
    assert document.pages[0].jpeg_bytes.startswith(b"\xff\xd8\xff")
    assert document.pages[0].width <= 1800
    assert document.pages[0].height <= 1800


def test_pdf_sample_rasterizes_both_pages() -> None:
    document = load_document(PDF.read_bytes(), PDF.name)
    assert document.kind == "pdf"
    assert [page.page_number for page in document.pages] == [1, 2]
    assert all(page.jpeg_bytes.startswith(b"\xff\xd8\xff") for page in document.pages)


def test_bad_files_are_rejected() -> None:
    with pytest.raises(AppError, match="empty"):
        load_document(b"", "quote.pdf")
    with pytest.raises(AppError, match="PDF, JPEG, or PNG"):
        load_document(b"not a document", "notes.txt")
    with pytest.raises(AppError, match="not a PDF"):
        load_document(b"hello", "quote.pdf")


def test_upload_names_cannot_escape_the_display_name() -> None:
    document = load_document(PNG.read_bytes(), r"../../etc/passwd.png")
    assert document.filename == "passwd.png"
    assert "/" not in document.filename
    assert ".." not in document.filename


def test_pdf_errors_do_not_include_local_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("renderer failed on /tmp/quote-pages-secret/input.pdf")

    monkeypatch.setattr("pdf2image.convert_from_bytes", explode)
    with pytest.raises(AppError) as caught:
        load_document(b"%PDF-1.4\n", "quote.pdf")
    message = str(caught.value)
    assert "/tmp/" not in message
    assert "quote-pages-secret" not in message
    assert "Could not render this PDF" in message
