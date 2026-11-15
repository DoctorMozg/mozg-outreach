"""Text from an uploaded CV, refusing files an application form or a reader can't use."""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PyPdfError

logger = logging.getLogger(__name__)

# Greenhouse refuses to parse résumés over 2.5 MB, so a bigger file is no use for applying.
MAX_PDF_BYTES = 2_500_000
# Less text than this means a scan or an image export: nothing for the AI or an ATS to read.
MIN_TEXT_CHARS = 200


class CvFileError(ValueError):
    """The upload is not a PDF this app can store and attach."""


def read_pdf(data: bytes) -> str:
    if not data.startswith(b"%PDF"):
        raise CvFileError("the file is not a PDF")
    if len(data) > MAX_PDF_BYTES:
        raise CvFileError(f"the PDF is {len(data) / 1e6:.1f} MB, over 2.5 MB")
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
        text = "" if encrypted else "\n".join(p.extract_text() or "" for p in reader.pages)
    # pypdf reports some malformed files as plain ValueError or KeyError, not its own errors.
    except (PyPdfError, ValueError, KeyError) as exc:
        raise CvFileError(f"the PDF can't be read: {exc}") from exc
    if encrypted:
        raise CvFileError("the PDF is password protected")
    text = text.strip()
    if len(text) < MIN_TEXT_CHARS:
        raise CvFileError(
            f"the PDF has no text layer ({len(text)} characters): export it from your editor "
            "instead of scanning or printing to an image"
        )
    logger.info("read a %d-page CV, %d characters", len(reader.pages), len(text))
    return text
