"""Single-file form-data adapter using only the standard library.

Filenames are metadata only. Nested MIME and transfer-encoded uploads are not
supported; document bytes go unchanged to the existing document parser.
"""

from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser

from app.services.document_parser import DocumentParserError


class MultipartUploadError(DocumentParserError):
    """The request does not contain one well-formed file upload."""


@dataclass(frozen=True)
class UploadedDocument:
    filename: str
    content_type: str | None
    content: bytes


def _check_headers(message: Message) -> None:
    if message.defects or any(getattr(header, "defects", ()) for header in message.values()):
        raise MultipartUploadError("Malformed multipart MIME payload")
    for name in ("content-type", "content-disposition", "content-transfer-encoding"):
        if len(message.get_all(name, [])) > 1:
            raise MultipartUploadError("Conflicting multipart headers")


def parse_upload(content_type: str | None, body: bytes) -> UploadedDocument:
    """Extract exactly one file part, rejecting ambiguous or malformed MIME."""
    if not content_type or "\r" in content_type or "\n" in content_type:
        raise MultipartUploadError("Expected multipart/form-data with a boundary")
    try:
        header = content_type.encode("ascii")
    except UnicodeEncodeError:
        raise MultipartUploadError("Malformed multipart content type") from None
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + header + b"\r\nMIME-Version: 1.0\r\n\r\n" + body,
    )
    _check_headers(message)
    if message.get_content_type() != "multipart/form-data" or not message.get_boundary():
        raise MultipartUploadError("Expected multipart/form-data with a boundary")
    if not message.is_multipart():
        raise MultipartUploadError("Multipart payload contains no file parts")
    files = []
    for part in message.iter_parts():
        _check_headers(part)
        if part.is_multipart() or part.get_content_maintype() == "multipart":
            raise MultipartUploadError("Nested multipart uploads are unsupported")
        if part.get_content_disposition() != "form-data":
            raise MultipartUploadError("Expected form-data parts")
        if part.get_param("name", header="content-disposition") == "file":
            files.append(part)
    if len(files) != 1:
        raise MultipartUploadError("Expected exactly one uploaded part named file")
    part = files[0]
    filename = part.get_filename()
    if not filename or not filename.strip():
        raise MultipartUploadError("The file upload must supply a filename")
    encoding = part.get("content-transfer-encoding", "binary").lower()
    if encoding not in ("binary", "7bit", "8bit"):
        raise MultipartUploadError("Transfer-encoded file uploads are unsupported")
    content = part.get_payload(decode=True)
    if not isinstance(content, bytes):
        raise MultipartUploadError("The file upload has no binary payload")
    return UploadedDocument(filename, part.get("content-type"), content)
