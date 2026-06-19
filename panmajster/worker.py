from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import models
from .config import get_settings
from .db import SessionLocal
from .mailer import send_email
from .reporting import generate_report_content, transcribe_asset


logger = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(timezone.utc)


def recover_stale_jobs() -> None:
    with SessionLocal() as db:
        threshold = now() - timedelta(minutes=15)
        jobs = db.scalars(
            select(models.Job).where(
                models.Job.status == "running",
                models.Job.started_at < threshold,
            )
        ).all()
        for job in jobs:
            job.status = "queued"
            job.run_after = now()
            job.last_error = "Zadanie wznowiono po przerwanym procesie."
        db.commit()


def process_next_job() -> bool:
    with SessionLocal() as db:
        job = db.scalar(
            select(models.Job)
            .where(models.Job.status == "queued", models.Job.run_after <= now())
            .order_by(models.Job.created_at.asc())
            .limit(1)
        )
        if not job:
            return False
        job.status = "running"
        job.started_at = now()
        job.attempts += 1
        db.commit()

        try:
            if job.job_type == "transcribe":
                if not get_settings().enable_server_transcription:
                    job.status = "done"
                    job.finished_at = now()
                    job.last_error = "Server transcription disabled by ENABLE_SERVER_TRANSCRIPTION."
                    db.commit()
                    return True
                asset = db.get(models.MediaAsset, job.payload["asset_id"])
                entry = db.get(models.Entry, job.payload["entry_id"])
                if not asset or not entry:
                    raise ValueError("Brak pliku lub wpisu do transkrypcji")
                text = transcribe_asset(asset)
                if text:
                    if asset.purpose == "voice_description" and not entry.body:
                        entry.body = text
                    elif asset.purpose == "voice_note":
                        if text not in entry.transcript:
                            entry.transcript = "\n\n".join(
                                part for part in [entry.transcript, text] if part
                            )
                    elif not entry.transcript:
                        entry.transcript = text
            elif job.job_type == "generate_report":
                report = db.get(models.Report, job.payload["report_id"])
                if not report:
                    raise ValueError("Brak raportu")
                report.content = generate_report_content(db, report)
                report.status = "draft"
            elif job.job_type == "send_email":
                send_email(
                    job.payload["to"],
                    job.payload["subject"],
                    job.payload["text"],
                )
            else:
                raise ValueError(f"Nieznany typ zadania: {job.job_type}")

            job.status = "done"
            job.finished_at = now()
            job.last_error = ""
            db.commit()
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            job.last_error = str(exc)[:2000]
            if job.attempts < 3:
                job.status = "queued"
                job.run_after = now() + timedelta(minutes=job.attempts)
            else:
                job.status = "failed"
                job.finished_at = now()
                if job.job_type == "generate_report":
                    report = db.get(models.Report, job.payload.get("report_id"))
                    if report:
                        report.status = "failed"
            db.commit()
        return True


async def worker_loop() -> None:
    settings = get_settings()
    recover_stale_jobs()
    while True:
        try:
            processed = await asyncio.to_thread(process_next_job)
        except Exception:
            logger.exception("Worker loop failed")
            processed = False
        await asyncio.sleep(0.2 if processed else settings.worker_poll_seconds)
