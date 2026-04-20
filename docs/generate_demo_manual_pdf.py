from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer


BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR / "manual-demo-camino-feliz.md"
TARGET = BASE_DIR / "manual-demo-camino-feliz.pdf"


def build_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="TitleGerayse",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=28,
            textColor=HexColor("#17311f"),
            alignment=TA_CENTER,
            spaceAfter=16,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H1Gerayse",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            textColor=HexColor("#17311f"),
            spaceBefore=12,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H2Gerayse",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=HexColor("#2f5d3a"),
            spaceBefore=10,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyGerayse",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=15,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BulletGerayse",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            leftIndent=8,
        )
    )
    return styles


def flush_paragraph(buffer: list[str], story: list, styles) -> None:
    if not buffer:
        return
    text = " ".join(part.strip() for part in buffer if part.strip())
    if text:
        story.append(Paragraph(text, styles["BodyGerayse"]))
    buffer.clear()


def flush_bullets(buffer: list[str], story: list, styles) -> None:
    if not buffer:
        return
    items = [
        ListItem(Paragraph(item.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), styles["BulletGerayse"]))
        for item in buffer
    ]
    story.append(
        ListFlowable(
            items,
            bulletType="bullet",
            start="circle",
            leftIndent=18,
        )
    )
    story.append(Spacer(1, 0.15 * cm))
    buffer.clear()


def parse_markdown(source_text: str, styles) -> list:
    story: list = []
    paragraph_buffer: list[str] = []
    bullet_buffer: list[str] = []

    for raw_line in source_text.splitlines():
        line = raw_line.strip()

        if not line:
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets(bullet_buffer, story, styles)
            story.append(Spacer(1, 0.12 * cm))
            continue

        if line.startswith("# "):
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets(bullet_buffer, story, styles)
            story.append(Paragraph(line[2:].strip(), styles["TitleGerayse"]))
            continue

        if line.startswith("## "):
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets(bullet_buffer, story, styles)
            story.append(Paragraph(line[3:].strip(), styles["H1Gerayse"]))
            continue

        if line.startswith("### "):
            flush_paragraph(paragraph_buffer, story, styles)
            flush_bullets(bullet_buffer, story, styles)
            story.append(Paragraph(line[4:].strip(), styles["H2Gerayse"]))
            continue

        if line.startswith("- "):
            flush_paragraph(paragraph_buffer, story, styles)
            bullet_buffer.append(line[2:].strip())
            continue

        if line[:2].isdigit() and line[1:3] == ". ":
            flush_paragraph(paragraph_buffer, story, styles)
            bullet_buffer.append(line[3:].strip())
            continue

        paragraph_buffer.append(
            line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    flush_paragraph(paragraph_buffer, story, styles)
    flush_bullets(bullet_buffer, story, styles)
    return story


def main() -> None:
    styles = build_styles()
    doc = SimpleDocTemplate(
        str(TARGET),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.5 * cm,
        title="Manual De Demo - Gerayse 1.0",
        author="OpenAI Codex",
    )
    source_text = SOURCE.read_text(encoding="utf-8")
    story = parse_markdown(source_text, styles)
    doc.build(story)
    print(f"PDF generado en: {TARGET}")


if __name__ == "__main__":
    main()
