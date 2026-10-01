"""Pure-Python document extraction service for OrderShield purchase orders (T009).

Accepts UTF-8 plain text (.txt / text/plain) and digital PDFs with extractable
text streams (.pdf / application/pdf) via pypdf. Produces deterministic canonical
text suitable for PurchaseOrderDocument.raw_text and downstream provenance /
grounding validation.

Scanned and image-only documents are explicitly unsupported and rejected
immediately without OCR or AI fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
from typing import Any, BinaryIO

import pypdf
import pypdf.errors

from app.models.schemas import LocationDataSchema

SUPPORTED_CONTENT_TYPES: frozenset[str] = frozenset({"text/plain", "application/pdf"})

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


class DocumentParserError(Exception):
    """Base exception for document parsing failures."""


class UnextractableTextError(DocumentParserError, ValueError):
    """Raised when a document is empty, corrupted, or lacks extractable text streams."""


class UnsupportedDocumentTypeError(DocumentParserError, ValueError):
    """Raised when a document MIME type or file extension is unsupported."""


@dataclass(frozen=True)
class PageSpan:
    """Deterministic character span for an individual PDF page within canonical raw_text.

    Attributes:
        page_number: 1-based page index.
        char_start: 0-based character start index in canonical raw_text (inclusive).
        char_end: 0-based character end index in canonical raw_text (exclusive).
    """

    page_number: int
    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError(f"page_number must be >= 1, got {self.page_number}")
        if self.char_start < 0:
            raise ValueError(f"char_start must be >= 0, got {self.char_start}")
        if self.char_end <= self.char_start:
            raise ValueError(
                f"char_end ({self.char_end}) must be greater than char_start ({self.char_start})"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return canonical PDF location dictionary conforming to LocationDataSchema."""
        return {
            "type": "pdf",
            "page_number": self.page_number,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }

    def to_schema(self) -> LocationDataSchema:
        """Return validated Pydantic LocationDataSchema model."""
        return LocationDataSchema.model_validate(self.to_dict())


@dataclass(frozen=True)
class LineSpan:
    """Deterministic character span for an individual line within canonical raw_text.

    Attributes:
        line_number: 1-based line index.
        char_start: 0-based character start index in canonical raw_text (inclusive).
        char_end: 0-based character end index in canonical raw_text (exclusive).
        text: Line text stripped of trailing newline characters.
    """

    line_number: int
    char_start: int
    char_end: int
    text: str

    def __post_init__(self) -> None:
        if self.line_number < 1:
            raise ValueError(f"line_number must be >= 1, got {self.line_number}")
        if self.char_start < 0:
            raise ValueError(f"char_start must be >= 0, got {self.char_start}")
        if self.char_end < self.char_start:
            raise ValueError(
                f"char_end ({self.char_end}) must be >= char_start ({self.char_start})"
            )

    def get_location(self, char_offset: int) -> dict[str, Any]:
        """Return canonical TXT location dictionary conforming to LocationDataSchema."""
        if char_offset < 0:
            raise ValueError(f"char_offset must be >= 0, got {char_offset}")
        return {
            "type": "txt",
            "line_number": self.line_number,
            "char_offset": char_offset,
        }


@dataclass(frozen=True)
class ParsedDocument:
    """Deterministic result of document text extraction.

    Attributes:
        raw_text: Canonical extracted text without semantic rewriting or normalization.
        content_type: Canonical MIME type ('text/plain' or 'application/pdf').
        filename: Optional source document filename.
        page_spans: Deterministic PDF page spans (empty tuple for plain text documents).
        line_spans: Deterministic line spans across canonical raw_text.
    """

    raw_text: str
    content_type: str
    filename: str | None = None
    page_spans: tuple[PageSpan, ...] = ()
    line_spans: tuple[LineSpan, ...] = ()

    @property
    def is_pdf(self) -> bool:
        return self.content_type == "application/pdf"

    @property
    def is_text(self) -> bool:
        return self.content_type == "text/plain"

    def locate_snippet(
        self,
        snippet: str,
        *,
        occurrence: int = 1,
        line_number: int | None = None,
        page_number: int | None = None,
    ) -> dict[str, Any] | None:
        """Find the location of verbatim snippet in canonical raw_text.

        Returns canonical location dict suitable for LocationDataSchema:
        - For .txt: {"type": "txt", "line_number": int, "char_offset": int}
        - For .pdf: {"type": "pdf", "page_number": int, "char_start": int, "char_end": int}
        Returns None if snippet is not found.
        """
        if not snippet:
            return None
        if occurrence < 1:
            raise ValueError(f"occurrence must be >= 1, got {occurrence}")

        if self.content_type == "text/plain":
            found_count = 0
            for line_span in self.line_spans:
                if line_number is not None and line_span.line_number != line_number:
                    continue
                col_offset = 0
                while True:
                    idx = line_span.text.find(snippet, col_offset)
                    if idx == -1:
                        break
                    found_count += 1
                    if found_count == occurrence:
                        return create_txt_location(
                            line_number=line_span.line_number,
                            char_offset=idx,
                        )
                    col_offset = idx + 1
            return None

        elif self.content_type == "application/pdf":
            found_count = 0
            start_search = 0
            while True:
                idx = self.raw_text.find(snippet, start_search)
                if idx == -1:
                    return None
                snippet_end = idx + len(snippet)
                matching_span = None
                for span in self.page_spans:
                    if span.char_start <= idx and snippet_end <= span.char_end:
                        if page_number is None or span.page_number == page_number:
                            matching_span = span
                        break
                if matching_span is not None:
                    found_count += 1
                    if found_count == occurrence:
                        return create_pdf_location(
                            page_number=matching_span.page_number,
                            char_start=idx,
                            char_end=snippet_end,
                        )
                start_search = idx + 1

        return None


