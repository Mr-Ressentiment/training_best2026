"""T013: canonical document text and locations, with no OCR or AI fallback."""

from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app.models.schemas import LocationDataSchema
from app.services.document_parser import (
    UnextractableTextError,
    parse_document,
)


def test_committed_utf8_po_preserves_decoded_source_exactly(po_clean_acme_path):
    source = po_clean_acme_path.read_bytes().decode("utf-8")

    parsed = parse_document(po_clean_acme_path)

    assert parsed.raw_text == source
    assert "Customer: Acme Industrial Supplies" in parsed.raw_text
    assert parsed.content_type == "text/plain"
    assert parsed.page_spans == ()


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_utf8_text_preserves_spacing_punctuation_currency_and_trailing_newline(newline):
    source = newline.join([
        "  Customer: Acme, S.A. — Chișinău  ",
        "PO: #10023; quoted: 'heavy-duty'!",
        "\tPrice: $25.00 / €23.10 / £20.01  ",
        "",
    ])

    parsed = parse_document(source.encode("utf-8"), filename="preservation.txt")

    assert parsed.raw_text == source
    assert parsed.raw_text.endswith(newline)


@pytest.mark.parametrize("snippet,line_number,char_offset", [
    ("Acme Industrial Supplies", 2, 10),
    ("PO-10023", 4, 11),
    ("18in stretch film heavy duty", 9, 0),
    ("$25.00", 9, 51),
])
def test_txt_locations_point_to_verbatim_snippets_in_canonical_text(
    po_clean_acme_path, snippet, line_number, char_offset,
):
    parsed = parse_document(po_clean_acme_path)

    location = parsed.locate_snippet(snippet)

    assert location == {
        "type": "txt", "line_number": line_number, "char_offset": char_offset,
    }
    LocationDataSchema.model_validate(location)
    canonical_line = parsed.raw_text.splitlines()[line_number - 1]
    assert canonical_line[char_offset:char_offset + len(snippet)] == snippet
    span = parsed.line_spans[line_number - 1]
    start = span.char_start + char_offset
    assert parsed.raw_text[start:start + len(snippet)] == snippet


def _write_digital_pdf(path: Path) -> None:
    """Create two selectable-text pages using the already accepted pypdf dependency."""
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for text in ("Acme Industrial Supplies", "PO-10023 / $25.00"):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = stream
    writer.write(path)


def test_digital_pdf_text_pages_and_locations_use_canonical_raw_text(tmp_path):
    path = tmp_path / "digital_po.pdf"
    _write_digital_pdf(path)

    parsed = parse_document(path)

    assert parsed.content_type == "application/pdf"
    assert parsed.raw_text
    assert "Acme Industrial Supplies" in parsed.raw_text
    assert "PO-10023 / $25.00" in parsed.raw_text
    assert [span.page_number for span in parsed.page_spans] == [1, 2]
    assert parsed.page_spans[0].char_start == 0
    assert parsed.page_spans[-1].char_end == len(parsed.raw_text)
    for snippet, page_number in (("Acme Industrial Supplies", 1), ("PO-10023", 2)):
        location = parsed.locate_snippet(snippet, page_number=page_number)
        assert location is not None
        assert location["type"] == "pdf"
        assert location["page_number"] == page_number
        assert parsed.raw_text[location["char_start"]:location["char_end"]] == snippet
        span = parsed.page_spans[page_number - 1]
        assert span.char_start <= location["char_start"] < location["char_end"] <= span.char_end
        LocationDataSchema.model_validate(location)
    assert parse_document(path) == parsed


def test_committed_unextractable_pdf_raises_explicit_error(po_unextractable_pdf_path):
    with pytest.raises(UnextractableTextError):
        parse_document(po_unextractable_pdf_path)
