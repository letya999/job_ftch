"""Generate localized legal PDFs for the public website."""

from __future__ import annotations

import json
import shutil
import subprocess
from html import escape
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "job_ftch_site"
OUTPUT = ROOT / "output" / "pdf"
PUBLIC = SITE / "public" / "legal"


def load_documents() -> dict[str, dict[str, dict[str, object]]]:
    code = "import {legalDocuments} from './src/lib/legal.ts'; console.log(JSON.stringify(legalDocuments))"
    result = subprocess.run(
        ["bun", "-e", code], cwd=SITE, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return json.loads(result.stdout)


def build_pdf(path: Path, document: dict[str, object], locale: str) -> None:
    regular = Path("C:/Windows/Fonts/arial.ttf")
    bold = Path("C:/Windows/Fonts/arialbd.ttf")
    pdfmetrics.registerFont(TTFont("LegalSans", regular))
    pdfmetrics.registerFont(TTFont("LegalSansBold", bold))
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="LegalSans",
        fontSize=10.5,
        leading=16,
        textColor=HexColor("#222222"),
        spaceAfter=10,
    )
    title = ParagraphStyle(
        "Title", parent=body, fontName="LegalSansBold", fontSize=20, leading=25, spaceAfter=16
    )
    heading = ParagraphStyle(
        "Heading",
        parent=body,
        fontName="LegalSansBold",
        fontSize=12.5,
        leading=17,
        spaceBefore=14,
        spaceAfter=7,
    )
    meta = ParagraphStyle(
        "Meta", parent=body, fontSize=9, leading=14, textColor=HexColor("#666666"), spaceAfter=18
    )
    updated = "24 августа 2026 года" if locale == "ru" else "24 August 2026"
    version = "Версия: 1.0" if locale == "ru" else "Version: 1.0"
    changed = "Дата изменения" if locale == "ru" else "Last updated"

    def page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("LegalSans", 8)
        canvas.setFillColor(HexColor("#777777"))
        canvas.drawCentredString(A4[0] / 2, 13 * mm, f"job_ftch · {doc.page}")
        canvas.restoreState()

    story = [
        Paragraph(escape(str(document["title"])), title),
        Paragraph(f"{version}<br/>{changed}: {updated}", meta),
        Paragraph(escape(str(document["summary"])), body),
        Spacer(1, 4 * mm),
    ]
    for section in document["sections"]:
        story.append(Paragraph(escape(str(section["title"])), heading))
        story.extend(Paragraph(escape(str(paragraph)), body) for paragraph in section["paragraphs"])

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=24 * mm,
        leftMargin=24 * mm,
        topMargin=24 * mm,
        bottomMargin=22 * mm,
        title=str(document["title"]),
        author="job_ftch contributors",
    )
    pdf.build(story, onFirstPage=page_number, onLaterPages=page_number)


def main() -> None:
    documents = load_documents()
    for locale, localized in documents.items():
        for slug, document in localized.items():
            output = OUTPUT / f"job_ftch-{slug}-{locale}.pdf"
            build_pdf(output, document, locale)
            public = PUBLIC / locale / f"{slug}.pdf"
            public.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output, public)
    print(f"Generated {sum(len(items) for items in documents.values())} PDFs")


if __name__ == "__main__":
    main()