def create_txt_location(line_number: int, char_offset: int) -> dict[str, Any]:
    """Create a validated plain text location dict conforming to LocationDataSchema."""
    if line_number < 1:
        raise ValueError(f"line_number must be >= 1, got {line_number}")
    if char_offset < 0:
        raise ValueError(f"char_offset must be >= 0, got {char_offset}")
    data = {"type": "txt", "line_number": line_number, "char_offset": char_offset}
    LocationDataSchema.model_validate(data)
    return data


def create_pdf_location(page_number: int, char_start: int, char_end: int) -> dict[str, Any]:
    """Create a validated PDF location dict conforming to LocationDataSchema."""
    if page_number < 1:
        raise ValueError(f"page_number must be >= 1, got {page_number}")
    if char_start < 0:
        raise ValueError(f"char_start must be >= 0, got {char_start}")
    if char_end <= char_start:
        raise ValueError(f"char_end ({char_end}) must be greater than char_start ({char_start})")
    data = {
        "type": "pdf",
        "page_number": page_number,
        "char_start": char_start,
        "char_end": char_end,
    }
    LocationDataSchema.model_validate(data)
    return data


def compute_line_spans(raw_text: str) -> tuple[LineSpan, ...]:
    """Compute line spans across canonical raw_text."""
    line_spans: list[LineSpan] = []
    current_offset = 0
    for line_number, line_with_ending in enumerate(
        raw_text.splitlines(keepends=True), start=1
    ):
        line_content = line_with_ending.rstrip("\r\n")
        line_spans.append(
            LineSpan(
                line_number=line_number,
                char_start=current_offset,
                char_end=current_offset + len(line_content),
                text=line_content,
            )
        )
        current_offset += len(line_with_ending)
    return tuple(line_spans)


def resolve_content_type(
    filename: str | None = None,
    content_type: str | None = None,
    content: bytes | str | None = None,
) -> str:
    """Resolve and validate the canonical content type ('text/plain' or 'application/pdf').

    Raises:
        UnsupportedDocumentTypeError: If the format is unsupported or conflicting.
    """
    resolved_by_type: str | None = None
    if content_type is not None:
        clean_type = content_type.split(";")[0].strip().lower()
        if clean_type in SUPPORTED_CONTENT_TYPES:
            resolved_by_type = clean_type
        else:
            raise UnsupportedDocumentTypeError(f"Unsupported content type: '{content_type}'")

    resolved_by_ext: str | None = None
    if filename is not None:
        ext = Path(filename).suffix.lower()
        if ext in SUPPORTED_EXTENSIONS:
            resolved_by_ext = SUPPORTED_EXTENSIONS[ext]
        elif ext:
            raise UnsupportedDocumentTypeError(
                f"Unsupported file extension: '{ext}' for file '{filename}'"
            )

    if resolved_by_type is not None and resolved_by_ext is not None:
        if resolved_by_type != resolved_by_ext:
            raise UnsupportedDocumentTypeError(
                f"Conflicting document formats: content_type '{content_type}' does not match filename '{filename}'"
            )
        return resolved_by_type

    if resolved_by_type is not None:
        return resolved_by_type

    if resolved_by_ext is not None:
        return resolved_by_ext

    # Format inference from content if neither content_type nor recognizable filename was provided
    if isinstance(content, str):
        return "text/plain"

    if isinstance(content, (bytes, bytearray)):
        if content.startswith(b"%PDF"):
            return "application/pdf"
        try:
            content.decode("utf-8", errors="strict")
            return "text/plain"
        except UnicodeDecodeError:
            pass

    raise UnsupportedDocumentTypeError(
        "Document format cannot be determined: neither valid content_type nor recognizable filename was provided"
    )


def _parse_text_bytes(
    content_bytes: bytes,
    filename: str | None = None,
) -> ParsedDocument:
    """Extract canonical text from UTF-8 plain text bytes."""
    if not content_bytes:
        raise UnextractableTextError("Plain text document is empty (0 bytes)")

    try:
        raw_text = content_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnextractableTextError(f"Document contains malformed UTF-8 bytes: {exc}") from exc

    if not raw_text.strip():
        raise UnextractableTextError("Plain text document contains no extractable text")

    line_spans = compute_line_spans(raw_text)

    return ParsedDocument(
        raw_text=raw_text,
        content_type="text/plain",
        filename=filename,
        page_spans=(),
        line_spans=line_spans,
    )


