from __future__ import annotations

import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

import qrcode
from openai import OpenAI
from PIL import Image as PILImage, UnidentifiedImageError
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import models, serializers
from .config import get_settings
from .storage import storage

PDF_MAX_IMAGE_SOURCE_BYTES = 10 * 1024 * 1024
PDF_MAX_IMAGE_PIXELS = 16_000_000
PDF_THUMBNAIL_MAX_SIZE = (900, 700)
PDF_IMAGE_JPEG_QUALITY = 72
PDF_MAX_IMAGES_PER_ENTRY = 4
PDF_MAX_IMAGES_PER_REPORT = 24
PM_NAVY = colors.HexColor("#062557")
PM_BLUE = colors.HexColor("#0b376d")
PM_ORANGE = colors.HexColor("#ff6b00")
PM_BORDER = colors.HexColor("#dbe4ef")
PM_SOFT = colors.HexColor("#f6f8fb")
PM_ORANGE_SOFT = colors.HexColor("#fff5ec")
PM_MUTED = colors.HexColor("#607089")

PILImage.MAX_IMAGE_PIXELS = PDF_MAX_IMAGE_PIXELS


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


def transcribe_upload(filename: str, content_type: str, content: bytes) -> str:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("Transkrypcja głosu nie jest jeszcze skonfigurowana")
    client = OpenAI(api_key=settings.openai_api_key)
    result = client.audio.transcriptions.create(
        model=settings.openai_transcription_model,
        file=(filename or "nagranie.webm", content, content_type or "audio/webm"),
        language="pl",
    )
    return result.text.strip()


def _font_name() -> str:
    if "PanMajsterFont" in pdfmetrics.getRegisteredFontNames():
        return "PanMajsterFont"
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


def _logo_flowable(width: float = 48 * mm):
    candidates = [
        Path(__file__).resolve().parent.parent / "static" / "brand" / "logo.png",
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "public"
        / "brand"
        / "logo.png",
    ]
    for candidate in candidates:
        if candidate.is_file():
            with PILImage.open(candidate) as source:
                ratio = source.height / source.width
            return Image(str(candidate), width=width, height=width * ratio)
    return None


@dataclass
class PdfMediaBudget:
    remaining_images: int = PDF_MAX_IMAGES_PER_REPORT


def _photo_flowable(asset: models.MediaAsset) -> tuple[Image | None, str | None]:
    if asset.size_bytes > PDF_MAX_IMAGE_SOURCE_BYTES:
        return None, "too_large"
    try:
        content = storage.read_bytes(asset.storage_key)
        if len(content) > PDF_MAX_IMAGE_SOURCE_BYTES:
            return None, "too_large"
        with PILImage.open(io.BytesIO(content)) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > PDF_MAX_IMAGE_PIXELS:
                return None, "too_large"
            source.load()
            source.thumbnail(PDF_THUMBNAIL_MAX_SIZE, PILImage.Resampling.LANCZOS)
            if source.mode not in ("RGB", "L"):
                thumbnail = source.convert("RGB")
            else:
                thumbnail = source.copy()
        output = io.BytesIO()
        thumbnail.save(output, format="JPEG", quality=PDF_IMAGE_JPEG_QUALITY, optimize=True)
        output.seek(0)
        width, height = thumbnail.size
        max_width = 52 * mm
        max_height = 40 * mm
        ratio = min(max_width / width, max_height / height)
        return Image(
            output,
            width=max(1, width * ratio),
            height=max(1, height * ratio),
        ), None
    except (UnidentifiedImageError, PILImage.DecompressionBombError, OSError):
        return None, "invalid"
    except Exception:
        return None, "read_error"


STATUS_LABELS = {
    "assigned": "Zlecone",
    "in_progress": "W realizacji",
    "completed": "Zakończono",
}


def _format_date(value) -> str:
    if not value:
        return "Nie podano"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    return value.strftime("%d.%m.%Y")


def _format_datetime(value: datetime | None) -> str:
    if not value:
        return "Nie podano"
    return value.strftime("%d.%m.%Y %H:%M")


def _format_amount(project: models.Project) -> str:
    if project.contract_amount is None:
        return "Nie podano"
    return f"{project.contract_amount} {project.contract_currency or 'PLN'}"


def _entry_title(item: models.Entry) -> str:
    return "Problem / uwaga" if item.kind == "problem" else "Wpis postępu"


