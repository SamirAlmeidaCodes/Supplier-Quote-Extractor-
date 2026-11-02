"""Streamlit layout for Supplier Quote Extractor.

The API key is read from a password input and passed straight to the provider
call. It is not written to session-state keys other than the widget's own
in-memory value, and nothing in this module caches, logs, or saves it.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from app.config import DEFAULT_REVIEW_THRESHOLD, PROVIDERS
from app.documents import PreparedDocument, load_document
from app.errors import AppError
from app.export import build_csv, build_excel
from app.extraction import ExtractionResult, extract_document
from app.providers import get_provider
from app.quality import header_frame, line_frame, review_issues
from app.redact import redact

logger = logging.getLogger(__name__)

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "samples" / "northwind_quote.pdf"
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Settings:
    provider_id: str
    provider_label: str
    api_key: str
    model: str
    mode: str
    threshold: float


def render() -> None:
    st.set_page_config(
        page_title="Supplier Quote Extractor",
        page_icon=":material/request_quote:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    settings = _sidebar()

    st.title("Supplier Quote Extractor", icon=":material/request_quote:")
    st.caption(
        "Turn a supplier quote into a table you can check, correct, and export. "
        "The document is sent to the vision model you choose. Nothing is stored on disk."
    )

    document = _document_picker()
    if document is None:
        return

    result: ExtractionResult | None = st.session_state.get("result")
    preview, detail = st.columns([1, 1], gap="large")
    with preview:
        _render_preview(document)
    with detail:
        result = _render_detail(document, settings, result)

    if result is None:
        return
    _render_lines_and_downloads(document, result, settings.threshold)


def _sidebar() -> Settings:
    st.sidebar.header("Model")
    labels = [spec.label for spec in PROVIDERS]
    selected = st.sidebar.selectbox("Provider", labels, index=0)
    spec = next(item for item in PROVIDERS if item.label == selected)
    st.sidebar.caption(spec.key_hint)
    st.sidebar.markdown(f"[Get a key]({spec.docs_url})")

    api_key = st.sidebar.text_input(
        "API key",
        type="password",
        key="api_key_input",
        placeholder="Paste a key. It stays in this session only.",
        help="Used for the next request, then kept only in this session's memory.",
    )
    override = st.sidebar.text_input(
        "Model override",
        key="model_override",
        placeholder=spec.default_model,
        help=f"Leave this blank to use {spec.default_model}.",
    )
    mode_label = st.sidebar.segmented_control(
        "Multi-page PDFs",
        ["All pages in one request", "Page by page, then merge"],
        default="All pages in one request",
        required=True,
        width="stretch",
        wrap=True,
        help="Long documents are batched automatically even in the first mode.",
    )
    threshold_pct = st.sidebar.slider(
        "Flag confidence below",
        min_value=50,
        max_value=95,
        value=int(DEFAULT_REVIEW_THRESHOLD * 100),
        step=5,
        format="%d%%",
    )
    if st.sidebar.button("Clear API key"):
        st.session_state.api_key_input = ""
        st.rerun()

    st.sidebar.caption(
        "The key is held in memory for this browser session. "
        "This app does not write it to disk, logs, a .env file, or the container image. "
        "Closing the session drops it."
    )
    return Settings(
        provider_id=spec.id,
        provider_label=spec.label,
        api_key=api_key,
        model=override.strip() or spec.default_model,
        mode="per_page" if mode_label == "Page by page, then merge" else "combined",
        threshold=threshold_pct / 100.0,
    )


def _document_picker() -> PreparedDocument | None:
    source = st.segmented_control(
        "Document",
        ["Upload a file", "Bundled sample"],
        default="Upload a file",
        required=True,
    )
    if source is None:
        source = "Upload a file"
    if source == "Upload a file":
        upload = st.file_uploader(
            "Supplier quote",
            type=["pdf", "png", "jpg", "jpeg"],
            help="PDF pages are rasterized. Photos of a printed quote work too.",
        )
        if upload is None:
            st.info("Upload a PDF or image, or switch to the bundled sample.")
            return None
        payload, filename = upload.getvalue(), upload.name
    else:
        if not SAMPLE_PATH.is_file():
            st.error("The bundled sample is missing from the samples folder.")
            return None
        payload, filename = SAMPLE_PATH.read_bytes(), SAMPLE_PATH.name
        st.caption("Two-page scanned quote from the fictional supplier Northwind Industrial Supply.")

    token = f"{source}:{filename}:{len(payload)}:{hashlib.sha256(payload).hexdigest()[:16]}"
    if st.session_state.get("doc_token") != token:
        try:
            document = load_document(payload, filename)
        except AppError as exc:
            st.error(str(exc))
            return None
        st.session_state.doc_token = token
        st.session_state.document = document
        st.session_state.result = None
        st.session_state.result_token = None
    return st.session_state.document


def _render_preview(document: PreparedDocument) -> None:
    st.subheader("Document")
    numbers = [page.page_number for page in document.pages]
    label = f"{len(document.pages)} page" if len(document.pages) == 1 else f"{len(document.pages)} pages"
    st.caption(f"{document.filename} · {label} · {document.kind.upper()}")
    if len(numbers) > 1:
        selected = st.selectbox("Page", numbers, format_func=lambda number: f"Page {number}")
    else:
        selected = numbers[0]
    page = next(item for item in document.pages if item.page_number == selected)
    st.image(
        page.jpeg_bytes,
        width="stretch",
        alt=f"Page {selected} of {document.filename}",
    )


def _render_detail(
    document: PreparedDocument,
    settings: Settings,
    result: ExtractionResult | None,
) -> ExtractionResult | None:
    """Right-hand column: the extract action and the editable header."""

    st.subheader("Extracted quote")
    has_key = bool(settings.api_key.strip())
    if not has_key:
        st.info("Paste an API key in the sidebar. Extraction stays disabled until you do.")

    if st.button(
        "Extract Quote Data",
        type="primary",
        disabled=not has_key,
        width="stretch",
        icon=":material/document_scanner:",
    ):
        result = _run_extraction(document, settings, result)

    if result is None:
        st.write("The preview on the left stays on this machine. No request is sent until you extract.")
        return None

    _ensure_seeds(result)
    _metrics(result)
    st.caption("Header fields are editable. Confidence is the model's score and does not change when you edit.")
    edited_header = st.data_editor(
        st.session_state.header_seed,
        disabled=["Field", "Confidence %"],
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        key=f"header-{st.session_state.result_token}",
        alt="Editable header fields from the extracted quote",
        column_config={
            "Field": st.column_config.TextColumn("Field", width="medium"),
            "Value": st.column_config.TextColumn("Value", width="large"),
            "Confidence %": st.column_config.NumberColumn("Confidence %", format="%.0f%%"),
        },
    )
    st.session_state.edited_header = edited_header
    return result


def _run_extraction(
    document: PreparedDocument,
    settings: Settings,
    result: ExtractionResult | None,
) -> ExtractionResult | None:
    with st.status("Extracting quote data…", expanded=True) as status:
        def on_progress(message: str) -> None:
            status.write(message)

        try:
            extracted = extract_document(
                document,
                get_provider(settings.provider_id),
                api_key=settings.api_key,
                model=settings.model,
                mode=settings.mode,
                on_progress=on_progress,
            )
        except AppError as exc:
            status.update(label="Extraction failed", state="error")
            st.error(str(exc))
            return result
        except Exception:
            logger.exception("Unexpected extraction failure")
            status.update(label="Extraction failed", state="error")
            st.error(
                "Extraction failed for an unexpected reason. "
                "The server log is redacted and does not include the API key."
            )
            return result

        st.session_state.result = extracted
        st.session_state.header_seed = header_frame(extracted.quote)
        st.session_state.line_seed = line_frame(extracted.quote)
        st.session_state.result_token = f"{st.session_state.doc_token}:{extracted.model}:{id(extracted)}"
        status.update(label="Extraction complete", state="complete")
        return extracted


def _metrics(result: ExtractionResult) -> None:
    quote = result.quote
    total = quote.grand_total.strip()
    currency = quote.currency.strip()
    shown_total = f"{currency} {total}".strip() if total else "—"
    mode = {"combined": "one request", "per_page": "page by page", "batched": "batched"}[result.mode]
    st.caption(
        f"{result.provider_label} · {result.model} · {result.page_count} pages · "
        f"{result.request_count} request(s) · {mode}"
    )
    one, two, three, four = st.columns(4)
    one.metric(
        "Supplier",
        quote.supplier_name.strip() or "—",
        border=True,
        height="stretch",
        icon=":material/storefront:",
    )
    two.metric(
        "Quote",
        quote.quote_number.strip() or "—",
        border=True,
        height="stretch",
        icon=":material/tag:",
    )
    three.metric(
        "Total",
        shown_total,
        border=True,
        height="stretch",
        icon=":material/payments:",
    )
    four.metric(
        "Lines",
        str(len(quote.line_items)),
        border=True,
        height="stretch",
        icon=":material/format_list_numbered:",
    )


def _ensure_seeds(result: ExtractionResult) -> None:
    token = f"{st.session_state.doc_token}:{result.model}:{id(result)}"
    if st.session_state.get("result_token") != token or "header_seed" not in st.session_state:
        st.session_state.header_seed = header_frame(result.quote)
        st.session_state.line_seed = line_frame(result.quote)
        st.session_state.result_token = token


def _render_lines_and_downloads(document: PreparedDocument, result: ExtractionResult, threshold: float) -> None:
    st.subheader("Line items")
    st.caption("Edit a cell to correct it. You can add or delete rows. Review flags follow the edited values.")
    _ensure_seeds(result)
    edited_header = st.session_state.get("edited_header", st.session_state.header_seed)
    edited_lines = st.data_editor(
        st.session_state.line_seed,
        disabled=["Confidence %"],
        hide_index=True,
        width="stretch",
        num_rows="dynamic",
        key=f"lines-{st.session_state.result_token}",
        alt="Editable line items from the extracted quote",
        column_config={
            "Line": st.column_config.NumberColumn("Line", width="small"),
            "SKU": st.column_config.TextColumn("SKU", width="small"),
            "Description": st.column_config.TextColumn("Description", width="large"),
            "Quantity": st.column_config.TextColumn("Quantity", width="small"),
            "Unit": st.column_config.TextColumn("Unit", width="small"),
            "Unit price": st.column_config.TextColumn("Unit price", width="small"),
            "Line total": st.column_config.TextColumn("Line total", width="small"),
            "Confidence %": st.column_config.NumberColumn("Confidence %", format="%.0f%%"),
        },
    )

    issues = review_issues(
        edited_header,
        edited_lines,
        threshold=threshold,
        model_warnings=result.model_warnings,
    )
    _render_issues(issues)

    stem = _SAFE_STEM.sub("_", Path(document.filename).stem).strip("._") or "quote"
    csv_bytes = build_csv(edited_header, edited_lines)
    excel_bytes = build_excel(edited_header, edited_lines, issues)
    csv_col, excel_col = st.columns(2)
    csv_col.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name=f"{stem}-quote.csv",
        mime="text/csv",
        width="stretch",
        icon=":material/download:",
    )
    excel_col.download_button(
        "Download Excel",
        data=excel_bytes,
        file_name=f"{stem}-quote.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
        icon=":material/download:",
    )

    with st.expander("Validated JSON"):
        st.code(redact(result.raw_json), language="json")


def _render_issues(issues: list) -> None:
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    for issue in errors:
        st.error(issue.message)
    if warnings:
        with st.expander(f"Needs human review ({len(warnings)})", expanded=True):
            for issue in warnings:
                st.warning(issue.message)
    elif not errors:
        st.success("No review flags at the current threshold.")
