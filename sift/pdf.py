"""Read PDF links. The browser can't show them, so we download the file and pull out the text.

Universities, governments and events often put the real information in PDFs.
"""

import io
from urllib.parse import urlparse

import requests
from pypdf import PdfReader

# Skip very large PDFs (scanned reports, brochures). They are slow and rarely the answer.
MAX_PDF_SIZE = 20_000_000  # bytes


def is_pdf(url):
    return urlparse(url).path.lower().endswith(".pdf")


def pdf_text(url):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    if len(response.content) > MAX_PDF_SIZE:
        raise ValueError(f"PDF is larger than {MAX_PDF_SIZE // 1_000_000} MB, skipped")

    reader = PdfReader(io.BytesIO(response.content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