def _entry_text(item: models.Entry) -> str:
    parts = []
    if item.body:
        parts.append(item.body.strip())
    if item.transcript:
        parts.append(f"Transkrypcja audio: {item.transcript.strip()}")
    if not parts and item.media:
        parts.append("Dodano materiały bez opisu tekstowego.")
    return "\n".join(parts) if parts else "Brak opisu."


def _entry_author(item: models.Entry) -> str:
    if item.author:
        return item.author.name or item.author.email or "Użytkownik"
    return item.guest_label or "Link wykonawcy"


def _entry_stage(item: models.Entry) -> str:
    return item.stage.title if item.stage else "Bez etapu"


def _project_worker_label(db: Session, project: models.Project) -> str:
    return serializers.public_contractor_name(db, project)


def _project_report_entries(
    db: Session,
    project_id: str,
    report_type: str,
    report_date: date | None,
) -> list[models.Entry]:
    entries = list(
        db.scalars(
            select(models.Entry)
            .options(
                selectinload(models.Entry.stage),
                selectinload(models.Entry.media),
                selectinload(models.Entry.author),
            )
            .where(models.Entry.project_id == project_id)
            .order_by(models.Entry.occurred_at.asc(), models.Entry.created_at.asc())
        ).all()
    )
    if report_type != "daily":
        return entries
    selected = report_date or datetime.now(timezone.utc).date()
    return [item for item in entries if item.occurred_at.date() == selected]


def _report_styles(font: str):
    styles = getSampleStyleSheet()
    for style_name in ("Title", "Heading1", "Heading2", "BodyText", "Normal"):
        styles[style_name].fontName = font
    styles["Title"].textColor = PM_NAVY
    styles["Title"].fontSize = 25
    styles["Title"].leading = 29
    styles["Title"].spaceAfter = 1 * mm
    styles["Heading1"].textColor = PM_NAVY
    styles["Heading1"].fontSize = 14
    styles["Heading1"].leading = 17
    styles["Heading1"].spaceBefore = 2 * mm
    styles["Heading1"].spaceAfter = 2 * mm
    styles["Heading2"].textColor = PM_BLUE
    styles["Heading2"].fontSize = 12
    styles["Heading2"].leading = 15
    styles["BodyText"].fontSize = 9.5
    styles["BodyText"].leading = 13
    styles.add(
        ParagraphStyle(
            "SmallMuted",
            parent=styles["BodyText"],
            fontSize=8,
            leading=11,
            textColor=PM_MUTED,
        )
    )
    styles.add(
        ParagraphStyle(
            "Badge",
            parent=styles["BodyText"],
            fontSize=8,
            leading=10,
            textColor=PM_NAVY,
        )
    )
    styles.add(
        ParagraphStyle(
            "Brand",
            parent=styles["Heading1"],
            fontSize=16,
            leading=18,
            textColor=PM_NAVY,
        )
    )
    styles.add(
        ParagraphStyle(
            "OrangeLabel",
            parent=styles["SmallMuted"],
            textColor=PM_ORANGE,
            fontSize=8.5,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            "FooterNote",
            parent=styles["SmallMuted"],
            alignment=TA_CENTER,
            textColor=colors.white,
            leading=12,
        )
    )
    return styles


