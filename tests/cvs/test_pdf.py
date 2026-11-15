import pytest

from mozg_outreach.cvs.pdf import MAX_PDF_BYTES, CvFileError, read_pdf
from tests.cvs.pdfs import make_pdf


class TestReadPdf:
    def test_returns_the_text_layer(self, cv_pdf: bytes) -> None:
        text = read_pdf(cv_pdf)
        assert text.startswith("Jane Example - Senior Python Engineer")
        assert "FastAPI and PostgreSQL" in text

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (b"Jane Example, Python engineer", "not a PDF"),
            (b"%PDF-1.4\nbroken", "can't be read"),
            (make_pdf(["Jane Example"]), "no text layer"),
            (b"%PDF-1.4\n" + b"0" * MAX_PDF_BYTES, "over 2.5 MB"),
        ],
        ids=["not-pdf", "broken", "scan", "too-big"],
    )
    def test_refuses_files_a_form_or_reader_cannot_use(self, data: bytes, message: str) -> None:
        with pytest.raises(CvFileError, match=message):
            read_pdf(data)
