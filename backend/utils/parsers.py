"""Document parsing utilities — PDF, CSV, OCR fallback."""
import os
import logging
from typing import Optional
import pdfplumber
import pandas as pd

logger = logging.getLogger(__name__)


def parse_pdf(file_path: str) -> str:
    """Extract text from PDF. Falls back to OCR if no text layer."""
    text = _extract_pdf_text(file_path)
    if text and len(text.strip()) > 50:
        return text
    logger.info(f"No text layer in {file_path}, attempting OCR")
    return _ocr_pdf(file_path)


def _extract_pdf_text(file_path: str) -> str:
    """Extract text using pdfplumber."""
    try:
        full_text = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text.append(page_text)
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if row:
                            full_text.append(" | ".join(str(cell or "") for cell in row))
        return "\n".join(full_text)
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""


def _ocr_pdf(file_path: str) -> str:
    """OCR fallback using pytesseract."""
    try:
        import pytesseract
        from pdf2image import convert_from_path
        images = convert_from_path(file_path, dpi=300)
        text = []
        for i, img in enumerate(images):
            page_text = pytesseract.image_to_string(img)
            text.append(f"--- Page {i+1} ---\n{page_text}")
        return "\n".join(text)
    except ImportError:
        logger.warning("pytesseract or pdf2image not installed, OCR unavailable")
        return ""
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return ""


def parse_csv(file_path: str) -> str:
    """Parse CSV and return structured text representation."""
    try:
        df = pd.read_csv(file_path)
        lines = []
        lines.append(f"CSV with {len(df)} rows and {len(df.columns)} columns")
        lines.append(f"Columns: {', '.join(df.columns)}")
        lines.append("")
        # Include first 50 rows as structured text
        for idx, row in df.head(50).iterrows():
            row_parts = [f"{col}: {val}" for col, val in row.items() if pd.notna(val)]
            lines.append(" | ".join(row_parts))
        if len(df) > 50:
            lines.append(f"\n... and {len(df) - 50} more rows")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"CSV parsing failed: {e}")
        return ""


def parse_csv_raw(file_path: str) -> Optional[pd.DataFrame]:
    """Return raw DataFrame for direct parsing of known formats."""
    try:
        return pd.read_csv(file_path)
    except Exception as e:
        logger.error(f"CSV raw parse failed: {e}")
        return None


def parse_document(file_path: str) -> str:
    """Route to correct parser based on file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return parse_pdf(file_path)
    elif ext == ".csv":
        return parse_csv(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")
