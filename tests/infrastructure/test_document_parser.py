from unittest.mock import patch

import pytest

from job_ftch.infrastructure.document_parser import DocumentParseError, parse_document


def test_parse_document_pdf_failure_raises_not_returns_string():
    with (
        patch(
            "job_ftch.infrastructure.document_parser._extract_pdf",
            side_effect=DocumentParseError("failed"),
        ),
        pytest.raises(DocumentParseError, match="failed"),
    ):
        parse_document(b"fake pdf", "resume.pdf")


def test_parse_document_docx_failure_raises_not_returns_string():
    with (
        patch(
            "job_ftch.infrastructure.document_parser._extract_docx",
            side_effect=DocumentParseError("failed"),
        ),
        pytest.raises(DocumentParseError, match="failed"),
    ):
        parse_document(b"fake docx", "resume.docx")
