"""Safe text extraction for supported local document formats."""

from pathlib import Path
import zipfile


TEXT_EXTENSIONS = {".txt", ".md", ".rst"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS
MAX_EXTRACTED_CHARACTERS = 500_000
MAX_DOCX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_DOCX_MEMBERS = 2_000
MAX_COMPRESSION_RATIO = 100
MAX_PDF_PAGES = 200


class UnsupportedDocumentError(ValueError):
    pass


def extract_text(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_EXTRACTED_CHARACTERS]
    if extension == ".docx":
        return _extract_docx(path)
    if extension == ".pdf":
        return _extract_pdf(path)
    raise UnsupportedDocumentError(f"Unsupported file type: {extension or '(no extension)'}")


def _extract_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            uncompressed_size = sum(member.file_size for member in members)
            if len(members) > MAX_DOCX_MEMBERS or uncompressed_size > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise UnsupportedDocumentError("DOCX archive exceeds extraction safety limits.")
            if any(
                member.file_size > 0
                and member.file_size / max(member.compress_size, 1) > MAX_COMPRESSION_RATIO
                for member in members
            ):
                raise UnsupportedDocumentError("DOCX archive compression ratio exceeds safety limits.")
            if "word/document.xml" not in archive.namelist():
                raise UnsupportedDocumentError("DOCX archive does not contain a document body.")
    except zipfile.BadZipFile as error:
        raise UnsupportedDocumentError("DOCX file is not a valid ZIP archive.") from error

    try:
        from docx import Document
    except ImportError as error:
        raise RuntimeError("Install the document extra with `pip install -e '.[documents]'` for DOCX support.") from error

    document = Document(path)
    text_parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
    for table in document.tables:
        for row in table.rows:
            text_parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(text_parts)[:MAX_EXTRACTED_CHARACTERS]


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("Install the document extra with `pip install -e '.[documents]'` for PDF support.") from error

    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise UnsupportedDocumentError("Encrypted PDF files cannot be indexed.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise UnsupportedDocumentError("PDF page count exceeds the extraction safety limit.")
        text_parts = []
        extracted_characters = 0
        for page in reader.pages:
            remaining = MAX_EXTRACTED_CHARACTERS - extracted_characters
            if remaining <= 0:
                break
            page_text = page.extract_text() or ""
            page_text = page_text[:remaining]
            text_parts.append(page_text)
            extracted_characters += len(page_text)
        return "\n".join(text_parts)
    except UnsupportedDocumentError:
        raise
    except Exception as error:
        raise UnsupportedDocumentError("PDF text extraction failed.") from error