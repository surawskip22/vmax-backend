from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import qrcode
from openai import OpenAI
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import models
from .config import get_settings
from .storage import storage


def _report_entries(db: Session, report: models.Report) -> list[models.Entry]:
    query = (
        select(models.Entry)
        .options(
            selectinload(models.Entry.stage),
            selectinload(models.Entry.media),
            selectinload(models.Entry.author),
        )
        .where(models.Entry.project_id == report.project_id)
        .order_by(models.Entry.occurred_at.asc())
    )
    if report.period_from:
        query = query.where(models.Entry.occurred_at >= report.period_from)
    if report.period_to:
        query = query.where(models.Entry.occurred_at <= report.period_to)
    return list(db.scalars(query).all())


def deterministic_content(
    project: models.Project, entries: list[models.Entry]
) -> dict:
    stage_groups: dict[str, list[dict]] = {}
    problems: list[dict] = []
    for item in entries:
        stage_title = item.stage.title if item.stage else "Bez etapu"
        note = item.body or item.transcript or "Dodano dokumentację zdjęciową."
        serialized = {
            "entry_id": item.id,
            "date": item.occurred_at.date().isoformat(),
            "text": note,
            "kind": item.kind,
            "problem_status": item.problem_status,
            "media_ids": [asset.id for asset in item.media if asset.kind == "image"],
        }
        stage_groups.setdefault(stage_title, []).append(serialized)
        if item.kind == "problem":
            problems.append(serialized)

    return {
        "project_name": project.name,
        "client_name": project.client_name,
        "address": project.address,
        "summary": (
            f"Raport obejmuje {len(entries)} wpisów z realizacji „{project.name}”."
            if entries
            else "W wybranym okresie nie dodano jeszcze wpisów."
        ),
        "stages": [
            {"title": title, "entries": grouped}
            for title, grouped in stage_groups.items()
        ],
        "problems": problems,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _merge_generated_content(fallback: dict, generated: dict) -> dict:
    generated_entries: dict[str, dict] = {}
    for group in generated.get("stages", []):
        for item in group.get("entries", []):
            if item.get("entry_id"):
                generated_entries[item["entry_id"]] = item
    for item in generated.get("problems", []):
        if item.get("entry_id"):
            generated_entries[item["entry_id"]] = item

    for group in fallback["stages"]:
        for item in group["entries"]:
            rewritten = generated_entries.get(item["entry_id"], {}).get("text")
            if isinstance(rewritten, str) and rewritten.strip():
                item["text"] = rewritten.strip()
    for item in fallback["problems"]:
        rewritten = generated_entries.get(item["entry_id"], {}).get("text")
        if isinstance(rewritten, str) and rewritten.strip():
            item["text"] = rewritten.strip()

    summary = generated.get("summary")
    if isinstance(summary, str) and summary.strip():
        fallback["summary"] = summary.strip()
    return fallback


def generate_report_content(db: Session, report: models.Report) -> dict:
    project = db.get(models.Project, report.project_id)
    entries = _report_entries(db, report)
    fallback = deterministic_content(project, entries)
    settings = get_settings()
    if not settings.openai_api_key or not entries:
        return fallback

    source = [
        {
            "entry_id": item.id,
            "date": item.occurred_at.date().isoformat(),
            "stage": item.stage.title if item.stage else "Bez etapu",
            "kind": item.kind,
            "status": item.problem_status,
            "text": item.body or item.transcript,
            "media_ids": [
                asset.id for asset in item.media if asset.kind == "image"
            ],
        }
        for item in entries
    ]
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_report_model,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Tworzysz rzeczowy raport z prac terenowych po polsku. "
                    "Nie dopisuj faktów. Zwróć JSON z polami summary, stages i problems. "
                    "stages to lista obiektów {title, entries}; entries zawiera obiekty "
                    "{entry_id,date,text,kind,problem_status,media_ids}. Zachowaj entry_id "
                    "i media_ids z danych wejściowych."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "project": {
                            "name": project.name,
                            "client_name": project.client_name,
                            "address": project.address,
                        },
                        "entries": source,
                        "fallback": fallback,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    generated = json.loads(response.choices[0].message.content or "{}")
    return _merge_generated_content(fallback, generated)


def transcribe_asset(asset: models.MediaAsset) -> str:
    settings = get_settings()
    if not settings.openai_api_key:
        return ""
    client = OpenAI(api_key=settings.openai_api_key)
    with storage.open(asset.storage_key) as audio:
        result = client.audio.transcriptions.create(
            model=settings.openai_transcription_model,
            file=audio,
            language="pl",
        )
    return result.text


def _font_name() -> str:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                pdfmetrics.registerFont(TTFont("PanMajsterFont", str(candidate)))
                return "PanMajsterFont"
            except Exception:
                continue
    return "Helvetica"


def render_pdf(report: models.Report, share_url: str) -> bytes:
    content = report.content or {}
    font = _font_name()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=report.title,
    )
    styles = getSampleStyleSheet()
    for style_name in ("Title", "Heading1", "Heading2", "BodyText"):
        styles[style_name].fontName = font
    styles.add(
        ParagraphStyle(
            "Meta",
            parent=styles["BodyText"],
            textColor=colors.HexColor("#5d675f"),
            fontSize=9,
            leading=13,
        )
    )
    styles.add(
        ParagraphStyle(
            "Footer",
            parent=styles["Meta"],
            alignment=TA_CENTER,
        )
    )

    story = [
        Paragraph("PAN MAJSTER", styles["Meta"]),
        Spacer(1, 5 * mm),
        Paragraph(report.title, styles["Title"]),
        Paragraph(content.get("project_name", ""), styles["Heading2"]),
        Spacer(1, 3 * mm),
    ]
    meta_rows = [
        ["Klient", content.get("client_name") or "—"],
        ["Adres", content.get("address") or "—"],
        ["Wygenerowano", datetime.now().strftime("%d.%m.%Y %H:%M")],
    ]
    meta = Table(meta_rows, colWidths=[35 * mm, 120 * mm])
    meta.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#687169")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend(
        [
            meta,
            Spacer(1, 6 * mm),
            Paragraph(content.get("summary", ""), styles["BodyText"]),
            Spacer(1, 7 * mm),
        ]
    )

    for group in content.get("stages", []):
        story.append(Paragraph(group.get("title", "Etap"), styles["Heading1"]))
        for item in group.get("entries", []):
            date_label = item.get("date", "")
            kind_label = "Problem" if item.get("kind") == "problem" else "Postęp"
            story.append(
                Paragraph(f"{date_label} · {kind_label}", styles["Meta"])
            )
            story.append(Paragraph(item.get("text", ""), styles["BodyText"]))
            story.append(Spacer(1, 3 * mm))
        story.append(Spacer(1, 3 * mm))

    story.append(PageBreak())
    story.append(Paragraph("Dostęp do raportu online", styles["Heading1"]))
    qr_buffer = io.BytesIO()
    qrcode.make(share_url).save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    story.append(Image(qr_buffer, width=45 * mm, height=45 * mm))
    story.append(Paragraph(share_url, styles["Footer"]))
    story.append(Spacer(1, 7 * mm))
    story.append(
        Paragraph(
            "Raport wygenerowany w aplikacji Pan Majster.",
            styles["Footer"],
        )
    )
    document.build(story)
    return buffer.getvalue()