def _card_table(rows: list[list], font: str, col_widths: list[float]):
    table = Table(rows, colWidths=col_widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.75, PM_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, PM_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _footer_table(text: str, styles, font: str):
    table = Table(
        [[Paragraph(escape(text), styles["FooterNote"])]],
        colWidths=[158 * mm],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("BACKGROUND", (0, 0), (-1, -1), PM_NAVY),
                ("BOX", (0, 0), (-1, -1), 0.75, PM_NAVY),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _photo_grid(
    assets: list[models.MediaAsset],
    media_budget: PdfMediaBudget,
) -> tuple[Table | None, Counter[str]]:
    photos = []
    skipped: Counter[str] = Counter()
    for asset in assets:
        if asset.kind != "image":
            continue
        if len(photos) >= PDF_MAX_IMAGES_PER_ENTRY or media_budget.remaining_images <= 0:
            skipped["limit"] += 1
            continue
        photo, reason = _photo_flowable(asset)
        if photo:
            photos.append(photo)
            media_budget.remaining_images -= 1
        elif reason:
            skipped[reason] += 1
    if not photos:
        return None, skipped
    rows = []
    for index in range(0, len(photos), 3):
        row = photos[index : index + 3]
        while len(row) < 3:
            row.append("")
        rows.append(row)
    table = Table(rows, colWidths=[52 * mm, 52 * mm, 52 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
            ]
        )
    )
    return table, skipped


def _skipped_media_notes(skipped: Counter[str]) -> list[str]:
    notes = []
    large = skipped.get("too_large", 0)
    invalid = skipped.get("invalid", 0)
    read_error = skipped.get("read_error", 0)
    limit = skipped.get("limit", 0)
    if large:
        notes.append(f"Pominięto {large} zdjęć - zbyt duży plik lub rozdzielczość.")
    if invalid:
        notes.append(f"Pominięto {invalid} zdjęć - uszkodzony lub nierozpoznany plik.")
    if read_error:
        notes.append(f"Pominięto {read_error} zdjęć - nie udało się odczytać pliku.")
    if limit:
        notes.append(f"Pominięto {limit} zdjęć z powodu limitu raportu.")
    return notes


def _entry_block(
    item: models.Entry,
    styles,
    font: str,
    media_budget: PdfMediaBudget,
):
    status = ""
    if item.kind == "problem":
        status = "Rozwiązany" if item.problem_status == "resolved" else "Otwarty"
    meta_rows = [
        [
            Paragraph(escape(_entry_title(item)), styles["Heading2"]),
            Paragraph(escape(_format_datetime(item.occurred_at)), styles["SmallMuted"]),
            Paragraph(escape(_entry_stage(item)), styles["SmallMuted"]),
            Paragraph(escape(_entry_author(item)), styles["SmallMuted"]),
        ]
    ]
    if status:
        meta_rows.append(
            [
                Paragraph("Status problemu", styles["SmallMuted"]),
                Paragraph(escape(status), styles["SmallMuted"]),
                "",
                "",
            ]
        )
    block = [
        _card_table(meta_rows, font, [43 * mm, 38 * mm, 38 * mm, 38 * mm]),
        Spacer(1, 2 * mm),
        Paragraph(escape(_entry_text(item)).replace("\n", "<br/>"), styles["BodyText"]),
    ]
    audio_assets = [asset for asset in item.media if asset.kind == "audio"]
    if audio_assets:
        block.append(Spacer(1, 1.5 * mm))
        block.append(
            Paragraph(
                escape(
                    "Nagrania audio: "
                    + ", ".join(asset.original_name for asset in audio_assets)
                ),
                styles["SmallMuted"],
            )
        )
    photos, skipped = _photo_grid(item.media, media_budget)
    if photos:
        block.extend([Spacer(1, 2 * mm), photos])
    for note in _skipped_media_notes(skipped):
        block.append(Paragraph(escape(note), styles["SmallMuted"]))
    block.append(Spacer(1, 5 * mm))
    return KeepTogether(block)


def render_project_report_pdf(
    db: Session,
    access,
    report_type: str,
    report_date: date | None = None,
) -> tuple[str, bytes]:
    project = access.project
    font = _font_name()
    styles = _report_styles(font)
    is_daily = report_type == "daily"
    selected_date = report_date or datetime.now(timezone.utc).date()
    entries = _project_report_entries(db, project.id, report_type, selected_date)
    media_budget = PdfMediaBudget()
    problems = [item for item in entries if item.kind == "problem"]
    image_count = sum(1 for item in entries for asset in item.media if asset.kind == "image")
    title = (
        "Raport dzienny / raport postępu"
        if is_daily
        else "Raport końcowy zlecenia"
    )
    filename = (
        f"raport-dzienny-{selected_date.isoformat()}-{project.id}.pdf"
        if is_daily
        else f"raport-koncowy-{project.id}.pdf"
    )
    disclaimer = (
        "Raport dzienny pokazuje postęp prac i nie jest rozliczeniem końcowym "
        "ani dokumentem księgowym."
        if is_daily
        else "Raport nie jest fakturą ani dokumentem księgowym. Kwoty mają "
        "charakter informacyjny i wynikają z danych wpisanych w aplikacji."
    )

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=title,
    )
    status_label = STATUS_LABELS.get(project.status, project.status or "Brak statusu")
    story = []
    logo = _logo_flowable(width=38 * mm)
    generated_date = _format_date(selected_date if is_daily else datetime.now())
    header_cells = [
        logo or Paragraph("PAN MAJSTER", styles["Brand"]),
        Paragraph(
            "<b>" + escape(title) + "</b><br/>"
            + "Dokument wygenerowany w aplikacji Pan Majster",
            styles["BodyText"],
        ),
        Paragraph(
            "<b>Data</b><br/>" + escape(generated_date) + "<br/><br/>"
            + "<b>Status</b><br/>" + escape(status_label),
            styles["BodyText"],
        ),
    ]
    header = Table([header_cells], colWidths=[48 * mm, 78 * mm, 32 * mm], hAlign="LEFT")
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, -1), 1.2, PM_NAVY),
            ]
        )
    )
    story.extend(
        [
            header,
            Spacer(1, 4 * mm),
            Paragraph(escape(project.name), styles["Title"]),
            Paragraph("Podsumowanie postępu i historii prac", styles["OrangeLabel"]),
            Spacer(1, 3 * mm),
        ]
    )
    story.extend(
        [
            _card_table(
                [
                    [
                        Paragraph("<b>Status</b><br/>" + escape(status_label), styles["BodyText"]),
                        Paragraph(
                            "<b>Wykonawca</b><br/>"
                            + escape(_project_worker_label(db, project)),
                            styles["BodyText"],
                        ),
                        Paragraph(
                            "<b>Wygenerował</b><br/>" + escape(access.label),
                            styles["BodyText"],
                        ),
                    ],
                    [
                        Paragraph(
                            "<b>Klient / inwestor</b><br/>"
                            + escape(project.client_name or "Nie podano"),
                            styles["BodyText"],
                        ),
                        Paragraph(
                            "<b>Adres</b><br/>"
                            + escape(project.address or "Nie podano"),
                            styles["BodyText"],
                        ),
                        Paragraph(
                            "<b>Data raportu</b><br/>"
                            + escape(generated_date),
                            styles["BodyText"],
                        ),
                    ],
                ],
                font,
                [52 * mm, 52 * mm, 52 * mm],
            ),
            Spacer(1, 5 * mm),
        ]
    )

    if not is_daily:
        story.extend(
            [
                Paragraph("Kwoty / podsumowanie", styles["Heading1"]),
                _card_table(
                    [
                        [
                            Paragraph(
                                "<b>Start prac</b><br/>"
                                + escape(_format_date(project.planned_start_date or project.started_at)),
                                styles["BodyText"],
                            ),
                            Paragraph(
                                "<b>Zakończenie prac</b><br/>"
                                + escape(
                                    _format_date(project.finished_at)
                                    if project.finished_at
                                    else "Nie zakończono"
                                ),
                                styles["BodyText"],
                            ),
                            Paragraph(
                                "<b>Kwota umowna</b><br/>"
                                + escape(_format_amount(project)),
                                styles["BodyText"],
                            ),
                        ],
                        [
                            Paragraph(
                                "<b>Liczba wpisów</b><br/>" + str(len(entries)),
                                styles["BodyText"],
                            ),
                            Paragraph(
                                "<b>Zdjęcia</b><br/>" + str(image_count),
                                styles["BodyText"],
                            ),
                            Paragraph(
                                "<b>Problemy / uwagi</b><br/>" + str(len(problems)),
                                styles["BodyText"],
                            ),
                        ],
                    ],
                    font,
                    [52 * mm, 52 * mm, 52 * mm],
                ),
                Spacer(1, 6 * mm),
            ]
        )

    story.extend([Paragraph("Wpisy / Historia prac", styles["Heading1"]), Spacer(1, 2 * mm)])
    if not entries:
        empty = (
            "Brak wpisów postępu dla wybranej daty."
            if is_daily
            else "Brak wpisów postępu w tym zleceniu."
        )
        story.append(Paragraph(escape(empty), styles["BodyText"]))
        story.append(Spacer(1, 5 * mm))
    else:
        for entry in entries:
            story.append(_entry_block(entry, styles, font, media_budget))

    story.extend([Paragraph("Problemy i uwagi", styles["Heading1"]), Spacer(1, 2 * mm)])
    if not problems:
        story.extend(
            [
                Paragraph("Brak problemów i uwag w zakresie raportu.", styles["BodyText"]),
                Spacer(1, 5 * mm),
            ]
        )
    else:
        for entry in problems:
            story.append(_entry_block(entry, styles, font, media_budget))

    story.extend(
        [
            Spacer(1, 5 * mm),
            _footer_table(disclaimer, styles, font),
        ]
    )
    document.build(story)
    return filename, buffer.getvalue()


