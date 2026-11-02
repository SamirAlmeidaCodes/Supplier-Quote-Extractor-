"""Turn an upload into JPEG page images.

PDFs are rasterized with Poppler (``pdftoppm`` via pdf2image) because a scanned
quote often has no text layer. Phone photos are normalized to RGB JPEG and
shrunk so a multi-page request stays inside provider image limits.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import (
    JPEG_QUALITY_START,
    MAX_EDGE_PX,
    MAX_PAGES,
    MAX_UPLOAD_BYTES,
    TARGET_IMAGE_BYTES,
)
from app.errors import AppError

PDF_SIGNATURE = b"%PDF"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
# A phone photo of a quote does not need more than this. It also blocks
# decompression bombs before Pillow expands them.
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


@dataclass(frozen=True)
class PageImage:
    """One page ready to preview and to send to a vision model."""

    page_number: int
    jpeg_bytes: bytes
    width: int
    height: int


@dataclass(frozen=True)
class PreparedDocument:
    filename: str
    kind: str
    pages: tuple[PageImage, ...]


def load_document(data: bytes, filename: str) -> PreparedDocument:
    """Validate ``data`` and return one JPEG per page."""

    if not data:
        raise AppError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError("The file is larger than 20 MB. Export a smaller PDF or a single photo.")

    name = filename or "quote"
    if data.startswith(PDF_SIGNATURE) or name.lower().endswith(".pdf"):
        if not data.startswith(PDF_SIGNATURE):
            raise AppError("This file has a PDF name but is not a PDF.")
        pages = _pdf_pages(data)
        kind = "pdf"
    elif _is_image(data):
        pages = (_image_page(data, 1),)
        kind = "image"
    else:
        raise AppError("Upload a PDF, JPEG, or PNG. Other file types are not read.")

    if not pages:
        raise AppError("No pages could be read from this file.")
    if len(pages) > MAX_PAGES:
        raise AppError(
            f"This document has more than {MAX_PAGES} pages. Split it and extract each part."
        )
    return PreparedDocument(filename=name, kind=kind, pages=tuple(pages))


def _is_image(data: bytes) -> bool:
    return data.startswith(PNG_SIGNATURE) or data.startswith(JPEG_SIGNATURE)


def _pdf_pages(data: bytes) -> list[PageImage]:
    try:
        from pdf2image import convert_from_bytes
        from pdf2image.exceptions import PDFPageCountError, PDFPopplerTimeoutError, PDFSyntaxError
    except ImportError as exc:  # pragma: no cover - the image always installs pdf2image
        raise AppError("PDF support is not installed in this environment.") from exc

    try:
        rendered = convert_from_bytes(
            data,
            dpi=140,
            fmt="png",
            thread_count=1,
            # Poppler writes its working files here. The container mounts /tmp
            # as a tmpfs, so those files disappear when the container stops.
        )
    except (PDFPageCountError, PDFSyntaxError, PDFPopplerTimeoutError):
        raise AppError(
            "Could not read this PDF. It may be encrypted, damaged, or empty."
        ) from None
    except Exception as exc:
        # pdf2image raises a plain exception when the poppler binary is missing
        # or the file is password-protected. Keep the detail, drop any path
        # noise, and never include request secrets (there are none here).
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else "unknown error"
        lowered = message.lower()
        if "password" in lowered or "encrypted" in lowered:
            raise AppError("This PDF is encrypted. Export a copy without a password and upload that.") from None
        if "poppler" in lowered or "pdftoppm" in lowered or "pdfinfo" in lowered:
            raise AppError("PDF rendering is unavailable because Poppler is not installed.") from None
        raise AppError(f"Could not render this PDF. {message}") from None

    if not rendered:
        raise AppError("This PDF has no pages.")
    return [_pil_to_page(image, index) for index, image in enumerate(rendered, start=1)]


def _image_page(data: bytes, page_number: int) -> PageImage:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return _pil_to_page(image, page_number)
    except Image.DecompressionBombError:
        raise AppError("This image is too large to open safely.") from None
    except (UnidentifiedImageError, OSError):
        raise AppError("This image could not be opened. Upload a JPEG or PNG.") from None


def _pil_to_page(image: Image.Image, page_number: int) -> PageImage:
    prepared = ImageOps.exif_transpose(image)
    if prepared.mode != "RGB":
        prepared = prepared.convert("RGB")
    prepared = _downscale(prepared, MAX_EDGE_PX)
    jpeg = _encode_jpeg(prepared)
    return PageImage(
        page_number=page_number,
        jpeg_bytes=jpeg,
        width=prepared.width,
        height=prepared.height,
    )


def _downscale(image: Image.Image, max_edge: int) -> Image.Image:
    longest = max(image.width, image.height)
    if longest <= max_edge:
        return image
    scale = max_edge / longest
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _encode_jpeg(image: Image.Image) -> bytes:
    """Encode a page, lowering quality and size until it is small enough to send."""

    quality = JPEG_QUALITY_START
    current = image
    while True:
        buffer = io.BytesIO()
        current.save(buffer, format="JPEG", quality=quality, optimize=True)
        payload = buffer.getvalue()
        if len(payload) <= TARGET_IMAGE_BYTES or (quality <= 60 and max(current.size) <= 1200):
            return payload
        if quality > 60:
            quality -= 10
            continue
        current = _downscale(current, max(800, int(max(current.size) * 0.8)))
