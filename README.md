# Supplier Quote Extractor

A local Streamlit app that reads a supplier quote (PDF or photo) with a vision model and returns an editable table. It is a portfolio project for document understanding, structured extraction, and containerization.

You run it on your own machine. The image has no API key in it. After the app is up, you paste a key into the sidebar, upload a quote, and download CSV or Excel.

## Run it

From this directory:

```bash
docker compose up --build
```

Open [http://localhost:8501](http://localhost:8501).

1. In the sidebar, leave **xAI (Grok)** selected or choose OpenAI, Anthropic, or Google Gemini.
2. Paste that provider's API key into the password field.
3. Upload a PDF, JPEG, or PNG, or choose **Bundled sample**.
4. Click **Extract Quote Data**.
5. Correct any cell, read the review flags, then download CSV or Excel.

Stop the app with Ctrl+C, then remove the container:

```bash
docker compose down
```

There is no volume to delete. Uploads live in a temporary filesystem that disappears with the container. The host port is `8501`. If that port is taken, change the left-hand side of `"8501:8501"` in `docker-compose.yml`.

The first build downloads Python and Poppler. Later starts are faster.

## Security

- The only place a key enters the app is the sidebar password field after the container is running.
- The key is held in Streamlit's in-memory session for that browser tab. Closing the session drops it.
- The app does not read environment variables, does not call `load_dotenv`, and does not write a `.env`, `secrets.toml`, or any other file that contains the key.
- Provider calls put the key in a request header only. It is not placed in the JSON body or the URL. Gemini's key is sent as `x-goog-api-key`, not as a query parameter.
- Error text and server logs pass through a redaction filter before they are shown.
- The container filesystem is read-only. `/tmp` and the app user's home directory are tmpfs mounts, so rasterized pages and Streamlit's own session files are not stored on disk.
- The image is built without secrets and the Compose file has no `env_file` and no `secrets`.
- xAI is called with chat completions, not the Responses API. The Responses API stores response objects for 30 days. The provider still processes the quote under its own retention policy for that request.

Do not add a `.env` file for this project. A key left on disk is outside the threat model this app is built for.

## Get an API key

Create a key in the provider's console, copy it, and paste it into the sidebar. The default model is filled in for you. Type another model id only when you want to override it.

| Provider | Default model | Where to create a key |
| --- | --- | --- |
| xAI (Grok) | `grok-4.7` | [console.x.ai](https://console.x.ai/login?mode=sign-up) · [API keys](https://console.x.ai/team/default/api-keys) |
| OpenAI | `gpt-4.1` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Anthropic | `claude-sonnet-4-6` | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) |
| Google Gemini | `gemini-2.5-flash` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

xAI is the default. `grok-4.7` accepts images and structured JSON. A quote with several pages can take a minute because the model reasons. Docs: [docs.x.ai](https://docs.x.ai).

Each key works only with its own provider. A 401 from the app means the key and the selected provider do not match.

## What the extraction returns

The model must return JSON that matches a Pydantic schema. Missing values are empty strings, and every value has a confidence score from 0 to 1.

- Supplier name
- Quote number, quote date, and valid-until date
- Currency (ISO 4217)
- Payment terms and delivery / lead time
- Notes
- Subtotal, tax, and grand total
- Line items: SKU, description, quantity, unit, unit price, line total
- A confidence score on each of those fields
- Warnings the model itself raises (illegible print, conflicting totals)

Dates are requested as `YYYY-MM-DD` when the printed date is unambiguous. Amounts are requested without currency symbols.

After validation, local checks flag:

- a missing supplier or a quote with no lines
- a missing quote number, date, currency, or grand total
- confidence below the sidebar threshold (default 70%)
- a quantity or price that is not a number
- a line total that does not match quantity times unit price
- duplicate rows
- a subtotal or grand total that does not match the lines

Editing a cell recomputes the math flags. It does not change the model's confidence score.

## Multi-page PDFs

PDF pages are rasterized with Poppler at 140 DPI, then resized so the longest edge is at most 1800 pixels. The sample PDF is image-only, which is the same situation as a scanned quote: there is no text layer to parse.

- **All pages in one request** sends up to 4 pages together so the model can see the whole quote. Longer files are sent in batches of 4 and merged.
- **Page by page, then merge** sends one page per request. Header fields are kept from the page with the higher confidence. Identical line items are dropped.

The merge prefers a filled value over an empty one, then the higher confidence.

## Sample quote

`samples/northwind_quote.png` is a one-page quote. `samples/northwind_quote.pdf` is the same quote split across two scanned pages. Both are synthetic. Northwind Industrial Supply and Helios Fabrication LLC are fictional.

The answer key is `samples/expected.json`. A correct read of the full quote looks like this:

| Field | Value |
| --- | --- |
| Supplier | Northwind Industrial Supply |
| Quote number | NW-Q-2026-1842 |
| Quote date | 2026-03-12 (printed as 12 March 2026) |
| Valid until | 2026-04-11 (printed as 11 April 2026) |
| Currency | USD |
| Payment terms | Net 30 from invoice date |
| Delivery | 3-4 weeks, EXW Portland warehouse |
| Subtotal | 797.00 |
| Tax | 0.00 |
| Grand total | 797.00 |

Seven lines, including an EXW handling charge, sum to 797.00. The customer on the page is Helios Fabrication LLC. That name is not the supplier.

Regenerate the images after editing the answer key:

```bash
python scripts/generate_samples.py
```

The script needs DejaVu Sans or Liberation Sans.

## How the code is split

```
app/main.py         Streamlit entry point
app/ui.py           Sidebar, preview, editors, downloads
app/documents.py    PDF rasterizing and image normalization
app/prompts.py      Extraction instructions
app/providers.py    xAI, OpenAI, Anthropic, and Gemini calls
app/schema.py       Pydantic models and strict JSON Schema
app/extraction.py   Batching and multi-page merge
app/quality.py      Review flags
app/export.py       CSV and Excel
app/redact.py       Removes keys from errors and logs
```

The JSON Schema sets `additionalProperties` to false and lists every field as required. Optional values use an empty string instead of null, which keeps the schema inside the structured-output subset those four APIs accept. Numeric bounds are enforced in Python after the response arrives, because several providers reject `minimum` / `maximum` in strict mode.

## Local development

Poppler must be on your PATH (`pdftoppm`). Debian/Ubuntu: `sudo apt install poppler-utils`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
streamlit run app/main.py
```

`python-dotenv` is installed because it is part of the requested stack. The application never imports it.

## Tests

`pytest` covers the schema, the sample document, quality checks, merge behavior, CSV/Excel export, and the provider request builders. Provider tests use an in-memory HTTP transport. They do not call xAI, OpenAI, Anthropic, or Google, and they assert that a key never appears in a URL or a JSON body.

A live extraction still needs a real key and spends provider credits. Use the bundled sample for that check.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| The provider rejected the API key | The key is for a different provider, or it was copied with extra text. |
| The model was not found | Clear the model override, or paste a current model id from that provider. |
| The provider took too long | Try page-by-page mode, or a shorter quote. Grok 4.7 can spend a minute reasoning. |
| This PDF is encrypted | Export a copy without a password. The app will not ask you for one. |
| PDF rendering is unavailable | The image is missing Poppler. Rebuild with `docker compose up --build`. |
| Port 8501 is in use | Change the host port in `docker-compose.yml`. |

## License

MIT. See [LICENSE](LICENSE).