def render_pdf(db: Session, report: models.Report, share_url: str) -> bytes:
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
    styles["Title"].textColor = PM_NAVY
    styles["Heading1"].textColor = PM_NAVY
    styles["Heading2"].textColor = PM_BLUE
    styles.add(
        ParagraphStyle(
            "Meta",
            parent=styles["BodyText"],
            textColor=PM_MUTED,
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
    styles.add(
        ParagraphStyle(
            "EntryTitle",
            parent=styles["Heading2"],
            fontSize=12,
            leading=15,
            textColor=PM_NAVY,
            spaceAfter=2 * mm,
        )
    )

    story = []
    logo = _logo_flowable()
    if logo:
        story.extend([logo, Spacer(1, 4 * mm)])
    story.extend(
        [
            Paragraph(escape(report.title), styles["Title"]),
            Paragraph(escape(content.get("project_name", "")), styles["Heading2"]),
            Spacer(1, 3 * mm),
        ]
    )
    period_from = (
        report.period_from.strftime("%d.%m.%Y") if report.period_from else "początek"
    )
    period_to = (
        report.period_to.strftime("%d.%m.%Y") if report.period_to else "dzisiaj"
    )
    meta_rows = [
        ["Klient / inwestor", content.get("client_name") or "—"],
        ["Adres realizacji", content.get("address") or "—"],
        ["Zakres raportu", f"{period_from} – {period_to}"],
        ["Wygenerowano", datetime.now().strftime("%d.%m.%Y %H:%M")],
    ]
    meta = Table(meta_rows, colWidths=[35 * mm, 120 * mm])
    meta.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), PM_NAVY),
                ("BACKGROUND", (0, 0), (-1, -1), PM_SOFT),
                ("BOX", (0, 0), (-1, -1), 0.75, PM_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, PM_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend(
        [
            meta,
            Spacer(1, 6 * mm),
            Paragraph("Podsumowanie", styles["Heading1"]),
            Paragraph(escape(content.get("summary", "")), styles["BodyText"]),
            Spacer(1, 7 * mm),
        ]
    )

    media_budget = PdfMediaBudget()
    for group in content.get("stages", []):
        story.append(
            Paragraph(escape(group.get("title", "Etap")), styles["Heading1"])
        )
        for item in group.get("entries", []):
            date_label = item.get("date", "")
            kind_label = "Problem" if item.get("kind") == "problem" else "Postęp"
            block = [
                Paragraph(
                    escape(f"{date_label} · {kind_label}"),
                    styles["EntryTitle"],
                ),
                Paragraph(escape(item.get("text", "")), styles["BodyText"]),
                Spacer(1, 2 * mm),
            ]
            assets = []
            for asset_id in item.get("media_ids", []):
                asset = db.get(models.MediaAsset, asset_id)
                if asset and asset.kind == "image":
                    assets.append(asset)
            if assets:
                photo_table, skipped = _photo_grid(assets, media_budget)
                if photo_table:
                    block.append(photo_table)
                for note in _skipped_media_notes(skipped):
                    block.append(Paragraph(escape(note), styles["Meta"]))
            block.append(Spacer(1, 4 * mm))
            story.append(KeepTogether(block))
        story.append(Spacer(1, 3 * mm))

    story.append(PageBreak())
    story.append(Paragraph("Bieżący podgląd zlecenia", styles["Heading1"]))
    story.append(
        Paragraph(
            "Ten kod prowadzi do stałego linku klienta. Nowe zdjęcia, opisy "
            "i kolejne opublikowane raporty pojawią się tam automatycznie.",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 4 * mm))
    qr_buffer = io.BytesIO()
    qrcode.make(share_url).save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    story.append(Image(qr_buffer, width=45 * mm, height=45 * mm))
    story.append(Paragraph(share_url, styles["Footer"]))
    story.append(Spacer(1, 7 * mm))
    footer_logo = _logo_flowable(width=36 * mm)
    if footer_logo:
        story.extend([footer_logo, Spacer(1, 3 * mm)])
    story.append(
        Paragraph(
            "Pan Majster · Zdjęcie. Głos. Raport.",
            styles["Footer"],
        )
    )
    document.build(story)
    return buffer.getvalue()
