"""Draw the bundled synthetic quote.

The PNG is a one-page scan of the full quote. The PDF is the same quote split
across two image-only pages, so a text extractor would see nothing and the
vision path is what the app has to use.

Amounts and descriptions come from ``samples/expected.json``. Dates are printed
as unambiguous month names; the expected JSON stores the ISO form a correct
extraction should return.

Run from the repository root:

    python scripts/generate_samples.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ROOT / "samples" / "expected.json"
PNG_PATH = ROOT / "samples" / "northwind_quote.png"
PDF_PATH = ROOT / "samples" / "northwind_quote.pdf"

WIDTH = 1275
HEIGHT = 1650
MARGIN = 58

INK = (31, 41, 51)
MUTED = (82, 96, 109)
RULE = (208, 213, 221)
BAND = (31, 58, 77)
PAPER = (255, 252, 247)
WHITE = (255, 252, 247)
COPPER = (168, 122, 68)
ZEBRA = (244, 241, 234)

DISPLAY_DATES = {
    "2026-03-12": "12 March 2026",
    "2026-04-11": "11 April 2026",
}
CUSTOMER = (
    "Prepared for",
    "Helios Fabrication LLC",
    "Customer reference PO-REQ-3391",
)
ADDRESS = (
    "4180 N Basin Avenue",
    "Portland, OR 97217, USA",
    "+1 (503) 555-0148",
    "quotes@northwind-supply.example",
)
FIRST_PDF_LINES = 4

FONT_CANDIDATES = (
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
)


def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    for regular, heavy in FONT_CANDIDATES:
        path = heavy if bold else regular
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise SystemExit("Install DejaVu Sans or Liberation Sans to regenerate the samples.")


def wrap(text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if font.getlength(trial) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def draw_page(
    quote: dict,
    lines: list[dict],
    *,
    show_totals: bool,
    continued: str,
    page_label: str,
    first_line_number: int = 1,
) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    regular = {size: load_font(size) for size in (13, 15, 16, 18, 28)}
    bold = {size: load_font(size, bold=True) for size in (13, 15, 16, 22, 28)}

    band_bottom = 168
    draw.rectangle((0, 0, WIDTH, band_bottom), fill=BAND)
    draw.rectangle((0, band_bottom, WIDTH, band_bottom + 8), fill=COPPER)
    draw.text((MARGIN, 26), quote["supplier_name"], font=bold[28], fill=WHITE)
    y = 66
    for line in ADDRESS:
        draw.text((MARGIN, y), line, font=regular[15], fill=(226, 232, 236))
        y += 20
    quotation = "QUOTATION"
    q_width = bold[22].getlength(quotation)
    draw.text((WIDTH - MARGIN - q_width, 36), quotation, font=bold[22], fill=WHITE)
    number = quote["quote_number"]
    n_width = regular[16].getlength(number)
    draw.text((WIDTH - MARGIN - n_width, 70), number, font=regular[16], fill=WHITE)

    y = band_bottom + 28
    meta = [
        ("Quote number", quote["quote_number"]),
        ("Quote date", DISPLAY_DATES[quote["quote_date"]]),
        ("Valid until", DISPLAY_DATES[quote["valid_until"]]),
        ("Currency", quote["currency"]),
        ("Payment terms", quote["payment_terms"]),
        ("Delivery", quote["delivery_lead_time"]),
    ]
    box_w = (WIDTH - MARGIN * 2 - 16) / 2
    for index, (label, value) in enumerate(meta):
        column = index % 2
        row = index // 2
        x = MARGIN + column * (box_w + 16)
        top = y + row * 58
        draw.rounded_rectangle((x, top, x + box_w, top + 50), radius=8, outline=RULE, width=1)
        draw.text((x + 12, top + 6), label.upper(), font=bold[13], fill=MUTED)
        draw.text((x + 12, top + 24), value, font=regular[16], fill=INK)

    y = band_bottom + 28 + 3 * 58 + 16
    draw.text((MARGIN, y), CUSTOMER[0].upper(), font=bold[13], fill=MUTED)
    draw.text((MARGIN, y + 18), CUSTOMER[1], font=bold[16], fill=INK)
    draw.text((MARGIN, y + 40), CUSTOMER[2], font=regular[15], fill=MUTED)
    y += 78

    columns = (
        ("#", 36),
        ("SKU", 130),
        ("Description", 455),
        ("Qty", 70),
        ("Unit", 70),
        ("Unit price", 120),
        ("Line total", 130),
    )
    content_width = sum(width for _, width in columns)
    scale = (WIDTH - MARGIN * 2) / content_width
    widths = [int(width * scale) for _, width in columns]
    widths[-1] += (WIDTH - MARGIN * 2) - sum(widths)
    headers = [label for label, _ in columns]

    def row_x(index: int) -> int:
        return MARGIN + sum(widths[:index])

    header_h = 36
    draw.rectangle((MARGIN, y, WIDTH - MARGIN, y + header_h), fill=BAND)
    for index, label in enumerate(headers):
        draw.text((row_x(index) + 8, y + 9), label, font=bold[13], fill=WHITE)
    y += header_h

    row_h = 42
    for line_index, item in enumerate(lines, start=first_line_number):
        if line_index % 2 == 0:
            draw.rectangle((MARGIN, y, WIDTH - MARGIN, y + row_h), fill=ZEBRA)
        values = [
            str(line_index),
            item["sku"],
            item["description"],
            item["quantity"],
            item["unit"],
            item["unit_price"],
            item["line_total"],
        ]
        for index, value in enumerate(values):
            font = regular[15]
            text_width = font.getlength(value)
            x = row_x(index) + 8
            if index in {3, 5, 6}:
                x = row_x(index) + widths[index] - 8 - text_width
            draw.text((x, y + 12), value, font=font, fill=INK)
        y += row_h
        draw.line((MARGIN, y, WIDTH - MARGIN, y), fill=RULE, width=1)

    y += 16
    if continued:
        draw.text((MARGIN, y), continued, font=bold[16], fill=COPPER)
        y += 28

    if show_totals:
        totals = (
            ("Subtotal", quote["subtotal"]),
            ("Tax", quote["tax"]),
            ("Grand total", quote["grand_total"]),
        )
        box_width = 360
        box_x = WIDTH - MARGIN - box_width
        draw.rounded_rectangle(
            (box_x, y, WIDTH - MARGIN, y + 112),
            radius=8,
            outline=RULE,
            width=1,
        )
        for index, (label, amount) in enumerate(totals):
            top = y + 12 + index * 32
            font = bold[16] if label == "Grand total" else regular[16]
            draw.text((box_x + 16, top), label, font=font, fill=INK)
            amount_text = f"USD {amount}"
            amount_width = font.getlength(amount_text)
            draw.text((WIDTH - MARGIN - 16 - amount_width, top), amount_text, font=font, fill=INK)
        y += 128
        draw.text((MARGIN, y), "NOTES", font=bold[13], fill=MUTED)
        y += 20
        for line in wrap(quote["notes"], regular[15], WIDTH - MARGIN * 2):
            draw.text((MARGIN, y), line, font=regular[15], fill=INK)
            y += 22

    footer = f"Synthetic sample for software testing. Not a commercial offer.  {page_label}"
    draw.line((MARGIN, HEIGHT - 48, WIDTH - MARGIN, HEIGHT - 48), fill=RULE, width=1)
    draw.text((MARGIN, HEIGHT - 36), footer, font=regular[13], fill=MUTED)
    return image


def main() -> None:
    quote = json.loads(EXPECTED.read_text(encoding="utf-8"))
    lines = quote["line_items"]
    full = draw_page(
        quote,
        lines,
        show_totals=True,
        continued="",
        page_label="Page 1 of 1",
    )
    page_one = draw_page(
        quote,
        lines[:FIRST_PDF_LINES],
        show_totals=False,
        continued="Continued on page 2. Totals are printed on the final page.",
        page_label="Page 1 of 2",
    )
    page_two = draw_page(
        quote,
        lines[FIRST_PDF_LINES:],
        show_totals=True,
        continued="",
        page_label="Page 2 of 2",
        first_line_number=FIRST_PDF_LINES + 1,
    )
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    full.save(PNG_PATH, format="PNG", optimize=True)
    page_one.save(
        PDF_PATH,
        format="PDF",
        resolution=150.0,
        save_all=True,
        append_images=[page_two],
    )
    print(f"Wrote {PNG_PATH}")
    print(f"Wrote {PDF_PATH}")


if __name__ == "__main__":
    main()
