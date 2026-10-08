"""Isolated, resource-limited document extraction. JSON over stdout only."""

import json
import sys
from pathlib import Path

from .ingestion import MAX_EXTRACTED_CHARACTERS, extract_text


def extract(path: Path):
    # Format-specific labels form useful citation anchors without executing content.
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        from .ingestion import MAX_PDF_PAGES, UnsupportedDocumentError

        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > MAX_PDF_PAGES:
            raise UnsupportedDocumentError("Encrypted PDF or PDF exceeds 200-page limit.")
        sections, count = [], 0
        for i, page in enumerate(reader.pages, 1):
            if count >= MAX_EXTRACTED_CHARACTERS:
                break
            text = (page.extract_text() or "")[: MAX_EXTRACTED_CHARACTERS - count]
            sections.append({"label": f"Page {i}", "text": text})
            count += len(text)
    else:
        text = extract_text(path)
        import re

        pattern = (
            r"(?=^Slide \d+$)"
            if path.suffix.lower() == ".pptx"
            else r"(?=^Sheet: .+$)"
            if path.suffix.lower() == ".xlsx"
            else r"(?=^#{1,6} .+$)"
            if path.suffix.lower() == ".md"
            else None
        )
        parts = re.split(pattern, text, flags=re.MULTILINE) if pattern else [text]
        sections = [
            {"label": p.splitlines()[0][:100] if pattern else "Document", "text": p}
            for p in parts
            if p.strip()
        ]
        count = len(text)
    text = "\n\n".join(s["text"] for s in sections)
    if not text.strip():
        raise ValueError("No selectable text. Scanned documents require OCR, which is not enabled.")
    warnings = []
    if count >= MAX_EXTRACTED_CHARACTERS:
        warnings.append("Partial extraction: reached the 500,000-character limit.")
    if path.suffix.lower() == ".xlsx":
        warnings.append(
            "Spreadsheet extraction uses cached cell values, up to 50,000 cells; formulas are not evaluated."
        )
    return {"text": text, "sections": sections, "warnings": warnings}


if __name__ == "__main__":
    try:
        if sys.platform == "linux":
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
            resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        print(json.dumps(extract(Path(sys.argv[1]))))
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:400]}))
        sys.exit(1)
