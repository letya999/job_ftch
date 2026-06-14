"""Tests for document text extraction."""

from __future__ import annotations

from job_ftch.infrastructure.document_parser import parse_document


def test_parse_txt():
    content = b"Hello world"
    assert parse_document(content, "file.txt") == "Hello world"


def test_parse_md():
    content = b"# Title\n**bold** and _italic_ text"
    result = parse_document(content, "file.md")
    assert "Title" in result
    assert "bold" in result
    assert "**" not in result


def test_parse_html():
    content = b"<html><body><h1>Job Title</h1><p>Description here</p></body></html>"
    result = parse_document(content, "file.html")
    assert "Job Title" in result
    assert "Description here" in result
    assert "<h1>" not in result


def test_parse_unknown_extension_utf8():
    content = "Привет мир".encode()
    result = parse_document(content, "file.xyz")
    assert "Привет" in result


def test_parse_md_strips_links():
    content = b"Check [this link](https://example.com) for details"
    result = parse_document(content, "notes.md")
    assert "this link" in result
    assert "https://example.com" not in result