def _parse_pdf_bytes(
    content_bytes: bytes,
    filename: str | None = None,
) -> ParsedDocument:
    """Extract canonical text from digital PDF text streams using pypdf."""
    if not content_bytes:
        raise UnextractableTextError("PDF document is empty (0 bytes)")

    try:
        stream = io.BytesIO(content_bytes)
        reader = pypdf.PdfReader(stream)
    except Exception as exc:
        raise UnextractableTextError(f"Failed to read PDF document: {exc}") from exc

    if reader.is_encrypted:
        try:
            decrypted = reader.decrypt("")
            if decrypted == 0:
                raise UnextractableTextError(
                    "Encrypted PDF document cannot be decrypted without password"
                )
        except Exception as exc:
            raise UnextractableTextError(f"Encrypted PDF document could not be read: {exc}") from exc

    if len(reader.pages) == 0:
        raise UnextractableTextError("PDF document contains no pages")

    extracted_pages: list[tuple[int, str]] = []
    for page_idx, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise UnextractableTextError(
                f"Failed to extract text from PDF page {page_idx}: {exc}"
            ) from exc

        if page_text and page_text.strip():
            extracted_pages.append((page_idx, page_text))

    if not extracted_pages:
        raise UnextractableTextError("PDF document contains no extractable textual content")

    raw_parts: list[str] = []
    page_spans: list[PageSpan] = []
    current_offset = 0

    for i, (page_num, text) in enumerate(extracted_pages):
        if i > 0:
            raw_parts.append("\n")
            current_offset += 1

        char_start = current_offset
        char_end = current_offset + len(text)
        page_spans.append(
            PageSpan(
                page_number=page_num,
                char_start=char_start,
                char_end=char_end,
            )
        )
        raw_parts.append(text)
        current_offset += len(text)

    raw_text = "".join(raw_parts)
    line_spans = compute_line_spans(raw_text)

    return ParsedDocument(
        raw_text=raw_text,
        content_type="application/pdf",
        filename=filename,
        page_spans=tuple(page_spans),
        line_spans=line_spans,
    )


def parse_document(
    content: bytes | str | BinaryIO | Path,
    filename: str | None = None,
    content_type: str | None = None,
) -> ParsedDocument:
    """Parse document content into canonical raw_text and deterministic location metadata.

    Supports UTF-8 plain text (.txt / text/plain) and digital PDFs (.pdf / application/pdf).

    Args:
        content: Raw bytes, string text, file-like binary stream, or Path to document file.
        filename: Optional filename used for format resolution and metadata.
        content_type: Optional MIME content type ('text/plain' or 'application/pdf').

    Returns:
        ParsedDocument containing canonical raw_text, content_type, and location spans.

    Raises:
        UnextractableTextError: If document is empty, corrupted, has invalid UTF-8 bytes,
                                or lacks extractable text streams.
        UnsupportedDocumentTypeError: If content_type or file extension is unsupported.
    """
    if isinstance(content, Path):
        filename = filename or content.name
        content_bytes = content.read_bytes()
    elif isinstance(content, str):
        if os.path.isfile(content):
            path_obj = Path(content)
            filename = filename or path_obj.name
            content_bytes = path_obj.read_bytes()
        else:
            resolved_type = resolve_content_type(
                filename=filename,
                content_type=content_type or "text/plain",
                content=content,
            )
            if resolved_type != "text/plain":
                raise UnsupportedDocumentTypeError(
                    f"String content cannot be parsed as '{resolved_type}'; string input requires 'text/plain'"
                )
            if not content.strip():
                raise UnextractableTextError("Plain text document contains no extractable text")
            line_spans = compute_line_spans(content)
            return ParsedDocument(
                raw_text=content,
                content_type="text/plain",
                filename=filename,
                page_spans=(),
                line_spans=line_spans,
            )
    elif hasattr(content, "read"):
        content_data = content.read()
        if isinstance(content_data, str):
            content_bytes = content_data.encode("utf-8")
        else:
            content_bytes = bytes(content_data)
    elif isinstance(content, (bytes, bytearray)):
        content_bytes = bytes(content)
    else:
        raise UnsupportedDocumentTypeError(
            f"Unsupported input type '{type(content).__name__}'; expected bytes, str, BinaryIO, or Path"
        )

    resolved_content_type = resolve_content_type(
        filename=filename,
        content_type=content_type,
        content=content_bytes,
    )

    if resolved_content_type == "text/plain":
        return _parse_text_bytes(content_bytes, filename=filename)
    elif resolved_content_type == "application/pdf":
        return _parse_pdf_bytes(content_bytes, filename=filename)
    else:
        raise UnsupportedDocumentTypeError(f"Unsupported content type '{resolved_content_type}'")


def parse_file(
    file_path: str | Path,
    content_type: str | None = None,
) -> ParsedDocument:
    """Parse document directly from a filesystem path."""
    path_obj = Path(file_path)
    if not path_obj.is_file():
        raise FileNotFoundError(f"Document file not found: {file_path}")
    return parse_document(
        content=path_obj.read_bytes(),
        filename=path_obj.name,
        content_type=content_type,
    )
