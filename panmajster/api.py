from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import re
import time
from threading import Lock
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from . import models, serializers
from .access import (
    ProjectAccess,
    active_date,
    can_create_project,
    can_manage_people,
    can_manage_workspace,
    can_manage_workers,
    current_user,
    find_pending_invitations,
    get_project_access,
    is_company_owner,
    is_company_worker,
    is_independent_contractor,
    is_investor,
    now,
    project_role,
    user_projects_query,
)
from .config import get_settings
from .db import get_db
from .demo_seed import DEMO_EMAILS, DEMO_PASSWORD, seed_demo_data
from .mailer import send_email, send_otp
from .reporting import render_pdf, render_project_report_pdf, transcribe_upload
from .security import hash_secret, normalize_email, otp_code, random_token, verify_secret
from .service_taxonomy import SERVICE_TAG_SLUGS
from .storage import storage
from .templates import STAGE_TEMPLATES


router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
settings = get_settings()
SLUG_RE = re.compile(r"[^a-z0-9]+")
PROJECT_STATUS_ASSIGNED = "assigned"
PROJECT_STATUS_IN_PROGRESS = "in_progress"
PROJECT_STATUS_COMPLETED = "completed"
DEFAULT_ENTRY_STAGE_TITLE = "W trakcie realizacji"
DEFAULT_CONTRACT_CURRENCY = "PLN"
PROJECT_CONTRACT_FIELDS = {
    "planned_start_date",
    "planned_end_date",
    "schedule_uncertainty_days",
    "contract_amount",
    "contract_currency",
}
DEMO_ADMIN_TOKEN_PREFIX = "demo-admin:"

_report_generation_locks: dict[str, Lock] = {}
_report_generation_locks_guard = Lock()


def acquire_report_generation_lock(project_id: str) -> Lock | None:
    with _report_generation_locks_guard:
        lock = _report_generation_locks.setdefault(project_id, Lock())
    if not lock.acquire(blocking=False):
        return None
    return lock


def release_report_generation_lock(project_id: str, lock: Lock) -> None:
    lock.release()
    with _report_generation_locks_guard:
        current = _report_generation_locks.get(project_id)
        if current is lock and not lock.locked():
            _report_generation_locks.pop(project_id, None)


def stored_file_response(
    storage_key: str, media_type: str, filename: str | None = None
) -> Response:
    try:
        content = storage.read_bytes(storage_key)
    except FileNotFoundError:
        raise HTTPException(404, "Plik nie istnieje w magazynie")
    except Exception as exc:
        logger.exception("Failed to read stored file %s", storage_key)
        raise HTTPException(503, "Nie udało się otworzyć pliku") from exc
    headers = {}
    if filename:
        encoded_name = quote(filename.replace('"', ""))
        headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{encoded_name}"
        )
    return Response(content=content, media_type=media_type, headers=headers)


def report_pdf_generation_error(exc: Exception) -> HTTPException:
    logger.exception("PDF report generation failed")
    return HTTPException(503, "Nie udało się wygenerować raportu PDF")


def normalize_public_profile_slug(value: str) -> str:
    slug = SLUG_RE.sub("-", value.strip().lower()).strip("-")[:140].strip("-")
    if not slug:
        raise HTTPException(422, "Podaj poprawny adres wizytówki")
    return slug


def unique_public_profile_slug(
    db: Session, base_slug: str, ignore_profile_id: str | None = None
) -> str:
    base = normalize_public_profile_slug(base_slug)
    candidate = base
    suffix = 2
    while True:
        query = select(models.PublicProfile).where(models.PublicProfile.slug == candidate)
        if ignore_profile_id:
            query = query.where(models.PublicProfile.id != ignore_profile_id)
        if not db.scalar(query):
            return candidate
        suffix_text = f"-{suffix}"
        candidate = normalize_public_profile_slug(
            f"{base[: 140 - len(suffix_text)].strip('-')}{suffix_text}"
        )
        suffix += 1


def validate_public_profile_specializations(values: list[str] | None) -> list[str]:
    if not values:
        return []
    cleaned: list[str] = []
    for value in values:
        slug = value.strip().lower()
        if slug not in SERVICE_TAG_SLUGS:
            raise HTTPException(422, f"Nieznana specjalizacja: {value}")
        if slug not in cleaned:
            cleaned.append(slug)
    return cleaned[:12]


def validate_contact_email(value: str) -> str:
    email = value.strip()
    if not email:
        return ""
    try:
        return validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise HTTPException(422, "Podaj poprawny e-mail kontaktowy") from exc


def clean_realization_work_scope(values: list[str] | None) -> list[str]:
    if not values:
        return []
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in cleaned:
            cleaned.append(item[:80])
    return cleaned[:10]


def clean_realization_urls(values: list[str] | None) -> list[str]:
    if not values:
        return []
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in cleaned:
            cleaned.append(item[:2000])
    return cleaned[:10]


def clean_realization_currency(value: str | None) -> str:
    currency = (value or "PLN").strip().upper()
    return currency[:3] or "PLN"


def public_profile_realization_payload(
    item: models.PublicProfileRealization, *, public: bool = False
) -> dict:
    visible_amount = item.amount is not None and (not public or item.show_amount)
    return {
        "id": item.id,
        "owner_type": item.owner_type,
        "owner_id": item.owner_id,
        "project_id": item.project_id,
        "title": item.title,
        "public_description": item.public_description,
        "location_public": item.location_public,
        "work_scope": item.work_scope or [],
        "completion_date": item.completion_date.isoformat() if item.completion_date else None,
        "amount": str(item.amount) if visible_amount else None,
        "currency": item.currency,
        "show_amount": item.show_amount,
        "status": item.status,
        "cover_image_url": item.cover_image_url,
        "gallery_image_urls": item.gallery_image_urls or [],
        "sort_order": item.sort_order,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def public_profile_realizations_for(
    db: Session, owner_type: str, owner_id: str, *, public_only: bool = False
) -> list[models.PublicProfileRealization]:
    query = (
        select(models.PublicProfileRealization)
        .where(
            models.PublicProfileRealization.owner_type == owner_type,
            models.PublicProfileRealization.owner_id == owner_id,
        )
        .order_by(
            models.PublicProfileRealization.sort_order.asc(),
            models.PublicProfileRealization.created_at.desc(),
        )
    )
    if public_only:
        query = query.where(models.PublicProfileRealization.status == "published")
    return list(db.scalars(query).all())


def public_profile_payload(
    profile: models.PublicProfile,
    realizations: list[models.PublicProfileRealization] | None = None,
    *,
    public: bool = False,
) -> dict:
    return {
        "id": profile.id,
        "owner_type": profile.owner_type,
        "owner_id": profile.owner_id,
        "display_name": profile.display_name,
        "public_description": profile.public_description,
        "contact_phone": profile.contact_phone,
        "contact_email": profile.contact_email,
        "specializations": profile.specializations or [],
        "service_area": profile.service_area,
        "is_public": profile.is_public,
        "slug": profile.slug,
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
        "realizations": [
            public_profile_realization_payload(item, public=public)
            for item in (realizations or [])
        ],
    }


def job_posting_interest_payload(
    item: models.JobPostingInterest,
    *,
    profile: models.PublicProfile | None = None,
    include_contact: bool = False,
) -> dict:
    data = {
        "id": item.id,
        "job_posting_id": item.job_posting_id,
        "contractor_owner_type": item.contractor_owner_type,
        "contractor_owner_id": item.contractor_owner_id,
        "public_profile_id": item.public_profile_id,
        "message": item.message,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    if include_contact and profile:
        data["contractor"] = {
            "display_name": profile.display_name,
            "owner_type": profile.owner_type,
            "specializations": profile.specializations or [],
            "service_area": profile.service_area,
            "contact_phone": profile.contact_phone,
            "contact_email": profile.contact_email,
            "slug": profile.slug,
            "is_public": profile.is_public,
        }
    return data


def money_payload(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def job_posting_offer_payload(
    item: models.JobPostingOffer,
    *,
    profile: models.PublicProfile | None = None,
    include_contact: bool = False,
    job_posting: models.JobPosting | None = None,
) -> dict:
    data = {
        "id": item.id,
        "job_posting_id": item.job_posting_id,
        "interest_id": item.interest_id,
        "contractor_owner_type": item.contractor_owner_type,
        "contractor_owner_id": item.contractor_owner_id,
        "public_profile_id": item.public_profile_id,
        "title": item.title,
        "scope_summary": item.scope_summary,
        "assumptions": item.assumptions,
        "estimated_price": money_payload(item.estimated_price),
        "price_note": item.price_note,
        "planned_start": item.planned_start,
        "planned_end": item.planned_end,
        "status": item.status,
        "sent_at": item.sent_at.isoformat() if item.sent_at else None,
        "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        "rejected_at": item.rejected_at.isoformat() if item.rejected_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    if include_contact and profile:
        data["contractor"] = {
            "display_name": profile.display_name,
            "owner_type": profile.owner_type,
            "specializations": profile.specializations or [],
            "service_area": profile.service_area,
            "contact_phone": profile.contact_phone,
            "contact_email": profile.contact_email,
            "slug": profile.slug,
            "is_public": profile.is_public,
        }
    if job_posting:
        data["job_posting"] = job_posting_payload(job_posting, public=True)
    return data


def job_posting_payload(
    item: models.JobPosting,
    *,
    public: bool = False,
    my_interest: models.JobPostingInterest | None = None,
    my_offer: models.JobPostingOffer | None = None,
    interests: list[models.JobPostingInterest] | None = None,
    offers: list[models.JobPostingOffer] | None = None,
    db: Session | None = None,
) -> dict:
    data = {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "location": item.location,
        "budget_label": item.budget_label,
        "deadline": item.deadline,
        "specializations": item.specializations or [],
        "current_state_description": item.current_state_description,
        "target_contractor_type": item.target_contractor_type,
        "status": item.status,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    if not public:
        data["investor_id"] = item.investor_id
    if my_interest:
        data["my_interest"] = job_posting_interest_payload(my_interest)
    if my_offer:
        data["my_offer"] = job_posting_offer_payload(my_offer)
    if interests is not None:
        data["interest_count"] = len(interests)
        data["interests"] = [
            job_posting_interest_payload(
                interest,
                profile=db.get(models.PublicProfile, interest.public_profile_id) if db else None,
                include_contact=True,
            )
            for interest in interests
        ]
    if offers is not None:
        data["offer_count"] = len(offers)
        data["offers"] = [
            job_posting_offer_payload(
                offer,
                profile=db.get(models.PublicProfile, offer.public_profile_id) if db else None,
                include_contact=True,
            )
            for offer in offers
        ]
    return data


def apply_job_posting_changes(
    item: models.JobPosting,
    payload: JobPostingCreate | JobPostingUpdate,
    *,
    partial: bool = False,
) -> None:
    changes = payload.model_dump(exclude_unset=partial)
    for key in [
        "title",
        "description",
        "location",
        "budget_label",
        "deadline",
        "current_state_description",
    ]:
        if key in changes:
            setattr(item, key, (changes[key] or "").strip())
    if "specializations" in changes:
        item.specializations = validate_public_profile_specializations(
            changes["specializations"]
        )
    if "target_contractor_type" in changes:
        item.target_contractor_type = changes["target_contractor_type"] or "any"
    if "status" in changes:
        status = changes["status"] or "draft"
        if status == "published":
            if not item.title.strip() or not item.location.strip():
                raise HTTPException(
                    422,
                    "Do publikacji ogloszenia potrzebny jest tytul i lokalizacja",
                )
            item.published_at = item.published_at or now()
        else:
            item.published_at = None
        item.status = status


def public_profile_owner_defaults(
    db: Session, user: models.User, owner_type: str
) -> tuple[str, str, str, str]:
    if owner_type == "independent_contractor":
        if user.profile_type != "independent_contractor":
            raise HTTPException(403, "Ten typ konta nie ma wizytówki wykonawcy")
        display_name = (
            user.public_profile_name
            or user.name
            or user.email.split("@", maxsplit=1)[0]
            or "Samodzielny majster"
        )
        return user.id, display_name, user.phone or "", ""

    if owner_type == "company":
        if user.profile_type != "company_owner":
            raise HTTPException(403, "Ten typ konta nie ma wizytówki firmy")
        workspace = db.scalar(
            select(models.Workspace)
            .where(
                models.Workspace.owner_id == user.id,
                models.Workspace.kind == "company",
            )
            .order_by(models.Workspace.created_at)
        )
        if not workspace:
            raise HTTPException(404, "Nie znaleziono firmy dla tego konta")
        return (
            workspace.id,
            workspace.name or "Firma wykonawcza",
            workspace.phone or "",
            workspace.address or "",
        )

    raise HTTPException(422, "Nieobsługiwany typ właściciela profilu")


def contractor_interest_identity(db: Session, user: models.User) -> tuple[str, str]:
    if is_independent_contractor(user):
        return "independent_contractor", user.id
    if is_company_owner(user):
        owner_id, _, _, _ = public_profile_owner_defaults(db, user, "company")
        return "company", owner_id
    raise HTTPException(403, "Tylko wykonawcy moga zglaszac zainteresowanie")


def public_profile_for_owner(
    db: Session, owner_type: str, owner_id: str
) -> models.PublicProfile | None:
    return db.scalar(
        select(models.PublicProfile).where(
            models.PublicProfile.owner_type == owner_type,
            models.PublicProfile.owner_id == owner_id,
        )
    )


def job_interest_profile_context(db: Session, user: models.User) -> dict:
    owner_type, owner_id = contractor_interest_identity(db, user)
    profile = public_profile_for_owner(db, owner_type, owner_id)
    reason = ""
    if not profile or not profile.is_public:
        reason = "Wlacz publiczna wizytowke, zeby zglaszac zainteresowanie zleceniami."
    elif not ((profile.contact_phone or "").strip() or (profile.contact_email or "").strip()):
        reason = "Uzupelnij telefon lub e-mail w wizytowce, zeby inwestor mogl sie z Toba skontaktowac."
    return {
        "owner_type": owner_type,
        "owner_id": owner_id,
        "can_submit": not reason,
        "reason": reason,
        "public_profile": public_profile_payload(profile) if profile else None,
    }


def ready_public_profile_for_interest(
    db: Session, user: models.User
) -> tuple[str, str, models.PublicProfile]:
    owner_type, owner_id = contractor_interest_identity(db, user)
    profile = public_profile_for_owner(db, owner_type, owner_id)
    if not profile or not profile.is_public:
        raise HTTPException(
            422,
            "Wlacz publiczna wizytowke, zeby zglaszac zainteresowanie zleceniami.",
        )
    if not ((profile.contact_phone or "").strip() or (profile.contact_email or "").strip()):
        raise HTTPException(
            422,
            "Uzupelnij telefon lub e-mail w wizytowce, zeby inwestor mogl sie z Toba skontaktowac.",
        )
    return owner_type, owner_id, profile


def job_posting_interests_for(
    db: Session, posting_id: str
) -> list[models.JobPostingInterest]:
    return list(
        db.scalars(
            select(models.JobPostingInterest)
            .where(models.JobPostingInterest.job_posting_id == posting_id)
            .order_by(models.JobPostingInterest.created_at.desc())
        ).all()
    )


def job_posting_offers_for(
    db: Session,
    posting_id: str,
    *,
    investor_view: bool = False,
) -> list[models.JobPostingOffer]:
    query = select(models.JobPostingOffer).where(
        models.JobPostingOffer.job_posting_id == posting_id
    )
    if investor_view:
        query = query.where(models.JobPostingOffer.status != "draft")
    return list(
        db.scalars(
            query.order_by(
                models.JobPostingOffer.updated_at.desc(),
                models.JobPostingOffer.created_at.desc(),
            )
        ).all()
    )


def contractor_offer_for_posting(
    db: Session,
    posting_id: str,
    owner_type: str,
    owner_id: str,
) -> models.JobPostingOffer | None:
    return db.scalar(
        select(models.JobPostingOffer).where(
            models.JobPostingOffer.job_posting_id == posting_id,
            models.JobPostingOffer.contractor_owner_type == owner_type,
            models.JobPostingOffer.contractor_owner_id == owner_id,
        )
    )


def contractor_interest_for_offer(
    db: Session,
    posting_id: str,
    owner_type: str,
    owner_id: str,
) -> models.JobPostingInterest | None:
    return db.scalar(
        select(models.JobPostingInterest).where(
            models.JobPostingInterest.job_posting_id == posting_id,
            models.JobPostingInterest.contractor_owner_type == owner_type,
            models.JobPostingInterest.contractor_owner_id == owner_id,
        )
    )


def apply_job_posting_offer_changes(
    item: models.JobPostingOffer,
    payload: JobPostingOfferCreate | JobPostingOfferUpdate,
    *,
    partial: bool = False,
) -> None:
    changes = payload.model_dump(exclude_unset=partial)
    for key in [
        "title",
        "scope_summary",
        "assumptions",
        "price_note",
        "planned_start",
        "planned_end",
    ]:
        if key in changes:
            setattr(item, key, (changes[key] or "").strip())
    if "estimated_price" in changes:
        item.estimated_price = changes["estimated_price"]
    if "status" in changes:
        status = changes["status"] or "draft"
        if status == "sent":
            if not item.title.strip() or not item.scope_summary.strip():
                raise HTTPException(
                    422,
                    "Do wyslania oferty potrzebny jest tytul i zakres prac",
                )
            item.sent_at = item.sent_at or now()
        item.status = status


def ensure_estimate_share(db: Session, item: models.Estimate) -> str:
    if not item.share_token:
        token = random_token(30)
        while db.scalar(select(models.Estimate.id).where(models.Estimate.share_token == token)):
            token = random_token(30)
        item.share_token = token
    item.share_active = True
    item.shared_at = item.shared_at or now()
    return item.share_token


def estimate_share_url(item: models.Estimate) -> str | None:
    if not item.share_token:
        return None
    return f"/estimate/{item.share_token}"


def public_profile_for_estimate_owner(
    db: Session,
    item: models.Estimate,
) -> models.PublicProfile | None:
    return db.scalar(
        select(models.PublicProfile).where(
            models.PublicProfile.owner_type == item.owner_type,
            models.PublicProfile.owner_id == item.owner_id,
        )
    )


def estimate_public_owner_payload(db: Session, item: models.Estimate) -> dict:
    profile = public_profile_for_estimate_owner(db, item)
    public_profile = profile if profile and profile.is_public else None
    if item.owner_type == "company":
        workspace = db.get(models.Workspace, item.owner_id)
        display_name = (
            public_profile.display_name
            if public_profile and public_profile.display_name
            else (workspace.name if workspace else "") or "Firma wykonawcza"
        )
        return {
            "owner_type": "company",
            "display_name": display_name,
            "contact_phone": public_profile.contact_phone if public_profile else "",
            "contact_email": public_profile.contact_email if public_profile else "",
            "slug": public_profile.slug if public_profile else "",
            "profile_url": f"/public-profiles/{public_profile.slug}" if public_profile and public_profile.slug else "",
        }
    user = db.get(models.User, item.owner_id)
    display_name = ""
    if user:
        display_name = user.public_profile_name or user.name
    if public_profile and public_profile.display_name:
        display_name = public_profile.display_name
    return {
        "owner_type": "independent_contractor",
        "display_name": display_name or "Samodzielny majster",
        "contact_phone": public_profile.contact_phone if public_profile else "",
        "contact_email": public_profile.contact_email if public_profile else "",
        "slug": public_profile.slug if public_profile else "",
        "profile_url": f"/public-profiles/{public_profile.slug}" if public_profile and public_profile.slug else "",
    }


def public_estimate_payload(db: Session, item: models.Estimate) -> dict:
    return {
        "id": item.id,
        "number": item.id[:8].upper(),
        "owner": estimate_public_owner_payload(db, item),
        "recipient_name": item.recipient_name,
        "recipient_email": item.recipient_email,
        "recipient_phone": item.recipient_phone,
        "title": item.title,
        "scope_summary": item.scope_summary,
        "assumptions": item.assumptions,
        "estimated_price": money_payload(item.estimated_price),
        "price_note": item.price_note,
        "planned_start": item.planned_start,
        "planned_end": item.planned_end,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
        "sent_at": item.sent_at.isoformat() if item.sent_at else None,
        "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        "rejected_at": item.rejected_at.isoformat() if item.rejected_at else None,
        "shared_at": item.shared_at.isoformat() if item.shared_at else None,
    }


def estimate_payload(item: models.Estimate) -> dict:
    return {
        "id": item.id,
        "owner_type": item.owner_type,
        "owner_id": item.owner_id,
        "created_by_id": item.created_by_id,
        "approved_by_id": item.approved_by_id,
        "recipient_type": item.recipient_type,
        "recipient_name": item.recipient_name,
        "recipient_email": item.recipient_email,
        "recipient_phone": item.recipient_phone,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "project_id": item.project_id,
        "draft_origin": item.draft_origin,
        "draft_origin_label": item.draft_origin_label,
        "title": item.title,
        "scope_summary": item.scope_summary,
        "assumptions": item.assumptions,
        "estimated_price": money_payload(item.estimated_price),
        "price_note": item.price_note,
        "planned_start": item.planned_start,
        "planned_end": item.planned_end,
        "status": item.status,
        "share_url": estimate_share_url(item),
        "share_active": item.share_active,
        "shared_at": item.shared_at.isoformat() if item.shared_at else None,
        "sent_at": item.sent_at.isoformat() if item.sent_at else None,
        "approved_at": item.approved_at.isoformat() if item.approved_at else None,
        "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        "rejected_at": item.rejected_at.isoformat() if item.rejected_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


DEFAULT_PROJECT_CONTRACT_LEGAL_NOTE = (
    "To jest umowa wykonania prac przygotowana na podstawie ustalonego zakresu zlecenia. "
    "Przed akceptacja sprawdz dane stron, zakres, terminy i wynagrodzenie."
)
DEFAULT_PROJECT_CONTRACT_CHANGE_TERMS = "Zmiany zakresu prac wymagaja potwierdzenia przez obie strony."
DEFAULT_PROJECT_CONTRACT_TERMS = "Zakres, terminy i wynagrodzenie zgodnie z ustaleniami zlecenia."


def project_contract_share_url(item: models.ProjectContract) -> str | None:
    if not item.share_token:
        return None
    return f"/contract/{item.share_token}"


def ensure_project_contract_share(db: Session, item: models.ProjectContract) -> str:
    if not item.share_token:
        token = random_token(30)
        while db.scalar(select(models.ProjectContract.id).where(models.ProjectContract.share_token == token)):
            token = random_token(30)
        item.share_token = token
    item.share_active = True
    return item.share_token


def project_contract_number(item: models.ProjectContract) -> str:
    return item.contract_number or f"UM-{item.id[:8].upper()}"


def public_profile_for_contract_owner(
    db: Session,
    owner_type: str,
    owner_id: str,
) -> models.PublicProfile | None:
    return db.scalar(
        select(models.PublicProfile).where(
            models.PublicProfile.owner_type == owner_type,
            models.PublicProfile.owner_id == owner_id,
        )
    )


def project_contract_public_owner_payload(db: Session, item: models.ProjectContract) -> dict:
    profile = public_profile_for_contract_owner(db, item.owner_type, item.owner_id)
    public_profile = profile if profile and profile.is_public else None
    if item.owner_type == "company":
        workspace = db.get(models.Workspace, item.owner_id)
        display_name = (
            item.contractor_name
            or (public_profile.display_name if public_profile and public_profile.display_name else "")
            or (workspace.name if workspace else "")
            or "Firma wykonawcza"
        )
        return {
            "owner_type": "company",
            "display_name": display_name,
            "contact_phone": item.contractor_phone or (public_profile.contact_phone if public_profile else ""),
            "contact_email": item.contractor_email or (public_profile.contact_email if public_profile else ""),
            "slug": public_profile.slug if public_profile else "",
            "profile_url": f"/public-profiles/{public_profile.slug}" if public_profile and public_profile.slug else "",
        }
    user = db.get(models.User, item.owner_id)
    display_name = item.contractor_name
    if not display_name and user:
        display_name = user.public_profile_name or user.name
    if public_profile and public_profile.display_name and not item.contractor_name:
        display_name = public_profile.display_name
    return {
        "owner_type": "independent_contractor",
        "display_name": display_name or "Samodzielny majster",
        "contact_phone": item.contractor_phone or (public_profile.contact_phone if public_profile else ""),
        "contact_email": item.contractor_email or (public_profile.contact_email if public_profile else ""),
        "slug": public_profile.slug if public_profile else "",
        "profile_url": f"/public-profiles/{public_profile.slug}" if public_profile and public_profile.slug else "",
    }


def project_contract_payload(item: models.ProjectContract) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "owner_type": item.owner_type,
        "owner_id": item.owner_id,
        "company_id": item.company_id,
        "created_by_id": item.created_by_id,
        "status": item.status,
        "draft_origin": item.draft_origin,
        "draft_origin_label": item.draft_origin_label,
        "share_url": project_contract_share_url(item),
        "share_active": item.share_active,
        "contract_number": project_contract_number(item),
        "contractor_name": item.contractor_name,
        "contractor_email": item.contractor_email,
        "contractor_phone": item.contractor_phone,
        "client_name": item.client_name,
        "client_email": item.client_email,
        "client_phone": item.client_phone,
        "work_address": item.work_address,
        "project_name": item.project_name,
        "scope_summary": item.scope_summary,
        "terms_summary": item.terms_summary,
        "planned_start": item.planned_start,
        "planned_end": item.planned_end,
        "price_amount": money_payload(item.price_amount),
        "price_currency": item.price_currency,
        "price_note": item.price_note,
        "deposit_amount": money_payload(item.deposit_amount),
        "change_terms": item.change_terms,
        "attachments_note": item.attachments_note,
        "legal_note": item.legal_note,
        "sent_at": item.sent_at.isoformat() if item.sent_at else None,
        "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        "rejected_at": item.rejected_at.isoformat() if item.rejected_at else None,
        "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def guest_estimate_draft_payload(item: models.Estimate) -> dict:
    return {
        "id": item.id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "project_id": item.project_id,
        "draft_origin": item.draft_origin,
        "draft_origin_label": item.draft_origin_label,
        "title": item.title,
        "scope_summary": item.scope_summary,
        "estimated_price": money_payload(item.estimated_price),
        "price_note": item.price_note,
        "planned_start": item.planned_start,
        "planned_end": item.planned_end,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
    }


def guest_project_contract_draft_payload(item: models.ProjectContract) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "status": item.status,
        "draft_origin": item.draft_origin,
        "draft_origin_label": item.draft_origin_label,
        "project_name": item.project_name,
        "scope_summary": item.scope_summary,
        "planned_start": item.planned_start,
        "planned_end": item.planned_end,
        "price_amount": money_payload(item.price_amount),
        "price_currency": item.price_currency,
        "price_note": item.price_note,
        "created_at": item.created_at.isoformat(),
    }


def public_project_contract_payload(db: Session, item: models.ProjectContract) -> dict:
    return {
        "number": project_contract_number(item),
        "owner": project_contract_public_owner_payload(db, item),
        "contractor_name": item.contractor_name,
        "contractor_email": item.contractor_email,
        "contractor_phone": item.contractor_phone,
        "client_name": item.client_name,
        "client_email": item.client_email,
        "client_phone": item.client_phone,
        "work_address": item.work_address,
        "project_name": item.project_name,
        "scope_summary": item.scope_summary,
        "terms_summary": item.terms_summary,
        "planned_start": item.planned_start,
        "planned_end": item.planned_end,
        "price_amount": money_payload(item.price_amount),
        "price_currency": item.price_currency,
        "price_note": item.price_note,
        "deposit_amount": money_payload(item.deposit_amount),
        "change_terms": item.change_terms,
        "attachments_note": item.attachments_note,
        "legal_note": item.legal_note,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
        "sent_at": item.sent_at.isoformat() if item.sent_at else None,
        "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        "rejected_at": item.rejected_at.isoformat() if item.rejected_at else None,
        "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at else None,
    }


DEFAULT_FINAL_REPORT_LEGAL_NOTE = (
    "To jest raport koncowy z wykonanych prac. Nie jest faktura, umowa ani wezwaniem do zaplaty."
)
DEFAULT_FINAL_REPORT_WARRANTY_NOTE = (
    "Ewentualne uwagi gwarancyjne i odbiorowe wymagaja osobnego potwierdzenia stron."
)


def project_final_report_share_url(item: models.ProjectFinalReport) -> str | None:
    if not item.share_token:
        return None
    return f"/final-report/{item.share_token}"


def ensure_project_final_report_share(db: Session, item: models.ProjectFinalReport) -> str:
    if not item.share_token:
        token = random_token(30)
        while db.scalar(
            select(models.ProjectFinalReport.id).where(
                models.ProjectFinalReport.share_token == token
            )
        ):
            token = random_token(30)
        item.share_token = token
    item.share_active = True
    return item.share_token


def project_final_report_number(item: models.ProjectFinalReport) -> str:
    return item.report_number or f"RK-{item.id[:8].upper()}"


def project_final_report_public_owner_payload(
    db: Session, item: models.ProjectFinalReport
) -> dict:
    profile = public_profile_for_contract_owner(db, item.owner_type, item.owner_id)
    public_profile = profile if profile and profile.is_public else None
    if item.owner_type == "company":
        workspace = db.get(models.Workspace, item.owner_id)
        display_name = (
            item.contractor_name
            or (public_profile.display_name if public_profile and public_profile.display_name else "")
            or (workspace.name if workspace else "")
            or "Firma wykonawcza"
        )
        return {
            "owner_type": "company",
            "display_name": display_name,
            "contact_phone": item.contractor_phone or (public_profile.contact_phone if public_profile else ""),
            "contact_email": item.contractor_email or (public_profile.contact_email if public_profile else ""),
            "slug": public_profile.slug if public_profile else "",
            "profile_url": f"/public-profiles/{public_profile.slug}" if public_profile and public_profile.slug else "",
        }
    user = db.get(models.User, item.owner_id)
    display_name = item.contractor_name
    if not display_name and user:
        display_name = user.public_profile_name or user.name
    if public_profile and public_profile.display_name and not item.contractor_name:
        display_name = public_profile.display_name
    return {
        "owner_type": "independent_contractor",
        "display_name": display_name or "Samodzielny majster",
        "contact_phone": item.contractor_phone or (public_profile.contact_phone if public_profile else ""),
        "contact_email": item.contractor_email or (public_profile.contact_email if public_profile else ""),
        "slug": public_profile.slug if public_profile else "",
        "profile_url": f"/public-profiles/{public_profile.slug}" if public_profile and public_profile.slug else "",
    }


def project_final_report_payload(item: models.ProjectFinalReport) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "owner_type": item.owner_type,
        "owner_id": item.owner_id,
        "company_id": item.company_id,
        "created_by_id": item.created_by_id,
        "status": item.status,
        "draft_origin": item.draft_origin,
        "draft_origin_label": item.draft_origin_label,
        "share_url": project_final_report_share_url(item),
        "share_active": item.share_active,
        "report_number": project_final_report_number(item),
        "contractor_name": item.contractor_name,
        "contractor_email": item.contractor_email,
        "contractor_phone": item.contractor_phone,
        "client_name": item.client_name,
        "client_email": item.client_email,
        "client_phone": item.client_phone,
        "work_address": item.work_address,
        "project_name": item.project_name,
        "work_summary": item.work_summary,
        "completed_scope": item.completed_scope,
        "issues_and_solutions": item.issues_and_solutions,
        "materials_note": item.materials_note,
        "final_cost_amount": money_payload(item.final_cost_amount),
        "final_cost_currency": item.final_cost_currency,
        "final_cost_note": item.final_cost_note,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
        "client_comment": item.client_comment,
        "warranty_note": item.warranty_note,
        "attachments_note": item.attachments_note,
        "legal_note": item.legal_note,
        "sent_at": item.sent_at.isoformat() if item.sent_at else None,
        "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        "rejected_at": item.rejected_at.isoformat() if item.rejected_at else None,
        "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def guest_project_final_report_draft_payload(item: models.ProjectFinalReport) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "status": item.status,
        "draft_origin": item.draft_origin,
        "draft_origin_label": item.draft_origin_label,
        "project_name": item.project_name,
        "work_summary": item.work_summary,
        "completed_scope": item.completed_scope,
        "final_cost_amount": money_payload(item.final_cost_amount),
        "final_cost_currency": item.final_cost_currency,
        "completed_at": item.completed_at,
        "created_at": item.created_at.isoformat(),
    }


def public_project_final_report_payload(
    db: Session, item: models.ProjectFinalReport
) -> dict:
    return {
        "number": project_final_report_number(item),
        "owner": project_final_report_public_owner_payload(db, item),
        "contractor_name": item.contractor_name,
        "contractor_email": item.contractor_email,
        "contractor_phone": item.contractor_phone,
        "client_name": item.client_name,
        "client_email": item.client_email,
        "client_phone": item.client_phone,
        "work_address": item.work_address,
        "project_name": item.project_name,
        "work_summary": item.work_summary,
        "completed_scope": item.completed_scope,
        "issues_and_solutions": item.issues_and_solutions,
        "materials_note": item.materials_note,
        "final_cost_amount": money_payload(item.final_cost_amount),
        "final_cost_currency": item.final_cost_currency,
        "final_cost_note": item.final_cost_note,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
        "client_comment": item.client_comment,
        "warranty_note": item.warranty_note,
        "attachments_note": item.attachments_note,
        "legal_note": item.legal_note,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
        "sent_at": item.sent_at.isoformat() if item.sent_at else None,
        "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        "rejected_at": item.rejected_at.isoformat() if item.rejected_at else None,
        "cancelled_at": item.cancelled_at.isoformat() if item.cancelled_at else None,
    }


def project_contract_actor_for_project(db: Session, access: ProjectAccess, user: models.User) -> str:
    if is_investor(user):
        raise HTTPException(403, "Tylko wykonawca albo szef firmy moze tworzyc umowy")
    if is_company_worker(user):
        if not access.project.workspace_id:
            raise HTTPException(403, "To nie jest zlecenie firmy")
        if not project_role(db, access.project.id, user.id):
            raise HTTPException(403, "Pracownik moze przygotowac szkic umowy tylko do przypisanego zlecenia")
        return "company_worker"
    if is_independent_contractor(user):
        if access.project.workspace_id:
            raise HTTPException(403, "To zlecenie nalezy do firmy")
        if not access.can_manage():
            raise HTTPException(403, "Brak uprawnien do umowy dla tego zlecenia")
        return "independent_contractor"
    if is_company_owner(user):
        if not access.project.workspace_id:
            raise HTTPException(403, "To nie jest zlecenie firmy")
        if not can_manage_workspace(db, access.project.workspace_id, user.id):
            raise HTTPException(403, "Brak dostepu do umowy firmy")
        if not access.can_manage():
            raise HTTPException(403, "Brak uprawnien do umowy dla tego zlecenia")
        return "company_owner"
    raise HTTPException(403, "Brak dostepu do umow")


def project_contract_actor_for_item(
    db: Session,
    user: models.User,
    item: models.ProjectContract,
) -> str:
    project = db.get(models.Project, item.project_id)
    if not project:
        raise HTTPException(404, "Umowa nie istnieje")
    role = project_role(db, project.id, user.id)
    if not role:
        raise HTTPException(404, "Umowa nie istnieje")
    access = ProjectAccess(project=project, user=user, role=role)
    return project_contract_actor_for_project(db, access, user)


def visible_project_contracts_for_user(
    db: Session,
    user: models.User,
    project_id: str | None = None,
) -> list[models.ProjectContract]:
    if is_investor(user):
        raise HTTPException(403, "Inwestor nie ma dostepu do umow wykonawcy")
    if not (is_independent_contractor(user) or is_company_owner(user) or is_company_worker(user)):
        raise HTTPException(403, "Brak dostepu do umow")
    project_ids = [
        row[0].id
        for row in db.execute(user_projects_query(user.id)).all()
        if not project_id or row[0].id == project_id
    ]
    if not project_ids:
        return []
    query = select(models.ProjectContract).where(models.ProjectContract.project_id.in_(project_ids))
    return list(
        db.scalars(
            query.order_by(
                models.ProjectContract.updated_at.desc(),
                models.ProjectContract.created_at.desc(),
            )
        ).all()
    )


def contract_owner_from_project(project: models.Project, user: models.User) -> tuple[str, str, str | None]:
    if project.workspace_id:
        return "company", project.workspace_id, project.workspace_id
    return "independent_contractor", user.id, None


def date_payload(value: date | None) -> str:
    return value.isoformat() if value else ""


def project_contract_contractor_defaults(
    db: Session,
    owner_type: str,
    owner_id: str,
    user: models.User,
) -> tuple[str, str, str]:
    profile = public_profile_for_contract_owner(db, owner_type, owner_id)
    if profile:
        name = profile.display_name.strip()
        phone = profile.contact_phone.strip()
        email = profile.contact_email.strip()
        if name or phone or email:
            return name, email, phone
    if owner_type == "company":
        workspace = db.get(models.Workspace, owner_id)
        return (workspace.name if workspace else "Firma wykonawcza", "", workspace.phone if workspace else "")
    return user.public_profile_name or user.name or "Samodzielny majster", "", user.phone or ""


def build_project_contract_from_project(
    db: Session,
    project: models.Project,
    user: models.User,
    *,
    status: str = "draft",
    created_by_id: str | None = None,
    draft_origin: str = "manual",
    draft_origin_label: str = "",
) -> models.ProjectContract:
    owner_type, owner_id, company_id = contract_owner_from_project(project, user)
    contractor_name, contractor_email, contractor_phone = project_contract_contractor_defaults(
        db, owner_type, owner_id, user
    )
    item = models.ProjectContract(
        project_id=project.id,
        owner_type=owner_type,
        owner_id=owner_id,
        company_id=company_id,
        created_by_id=created_by_id or user.id,
        status=status,
        draft_origin=draft_origin,
        draft_origin_label=draft_origin_label,
        share_active=False,
        contract_number="",
        contractor_name=contractor_name,
        contractor_email=contractor_email,
        contractor_phone=contractor_phone,
        client_name=project.client_name or "",
        client_email=project.client_email or "",
        client_phone="",
        work_address=project.address or "",
        project_name=project.name or "Umowa wykonania prac",
        scope_summary=project.description or "",
        terms_summary=DEFAULT_PROJECT_CONTRACT_TERMS,
        planned_start=date_payload(project.planned_start_date),
        planned_end=date_payload(project.planned_end_date),
        price_amount=project.contract_amount,
        price_currency=project.contract_currency or DEFAULT_CONTRACT_CURRENCY,
        price_note="",
        deposit_amount=None,
        change_terms=DEFAULT_PROJECT_CONTRACT_CHANGE_TERMS,
        attachments_note="",
        legal_note=DEFAULT_PROJECT_CONTRACT_LEGAL_NOTE,
    )
    return item


def apply_project_contract_changes(
    item: models.ProjectContract,
    payload: ProjectContractUpdate,
    *,
    partial: bool = True,
) -> None:
    changes = payload.model_dump(exclude_unset=partial)
    for key in [
        "contractor_name",
        "contractor_phone",
        "client_name",
        "client_phone",
        "work_address",
        "project_name",
        "scope_summary",
        "terms_summary",
        "planned_start",
        "planned_end",
        "price_note",
        "change_terms",
        "attachments_note",
        "legal_note",
    ]:
        if key in changes:
            setattr(item, key, (changes[key] or "").strip())
    if "contractor_email" in changes:
        item.contractor_email = optional_email(changes["contractor_email"] or "")
    if "client_email" in changes:
        item.client_email = optional_email(changes["client_email"] or "")
    if "price_currency" in changes:
        currency = (changes["price_currency"] or DEFAULT_CONTRACT_CURRENCY).strip().upper()[:3]
        item.price_currency = currency or DEFAULT_CONTRACT_CURRENCY
    if "price_amount" in changes:
        item.price_amount = changes["price_amount"]
    if "deposit_amount" in changes:
        item.deposit_amount = changes["deposit_amount"]


def ensure_project_contract_editable(
    item: models.ProjectContract,
    actor: str,
    user: models.User,
) -> None:
    if actor == "company_worker":
        if item.created_by_id != user.id:
            raise HTTPException(403, "Pracownik moze edytowac tylko wlasny szkic umowy")
        if item.status != "pending_approval":
            raise HTTPException(422, "Pracownik moze edytowac tylko szkic do zatwierdzenia")
        return
    if item.status not in {"draft", "pending_approval"}:
        raise HTTPException(422, "Tylko szkic umowy jest edytowalny")


def ensure_project_contract_ready_to_send(item: models.ProjectContract) -> None:
    if not item.project_name.strip() or not item.scope_summary.strip():
        raise HTTPException(422, "Do wyslania umowy potrzebny jest tytul i zakres prac")
    if not item.client_name.strip():
        raise HTTPException(422, "Do wyslania umowy potrzebne sa dane klienta")


def change_project_contract_status(
    db: Session,
    item: models.ProjectContract,
    status: ProjectContractStatus,
    actor: str,
    user: models.User,
) -> None:
    if status == item.status:
        if status == "sent" and actor in {"independent_contractor", "company_owner"}:
            ensure_project_contract_share(db, item)
        return
    if item.status in {"accepted", "rejected", "cancelled"}:
        raise HTTPException(422, "Ten status umowy jest finalny")
    if actor == "company_worker":
        if (
            status == "cancelled"
            and item.status == "pending_approval"
            and item.created_by_id == user.id
        ):
            item.status = "cancelled"
            item.cancelled_at = item.cancelled_at or now()
            item.share_active = False
            return
        raise HTTPException(403, "Pracownik firmy nie moze wyslac ani zatwierdzic umowy")
    if status == "sent":
        if actor not in {"independent_contractor", "company_owner"}:
            raise HTTPException(403, "Brak uprawnien do wyslania umowy")
        if item.status not in {"draft", "pending_approval"}:
            raise HTTPException(422, "Umowe mozna wyslac tylko ze szkicu")
        ensure_project_contract_ready_to_send(item)
        item.status = "sent"
        item.sent_at = item.sent_at or now()
        ensure_project_contract_share(db, item)
        return
    if status == "draft" and actor == "company_owner" and item.status == "pending_approval":
        item.status = "draft"
        return
    if status == "cancelled" and item.status in {"draft", "pending_approval", "sent"}:
        if actor not in {"independent_contractor", "company_owner"}:
            raise HTTPException(403, "Brak uprawnien do anulowania umowy")
        item.status = "cancelled"
        item.cancelled_at = item.cancelled_at or now()
        item.share_active = False
        return
    raise HTTPException(422, "Nielegalna zmiana statusu umowy")


def project_final_report_actor_for_project(
    db: Session, access: ProjectAccess, user: models.User
) -> str:
    if is_investor(user):
        raise HTTPException(403, "Inwestor nie tworzy raportow koncowych wykonawcy")
    if is_company_worker(user):
        if not access.project.workspace_id:
            raise HTTPException(403, "To nie jest zlecenie firmy")
        if not project_role(db, access.project.id, user.id):
            raise HTTPException(403, "Pracownik moze przygotowac raport tylko do przypisanego zlecenia")
        return "company_worker"
    if is_independent_contractor(user):
        if access.project.workspace_id:
            raise HTTPException(403, "To zlecenie nalezy do firmy")
        if not access.can_manage():
            raise HTTPException(403, "Brak uprawnien do raportu koncowego")
        return "independent_contractor"
    if is_company_owner(user):
        if not access.project.workspace_id:
            raise HTTPException(403, "To nie jest zlecenie firmy")
        if not can_manage_workspace(db, access.project.workspace_id, user.id):
            raise HTTPException(403, "Brak dostepu do raportu firmy")
        if not access.can_manage():
            raise HTTPException(403, "Brak uprawnien do raportu koncowego")
        return "company_owner"
    raise HTTPException(403, "Brak dostepu do raportow koncowych")


def project_final_report_actor_for_item(
    db: Session,
    user: models.User,
    item: models.ProjectFinalReport,
) -> str:
    project = db.get(models.Project, item.project_id)
    if not project:
        raise HTTPException(404, "Raport koncowy nie istnieje")
    role = project_role(db, project.id, user.id)
    if not role:
        raise HTTPException(404, "Raport koncowy nie istnieje")
    access = ProjectAccess(project=project, user=user, role=role)
    return project_final_report_actor_for_project(db, access, user)


def visible_project_final_reports_for_user(
    db: Session,
    user: models.User,
    project_id: str | None = None,
) -> list[models.ProjectFinalReport]:
    if is_investor(user):
        raise HTTPException(403, "Inwestor nie ma dostepu do raportow koncowych wykonawcy")
    if not (is_independent_contractor(user) or is_company_owner(user) or is_company_worker(user)):
        raise HTTPException(403, "Brak dostepu do raportow koncowych")
    project_ids = [
        row[0].id
        for row in db.execute(user_projects_query(user.id)).all()
        if not project_id or row[0].id == project_id
    ]
    if not project_ids:
        return []
    query = select(models.ProjectFinalReport).where(
        models.ProjectFinalReport.project_id.in_(project_ids)
    )
    return list(
        db.scalars(
            query.order_by(
                models.ProjectFinalReport.updated_at.desc(),
                models.ProjectFinalReport.created_at.desc(),
            )
        ).all()
    )


def build_project_final_report_from_project(
    db: Session,
    project: models.Project,
    user: models.User,
    *,
    status: str = "draft",
    created_by_id: str | None = None,
    draft_origin: str = "manual",
    draft_origin_label: str = "",
) -> models.ProjectFinalReport:
    owner_type, owner_id, company_id = contract_owner_from_project(project, user)
    contractor_name, contractor_email, contractor_phone = project_contract_contractor_defaults(
        db, owner_type, owner_id, user
    )
    planned_start = date_payload(project.planned_start_date)
    planned_end = date_payload(project.planned_end_date)
    completed_date = project.finished_at.date().isoformat() if project.finished_at else planned_end
    item = models.ProjectFinalReport(
        project_id=project.id,
        owner_type=owner_type,
        owner_id=owner_id,
        company_id=company_id,
        created_by_id=created_by_id or user.id,
        status=status,
        draft_origin=draft_origin,
        draft_origin_label=draft_origin_label,
        contractor_name=contractor_name,
        contractor_email=contractor_email,
        contractor_phone=contractor_phone,
        client_name=project.client_name or "",
        client_email=project.client_email or "",
        client_phone="",
        work_address=project.address or "",
        project_name=project.name or "Raport koncowy",
        work_summary=project.description or "",
        completed_scope=(
            f"Podsumowanie wykonanych prac dla zlecenia: {project.name}."
            if project.name
            else ""
        ),
        issues_and_solutions="",
        materials_note="",
        final_cost_amount=project.contract_amount,
        final_cost_currency=project.contract_currency or DEFAULT_CONTRACT_CURRENCY,
        final_cost_note="Kwota zgodnie z ustaleniami zlecenia." if project.contract_amount else "",
        started_at=planned_start,
        completed_at=completed_date,
        client_comment="",
        warranty_note=DEFAULT_FINAL_REPORT_WARRANTY_NOTE,
        attachments_note="",
        legal_note=DEFAULT_FINAL_REPORT_LEGAL_NOTE,
    )
    return item


def apply_project_final_report_changes(
    item: models.ProjectFinalReport,
    payload: ProjectFinalReportUpdate,
    *,
    partial: bool,
) -> None:
    changes = payload.model_dump(exclude_unset=partial)
    for key, value in changes.items():
        if key in {"contractor_email", "client_email"}:
            setattr(item, key, optional_email(value or ""))
        elif key == "final_cost_currency":
            item.final_cost_currency = normalize_contract_currency(
                value, item.final_cost_amount
            ) or DEFAULT_CONTRACT_CURRENCY
        elif key == "final_cost_amount":
            item.final_cost_amount = value
            item.final_cost_currency = normalize_contract_currency(
                item.final_cost_currency, value
            ) or DEFAULT_CONTRACT_CURRENCY
        elif isinstance(value, str):
            setattr(item, key, value.strip())
        else:
            setattr(item, key, value)


def ensure_project_final_report_editable(
    item: models.ProjectFinalReport,
    actor: str,
    user: models.User,
) -> None:
    if actor == "company_worker":
        if item.created_by_id != user.id:
            raise HTTPException(403, "Pracownik moze edytowac tylko wlasny szkic raportu")
        if item.status != "pending_approval":
            raise HTTPException(422, "Pracownik moze edytowac tylko szkic do zatwierdzenia")
        return
    if item.status not in {"draft", "pending_approval"}:
        raise HTTPException(422, "Tylko szkic raportu jest edytowalny")


def ensure_project_final_report_ready_to_send(item: models.ProjectFinalReport) -> None:
    if not item.project_name.strip() or not item.work_summary.strip():
        raise HTTPException(422, "Do wyslania raportu potrzebny jest tytul i podsumowanie prac")
    if not item.client_name.strip():
        raise HTTPException(422, "Do wyslania raportu potrzebne sa dane klienta")


def change_project_final_report_status(
    db: Session,
    item: models.ProjectFinalReport,
    status: ProjectFinalReportStatus,
    actor: str,
    user: models.User,
) -> None:
    if status == item.status:
        if status == "sent" and actor in {"independent_contractor", "company_owner"}:
            ensure_project_final_report_share(db, item)
        return
    if item.status in {"accepted", "rejected", "cancelled"}:
        raise HTTPException(422, "Ten status raportu jest finalny")
    if actor == "company_worker":
        if (
            status == "cancelled"
            and item.status == "pending_approval"
            and item.created_by_id == user.id
        ):
            item.status = "cancelled"
            item.cancelled_at = item.cancelled_at or now()
            item.share_active = False
            return
        raise HTTPException(403, "Pracownik firmy nie moze wyslac raportu klientowi")
    if status == "sent":
        if actor not in {"independent_contractor", "company_owner"}:
            raise HTTPException(403, "Brak uprawnien do wyslania raportu")
        if item.status not in {"draft", "pending_approval"}:
            raise HTTPException(422, "Raport mozna wyslac tylko ze szkicu")
        ensure_project_final_report_ready_to_send(item)
        item.status = "sent"
        item.sent_at = item.sent_at or now()
        ensure_project_final_report_share(db, item)
        return
    if status == "draft" and actor == "company_owner" and item.status == "pending_approval":
        item.status = "draft"
        return
    if status == "cancelled" and item.status in {"draft", "pending_approval", "sent"}:
        if actor not in {"independent_contractor", "company_owner"}:
            raise HTTPException(403, "Brak uprawnien do anulowania raportu")
        item.status = "cancelled"
        item.cancelled_at = item.cancelled_at or now()
        item.share_active = False
        return
    raise HTTPException(422, "Nielegalna zmiana statusu raportu")


def public_project_final_report_by_token(
    db: Session, token: str
) -> models.ProjectFinalReport:
    item = db.scalar(
        select(models.ProjectFinalReport).where(
            models.ProjectFinalReport.share_token == token,
            models.ProjectFinalReport.share_active.is_(True),
        )
    )
    if not item or item.status not in {"sent", "accepted", "rejected"}:
        raise HTTPException(404, "Raport koncowy nie istnieje albo link wygasl")
    return item


def public_project_contract_by_token(db: Session, token: str) -> models.ProjectContract:
    item = db.scalar(
        select(models.ProjectContract).where(
            models.ProjectContract.share_token == token,
            models.ProjectContract.share_active.is_(True),
        )
    )
    if not item or item.status not in {"sent", "accepted", "rejected"}:
        raise HTTPException(404, "Umowa nie istnieje albo link wygasl")
    return item


def guest_document_draft_access(
    request: Request,
    db: Session,
    project_id: str,
) -> ProjectAccess:
    access = get_project_access(request, db, project_id, allow_guest=True)
    if not access.guest:
        raise HTTPException(403, "Ten endpoint jest tylko dla linku wykonawcy")
    access.require_add()
    if not access.project.workspace_id:
        raise HTTPException(403, "Szkice z linku wykonawcy sa dostepne tylko dla zlecen firmowych")
    return access


def draft_origin_label_from_guest(guest: models.GuestInvite | None) -> str:
    label = (guest.label if guest else "").strip()
    return (label or "Link wykonawcy /g")[:180]


def guest_link_creator(db: Session, guest: models.GuestInvite) -> models.User:
    user = db.get(models.User, guest.created_by_id)
    if not user:
        raise HTTPException(404, "Nie znaleziono wlasciciela linku wykonawcy")
    return user

def company_workspace_ids_for_user(
    db: Session,
    user: models.User,
    *,
    manager_only: bool = False,
) -> list[str]:
    query = select(models.WorkspaceMember.workspace_id).where(
        models.WorkspaceMember.user_id == user.id
    )
    if manager_only:
        query = query.where(models.WorkspaceMember.role.in_(["owner", "admin"]))
    return list(db.scalars(query).all())


def require_company_workspace_member(
    db: Session,
    user: models.User,
    workspace_id: str,
) -> None:
    membership = db.scalar(
        select(models.WorkspaceMember).where(
            models.WorkspaceMember.workspace_id == workspace_id,
            models.WorkspaceMember.user_id == user.id,
        )
    )
    if not membership:
        raise HTTPException(404, "Oferta nie istnieje")


def resolve_estimate_owner(
    db: Session,
    user: models.User,
    owner_type: str | None,
    owner_id: str | None,
) -> tuple[str, str, str]:
    if is_independent_contractor(user):
        if owner_type and owner_type != "independent_contractor":
            raise HTTPException(403, "Samodzielny majster moze tworzyc tylko swoje oferty")
        if owner_id and owner_id != user.id:
            raise HTTPException(403, "Nie mozesz tworzyc ofert innego wykonawcy")
        return "independent_contractor", user.id, "independent_contractor"

    if is_company_owner(user):
        if owner_type and owner_type != "company":
            raise HTTPException(403, "Szef firmy moze tworzyc oferty firmy")
        workspace_ids = company_workspace_ids_for_user(db, user, manager_only=True)
        selected_owner_id = owner_id or (workspace_ids[0] if workspace_ids else None)
        if not selected_owner_id:
            raise HTTPException(404, "Nie znaleziono firmy dla tego konta")
        if selected_owner_id not in workspace_ids:
            raise HTTPException(403, "Brak dostepu do tej firmy")
        return "company", selected_owner_id, "company_owner"

    if is_company_worker(user):
        if owner_type and owner_type != "company":
            raise HTTPException(403, "Pracownik firmy moze przygotowac tylko szkic firmy")
        workspace_ids = company_workspace_ids_for_user(db, user)
        selected_owner_id = owner_id or (workspace_ids[0] if workspace_ids else None)
        if not selected_owner_id:
            raise HTTPException(403, "Nie masz przypisanej firmy")
        if selected_owner_id not in workspace_ids:
            raise HTTPException(403, "Brak dostepu do tej firmy")
        return "company", selected_owner_id, "company_worker"

    raise HTTPException(403, "Inwestor nie tworzy ofert wykonawcy")


def estimate_actor_for_item(
    db: Session,
    user: models.User,
    item: models.Estimate,
) -> str:
    if is_independent_contractor(user):
        if item.owner_type == "independent_contractor" and item.owner_id == user.id:
            return "independent_contractor"
        raise HTTPException(404, "Oferta nie istnieje")

    if is_company_owner(user):
        if item.owner_type == "company" and can_manage_workspace(db, item.owner_id, user.id):
            return "company_owner"
        raise HTTPException(404, "Oferta nie istnieje")

    if is_company_worker(user):
        if item.owner_type != "company":
            raise HTTPException(404, "Oferta nie istnieje")
        require_company_workspace_member(db, user, item.owner_id)
        if item.created_by_id != user.id:
            raise HTTPException(404, "Oferta nie istnieje")
        return "company_worker"

    raise HTTPException(403, "Inwestor nie ma dostepu do ofert wykonawcy")


def visible_estimates_for_user(db: Session, user: models.User) -> list[models.Estimate]:
    if is_independent_contractor(user):
        query = select(models.Estimate).where(
            models.Estimate.owner_type == "independent_contractor",
            models.Estimate.owner_id == user.id,
        )
    elif is_company_owner(user):
        workspace_ids = company_workspace_ids_for_user(db, user, manager_only=True)
        if not workspace_ids:
            return []
        query = select(models.Estimate).where(
            models.Estimate.owner_type == "company",
            models.Estimate.owner_id.in_(workspace_ids),
        )
    elif is_company_worker(user):
        workspace_ids = company_workspace_ids_for_user(db, user)
        if not workspace_ids:
            return []
        query = select(models.Estimate).where(
            models.Estimate.owner_type == "company",
            models.Estimate.owner_id.in_(workspace_ids),
            models.Estimate.created_by_id == user.id,
        )
    else:
        raise HTTPException(403, "Inwestor nie ma dostepu do ofert wykonawcy")

    return list(
        db.scalars(
            query.order_by(
                models.Estimate.updated_at.desc(),
                models.Estimate.created_at.desc(),
            )
        ).all()
    )


def validate_estimate_source(
    db: Session,
    owner_type: str,
    owner_id: str,
    source_type: str,
    source_id: str | None,
    *,
    actor: str | None = None,
    user: models.User | None = None,
) -> None:
    if source_type == "manual":
        if source_id:
            raise HTTPException(422, "Oferta manualna nie ma source_id")
        return
    if source_type == "project":
        if not source_id:
            raise HTTPException(422, "Oferta powiazana ze zleceniem wymaga source_id")
        project = db.get(models.Project, source_id)
        if not project:
            raise HTTPException(404, "Zlecenie nie istnieje")
        if owner_type == "independent_contractor":
            if project.created_by_id != owner_id:
                raise HTTPException(403, "Nie masz dostepu do tego zlecenia")
        elif project.workspace_id != owner_id:
            raise HTTPException(403, "To zlecenie nie nalezy do tej firmy")
        if actor == "company_worker" and (
            not user or not project_role(db, project.id, user.id)
        ):
            raise HTTPException(403, "Pracownik moze przygotowac szkic tylko do przypisanego zlecenia")
        return
    if source_type == "job_posting":
        if source_id and not db.get(models.JobPosting, source_id):
            raise HTTPException(404, "Ogloszenie nie istnieje")
        return
    raise HTTPException(422, "Nieobslugiwane zrodlo oferty")


def apply_estimate_changes(
    item: models.Estimate,
    payload: EstimateCreate | EstimateUpdate,
    *,
    db: Session,
    actor: str | None = None,
    user: models.User | None = None,
    partial: bool = False,
) -> None:
    changes = payload.model_dump(exclude_unset=partial)
    if "source_type" in changes or "source_id" in changes:
        source_type = changes.get("source_type", item.source_type) or "manual"
        source_id = changes.get("source_id", item.source_id)
        validate_estimate_source(
            db,
            item.owner_type,
            item.owner_id,
            source_type,
            source_id,
            actor=actor,
            user=user,
        )
        item.source_type = source_type
        item.source_id = source_id
    for key in ["recipient_name", "recipient_phone", "title", "scope_summary", "assumptions", "price_note", "planned_start", "planned_end"]:
        if key in changes:
            setattr(item, key, (changes[key] or "").strip())
    if "recipient_type" in changes:
        item.recipient_type = changes["recipient_type"] or "manual"
    if "recipient_email" in changes:
        item.recipient_email = optional_email(changes["recipient_email"] or "")
    if "estimated_price" in changes:
        item.estimated_price = changes["estimated_price"]


def ensure_estimate_ready_to_send(item: models.Estimate) -> None:
    if not item.title.strip() or not item.scope_summary.strip():
        raise HTTPException(422, "Do wyslania oferty potrzebny jest tytul i zakres prac")


def apply_initial_estimate_status(
    db: Session,
    item: models.Estimate,
    status: str,
    actor: str,
    user: models.User,
) -> None:
    if actor == "company_worker":
        if status not in {"draft", "pending_approval"}:
            raise HTTPException(403, "Pracownik firmy nie moze wyslac oferty")
        item.status = status
        return

    if status == "pending_approval":
        raise HTTPException(422, "Zatwierdzenia wymaga tylko szkic pracownika")
    if status == "sent":
        ensure_estimate_ready_to_send(item)
        item.status = "sent"
        item.sent_at = item.sent_at or now()
        ensure_estimate_share(db, item)
        if actor == "company_owner":
            item.approved_by_id = user.id
            item.approved_at = item.approved_at or now()
        return
    if status != "draft":
        raise HTTPException(422, "Nielegalny status poczatkowy oferty")
    item.status = "draft"


def change_estimate_status(
    db: Session,
    item: models.Estimate,
    status: str,
    actor: str,
    user: models.User,
) -> None:
    if status == item.status:
        if status == "sent":
            ensure_estimate_share(db, item)
        return
    if item.status in {"accepted", "rejected", "cancelled"}:
        raise HTTPException(422, "Ten status oferty jest finalny")

    if actor == "company_worker":
        if item.status == "draft" and status in {"pending_approval", "cancelled"}:
            item.status = status
            if status == "cancelled":
                item.share_active = False
            return
        if item.status == "pending_approval" and status == "cancelled":
            item.status = status
            item.share_active = False
            return
        raise HTTPException(403, "Pracownik firmy nie moze wyslac ani zatwierdzic oferty")

    if actor == "independent_contractor":
        if item.status == "draft" and status == "sent":
            ensure_estimate_ready_to_send(item)
            item.status = "sent"
            item.sent_at = item.sent_at or now()
            ensure_estimate_share(db, item)
            return
        if item.status == "draft" and status == "cancelled":
            item.status = status
            item.share_active = False
            return
        if item.status == "sent" and status in {"accepted", "rejected", "cancelled"}:
            item.status = status
            if status == "accepted":
                item.accepted_at = item.accepted_at or now()
            elif status == "rejected":
                item.rejected_at = item.rejected_at or now()
            else:
                item.share_active = False
            return
        raise HTTPException(422, "Nielegalna zmiana statusu oferty")

    if actor == "company_owner":
        if item.status == "pending_approval" and status == "approved_by_owner":
            item.status = status
            item.approved_by_id = user.id
            item.approved_at = item.approved_at or now()
            return
        if item.status in {"draft", "pending_approval", "approved_by_owner"} and status == "sent":
            ensure_estimate_ready_to_send(item)
            item.status = "sent"
            item.sent_at = item.sent_at or now()
            ensure_estimate_share(db, item)
            item.approved_by_id = item.approved_by_id or user.id
            item.approved_at = item.approved_at or now()
            return
        if item.status in {"draft", "pending_approval", "approved_by_owner"} and status == "cancelled":
            item.status = status
            item.share_active = False
            return
        if item.status == "sent" and status in {"accepted", "rejected", "cancelled"}:
            item.status = status
            if status == "accepted":
                item.accepted_at = item.accepted_at or now()
            elif status == "rejected":
                item.rejected_at = item.rejected_at or now()
            else:
                item.share_active = False
            return
    raise HTTPException(422, "Nielegalna zmiana statusu oferty")


def ensure_estimate_editable(item: models.Estimate, actor: str) -> None:
    if actor == "company_worker":
        if item.status not in {"draft", "pending_approval"}:
            raise HTTPException(422, "Pracownik moze edytowac tylko wlasny szkic")
        return
    if item.status not in {"draft", "pending_approval", "approved_by_owner"}:
        raise HTTPException(422, "Wyslana lub finalna oferta nie jest edytowalna")


def parse_estimate_project_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def estimate_project_name(item: models.Estimate) -> str:
    title = item.title.strip()
    if len(title) < 2:
        title = "Zlecenie z oferty"
    return title[:200].strip() or "Zlecenie z oferty"


def estimate_project_description(item: models.Estimate) -> str:
    sections = ["Utworzono z zaakceptowanej oferty/wyceny."]
    if item.scope_summary.strip():
        sections.append(f"Zakres prac:\n{item.scope_summary.strip()}")
    if item.assumptions.strip():
        sections.append(f"Zalozenia i uwagi:\n{item.assumptions.strip()}")
    if item.price_note.strip():
        sections.append(f"Notatka do ceny:\n{item.price_note.strip()}")
    if item.recipient_phone.strip():
        sections.append(f"Telefon odbiorcy: {item.recipient_phone.strip()}")
    return "\n\n".join(sections)


def build_project_from_estimate(
    db: Session,
    item: models.Estimate,
    user: models.User,
) -> models.Project:
    workspace_id = item.owner_id if item.owner_type == "company" else None
    contract_changes = {
        "planned_start_date": parse_estimate_project_date(item.planned_start),
        "planned_end_date": parse_estimate_project_date(item.planned_end),
        "schedule_uncertainty_days": None,
        "contract_amount": item.estimated_price,
        "contract_currency": DEFAULT_CONTRACT_CURRENCY if item.estimated_price is not None else None,
    }
    normalize_project_contract_changes(contract_changes)

    project = models.Project(
        workspace_id=workspace_id,
        worker_profile_id=None,
        created_by_id=user.id,
        name=estimate_project_name(item),
        client_name=item.recipient_name.strip(),
        client_email=item.recipient_email.strip(),
        address="",
        description=estimate_project_description(item),
        status=PROJECT_STATUS_ASSIGNED,
        template="custom",
        planned_start_date=contract_changes["planned_start_date"],
        planned_end_date=contract_changes["planned_end_date"],
        schedule_uncertainty_days=contract_changes["schedule_uncertainty_days"],
        contract_amount=contract_changes["contract_amount"],
        contract_currency=contract_changes["contract_currency"],
        started_at=now(),
        client_share_token=random_token(30),
    )
    db.add(project)
    db.flush()
    db.add(models.ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    for position, title in enumerate(STAGE_TEMPLATES["custom"]):
        if title.strip():
            db.add(
                models.ProjectStage(
                    project_id=project.id,
                    title=title.strip(),
                    position=position,
                    status="active" if position == 0 else "planned",
                )
            )
    return project


def get_or_create_public_profile(
    db: Session, user: models.User, owner_type: str
) -> models.PublicProfile:
    owner_id, display_name, contact_phone, service_area = public_profile_owner_defaults(
        db, user, owner_type
    )
    profile = db.scalar(
        select(models.PublicProfile).where(
            models.PublicProfile.owner_type == owner_type,
            models.PublicProfile.owner_id == owner_id,
        )
    )
    if profile:
        return profile

    profile = models.PublicProfile(
        owner_type=owner_type,
        owner_id=owner_id,
        display_name=display_name,
        public_description="",
        contact_phone=contact_phone,
        contact_email="",
        specializations=[],
        service_area=service_area,
        is_public=False,
        slug=unique_public_profile_slug(db, display_name),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def validate_realization_project(
    db: Session,
    user: models.User,
    profile: models.PublicProfile,
    project_id: str,
) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Zlecenie nie istnieje")
    if project.status != PROJECT_STATUS_COMPLETED:
        raise HTTPException(422, "Realizacja musi bazowaÄ‡ na zakoÅ„czonym zleceniu")
    if profile.owner_type == "independent_contractor":
        if project.created_by_id != user.id:
            raise HTTPException(403, "Nie masz dostÄ™pu do tego zlecenia")
    elif profile.owner_type == "company":
        if project.workspace_id != profile.owner_id:
            raise HTTPException(403, "To zlecenie nie naleÅ¼y do tej firmy")
    else:
        raise HTTPException(403, "Ten profil nie moÅ¼e mieÄ‡ realizacji")
    return project


def apply_public_profile_realization_changes(
    item: models.PublicProfileRealization,
    payload: PublicProfileRealizationCreate | PublicProfileRealizationUpdate,
    *,
    db: Session,
    user: models.User,
    profile: models.PublicProfile,
    require_project: bool = False,
) -> None:
    changes = payload.model_dump(exclude_unset=True)
    project_id = changes.pop("project_id", None)
    if require_project and not project_id:
        raise HTTPException(422, "Wybierz zakoÅ„czone zlecenie")
    if project_id:
        item.project_id = validate_realization_project(db, user, profile, project_id).id

    if "title" in changes:
        item.title = (changes["title"] or "").strip()
    if "public_description" in changes:
        item.public_description = (changes["public_description"] or "").strip()
    if "location_public" in changes:
        item.location_public = (changes["location_public"] or "").strip()
    if "work_scope" in changes:
        item.work_scope = clean_realization_work_scope(changes["work_scope"])
    if "completion_date" in changes:
        item.completion_date = changes["completion_date"]
    if "amount" in changes:
        item.amount = changes["amount"]
    if "currency" in changes:
        item.currency = clean_realization_currency(changes["currency"])
    if "show_amount" in changes:
        item.show_amount = bool(changes["show_amount"])
    if "cover_image_url" in changes:
        item.cover_image_url = (changes["cover_image_url"] or "").strip()
    if "gallery_image_urls" in changes:
        item.gallery_image_urls = clean_realization_urls(changes["gallery_image_urls"])
    if "sort_order" in changes:
        item.sort_order = int(changes["sort_order"] or 0)
    if "status" in changes:
        item.status = changes["status"] or "draft"
        if item.status == "published":
            item.published_at = item.published_at or datetime.now(timezone.utc)
        else:
            item.published_at = None


class OtpRequest(BaseModel):
    email: EmailStr


class OtpVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class PasswordLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=4, max_length=128)


class DemoAdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=200)


class DemoAdminReset(BaseModel):
    confirmation: str = Field(max_length=40)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    public_profile_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    locale: str | None = Field(default=None, max_length=10)
    preferred_mode: Literal["expanded", "field"] | None = None


class PublicProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=180)
    public_description: str | None = Field(default=None, max_length=3000)
    contact_phone: str | None = Field(default=None, max_length=40)
    contact_email: str | None = Field(default=None, max_length=320)
    specializations: list[str] | None = None
    service_area: str | None = Field(default=None, max_length=220)
    is_public: bool | None = None
    slug: str | None = Field(default=None, max_length=140)


class PublicProfileRealizationCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=220)
    public_description: str | None = Field(default="", max_length=4000)
    location_public: str | None = Field(default="", max_length=220)
    work_scope: list[str] | None = None
    completion_date: date | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default="PLN", max_length=3)
    show_amount: bool = False
    status: Literal["draft", "published"] = "draft"
    cover_image_url: str | None = Field(default="", max_length=2000)
    gallery_image_urls: list[str] | None = None
    sort_order: int | None = 0


class PublicProfileRealizationUpdate(BaseModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    title: str | None = Field(default=None, min_length=1, max_length=220)
    public_description: str | None = Field(default=None, max_length=4000)
    location_public: str | None = Field(default=None, max_length=220)
    work_scope: list[str] | None = None
    completion_date: date | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    show_amount: bool | None = None
    status: Literal["draft", "published"] | None = None
    cover_image_url: str | None = Field(default=None, max_length=2000)
    gallery_image_urls: list[str] | None = None
    sort_order: int | None = None


class OnboardingCreate(BaseModel):
    profile_type: Literal[
        "company_owner", "independent_contractor", "investor", "company_worker"
    ]
    preferred_mode: Literal["expanded", "field"] = "expanded"
    company_name: str | None = Field(default=None, max_length=180)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    kind: Literal["company", "personal"] = "company"
    description: str = Field(default="", max_length=3000)
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=300)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=3000)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)


class WorkspaceMemberInvite(BaseModel):
    email: EmailStr
    role: Literal["admin", "member"] = "member"


class WorkerProfileCreate(BaseModel):
    label: str = Field(min_length=1, max_length=160)
    profile_kind: Literal["craftsman", "crew"] = "craftsman"
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=1000)
    workspace_id: str | None = None


class WorkerProfileUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=160)
    profile_kind: Literal["craftsman", "crew"] | None = None
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=1000)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    workspace_id: str | None = None
    worker_profile_id: str | None = None
    client_name: str | None = Field(default="", max_length=180)
    client_email: str | None = Field(default="", max_length=320)
    address: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=5000)
    template: str = "custom"
    stages: list[str] = Field(default_factory=list)
    planned_start_date: date | None = None
    planned_end_date: date | None = None
    schedule_uncertainty_days: int | None = None
    contract_amount: Decimal | None = None
    contract_currency: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    worker_profile_id: str | None = None
    client_name: str | None = Field(default=None, max_length=180)
    client_email: str | None = Field(default=None, max_length=320)
    address: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    status: Literal["assigned", "in_progress", "completed"] | None = None
    portfolio_enabled: bool | None = None
    portfolio_slug: str | None = Field(default=None, max_length=120)
    portfolio_summary: str | None = Field(default=None, max_length=3000)
    details_locked: bool | None = None
    planned_start_date: date | None = None
    planned_end_date: date | None = None
    schedule_uncertainty_days: int | None = None
    contract_amount: Decimal | None = None
    contract_currency: str | None = None


class JobPostingCreate(BaseModel):
    title: str = Field(min_length=2, max_length=220)
    description: str = Field(default="", max_length=5000)
    location: str = Field(min_length=2, max_length=220)
    budget_label: str = Field(default="", max_length=120)
    deadline: str = Field(default="", max_length=160)
    specializations: list[str] | None = None
    current_state_description: str = Field(default="", max_length=4000)
    target_contractor_type: Literal["company", "independent_contractor", "any"] = "any"
    status: Literal["draft", "published"] = "draft"


class JobPostingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=220)
    description: str | None = Field(default=None, max_length=5000)
    location: str | None = Field(default=None, min_length=2, max_length=220)
    budget_label: str | None = Field(default=None, max_length=120)
    deadline: str | None = Field(default=None, max_length=160)
    specializations: list[str] | None = None
    current_state_description: str | None = Field(default=None, max_length=4000)
    target_contractor_type: Literal["company", "independent_contractor", "any"] | None = None
    status: Literal["draft", "published"] | None = None


class JobPostingInterestCreate(BaseModel):
    message: str = Field(default="", max_length=1000)


class JobPostingInterestUpdate(BaseModel):
    status: Literal["new", "contact", "rejected"]


class JobPostingOfferCreate(BaseModel):
    title: str = Field(min_length=1, max_length=220)
    scope_summary: str = Field(default="", max_length=5000)
    assumptions: str = Field(default="", max_length=4000)
    estimated_price: Decimal | None = Field(default=None, ge=0)
    price_note: str = Field(default="", max_length=1000)
    planned_start: str = Field(default="", max_length=160)
    planned_end: str = Field(default="", max_length=160)
    status: Literal["draft", "sent"] = "draft"


class JobPostingOfferUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=220)
    scope_summary: str | None = Field(default=None, max_length=5000)
    assumptions: str | None = Field(default=None, max_length=4000)
    estimated_price: Decimal | None = Field(default=None, ge=0)
    price_note: str | None = Field(default=None, max_length=1000)
    planned_start: str | None = Field(default=None, max_length=160)
    planned_end: str | None = Field(default=None, max_length=160)
    status: Literal["draft", "sent"] | None = None


class JobPostingOfferInvestorUpdate(BaseModel):
    status: Literal["accepted", "rejected"]


EstimateOwnerType = Literal["independent_contractor", "company"]
EstimateRecipientType = Literal["manual", "investor", "client"]
EstimateSourceType = Literal["manual", "project", "job_posting"]
EstimateStatus = Literal[
    "draft",
    "pending_approval",
    "approved_by_owner",
    "sent",
    "accepted",
    "rejected",
    "cancelled",
]


class EstimateCreate(BaseModel):
    owner_type: EstimateOwnerType | None = None
    owner_id: str | None = None
    recipient_type: EstimateRecipientType = "manual"
    recipient_name: str = Field(default="", max_length=180)
    recipient_email: str = Field(default="", max_length=320)
    recipient_phone: str = Field(default="", max_length=40)
    source_type: EstimateSourceType = "manual"
    source_id: str | None = None
    title: str = Field(min_length=1, max_length=220)
    scope_summary: str = Field(default="", max_length=5000)
    assumptions: str = Field(default="", max_length=4000)
    estimated_price: Decimal | None = Field(default=None, ge=0)
    price_note: str = Field(default="", max_length=1000)
    planned_start: str = Field(default="", max_length=160)
    planned_end: str = Field(default="", max_length=160)
    status: Literal["draft", "pending_approval", "sent"] = "draft"


class EstimateUpdate(BaseModel):
    recipient_type: EstimateRecipientType | None = None
    recipient_name: str | None = Field(default=None, max_length=180)
    recipient_email: str | None = Field(default=None, max_length=320)
    recipient_phone: str | None = Field(default=None, max_length=40)
    source_type: EstimateSourceType | None = None
    source_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=220)
    scope_summary: str | None = Field(default=None, max_length=5000)
    assumptions: str | None = Field(default=None, max_length=4000)
    estimated_price: Decimal | None = Field(default=None, ge=0)
    price_note: str | None = Field(default=None, max_length=1000)
    planned_start: str | None = Field(default=None, max_length=160)
    planned_end: str | None = Field(default=None, max_length=160)


class EstimateStatusUpdate(BaseModel):
    status: EstimateStatus


class PublicEstimateDecision(BaseModel):
    status: Literal["accepted", "rejected"]


ProjectContractStatus = Literal[
    "draft",
    "pending_approval",
    "sent",
    "accepted",
    "rejected",
    "cancelled",
]


class ProjectContractUpdate(BaseModel):
    contractor_name: str | None = Field(default=None, max_length=180)
    contractor_email: str | None = Field(default=None, max_length=320)
    contractor_phone: str | None = Field(default=None, max_length=40)
    client_name: str | None = Field(default=None, max_length=180)
    client_email: str | None = Field(default=None, max_length=320)
    client_phone: str | None = Field(default=None, max_length=40)
    work_address: str | None = Field(default=None, max_length=300)
    project_name: str | None = Field(default=None, min_length=1, max_length=220)
    scope_summary: str | None = Field(default=None, max_length=7000)
    terms_summary: str | None = Field(default=None, max_length=5000)
    planned_start: str | None = Field(default=None, max_length=160)
    planned_end: str | None = Field(default=None, max_length=160)
    price_amount: Decimal | None = Field(default=None, ge=0)
    price_currency: str | None = Field(default=None, max_length=3)
    price_note: str | None = Field(default=None, max_length=2000)
    deposit_amount: Decimal | None = Field(default=None, ge=0)
    change_terms: str | None = Field(default=None, max_length=3000)
    attachments_note: str | None = Field(default=None, max_length=3000)
    legal_note: str | None = Field(default=None, max_length=3000)


class ProjectContractStatusUpdate(BaseModel):
    status: ProjectContractStatus


class PublicContractDecision(BaseModel):
    status: Literal["accepted", "rejected"]


ProjectFinalReportStatus = Literal[
    "draft",
    "pending_approval",
    "sent",
    "accepted",
    "rejected",
    "cancelled",
]


class ProjectFinalReportUpdate(BaseModel):
    contractor_name: str | None = Field(default=None, max_length=180)
    contractor_email: str | None = Field(default=None, max_length=320)
    contractor_phone: str | None = Field(default=None, max_length=40)
    client_name: str | None = Field(default=None, max_length=180)
    client_email: str | None = Field(default=None, max_length=320)
    client_phone: str | None = Field(default=None, max_length=40)
    work_address: str | None = Field(default=None, max_length=300)
    project_name: str | None = Field(default=None, min_length=1, max_length=220)
    work_summary: str | None = Field(default=None, max_length=7000)
    completed_scope: str | None = Field(default=None, max_length=7000)
    issues_and_solutions: str | None = Field(default=None, max_length=5000)
    materials_note: str | None = Field(default=None, max_length=4000)
    final_cost_amount: Decimal | None = Field(default=None, ge=0)
    final_cost_currency: str | None = Field(default=None, max_length=3)
    final_cost_note: str | None = Field(default=None, max_length=2000)
    started_at: str | None = Field(default=None, max_length=160)
    completed_at: str | None = Field(default=None, max_length=160)
    client_comment: str | None = Field(default=None, max_length=3000)
    warranty_note: str | None = Field(default=None, max_length=3000)
    attachments_note: str | None = Field(default=None, max_length=3000)
    legal_note: str | None = Field(default=None, max_length=3000)


class ProjectFinalReportStatusUpdate(BaseModel):
    status: ProjectFinalReportStatus


class PublicFinalReportDecision(BaseModel):
    status: Literal["accepted", "rejected"]


class GuestEstimateDraftCreate(BaseModel):
    title: str = Field(min_length=1, max_length=220)
    scope_summary: str = Field(default="", max_length=5000)
    assumptions: str = Field(default="", max_length=4000)
    estimated_price: Decimal | None = Field(default=None, ge=0)
    price_note: str = Field(default="", max_length=1000)
    planned_start: str = Field(default="", max_length=160)
    planned_end: str = Field(default="", max_length=160)
    contact_note: str = Field(default="", max_length=1000)


class ProjectClientCoverUpdate(BaseModel):
    media_id: str | None = None


class StageCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)


class StageUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    status: Literal["planned", "active", "completed"] | None = None
    position: int | None = Field(default=None, ge=0)


class ProjectInvitationCreate(BaseModel):
    email: EmailStr
    role: Literal["viewer", "contributor", "manager"] = "contributor"


class GuestInviteCreate(BaseModel):
    label: str = Field(default="Gość", max_length=160)
    email: str = Field(default="", max_length=320)
    worker_profile_id: str | None = None
    kind: Literal["guest", "worker"] = "guest"
    permission: Literal["add", "history", "view"] = "add"
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


class EntryCreate(BaseModel):
    kind: Literal["update", "problem"] = "update"
    body: str = Field(default="", max_length=10000)
    transcript: str = Field(default="", max_length=20000)
    stage_id: str | None = None
    occurred_at: datetime | None = None
    client_ref: str | None = Field(default=None, max_length=100)


class EntryUpdate(BaseModel):
    body: str | None = Field(default=None, max_length=10000)
    transcript: str | None = Field(default=None, max_length=20000)
    stage_id: str | None = None
    occurred_at: datetime | None = None
    problem_status: Literal["open", "resolved"] | None = None


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    intent: Literal["comment", "confirm_resolved", "still_open", "suggest_solution"] = "comment"


class PublicCommentCreate(BaseModel):
    body: str = Field(default="", max_length=1000)
    intent: Literal["comment", "confirm_resolved", "still_open", "suggest_solution"] = "comment"


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str | None = Field(default=None, min_length=2, max_length=220)
    report_type: Literal["periodic", "final"] = "periodic"
    period_from: datetime | None = None
    period_to: datetime | None = None


class ReportUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=220)
    content: dict[str, Any] | None = None


class ReportPublish(BaseModel):
    pin: str | None = Field(default=None, min_length=4, max_length=12)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ClientLinkUpdate(BaseModel):
    active: bool | None = None
    pin: str | None = Field(default=None, min_length=4, max_length=12)
    remove_pin: bool = False
    rotate: bool = False


class PinCheck(BaseModel):
    pin: str | None = None


def require_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    return current_user(request, db)


def workspace_payload(
    db: Session, workspace: models.Workspace, user_id: str, details: bool = False
) -> dict:
    membership = db.scalar(
        select(models.WorkspaceMember).where(
            models.WorkspaceMember.workspace_id == workspace.id,
            models.WorkspaceMember.user_id == user_id,
        )
    )
    data = {
        "id": workspace.id,
        "name": workspace.name,
        "kind": workspace.kind,
        "description": workspace.description,
        "phone": workspace.phone,
        "address": workspace.address,
        "role": membership.role if membership else None,
    }
    if details:
        members = db.scalars(
            select(models.WorkspaceMember)
            .options(selectinload(models.WorkspaceMember.user))
            .where(models.WorkspaceMember.workspace_id == workspace.id)
            .order_by(models.WorkspaceMember.created_at)
        ).all()
        data["members"] = [
            {
                "id": member.id,
                "role": member.role,
                "user": serializers.user(member.user),
            }
            for member in members
        ]
        worker_profiles = db.scalars(
            select(models.WorkerProfile)
            .where(models.WorkerProfile.workspace_id == workspace.id)
            .order_by(models.WorkerProfile.created_at.desc())
        ).all()
        data["worker_profiles"] = [
            worker_profile_payload(db, item) for item in worker_profiles
        ]
        worker_links = db.scalars(
            select(models.GuestInvite)
            .where(
                models.GuestInvite.workspace_id == workspace.id,
                models.GuestInvite.kind == "worker",
            )
            .order_by(models.GuestInvite.created_at.desc())
        ).all()
        data["worker_links"] = [
            guest_invite_payload(db, item, include_project=True)
            for item in worker_links
        ]
    return data


def optional_email(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return normalize_email(validate_email(raw, check_deliverability=False).normalized)
    except EmailNotValidError:
        raise HTTPException(422, "Nieprawidłowy adres e-mail")


def create_session_response(
    db: Session, response: Response, user: models.User
) -> dict:
    user.last_login_at = now()
    accept_pending_invitations(db, user)
    raw_token = random_token()
    db.add(
        models.UserSession(
            token_hash=hash_secret(raw_token),
            user_id=user.id,
            expires_at=now() + timedelta(days=settings.session_days),
        )
    )
    db.commit()
    response.set_cookie(
        "pm_session",
        raw_token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )
    return {"user": user_payload(db, user)}


def demo_admin_password() -> str:
    if settings.demo_admin_password:
        return settings.demo_admin_password
    if not settings.is_production:
        return "Abecede123"
    return ""


def demo_reset_allowed() -> bool:
    return (
        settings.allow_demo_reset
        or os.getenv("ALLOW_DEMO_RESET") == "1"
        or os.getenv("PANMAJSTER_ALLOW_DEMO_RESET") == "1"
    )


def demo_admin_accounts_payload() -> list[dict[str, str]]:
    labels = {
        "szef@majster.pl": "Szef firmy",
        "inwestor@majster.pl": "Inwestor",
        "samodzielny@majster.pl": "Samodzielny majster",
        "pracownik@majster.pl": "Pracownik firmy",
        "pracownik2@majster.pl": "Pracownik firmy 2",
    }
    return [
        {"email": email, "password": DEMO_PASSWORD, "label": labels.get(email, "Konto demo")}
        for email in sorted(DEMO_EMAILS)
    ]


def create_demo_admin_token(username: str) -> str:
    payload = {
        "u": username,
        "exp": int(time.time()) + 60 * 60,
        "n": random_token(8),
    }
    raw = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"{raw}.{hash_secret(DEMO_ADMIN_TOKEN_PREFIX + raw)}"


def verify_demo_admin_token(token: str) -> dict[str, Any]:
    try:
        raw, signature = token.split(".", 1)
        if not verify_secret(DEMO_ADMIN_TOKEN_PREFIX + raw, signature):
            raise ValueError("invalid signature")
        payload = json.loads(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        )
    except Exception as exc:
        raise HTTPException(401, "Nieprawidłowy token panelu demo") from exc
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(401, "Sesja panelu demo wygasła")
    if payload.get("u") != settings.demo_admin_user:
        raise HTTPException(401, "Nieprawidłowy token panelu demo")
    return payload


def require_demo_admin(request: Request) -> None:
    if not settings.demo_admin_enabled:
        raise HTTPException(403, "Panel demo jest wyłączony")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        token = request.headers.get("x-demo-admin-token", "")
    if not token:
        raise HTTPException(401, "Brak tokenu panelu demo")
    verify_demo_admin_token(token)


def database_fingerprint() -> str:
    digest = hash_secret(settings.normalized_database_url)[:8]
    return f"db_{digest}"


def demo_user_visibility(db: Session) -> dict[str, Any]:
    users = db.scalars(
        select(models.User).where(models.User.email.in_(DEMO_EMAILS))
    ).all()
    users_by_email = {item.email: item for item in users}
    project_counts: dict[str, int] = {}
    entry_counts: dict[str, int] = {}
    owner_counts: dict[str, int] = {}
    demo_users = []
    for email in sorted(DEMO_EMAILS):
        user = users_by_email.get(email)
        if not user:
            project_counts[email] = 0
            entry_counts[email] = 0
            owner_counts[email] = 0
            continue
        visible_projects = db.execute(user_projects_query(user.id)).all()
        project_ids = [project.id for project, _role in visible_projects]
        project_counts[email] = len(project_ids)
        entry_counts[email] = int(
            db.scalar(
                select(func.count(models.Entry.id)).where(
                    models.Entry.project_id.in_(project_ids)
                )
            )
            if project_ids
            else 0
        )
        owner_counts[email] = int(
            db.scalar(
                select(func.count(models.Project.id)).where(
                    models.Project.created_by_id == user.id
                )
            )
            or 0
        )
        demo_users.append(
            {
                "id": user.id,
                "email": user.email,
                "role": user.profile_type or "",
                "name": user.name,
            }
        )
    client_links = int(
        db.scalar(
            select(func.count(models.Project.id)).where(
                models.Project.client_share_active.is_(True),
                models.Project.client_share_token.is_not(None),
            )
        )
        or 0
    )
    guest_links = int(
        db.scalar(
            select(func.count(models.GuestInvite.id)).where(
                models.GuestInvite.revoked_at.is_(None)
            )
        )
        or 0
    )
    return {
        "database_fingerprint": database_fingerprint(),
        "app_env": settings.app_env,
        "storage": storage.provider,
        "reset_backend_marker": "pan-majster-api",
        "demo_users_found": len(users),
        "demo_accounts": demo_users,
        "projects_after_reset_by_owner": owner_counts,
        "projects_visible_by_user": project_counts,
        "entries_visible_by_user": entry_counts,
        "workspace_count": int(db.scalar(select(func.count(models.Workspace.id))) or 0),
        "client_links": client_links,
        "guest_links": guest_links,
    }


def project_role_from_guest_permission(permission: str) -> str:
    return "viewer" if permission == "view" else "contributor"


def default_entry_stage_id(db: Session, project_id: str) -> str | None:
    preferred = db.scalar(
        select(models.ProjectStage.id).where(
            models.ProjectStage.project_id == project_id,
            models.ProjectStage.title == DEFAULT_ENTRY_STAGE_TITLE,
        )
    )
    if preferred:
        return preferred
    return db.scalar(
        select(models.ProjectStage.id)
        .where(models.ProjectStage.project_id == project_id)
        .order_by(models.ProjectStage.position)
    )


def set_final_project_stage_current(project: models.Project) -> None:
    stages = sorted(project.stages or [], key=lambda stage: stage.position)
    if not stages:
        return
    final_stage = stages[-1]
    for stage in stages:
        if stage.position < final_stage.position:
            stage.status = "completed"
        elif stage.id == final_stage.id:
            stage.status = "active"
        else:
            stage.status = "planned"


def worker_profile_payload(db: Session, item: models.WorkerProfile) -> dict:
    projects = db.scalars(
        select(models.Project)
        .where(models.Project.worker_profile_id == item.id)
        .order_by(models.Project.updated_at.desc())
    ).all()
    pending_invitation = None
    existing_user = None
    if item.email:
        existing_user = db.scalar(select(models.User).where(models.User.email == item.email))
        pending_invitation = db.scalar(
            select(models.Invitation).where(
                models.Invitation.email == item.email,
                models.Invitation.workspace_id == item.workspace_id,
                models.Invitation.accepted_at.is_(None),
                models.Invitation.revoked_at.is_(None),
            )
        )
    account_status = "link_only"
    if item.email and existing_user:
        account_status = "active"
    elif item.email and pending_invitation:
        account_status = "pending_email"
    elif item.email:
        account_status = "email_missing_invite"
    return {
        "id": item.id,
        "label": item.label,
        "profile_kind": item.profile_kind,
        "email": item.email,
        "phone": item.phone,
        "note": item.note,
        "workspace_id": item.workspace_id,
        "active": item.active,
        "account_type": "account" if item.email else "link_only",
        "account_status": account_status,
        "display_type": (
            "Ekipa"
            if item.profile_kind == "crew"
            else "Majster - czlonek firmy"
            if item.email
            else "Majster link-only"
        ),
        "assigned_projects": [
            {"id": project.id, "name": project.name, "status": project.status}
            for project in projects
        ],
        "created_at": serializers.iso(item.created_at),
        "updated_at": serializers.iso(item.updated_at),
    }


def guest_invite_payload(
    db: Session, item: models.GuestInvite, include_project: bool = False
) -> dict:
    data = {
        "id": item.id,
        "label": item.label,
        "email": item.email,
        "kind": item.kind,
        "account_type": "account" if item.email else "link_only",
        "permission": item.permission,
        "project_id": item.project_id,
        "worker_profile_id": item.worker_profile_id,
        "expires_at": serializers.iso(item.expires_at),
        "revoked_at": serializers.iso(item.revoked_at),
        "created_at": serializers.iso(item.created_at),
    }
    if include_project:
        project = db.get(models.Project, item.project_id)
        data["project_name"] = project.name if project else ""
    return data


def project_payload(
    db: Session, item: models.Project, role: str | None = None, details: bool = False
) -> dict:
    data = serializers.project(item, role=role, details=details)
    data["public_contractor_name"] = serializers.public_contractor_name(db, item)
    return data


def worker_profile_for_assignment(
    db: Session,
    worker_profile_id: str | None,
    user: models.User,
    workspace_id: str | None,
) -> models.WorkerProfile | None:
    if not worker_profile_id:
        return None
    worker = db.get(models.WorkerProfile, worker_profile_id)
    if not worker:
        raise HTTPException(404, "Nie znaleziono wykonawcy")
    if not worker.active:
        raise HTTPException(422, "Ten majster lub ekipa jest dezaktywowana")
    if worker.workspace_id:
        if workspace_id and worker.workspace_id != workspace_id:
            raise HTTPException(422, "Wykonawca jest przypisany do innej firmy")
        if not can_manage_workspace(db, worker.workspace_id, user.id):
            raise HTTPException(403, "Brak dostępu do wykonawcy")
    elif worker.owner_id != user.id:
        raise HTTPException(403, "Brak dostępu do wykonawcy")
    return worker


def ensure_worker_project_access(
    db: Session,
    project: models.Project,
    worker: models.WorkerProfile | None,
    invited_by_id: str,
) -> None:
    if not worker or not worker.email:
        return
    existing_user = db.scalar(select(models.User).where(models.User.email == worker.email))
    if existing_user:
        if existing_user.profile_type in {None, "", "worker"}:
            existing_user.profile_type = "company_worker"
        member = db.scalar(
            select(models.ProjectMember).where(
                models.ProjectMember.project_id == project.id,
                models.ProjectMember.user_id == existing_user.id,
            )
        )
        if member:
            member.role = "contributor"
        else:
            db.add(
                models.ProjectMember(
                    project_id=project.id,
                    user_id=existing_user.id,
                    role="contributor",
                )
            )
        return
    pending = db.scalar(
        select(models.Invitation).where(
            models.Invitation.project_id == project.id,
            models.Invitation.email == worker.email,
            models.Invitation.accepted_at.is_(None),
            models.Invitation.revoked_at.is_(None),
        )
    )
    if not pending:
        db.add(
            models.Invitation(
                project_id=project.id,
                email=worker.email,
                role="contributor",
                token_hash=hash_secret(random_token()),
                invited_by_id=invited_by_id,
                expires_at=now() + timedelta(days=14),
                accepted_at=None,
            )
        )


def available_worker_profiles(
    db: Session, user: models.User, workspace_id: str | None = None
) -> list[models.WorkerProfile]:
    if workspace_id:
        if not can_manage_workspace(db, workspace_id, user.id):
            raise HTTPException(403, "Brak dostępu do wykonawców")
        return db.scalars(
            select(models.WorkerProfile)
            .where(
                models.WorkerProfile.workspace_id == workspace_id,
                models.WorkerProfile.active.is_(True),
            )
            .order_by(models.WorkerProfile.created_at.desc())
        ).all()
    workspace_ids = [
        row[0]
        for row in db.execute(
            select(models.WorkspaceMember.workspace_id).where(
                models.WorkspaceMember.user_id == user.id,
                models.WorkspaceMember.role.in_(["owner", "admin"]),
            )
        ).all()
    ]
    conditions = [models.WorkerProfile.owner_id == user.id]
    if workspace_ids:
        conditions.append(models.WorkerProfile.workspace_id.in_(workspace_ids))
    return db.scalars(
        select(models.WorkerProfile)
        .where(or_(*conditions), models.WorkerProfile.active.is_(True))
        .order_by(models.WorkerProfile.created_at.desc())
    ).all()


def user_payload(db: Session, user: models.User) -> dict:
    workspaces = db.scalars(
        select(models.Workspace)
        .join(
            models.WorkspaceMember,
            models.WorkspaceMember.workspace_id == models.Workspace.id,
        )
        .where(models.WorkspaceMember.user_id == user.id)
        .order_by(models.Workspace.name)
    ).all()
    entitlement = db.scalar(
        select(models.BetaEntitlement).where(models.BetaEntitlement.user_id == user.id)
    )
    return {
        **serializers.user(user),
        "workspaces": [workspace_payload(db, item, user.id) for item in workspaces],
        "beta_access": bool(
            entitlement
            and entitlement.active
            and active_date(entitlement.expires_at)
        ),
    }


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.scalar(select(func.count(models.User.id)))
    return {
        "status": "ok",
        "service": "pan-majster",
        "storage": storage.provider,
    }


@router.get("/version")
def version():
    return {
        "service": "pan-majster",
        "build": "render-pan-majster",
        "commit_hint": "3acf140-or-newer",
        "features": {
            "password_login": True,
            "contract_terms_5d": True,
            "progress_stage_5c": True,
        },
    }


@router.post("/demo-admin/login")
def demo_admin_login(payload: DemoAdminLogin):
    if not settings.demo_admin_enabled:
        raise HTTPException(403, "Panel demo jest wyłączony")
    expected_password = demo_admin_password()
    if (
        not expected_password
        or payload.username != settings.demo_admin_user
        or not hmac.compare_digest(payload.password, expected_password)
    ):
        raise HTTPException(403, "Nieprawidłowy login albo hasło panelu demo")
    return {
        "token": create_demo_admin_token(payload.username),
        "demo_accounts": demo_admin_accounts_payload(),
        "reset_enabled": demo_reset_allowed(),
    }


@router.get("/demo-admin/status")
def demo_admin_status(request: Request, db: Session = Depends(get_db)):
    require_demo_admin(request)
    return {
        "status": "ok",
        "enabled": settings.demo_admin_enabled,
        "reset_enabled": demo_reset_allowed(),
        "diagnostics": demo_user_visibility(db),
        "demo_accounts": demo_admin_accounts_payload(),
    }


@router.post("/demo-admin/reset")
def demo_admin_reset(
    payload: DemoAdminReset,
    request: Request,
    db: Session = Depends(get_db),
):
    require_demo_admin(request)
    if not demo_reset_allowed():
        raise HTTPException(403, "Reset demo wymaga ALLOW_DEMO_RESET=1")
    if payload.confirmation != "RESET DEMO":
        raise HTTPException(400, "Wpisz dokładnie RESET DEMO")
    demo_users_before = int(
        db.scalar(
            select(func.count(models.User.id)).where(models.User.email.in_(DEMO_EMAILS))
        )
        or 0
    )
    result = seed_demo_data(db, reset=True, yes=True)
    diagnostics = demo_user_visibility(db)
    diagnostics["demo_users_created"] = max(
        0, int(diagnostics["demo_users_found"]) - demo_users_before
    )
    return {
        "status": "ok",
        "counts": result.counts,
        "company_statuses": result.company_statuses,
        "independent_statuses": result.independent_statuses,
        "investor_statuses": result.investor_statuses,
        "guest_links": result.guest_links,
        "client_links": result.client_links,
        "demo_accounts": demo_admin_accounts_payload(),
        "diagnostics": diagnostics,
        "note": "Dane demo zostały odtworzone. Raporty PDF zostają do wygenerowania ręcznie.",
    }


@router.post("/auth/request-code")
def request_code(payload: OtpRequest, request: Request, db: Session = Depends(get_db)):
    email = normalize_email(str(payload.email))
    recent = db.scalar(
        select(func.count(models.OtpCode.id)).where(
            models.OtpCode.email == email,
            models.OtpCode.created_at > now() - timedelta(minutes=15),
        )
    )
    if recent and recent >= 5:
        raise HTTPException(429, "Za dużo prób. Spróbuj ponownie za kilka minut.")

    code = otp_code()
    db.add(
        models.OtpCode(
            email=email,
            code_hash=hash_secret(code),
            expires_at=now() + timedelta(minutes=settings.otp_minutes),
        )
    )
    db.commit()
    delivered = send_otp(email, code)
    response: dict[str, Any] = {
        "ok": True,
        "delivered": delivered,
        "message": "Kod został wysłany na podany adres.",
    }
    if not settings.is_production:
        response["dev_code"] = code
    elif not delivered:
        raise HTTPException(503, "Wysyłka e-mail nie jest jeszcze skonfigurowana")
    return response


def accept_pending_invitations(db: Session, user: models.User) -> None:
    accepted_worker_invite = False
    for invitation in find_pending_invitations(db, user.email):
        if invitation.project_id:
            existing = db.scalar(
                select(models.ProjectMember).where(
                    models.ProjectMember.project_id == invitation.project_id,
                    models.ProjectMember.user_id == user.id,
                )
            )
            if not existing:
                db.add(
                    models.ProjectMember(
                        project_id=invitation.project_id,
                        user_id=user.id,
                        role=invitation.role,
                    )
                )
        if invitation.workspace_id:
            existing = db.scalar(
                select(models.WorkspaceMember).where(
                    models.WorkspaceMember.workspace_id == invitation.workspace_id,
                    models.WorkspaceMember.user_id == user.id,
                )
            )
            if not existing:
                db.add(
                    models.WorkspaceMember(
                        workspace_id=invitation.workspace_id,
                        user_id=user.id,
                        role=invitation.role,
                    )
                )
            if invitation.role in {"member", "admin"}:
                accepted_worker_invite = True
        invitation.accepted_at = now()
    if accepted_worker_invite and user.profile_type in {None, "", "worker"}:
        user.profile_type = "company_worker"


@router.get("/invitations/{token}")
def invitation_details(token: str, db: Session = Depends(get_db)):
    invitation = db.scalar(
        select(models.Invitation).where(
            models.Invitation.token_hash == hash_secret(token),
            models.Invitation.revoked_at.is_(None),
        )
    )
    if not invitation or not active_date(invitation.expires_at):
        raise HTTPException(404, "Zaproszenie jest nieaktywne lub wygasło")
    project = db.get(models.Project, invitation.project_id) if invitation.project_id else None
    workspace = (
        db.get(models.Workspace, invitation.workspace_id)
        if invitation.workspace_id
        else (db.get(models.Workspace, project.workspace_id) if project and project.workspace_id else None)
    )
    return {
        "email": invitation.email,
        "role": invitation.role,
        "kind": "workspace" if invitation.workspace_id else "project",
        "project_name": project.name if project else "",
        "workspace_name": workspace.name if workspace else "",
    }


@router.post("/auth/verify")
def verify_code(payload: OtpVerify, response: Response, db: Session = Depends(get_db)):
    email = normalize_email(str(payload.email))
    otp = db.scalar(
        select(models.OtpCode)
        .where(
            models.OtpCode.email == email,
            models.OtpCode.consumed_at.is_(None),
        )
        .order_by(models.OtpCode.created_at.desc())
    )
    if not otp or not active_date(otp.expires_at) or otp.attempts >= 5:
        raise HTTPException(400, "Kod wygasł. Poproś o nowy.")
    otp.attempts += 1
    if not verify_secret(payload.code, otp.code_hash):
        db.commit()
        raise HTTPException(400, "Nieprawidłowy kod")
    otp.consumed_at = now()

    user = db.scalar(select(models.User).where(models.User.email == email))
    if not user:
        user = models.User(
            email=email,
            is_admin=email in settings.admin_email_set,
        )
        db.add(user)
        db.flush()
        db.add(models.BetaEntitlement(user_id=user.id, active=True, note="Tester MVP"))
    return create_session_response(db, response, user)


@router.post("/auth/password")
def password_login(
    payload: PasswordLogin, response: Response, db: Session = Depends(get_db)
):
    email = normalize_email(str(payload.email))
    user = db.scalar(select(models.User).where(models.User.email == email))
    if not user or not user.password_hash:
        raise HTTPException(400, "Nieprawidłowy email albo hasło")
    if not verify_secret(payload.password, user.password_hash):
        raise HTTPException(400, "Nieprawidłowy email albo hasło")
    return create_session_response(db, response, user)


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw_token = request.cookies.get("pm_session")
    if raw_token:
        session = db.scalar(
            select(models.UserSession).where(
                models.UserSession.token_hash == hash_secret(raw_token)
            )
        )
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie("pm_session")
    return {"ok": True}


@router.get("/me")
def me(user: models.User = Depends(require_user), db: Session = Depends(get_db)):
    return user_payload(db, user)


@router.patch("/me")
def update_me(
    payload: UserUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    for key, value in payload.model_dump(exclude_unset=True).items():
        if isinstance(value, str):
            value = value.strip()
        setattr(user, key, value or "")
    db.commit()
    return user_payload(db, user)


@router.get("/job-postings/me")
def list_my_job_postings(
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not is_investor(user):
        raise HTTPException(403, "Tylko inwestor moze zarzadzac ogloszeniami")
    items = db.scalars(
        select(models.JobPosting)
        .where(models.JobPosting.investor_id == user.id)
        .order_by(models.JobPosting.updated_at.desc())
    ).all()
    return [
        job_posting_payload(
            item,
            interests=job_posting_interests_for(db, item.id),
            offers=job_posting_offers_for(db, item.id, investor_view=True),
            db=db,
        )
        for item in items
    ]


@router.post("/job-postings/me", status_code=201)
def create_my_job_posting(
    payload: JobPostingCreate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not is_investor(user):
        raise HTTPException(403, "Tylko inwestor moze oglosic zlecenie")
    item = models.JobPosting(investor_id=user.id)
    apply_job_posting_changes(item, payload)
    db.add(item)
    db.commit()
    db.refresh(item)
    return job_posting_payload(item, interests=[], offers=[], db=db)


@router.patch("/job-postings/me/{posting_id}")
def update_my_job_posting(
    posting_id: str,
    payload: JobPostingUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not is_investor(user):
        raise HTTPException(403, "Tylko inwestor moze zarzadzac ogloszeniami")
    item = db.get(models.JobPosting, posting_id)
    if not item or item.investor_id != user.id:
        raise HTTPException(404, "Ogloszenie nie istnieje")
    apply_job_posting_changes(item, payload, partial=True)
    db.commit()
    db.refresh(item)
    return job_posting_payload(
        item,
        interests=job_posting_interests_for(db, item.id),
        offers=job_posting_offers_for(db, item.id, investor_view=True),
        db=db,
    )


@router.get("/job-postings/me/interests")
def list_my_job_posting_interests(
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not is_investor(user):
        raise HTTPException(403, "Tylko inwestor widzi zainteresowania do ogloszen")
    items = db.scalars(
        select(models.JobPostingInterest)
        .join(models.JobPosting, models.JobPosting.id == models.JobPostingInterest.job_posting_id)
        .where(models.JobPosting.investor_id == user.id)
        .order_by(models.JobPostingInterest.created_at.desc())
    ).all()
    return [
        {
            **job_posting_interest_payload(
                item,
                profile=db.get(models.PublicProfile, item.public_profile_id),
                include_contact=True,
            ),
            "job_posting": job_posting_payload(db.get(models.JobPosting, item.job_posting_id), public=True),
        }
        for item in items
    ]


@router.patch("/job-postings/me/interests/{interest_id}")
def update_my_job_posting_interest(
    interest_id: str,
    payload: JobPostingInterestUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not is_investor(user):
        raise HTTPException(403, "Tylko inwestor moze zmieniac status zainteresowania")
    item = db.get(models.JobPostingInterest, interest_id)
    if not item:
        raise HTTPException(404, "Zainteresowanie nie istnieje")
    posting = db.get(models.JobPosting, item.job_posting_id)
    if not posting or posting.investor_id != user.id:
        raise HTTPException(404, "Zainteresowanie nie istnieje")
    item.status = payload.status
    db.commit()
    db.refresh(item)
    return job_posting_interest_payload(
        item,
        profile=db.get(models.PublicProfile, item.public_profile_id),
        include_contact=True,
    )


@router.get("/job-posting-interests/me/context")
def get_my_job_interest_context(
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return job_interest_profile_context(db, user)


@router.get("/job-postings/public")
def list_public_job_postings(
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not (is_company_owner(user) or is_independent_contractor(user)):
        raise HTTPException(403, "Tylko wykonawcy moga przegladac opublikowane zlecenia")
    owner_type, owner_id = contractor_interest_identity(db, user)
    items = db.scalars(
        select(models.JobPosting)
        .where(models.JobPosting.status == "published")
        .order_by(models.JobPosting.published_at.desc(), models.JobPosting.updated_at.desc())
    ).all()
    interests = db.scalars(
        select(models.JobPostingInterest).where(
            models.JobPostingInterest.contractor_owner_type == owner_type,
            models.JobPostingInterest.contractor_owner_id == owner_id,
        )
    ).all()
    offers = db.scalars(
        select(models.JobPostingOffer).where(
            models.JobPostingOffer.contractor_owner_type == owner_type,
            models.JobPostingOffer.contractor_owner_id == owner_id,
        )
    ).all()
    interest_by_posting = {item.job_posting_id: item for item in interests}
    offer_by_posting = {item.job_posting_id: item for item in offers}
    return [
        job_posting_payload(
            item,
            public=True,
            my_interest=interest_by_posting.get(item.id),
            my_offer=offer_by_posting.get(item.id),
        )
        for item in items
    ]


@router.post("/job-postings/public/{posting_id}/interest", status_code=201)
def create_job_posting_interest(
    posting_id: str,
    payload: JobPostingInterestCreate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    posting = db.get(models.JobPosting, posting_id)
    if not posting or posting.status != "published":
        raise HTTPException(422, "Zainteresowanie mozna zglosic tylko do opublikowanego ogloszenia")
    owner_type, owner_id, profile = ready_public_profile_for_interest(db, user)
    existing = db.scalar(
        select(models.JobPostingInterest).where(
            models.JobPostingInterest.job_posting_id == posting.id,
            models.JobPostingInterest.contractor_owner_type == owner_type,
            models.JobPostingInterest.contractor_owner_id == owner_id,
        )
    )
    if existing:
        raise HTTPException(409, "Zainteresowanie zostalo juz zgloszone")
    item = models.JobPostingInterest(
        job_posting_id=posting.id,
        contractor_owner_type=owner_type,
        contractor_owner_id=owner_id,
        public_profile_id=profile.id,
        message=(payload.message or "").strip(),
        status="new",
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Zainteresowanie zostalo juz zgloszone") from exc
    db.refresh(item)
    return job_posting_interest_payload(item)


@router.get("/job-posting-offers/me")
def list_my_job_posting_offers(
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    owner_type, owner_id = contractor_interest_identity(db, user)
    items = db.scalars(
        select(models.JobPostingOffer)
        .where(
            models.JobPostingOffer.contractor_owner_type == owner_type,
            models.JobPostingOffer.contractor_owner_id == owner_id,
        )
        .order_by(
            models.JobPostingOffer.updated_at.desc(),
            models.JobPostingOffer.created_at.desc(),
        )
    ).all()
    return [
        job_posting_offer_payload(
            item,
            job_posting=db.get(models.JobPosting, item.job_posting_id),
        )
        for item in items
    ]


@router.post("/job-postings/public/{posting_id}/offer", status_code=201)
def create_job_posting_offer(
    posting_id: str,
    payload: JobPostingOfferCreate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    posting = db.get(models.JobPosting, posting_id)
    if not posting or posting.status != "published":
        raise HTTPException(422, "Oferte mozna przygotowac tylko do opublikowanego ogloszenia")
    owner_type, owner_id = contractor_interest_identity(db, user)
    interest = contractor_interest_for_offer(db, posting.id, owner_type, owner_id)
    if not interest:
        raise HTTPException(422, "Najpierw zglos zainteresowanie tym zleceniem")
    existing = contractor_offer_for_posting(db, posting.id, owner_type, owner_id)
    if existing:
        raise HTTPException(409, "Oferta do tego ogloszenia juz istnieje")

    item = models.JobPostingOffer(
        job_posting_id=posting.id,
        interest_id=interest.id,
        contractor_owner_type=owner_type,
        contractor_owner_id=owner_id,
        public_profile_id=interest.public_profile_id,
    )
    apply_job_posting_offer_changes(item, payload)
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Oferta do tego ogloszenia juz istnieje") from exc
    db.refresh(item)
    return job_posting_offer_payload(item)


@router.patch("/job-posting-offers/me/{offer_id}")
def update_my_job_posting_offer(
    offer_id: str,
    payload: JobPostingOfferUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    owner_type, owner_id = contractor_interest_identity(db, user)
    item = db.get(models.JobPostingOffer, offer_id)
    if (
        not item
        or item.contractor_owner_type != owner_type
        or item.contractor_owner_id != owner_id
    ):
        raise HTTPException(404, "Oferta nie istnieje")
    if item.status != "draft":
        raise HTTPException(422, "Po wyslaniu oferta nie jest edytowalna")
    apply_job_posting_offer_changes(item, payload, partial=True)
    db.commit()
    db.refresh(item)
    return job_posting_offer_payload(item)


@router.get("/job-postings/me/offers")
def list_my_job_posting_offers_as_investor(
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not is_investor(user):
        raise HTTPException(403, "Tylko inwestor widzi oferty do swoich ogloszen")
    items = db.scalars(
        select(models.JobPostingOffer)
        .join(models.JobPosting, models.JobPosting.id == models.JobPostingOffer.job_posting_id)
        .where(
            models.JobPosting.investor_id == user.id,
            models.JobPostingOffer.status != "draft",
        )
        .order_by(
            models.JobPostingOffer.updated_at.desc(),
            models.JobPostingOffer.created_at.desc(),
        )
    ).all()
    return [
        job_posting_offer_payload(
            item,
            profile=db.get(models.PublicProfile, item.public_profile_id),
            include_contact=True,
            job_posting=db.get(models.JobPosting, item.job_posting_id),
        )
        for item in items
    ]


@router.patch("/job-postings/me/offers/{offer_id}")
def update_my_job_posting_offer_as_investor(
    offer_id: str,
    payload: JobPostingOfferInvestorUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not is_investor(user):
        raise HTTPException(403, "Tylko inwestor moze zmieniac status ofert")
    item = db.get(models.JobPostingOffer, offer_id)
    if not item:
        raise HTTPException(404, "Oferta nie istnieje")
    posting = db.get(models.JobPosting, item.job_posting_id)
    if not posting or posting.investor_id != user.id:
        raise HTTPException(404, "Oferta nie istnieje")
    if item.status == "draft":
        raise HTTPException(422, "Szkic oferty nie jest widoczny dla inwestora")
    item.status = payload.status
    if payload.status == "accepted":
        item.accepted_at = item.accepted_at or now()
        item.rejected_at = None
    else:
        item.rejected_at = item.rejected_at or now()
        item.accepted_at = None
    db.commit()
    db.refresh(item)
    return job_posting_offer_payload(
        item,
        profile=db.get(models.PublicProfile, item.public_profile_id),
        include_contact=True,
    )


@router.get("/estimates/me")
def list_my_estimates(
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return [estimate_payload(item) for item in visible_estimates_for_user(db, user)]


@router.post("/estimates/me", status_code=201)
def create_my_estimate(
    payload: EstimateCreate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    owner_type, owner_id, actor = resolve_estimate_owner(
        db, user, payload.owner_type, payload.owner_id
    )
    item = models.Estimate(
        owner_type=owner_type,
        owner_id=owner_id,
        created_by_id=user.id,
        draft_origin="worker" if actor == "company_worker" else "manual",
        draft_origin_label="od pracownika" if actor == "company_worker" else "",
    )
    apply_estimate_changes(item, payload, db=db, actor=actor, user=user)
    apply_initial_estimate_status(db, item, payload.status, actor, user)
    db.add(item)
    db.commit()
    db.refresh(item)
    return estimate_payload(item)


@router.post("/projects/{project_id}/guest-estimate-draft", status_code=201)
def create_guest_project_estimate_draft(
    project_id: str,
    payload: GuestEstimateDraftCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    access = guest_document_draft_access(request, db, project_id)
    project = access.project
    item = models.Estimate(
        owner_type="company",
        owner_id=project.workspace_id,
        created_by_id=access.guest.created_by_id,
        recipient_type="client",
        recipient_name=project.client_name or "",
        recipient_email=project.client_email or "",
        recipient_phone="",
        source_type="project",
        source_id=project.id,
        project_id=None,
        draft_origin="guest_link",
        draft_origin_label=draft_origin_label_from_guest(access.guest),
        title=payload.title.strip(),
        scope_summary=payload.scope_summary.strip(),
        assumptions=(
            payload.assumptions.strip()
            + (f"\n\nKontakt/notatka ekipy: {payload.contact_note.strip()}" if payload.contact_note.strip() else "")
        ).strip(),
        estimated_price=payload.estimated_price,
        price_note=payload.price_note.strip(),
        planned_start=payload.planned_start.strip(),
        planned_end=payload.planned_end.strip(),
        status="pending_approval",
    )
    validate_estimate_source(
        db,
        item.owner_type,
        item.owner_id,
        item.source_type,
        item.source_id,
        actor="guest_link",
        user=None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return guest_estimate_draft_payload(item)


@router.patch("/estimates/me/{estimate_id}")
def update_my_estimate(
    estimate_id: str,
    payload: EstimateUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.Estimate, estimate_id)
    if not item:
        raise HTTPException(404, "Oferta nie istnieje")
    actor = estimate_actor_for_item(db, user, item)
    ensure_estimate_editable(item, actor)
    apply_estimate_changes(item, payload, db=db, actor=actor, user=user, partial=True)
    db.commit()
    db.refresh(item)
    return estimate_payload(item)


@router.patch("/estimates/me/{estimate_id}/status")
def update_my_estimate_status(
    estimate_id: str,
    payload: EstimateStatusUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.Estimate, estimate_id)
    if not item:
        raise HTTPException(404, "Oferta nie istnieje")
    actor = estimate_actor_for_item(db, user, item)
    change_estimate_status(db, item, payload.status, actor, user)
    db.commit()
    db.refresh(item)
    return estimate_payload(item)


@router.post("/estimates/me/{estimate_id}/project", status_code=201)
def create_project_from_estimate(
    estimate_id: str,
    response: Response,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.scalar(
        select(models.Estimate)
        .where(models.Estimate.id == estimate_id)
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, "Oferta nie istnieje")

    actor = estimate_actor_for_item(db, user, item)
    if actor not in {"independent_contractor", "company_owner"}:
        raise HTTPException(403, "Tylko wykonawca albo szef firmy moze utworzyc zlecenie z oferty")
    if item.status != "accepted":
        raise HTTPException(422, "Zlecenie mozna utworzyc tylko z zaakceptowanej oferty")

    if item.project_id:
        project = db.get(models.Project, item.project_id)
        if project:
            response.status_code = 200
            return {
                "created": False,
                "project": project_payload(db, project, role="owner", details=True),
                "estimate": estimate_payload(item),
            }
        item.project_id = None
        db.flush()

    project = build_project_from_estimate(db, item, user)
    item.project_id = project.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        db.refresh(item)
        if item.project_id:
            project = db.get(models.Project, item.project_id)
            if project:
                response.status_code = 200
                return {
                    "created": False,
                    "project": project_payload(db, project, role="owner", details=True),
                    "estimate": estimate_payload(item),
                }
        raise HTTPException(409, "Zlecenie dla tej oferty juz istnieje")

    db.refresh(project)
    db.refresh(item)
    return {
        "created": True,
        "project": project_payload(db, project, role="owner", details=True),
        "estimate": estimate_payload(item),
    }


@router.delete("/estimates/me/{estimate_id}")
def delete_or_cancel_my_estimate(
    estimate_id: str,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.Estimate, estimate_id)
    if not item:
        raise HTTPException(404, "Oferta nie istnieje")
    actor = estimate_actor_for_item(db, user, item)
    if item.status in {"accepted", "rejected"}:
        raise HTTPException(422, "Zaakceptowana lub odrzucona oferta zostaje w historii")
    if actor == "company_worker" and item.status not in {"draft", "pending_approval"}:
        raise HTTPException(403, "Pracownik moze usunac tylko wlasny szkic")

    if item.status == "sent":
        item.status = "cancelled"
        item.share_active = False
        db.commit()
        db.refresh(item)
        return {"status": "cancelled", "estimate": estimate_payload(item)}

    if item.status in {"draft", "pending_approval", "approved_by_owner", "cancelled"}:
        db.delete(item)
        db.commit()
        return {"status": "deleted", "id": estimate_id}

    raise HTTPException(422, "Nie mozna usunac tej oferty")


def public_estimate_by_token(db: Session, token: str) -> models.Estimate:
    item = db.scalar(
        select(models.Estimate).where(
            models.Estimate.share_token == token,
            models.Estimate.share_active.is_(True),
        )
    )
    if not item or item.status not in {"sent", "accepted", "rejected"}:
        raise HTTPException(404, "Oferta nie istnieje albo link wygasl")
    return item


@router.get("/public/estimates/{token}")
def get_public_estimate(
    token: str,
    db: Session = Depends(get_db),
):
    return public_estimate_payload(db, public_estimate_by_token(db, token))


@router.post("/public/estimates/{token}/decision")
def decide_public_estimate(
    token: str,
    payload: PublicEstimateDecision,
    db: Session = Depends(get_db),
):
    item = public_estimate_by_token(db, token)
    if item.status == payload.status:
        return public_estimate_payload(db, item)
    if item.status != "sent":
        raise HTTPException(422, "Ta oferta ma juz finalna decyzje")
    item.status = payload.status
    if payload.status == "accepted":
        item.accepted_at = item.accepted_at or now()
        item.rejected_at = None
    else:
        item.rejected_at = item.rejected_at or now()
        item.accepted_at = None
    db.commit()
    db.refresh(item)
    return public_estimate_payload(db, item)


@router.get("/contracts/me")
def list_my_project_contracts(
    project_id: str | None = Query(default=None),
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return [
        project_contract_payload(item)
        for item in visible_project_contracts_for_user(db, user, project_id=project_id)
    ]


@router.get("/contracts/me/{contract_id}")
def get_my_project_contract(
    contract_id: str,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.ProjectContract, contract_id)
    if not item:
        raise HTTPException(404, "Umowa nie istnieje")
    if is_investor(user):
        raise HTTPException(403, "Inwestor nie ma dostepu do umow wykonawcy")
    if not project_role(db, item.project_id, user.id):
        raise HTTPException(404, "Umowa nie istnieje")
    return project_contract_payload(item)


@router.post("/projects/{project_id}/contract", status_code=201)
def create_project_contract(
    project_id: str,
    response: Response,
    request: Request,
    payload: ProjectContractUpdate | None = None,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    actor = project_contract_actor_for_project(db, access, user)
    existing = db.scalar(
        select(models.ProjectContract)
        .where(models.ProjectContract.project_id == access.project.id)
        .with_for_update()
    )
    if existing:
        response.status_code = 200
        return {"created": False, "contract": project_contract_payload(existing)}

    item = build_project_contract_from_project(
        db,
        access.project,
        user,
        status="pending_approval" if actor == "company_worker" else "draft",
        draft_origin="worker" if actor == "company_worker" else "manual",
        draft_origin_label="od pracownika" if actor == "company_worker" else "",
    )
    if payload is not None:
        apply_project_contract_changes(item, payload, partial=True)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(models.ProjectContract).where(models.ProjectContract.project_id == access.project.id)
        )
        if existing:
            response.status_code = 200
            return {"created": False, "contract": project_contract_payload(existing)}
        raise HTTPException(409, "Umowa dla tego zlecenia juz istnieje")
    db.refresh(item)
    return {"created": True, "contract": project_contract_payload(item)}


@router.post("/projects/{project_id}/guest-contract-draft", status_code=201)
def create_guest_project_contract_draft(
    project_id: str,
    response: Response,
    request: Request,
    payload: ProjectContractUpdate | None = None,
    db: Session = Depends(get_db),
):
    access = guest_document_draft_access(request, db, project_id)
    existing = db.scalar(
        select(models.ProjectContract)
        .where(models.ProjectContract.project_id == access.project.id)
        .with_for_update()
    )
    if existing:
        response.status_code = 200
        return {
            "created": False,
            "message": "Umowa dla tego zlecenia juz istnieje. Skontaktuj sie z szefem albo zglos uwagi.",
            "contract": None,
        }

    creator = guest_link_creator(db, access.guest)
    item = build_project_contract_from_project(
        db,
        access.project,
        creator,
        status="pending_approval",
        created_by_id=access.guest.created_by_id,
        draft_origin="guest_link",
        draft_origin_label=draft_origin_label_from_guest(access.guest),
    )
    if payload is not None:
        apply_project_contract_changes(item, payload, partial=True)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        response.status_code = 200
        return {
            "created": False,
            "message": "Umowa dla tego zlecenia juz istnieje. Skontaktuj sie z szefem albo zglos uwagi.",
            "contract": None,
        }
    db.refresh(item)
    return {"created": True, "contract": guest_project_contract_draft_payload(item)}


@router.patch("/contracts/me/{contract_id}")
def update_my_project_contract(
    contract_id: str,
    payload: ProjectContractUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.ProjectContract, contract_id)
    if not item:
        raise HTTPException(404, "Umowa nie istnieje")
    actor = project_contract_actor_for_item(db, user, item)
    ensure_project_contract_editable(item, actor, user)
    apply_project_contract_changes(item, payload, partial=True)
    db.commit()
    db.refresh(item)
    return project_contract_payload(item)


@router.patch("/contracts/me/{contract_id}/status")
def update_my_project_contract_status(
    contract_id: str,
    payload: ProjectContractStatusUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.ProjectContract, contract_id)
    if not item:
        raise HTTPException(404, "Umowa nie istnieje")
    actor = project_contract_actor_for_item(db, user, item)
    change_project_contract_status(db, item, payload.status, actor, user)
    db.commit()
    db.refresh(item)
    return project_contract_payload(item)


@router.get("/contracts/public/{token}")
def get_public_project_contract(
    token: str,
    db: Session = Depends(get_db),
):
    return public_project_contract_payload(db, public_project_contract_by_token(db, token))


@router.post("/contracts/public/{token}/decision")
def decide_public_project_contract(
    token: str,
    payload: PublicContractDecision,
    db: Session = Depends(get_db),
):
    item = public_project_contract_by_token(db, token)
    if item.status != "sent":
        raise HTTPException(422, "Ta umowa ma juz finalna decyzje")
    item.status = payload.status
    if payload.status == "accepted":
        item.accepted_at = item.accepted_at or now()
        item.rejected_at = None
    else:
        item.rejected_at = item.rejected_at or now()
        item.accepted_at = None
    db.commit()
    db.refresh(item)
    return public_project_contract_payload(db, item)


@router.post("/contracts/public/{token}/accept")
def accept_public_project_contract(
    token: str,
    db: Session = Depends(get_db),
):
    return decide_public_project_contract(token, PublicContractDecision(status="accepted"), db)


@router.patch("/contracts/public/{token}/accept")
def patch_accept_public_project_contract(
    token: str,
    db: Session = Depends(get_db),
):
    return decide_public_project_contract(token, PublicContractDecision(status="accepted"), db)


@router.post("/contracts/public/{token}/reject")
def reject_public_project_contract(
    token: str,
    db: Session = Depends(get_db),
):
    return decide_public_project_contract(token, PublicContractDecision(status="rejected"), db)


@router.patch("/contracts/public/{token}/reject")
def patch_reject_public_project_contract(
    token: str,
    db: Session = Depends(get_db),
):
    return decide_public_project_contract(token, PublicContractDecision(status="rejected"), db)


@router.get("/final-reports/me")
def list_my_project_final_reports(
    project_id: str | None = Query(default=None),
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return [
        project_final_report_payload(item)
        for item in visible_project_final_reports_for_user(db, user, project_id=project_id)
    ]


@router.get("/final-reports/me/{report_id}")
def get_my_project_final_report(
    report_id: str,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.ProjectFinalReport, report_id)
    if not item:
        raise HTTPException(404, "Raport koncowy nie istnieje")
    project_final_report_actor_for_item(db, user, item)
    return project_final_report_payload(item)


@router.post("/projects/{project_id}/final-report", status_code=201)
def create_project_final_report(
    project_id: str,
    response: Response,
    request: Request,
    payload: ProjectFinalReportUpdate | None = None,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    actor = project_final_report_actor_for_project(db, access, user)
    existing = db.scalar(
        select(models.ProjectFinalReport)
        .where(models.ProjectFinalReport.project_id == access.project.id)
        .with_for_update()
    )
    if existing:
        response.status_code = 200
        return {"created": False, "report": project_final_report_payload(existing)}

    item = build_project_final_report_from_project(
        db,
        access.project,
        user,
        status="pending_approval" if actor == "company_worker" else "draft",
        draft_origin="worker" if actor == "company_worker" else "manual",
        draft_origin_label="od pracownika" if actor == "company_worker" else "",
    )
    if payload is not None:
        apply_project_final_report_changes(item, payload, partial=True)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(models.ProjectFinalReport).where(
                models.ProjectFinalReport.project_id == access.project.id
            )
        )
        if existing:
            response.status_code = 200
            return {"created": False, "report": project_final_report_payload(existing)}
        raise HTTPException(409, "Raport koncowy dla tego zlecenia juz istnieje")
    db.refresh(item)
    return {"created": True, "report": project_final_report_payload(item)}


@router.post("/projects/{project_id}/guest-final-report-draft", status_code=201)
def create_guest_project_final_report_draft(
    project_id: str,
    response: Response,
    request: Request,
    payload: ProjectFinalReportUpdate | None = None,
    db: Session = Depends(get_db),
):
    access = guest_document_draft_access(request, db, project_id)
    existing = db.scalar(
        select(models.ProjectFinalReport)
        .where(models.ProjectFinalReport.project_id == access.project.id)
        .with_for_update()
    )
    if existing:
        response.status_code = 200
        return {
            "created": False,
            "message": "Raport koncowy dla tego zlecenia juz istnieje. Skontaktuj sie z szefem, jesli chcesz cos zmienic.",
            "report": None,
        }

    creator = guest_link_creator(db, access.guest)
    item = build_project_final_report_from_project(
        db,
        access.project,
        creator,
        status="pending_approval",
        created_by_id=access.guest.created_by_id,
        draft_origin="guest_link",
        draft_origin_label=draft_origin_label_from_guest(access.guest),
    )
    if payload is not None:
        apply_project_final_report_changes(item, payload, partial=True)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        response.status_code = 200
        return {
            "created": False,
            "message": "Raport koncowy dla tego zlecenia juz istnieje. Skontaktuj sie z szefem, jesli chcesz cos zmienic.",
            "report": None,
        }
    db.refresh(item)
    return {"created": True, "report": guest_project_final_report_draft_payload(item)}


@router.patch("/final-reports/me/{report_id}")
def update_my_project_final_report(
    report_id: str,
    payload: ProjectFinalReportUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.ProjectFinalReport, report_id)
    if not item:
        raise HTTPException(404, "Raport koncowy nie istnieje")
    actor = project_final_report_actor_for_item(db, user, item)
    ensure_project_final_report_editable(item, actor, user)
    apply_project_final_report_changes(item, payload, partial=True)
    db.commit()
    db.refresh(item)
    return project_final_report_payload(item)


@router.patch("/final-reports/me/{report_id}/status")
def update_my_project_final_report_status(
    report_id: str,
    payload: ProjectFinalReportStatusUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.ProjectFinalReport, report_id)
    if not item:
        raise HTTPException(404, "Raport koncowy nie istnieje")
    actor = project_final_report_actor_for_item(db, user, item)
    change_project_final_report_status(db, item, payload.status, actor, user)
    db.commit()
    db.refresh(item)
    return project_final_report_payload(item)


@router.get("/final-reports/public/{token}")
def get_public_project_final_report(
    token: str,
    db: Session = Depends(get_db),
):
    return public_project_final_report_payload(
        db, public_project_final_report_by_token(db, token)
    )


@router.post("/final-reports/public/{token}/decision")
def decide_public_project_final_report(
    token: str,
    payload: PublicFinalReportDecision,
    db: Session = Depends(get_db),
):
    item = public_project_final_report_by_token(db, token)
    if item.status != "sent":
        raise HTTPException(422, "Ten raport ma juz finalna decyzje")
    item.status = payload.status
    if payload.status == "accepted":
        item.accepted_at = item.accepted_at or now()
        item.rejected_at = None
    else:
        item.rejected_at = item.rejected_at or now()
        item.accepted_at = None
    db.commit()
    db.refresh(item)
    return public_project_final_report_payload(db, item)


@router.post("/final-reports/public/{token}/accept")
def accept_public_project_final_report(
    token: str,
    db: Session = Depends(get_db),
):
    return decide_public_project_final_report(
        token, PublicFinalReportDecision(status="accepted"), db
    )


@router.patch("/final-reports/public/{token}/accept")
def patch_accept_public_project_final_report(
    token: str,
    db: Session = Depends(get_db),
):
    return decide_public_project_final_report(
        token, PublicFinalReportDecision(status="accepted"), db
    )


@router.post("/final-reports/public/{token}/reject")
def reject_public_project_final_report(
    token: str,
    db: Session = Depends(get_db),
):
    return decide_public_project_final_report(
        token, PublicFinalReportDecision(status="rejected"), db
    )


@router.patch("/final-reports/public/{token}/reject")
def patch_reject_public_project_final_report(
    token: str,
    db: Session = Depends(get_db),
):
    return decide_public_project_final_report(
        token, PublicFinalReportDecision(status="rejected"), db
    )


@router.get("/public-profile/me")
def get_my_public_profile(
    owner_type: Literal["independent_contractor", "company"] = Query(...),
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_public_profile(db, user, owner_type)
    realizations = public_profile_realizations_for(
        db, profile.owner_type, profile.owner_id
    )
    return public_profile_payload(profile, realizations=realizations)


@router.patch("/public-profile/me")
def update_my_public_profile(
    payload: PublicProfileUpdate,
    owner_type: Literal["independent_contractor", "company"] = Query(...),
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_public_profile(db, user, owner_type)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if key == "specializations":
            profile.specializations = validate_public_profile_specializations(value)
        elif key == "contact_email":
            profile.contact_email = validate_contact_email(value or "")
        elif key == "slug":
            profile.slug = unique_public_profile_slug(
                db, value or profile.display_name, ignore_profile_id=profile.id
            )
        elif isinstance(value, str):
            setattr(profile, key, value.strip())
        else:
            setattr(profile, key, value)

    if not profile.slug:
        profile.slug = unique_public_profile_slug(
            db, profile.display_name, ignore_profile_id=profile.id
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Adres wizytówki jest już zajęty") from exc
    db.refresh(profile)
    return public_profile_payload(profile)


@router.get("/public-profile/me/realizations")
def list_my_public_profile_realizations(
    owner_type: Literal["independent_contractor", "company"] = Query(...),
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_public_profile(db, user, owner_type)
    items = public_profile_realizations_for(db, profile.owner_type, profile.owner_id)
    return [public_profile_realization_payload(item) for item in items]


@router.post("/public-profile/me/realizations", status_code=201)
def create_my_public_profile_realization(
    payload: PublicProfileRealizationCreate,
    owner_type: Literal["independent_contractor", "company"] = Query(...),
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_public_profile(db, user, owner_type)
    item = models.PublicProfileRealization(
        owner_type=profile.owner_type,
        owner_id=profile.owner_id,
    )
    apply_public_profile_realization_changes(
        item,
        payload,
        db=db,
        user=user,
        profile=profile,
        require_project=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return public_profile_realization_payload(item)


@router.patch("/public-profile/me/realizations/{realization_id}")
def update_my_public_profile_realization(
    realization_id: str,
    payload: PublicProfileRealizationUpdate,
    owner_type: Literal["independent_contractor", "company"] = Query(...),
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_public_profile(db, user, owner_type)
    item = db.get(models.PublicProfileRealization, realization_id)
    if not item or item.owner_type != profile.owner_type or item.owner_id != profile.owner_id:
        raise HTTPException(404, "Realizacja nie istnieje")
    apply_public_profile_realization_changes(item, payload, db=db, user=user, profile=profile)
    db.commit()
    db.refresh(item)
    return public_profile_realization_payload(item)


@router.delete("/public-profile/me/realizations/{realization_id}")
def delete_my_public_profile_realization(
    realization_id: str,
    owner_type: Literal["independent_contractor", "company"] = Query(...),
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    profile = get_or_create_public_profile(db, user, owner_type)
    item = db.get(models.PublicProfileRealization, realization_id)
    if not item or item.owner_type != profile.owner_type or item.owner_id != profile.owner_id:
        raise HTTPException(404, "Realizacja nie istnieje")
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.get("/public-profiles")
def list_public_profiles(
    q: str = Query(default="", max_length=160),
    specialization: str = Query(default="", max_length=120),
    service_area: str = Query(default="", max_length=160),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    search = q.strip().lower()
    specialization_filter = specialization.strip().lower()
    service_area_filter = service_area.strip().lower()
    profiles = list(
        db.scalars(
            select(models.PublicProfile)
            .where(
                models.PublicProfile.is_public.is_(True),
                models.PublicProfile.owner_type.in_(
                    ("independent_contractor", "company")
                ),
            )
            .order_by(models.PublicProfile.updated_at.desc())
        ).all()
    )

    def matches(profile: models.PublicProfile) -> bool:
        if search:
            haystack = " ".join(
                [
                    profile.display_name or "",
                    profile.public_description or "",
                ]
            ).lower()
            if search not in haystack:
                return False
        if specialization_filter:
            specializations = [
                str(item).strip().lower() for item in (profile.specializations or [])
            ]
            if specialization_filter not in specializations:
                return False
        if service_area_filter and service_area_filter not in (
            profile.service_area or ""
        ).lower():
            return False
        return True

    visible_profiles = [profile for profile in profiles if matches(profile)][:limit]
    return [
        public_profile_payload(
            profile,
            realizations=public_profile_realizations_for(
                db, profile.owner_type, profile.owner_id, public_only=True
            ),
            public=True,
        )
        for profile in visible_profiles
    ]


@router.get("/public-profiles/{slug}")
def get_public_profile(slug: str, db: Session = Depends(get_db)):
    normalized_slug = normalize_public_profile_slug(slug)
    profile = db.scalar(
        select(models.PublicProfile).where(models.PublicProfile.slug == normalized_slug)
    )
    if not profile or not profile.is_public:
        raise HTTPException(404, "Wizytówka nie jest dostępna publicznie")
    realizations = public_profile_realizations_for(
        db, profile.owner_type, profile.owner_id, public_only=True
    )
    return public_profile_payload(profile, realizations=realizations, public=True)


@router.post("/onboarding")
def complete_onboarding(
    payload: OnboardingCreate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if user.profile_type:
        raise HTTPException(409, "Profil został już wybrany")
    if payload.profile_type == "company_owner" and not (
        payload.company_name and payload.company_name.strip()
    ):
        raise HTTPException(422, "Podaj nazwę firmy")

    user.profile_type = payload.profile_type
    user.preferred_mode = (
        payload.preferred_mode
        if payload.profile_type == "independent_contractor"
        else "expanded"
    )
    if payload.profile_type == "company_owner":
        workspace = models.Workspace(
            name=payload.company_name.strip(),
            kind="company",
            owner_id=user.id,
        )
        db.add(workspace)
        db.flush()
        db.add(
            models.WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role="owner",
            )
        )
    db.commit()
    return user_payload(db, user)


@router.get("/workspaces")
def list_workspaces(
    user: models.User = Depends(require_user), db: Session = Depends(get_db)
):
    return user_payload(db, user)["workspaces"]


@router.post("/workspaces", status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not can_manage_people(user):
        raise HTTPException(403, "Ten typ konta nie zarzadza zespolem ani wykonawcami")
    workspace = models.Workspace(
        name=payload.name.strip(),
        kind=payload.kind,
        owner_id=user.id,
        description=payload.description.strip(),
        phone=payload.phone.strip(),
        address=payload.address.strip(),
    )
    db.add(workspace)
    db.flush()
    db.add(
        models.WorkspaceMember(
            workspace_id=workspace.id, user_id=user.id, role="owner"
        )
    )
    db.commit()
    return workspace_payload(db, workspace, user.id, details=True)


@router.get("/workspaces/{workspace_id}")
def get_workspace(
    workspace_id: str,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    workspace = db.get(models.Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Nie znaleziono firmy")
    membership = db.scalar(
        select(models.WorkspaceMember).where(
            models.WorkspaceMember.workspace_id == workspace_id,
            models.WorkspaceMember.user_id == user.id,
        )
    )
    if not membership:
        raise HTTPException(403, "Nie należysz do tej firmy")
    return workspace_payload(db, workspace, user.id, details=True)


@router.patch("/workspaces/{workspace_id}")
def update_workspace(
    workspace_id: str,
    payload: WorkspaceUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not can_manage_workspace(db, workspace_id, user.id):
        raise HTTPException(403, "Brak uprawnień do edycji firmy")
    workspace = db.get(models.Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Nie znaleziono firmy")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(workspace, key, (value or "").strip())
    db.commit()
    return workspace_payload(db, workspace, user.id, details=True)


@router.post("/workspaces/{workspace_id}/invite")
def invite_workspace_member(
    workspace_id: str,
    payload: WorkspaceMemberInvite,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not can_manage_workspace(db, workspace_id, user.id):
        raise HTTPException(403, "Brak uprawnień do zespołu")
    email = normalize_email(str(payload.email))
    existing_user = db.scalar(select(models.User).where(models.User.email == email))
    if existing_user:
        existing_member = db.scalar(
            select(models.WorkspaceMember).where(
                models.WorkspaceMember.workspace_id == workspace_id,
                models.WorkspaceMember.user_id == existing_user.id,
            )
        )
        if not existing_member:
            db.add(
                models.WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=existing_user.id,
                    role=payload.role,
                )
            )
    raw_token = random_token()
    invitation = models.Invitation(
        workspace_id=workspace_id,
        email=email,
        role=payload.role,
        token_hash=hash_secret(raw_token),
        invited_by_id=user.id,
        expires_at=now() + timedelta(days=14),
        accepted_at=now() if existing_user else None,
    )
    db.add(invitation)
    db.commit()
    send_email(
        email,
        "Zaproszenie do zespołu Pan Majster",
        (
            f"Dołącz do zespołu Pan Majster:\n"
            f"{settings.app_url}/invite/{raw_token}"
        ),
    )
    return {
        "ok": True,
        "email": email,
        "accepted": bool(existing_user),
        "url": f"{settings.app_url}/invite/{raw_token}",
    }


@router.get("/workers")
def list_workers(
    workspace_id: str | None = None,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not can_manage_people(user):
        return []
    return [
        worker_profile_payload(db, item)
        for item in available_worker_profiles(db, user, workspace_id)
    ]


@router.post("/workers", status_code=201)
def create_worker(
    payload: WorkerProfileCreate,
    response: Response,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not can_manage_people(user):
        raise HTTPException(403, "Samodzielny majster nie dodaje ekip pod sobą")
    workspace_id = payload.workspace_id
    if workspace_id:
        if not can_manage_workers(db, user, workspace_id):
            raise HTTPException(403, "Brak dostępu do firmy")
    elif user.profile_type == "company_owner":
        workspace_id = db.scalar(
            select(models.WorkspaceMember.workspace_id).where(
                models.WorkspaceMember.user_id == user.id,
                models.WorkspaceMember.role.in_(["owner", "admin"]),
            )
        )
    email = optional_email(payload.email)
    if email:
        existing_worker = db.scalar(
            select(models.WorkerProfile).where(
                models.WorkerProfile.email == email,
                models.WorkerProfile.workspace_id == workspace_id,
            )
        )
        if existing_worker:
            response.status_code = 200
            return {
                **worker_profile_payload(db, existing_worker),
                "message": "Ten wykonawca już istnieje na liście.",
                "existing": True,
            }
        existing_user = db.scalar(select(models.User).where(models.User.email == email))
    else:
        existing_user = None
    item = models.WorkerProfile(
        owner_id=user.id,
        workspace_id=workspace_id,
        label=payload.label.strip(),
        profile_kind=payload.profile_kind,
        email=email,
        phone=payload.phone.strip(),
        note=payload.note.strip(),
    )
    db.add(item)
    invitation_url = ""
    invite_message = ""
    if email and workspace_id:
        if existing_user:
            user_membership = db.scalar(
                select(models.WorkspaceMember).where(
                    models.WorkspaceMember.workspace_id == workspace_id,
                    models.WorkspaceMember.user_id == existing_user.id,
                )
            )
            if not user_membership:
                db.add(
                    models.WorkspaceMember(
                        workspace_id=workspace_id,
                        user_id=existing_user.id,
                        role="member",
                    )
                )
            if existing_user.profile_type in {None, "", "worker"}:
                existing_user.profile_type = "company_worker"
            invite_message = "Ten wykonawca ma już konto. Dodaliśmy go do listy wykonawców."
        else:
            pending = db.scalar(
                select(models.Invitation).where(
                    models.Invitation.workspace_id == workspace_id,
                    models.Invitation.email == email,
                    models.Invitation.accepted_at.is_(None),
                    models.Invitation.revoked_at.is_(None),
                )
            )
            if pending:
                response.status_code = 200
                invite_message = "Ten e-mail ma już zaproszenie do konta wykonawcy."
            else:
                raw_token = random_token()
                db.add(
                    models.Invitation(
                        workspace_id=workspace_id,
                        email=email,
                        role="member",
                        token_hash=hash_secret(raw_token),
                        invited_by_id=user.id,
                        expires_at=now() + timedelta(days=14),
                        accepted_at=None,
                    )
                )
                invitation_url = f"{settings.app_url}/invite/{raw_token}"
                invite_message = "Utworzono zaproszenie do stałego konta wykonawcy."
                send_email(
                    email,
                    "Zaproszenie do Pan Majster",
                    (
                        "Zostałeś zaproszony jako wykonawca / majster w Pan Majster.\n"
                        f"Potwierdź konto kodem e-mail i dołącz tutaj:\n{invitation_url}"
                    ),
                )
    db.commit()
    return {
        **worker_profile_payload(db, item),
        "message": invite_message or "Wykonawca dodany.",
        "invitation_url": invitation_url,
        "existing": False,
    }


@router.patch("/workers/{worker_id}")
def update_worker(
    worker_id: str,
    payload: WorkerProfileUpdate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = worker_profile_for_assignment(db, worker_id, user, None)
    changes = payload.model_dump(exclude_unset=True)
    if "email" in changes:
        changes["email"] = optional_email(changes["email"])
    for key, value in changes.items():
        setattr(item, key, (value or "").strip())
    db.commit()
    return worker_profile_payload(db, item)


@router.delete("/workers/{worker_id}")
def deactivate_worker(
    worker_id: str,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.WorkerProfile, worker_id)
    if not item:
        raise HTTPException(404, "Nie znaleziono majstra lub ekipy")
    if item.workspace_id:
        if not can_manage_workspace(db, item.workspace_id, user.id):
            raise HTTPException(403, "Brak dostepu do majstra lub ekipy")
    elif item.owner_id != user.id:
        raise HTTPException(403, "Brak dostepu do majstra lub ekipy")
    assigned_count = db.scalar(
        select(func.count(models.Project.id)).where(
            models.Project.worker_profile_id == item.id
        )
    )
    item.active = False
    for project in db.scalars(
        select(models.Project).where(models.Project.worker_profile_id == item.id)
    ).all():
        project.worker_profile_id = None
    db.commit()
    return {"ok": True, "deactivated": True, "assigned_count": assigned_count or 0}


@router.post("/workers/{worker_id}/activate")
def activate_worker(
    worker_id: str,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.WorkerProfile, worker_id)
    if not item:
        raise HTTPException(404, "Nie znaleziono majstra lub ekipy")
    if item.workspace_id:
        if not can_manage_workspace(db, item.workspace_id, user.id):
            raise HTTPException(403, "Brak dostepu do majstra lub ekipy")
    elif item.owner_id != user.id:
        raise HTTPException(403, "Brak dostepu do majstra lub ekipy")
    item.active = True
    db.commit()
    return worker_profile_payload(db, item)


@router.get("/projects")
def list_projects(
    user: models.User = Depends(require_user), db: Session = Depends(get_db)
):
    return [
        project_payload(db, project_item, role=role)
        for project_item, role in db.execute(user_projects_query(user.id)).all()
    ]


def normalize_contract_currency(
    currency: str | None, contract_amount: Decimal | None
) -> str | None:
    value = (currency or "").strip().upper()
    if not value:
        return DEFAULT_CONTRACT_CURRENCY if contract_amount is not None else None
    if len(value) != 3 or not value.isalpha():
        raise HTTPException(400, "Waluta musi miec trzyliterowy kod, np. PLN")
    return value


def validate_project_contract_terms(
    *,
    planned_start_date: date | None,
    planned_end_date: date | None,
    schedule_uncertainty_days: int | None,
    contract_amount: Decimal | None,
) -> None:
    if planned_start_date and planned_end_date and planned_end_date < planned_start_date:
        raise HTTPException(
            400,
            "Planowany koniec nie moze byc wczesniejszy niz planowany start",
        )
    if schedule_uncertainty_days is not None and schedule_uncertainty_days < 0:
        raise HTTPException(400, "Niepewnosc terminu nie moze byc ujemna")
    if contract_amount is not None and contract_amount < 0:
        raise HTTPException(400, "Kwota umowna nie moze byc ujemna")


def normalize_project_contract_changes(
    changes: dict[str, Any], project: models.Project | None = None
) -> None:
    planned_start_date = changes.get(
        "planned_start_date", project.planned_start_date if project else None
    )
    planned_end_date = changes.get(
        "planned_end_date", project.planned_end_date if project else None
    )
    schedule_uncertainty_days = changes.get(
        "schedule_uncertainty_days",
        project.schedule_uncertainty_days if project else None,
    )
    contract_amount = changes.get(
        "contract_amount", project.contract_amount if project else None
    )
    contract_currency = changes.get(
        "contract_currency", project.contract_currency if project else None
    )
    validate_project_contract_terms(
        planned_start_date=planned_start_date,
        planned_end_date=planned_end_date,
        schedule_uncertainty_days=schedule_uncertainty_days,
        contract_amount=contract_amount,
    )
    if "contract_currency" in changes or "contract_amount" in changes:
        changes["contract_currency"] = normalize_contract_currency(
            contract_currency, contract_amount
        )


@router.post("/projects", status_code=201)
def create_project(
    payload: ProjectCreate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not can_create_project(user):
        raise HTTPException(403, "Majster - czlonek firmy widzi tylko przypisane zlecenia")
    if payload.workspace_id and not can_manage_workspace(
        db, payload.workspace_id, user.id
    ):
        membership = db.scalar(
            select(models.WorkspaceMember).where(
                models.WorkspaceMember.workspace_id == payload.workspace_id,
                models.WorkspaceMember.user_id == user.id,
            )
        )
        if not membership:
            raise HTTPException(403, "Nie należysz do wybranej firmy")
    worker = worker_profile_for_assignment(
        db, payload.worker_profile_id, user, payload.workspace_id
    )
    workspace_id = payload.workspace_id or (worker.workspace_id if worker else None)
    contract_changes = payload.model_dump(include=PROJECT_CONTRACT_FIELDS)
    normalize_project_contract_changes(contract_changes)

    project = models.Project(
        workspace_id=workspace_id,
        worker_profile_id=worker.id if worker else None,
        created_by_id=user.id,
        name=payload.name.strip(),
        client_name=(payload.client_name or "").strip(),
        client_email=(payload.client_email or "").strip(),
        address=payload.address.strip(),
        description=payload.description.strip(),
        status=PROJECT_STATUS_ASSIGNED,
        template=payload.template if payload.template in STAGE_TEMPLATES else "custom",
        planned_start_date=contract_changes["planned_start_date"],
        planned_end_date=contract_changes["planned_end_date"],
        schedule_uncertainty_days=contract_changes["schedule_uncertainty_days"],
        contract_amount=contract_changes["contract_amount"],
        contract_currency=contract_changes["contract_currency"],
        started_at=now(),
        client_share_token=random_token(30),
    )
    db.add(project)
    db.flush()
    db.add(
        models.ProjectMember(project_id=project.id, user_id=user.id, role="owner")
    )
    ensure_worker_project_access(db, project, worker, user.id)
    stage_names = STAGE_TEMPLATES.get(project.template, STAGE_TEMPLATES["custom"])
    for position, title in enumerate(stage_names):
        if title.strip():
            db.add(
                models.ProjectStage(
                    project_id=project.id,
                    title=title.strip(),
                    position=position,
                    status="active" if position == 0 else "planned",
                )
            )
    db.commit()
    db.refresh(project)
    return project_payload(db, project, role="owner", details=True)


def project_detail_data(db: Session, access: ProjectAccess):
    project = access.project
    role = access.role
    members = db.scalars(
        select(models.ProjectMember)
        .options(selectinload(models.ProjectMember.user))
        .where(models.ProjectMember.project_id == project.id)
    ).all()
    worker_links = []
    if access.can_manage():
        worker_links = db.scalars(
            select(models.GuestInvite)
            .where(
                models.GuestInvite.project_id == project.id,
                models.GuestInvite.kind == "worker",
            )
            .order_by(models.GuestInvite.created_at.desc())
        ).all()
    worker_profile = (
        db.get(models.WorkerProfile, project.worker_profile_id)
        if project.worker_profile_id
        else None
    )
    return {
        **project_payload(db, project, role=role, details=True),
        "members": [
            {
                "id": member.id,
                "role": member.role,
                "user": serializers.user(member.user),
            }
            for member in members
        ],
        "worker_profile": (
            worker_profile_payload(db, worker_profile) if worker_profile else None
        ),
        "worker_links": [guest_invite_payload(db, item) for item in worker_links],
        "entry_count": db.scalar(
            select(func.count(models.Entry.id)).where(
                models.Entry.project_id == project.id
            )
        ),
        "open_problem_count": db.scalar(
            select(func.count(models.Entry.id)).where(
                models.Entry.project_id == project.id,
                models.Entry.kind == "problem",
                models.Entry.problem_status == "open",
            )
        ),
        "can_edit_details": access.can_edit_details(),
    }


def require_final_status_manage(access) -> None:
    access.require_manage()
    if is_company_worker(access.user):
        raise HTTPException(403, "Majster firmy nie zamyka finalnie zlecenia")


def require_close_project_access(access) -> None:
    if is_company_worker(access.user):
        access.require_add()
        return
    require_final_status_manage(access)


def require_reopen_project_access(access) -> None:
    if is_company_worker(access.user):
        access.require_add()
        return
    require_final_status_manage(access)


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request, db: Session = Depends(get_db)):
    access = get_project_access(request, db, project_id)
    if access.guest and not access.can_view_history():
        return {
            **project_payload(db, access.project, details=True),
            "guest": {
                "label": access.guest.label,
                "permission": access.guest.permission,
                "kind": access.guest.kind,
            },
            "members": [],
            "worker_links": [],
            "entry_count": None,
            "open_problem_count": None,
        }
    data = project_detail_data(db, access)
    if access.guest:
        data["guest"] = {
            "label": access.guest.label,
            "permission": access.guest.permission,
            "kind": access.guest.kind,
        }
        data["members"] = []
        data["worker_links"] = []
    return data


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    changes = payload.model_dump(exclude_unset=True)
    has_contract_changes = bool(PROJECT_CONTRACT_FIELDS.intersection(changes))
    if "details_locked" in changes or "worker_profile_id" in changes or "status" in changes:
        access.require_manage()
        if "status" in changes and is_company_worker(access.user):
            raise HTTPException(403, "Majster firmy nie zmienia finalnego statusu")
    else:
        access.require_edit_details()
    if has_contract_changes:
        if is_company_worker(access.user):
            raise HTTPException(403, "Majster firmy nie edytuje terminow i kwoty")
        access.require_manage()
        normalize_project_contract_changes(changes, access.project)
    if "portfolio_slug" in changes and changes["portfolio_slug"]:
        slug = SLUG_RE.sub("-", changes["portfolio_slug"].lower()).strip("-")
        if not slug:
            raise HTTPException(400, "Nieprawidłowy adres portfolio")
        changes["portfolio_slug"] = slug
    if "worker_profile_id" in changes:
        worker = worker_profile_for_assignment(
            db, changes["worker_profile_id"], access.user, access.project.workspace_id
        )
        changes["worker_profile_id"] = worker.id if worker else None
        ensure_worker_project_access(db, access.project, worker, access.user.id)
    for key, value in changes.items():
        setattr(access.project, key, value)
    if changes.get("status") == PROJECT_STATUS_COMPLETED and not access.project.finished_at:
        access.project.finished_at = now()
    db.commit()
    return project_payload(db, access.project, role=access.role, details=True)


@router.post("/projects/{project_id}/close")
def close_project(
    project_id: str, request: Request, db: Session = Depends(get_db)
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    require_close_project_access(access)
    access.project.status = PROJECT_STATUS_COMPLETED
    set_final_project_stage_current(access.project)
    if not access.project.finished_at:
        access.project.finished_at = now()
    db.commit()
    return project_detail_data(db, access)


@router.post("/projects/{project_id}/start")
def start_project(
    project_id: str, request: Request, db: Session = Depends(get_db)
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_add()
    if access.project.status == PROJECT_STATUS_COMPLETED:
        raise HTTPException(400, "Zakonczone zlecenie wymaga ponownego otwarcia")
    if access.project.status == PROJECT_STATUS_ASSIGNED:
        access.project.status = PROJECT_STATUS_IN_PROGRESS
        if not access.project.started_at:
            access.project.started_at = now()
        db.commit()
    return project_detail_data(db, access)


@router.post("/projects/{project_id}/reopen")
def reopen_project(
    project_id: str, request: Request, db: Session = Depends(get_db)
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    require_reopen_project_access(access)
    access.project.status = PROJECT_STATUS_IN_PROGRESS
    access.project.finished_at = None
    db.commit()
    return project_detail_data(db, access)


@router.post("/projects/{project_id}/stages", status_code=201)
def add_stage(
    project_id: str,
    payload: StageCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_edit_details()
    raise HTTPException(
        409,
        "Zlecenie ma trzy stałe etapy: przed rozpoczęciem, w trakcie i po zakończeniu",
    )


@router.patch("/projects/{project_id}/stages/{stage_id}")
def update_stage(
    project_id: str,
    stage_id: str,
    payload: StageUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_edit_details()
    item = db.get(models.ProjectStage, stage_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "Nie znaleziono etapu")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Pozycja etapu jest już zajęta")
    return serializers.stage(item)


@router.post("/projects/{project_id}/stages/{stage_id}/set-current")
@router.post("/projects/{project_id}/stages/{stage_id}")
def set_current_stage(
    project_id: str,
    stage_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id)
    access.require_add()
    item = db.get(models.ProjectStage, stage_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "Nie znaleziono etapu")
    stages = db.scalars(
        select(models.ProjectStage)
        .where(models.ProjectStage.project_id == project_id)
        .order_by(models.ProjectStage.position)
    ).all()
    for stage_item in stages:
        if stage_item.position < item.position:
            stage_item.status = "completed"
        elif stage_item.id == item.id:
            stage_item.status = "active"
        else:
            stage_item.status = "planned"
    if access.project.status == PROJECT_STATUS_ASSIGNED and item.position > 0:
        access.project.status = PROJECT_STATUS_IN_PROGRESS
    db.commit()
    db.refresh(access.project)
    if access.user:
        return project_detail_data(db, access)
    return {
        **project_payload(db, access.project, details=True),
        "guest": {
            "label": access.guest.label if access.guest else "",
            "permission": access.guest.permission if access.guest else "",
            "kind": access.guest.kind if access.guest else "",
        },
        "members": [],
        "worker_links": [],
    }


@router.post("/projects/{project_id}/invite")
def invite_project_member(
    project_id: str,
    payload: ProjectInvitationCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_manage()
    email = normalize_email(str(payload.email))
    existing_user = db.scalar(select(models.User).where(models.User.email == email))
    if existing_user:
        member = db.scalar(
            select(models.ProjectMember).where(
                models.ProjectMember.project_id == project_id,
                models.ProjectMember.user_id == existing_user.id,
            )
        )
        if member:
            member.role = payload.role
        else:
            db.add(
                models.ProjectMember(
                    project_id=project_id,
                    user_id=existing_user.id,
                    role=payload.role,
                )
            )
    raw_token = random_token()
    db.add(
        models.Invitation(
            project_id=project_id,
            email=email,
            role=payload.role,
            token_hash=hash_secret(raw_token),
            invited_by_id=access.user.id,
            expires_at=now() + timedelta(days=14),
            accepted_at=now() if existing_user else None,
        )
    )
    db.commit()
    send_email(
        email,
        f"Zaproszenie do projektu {access.project.name}",
        (
            f"Dołącz do projektu {access.project.name}:\n"
            f"{settings.app_url}/invite/{raw_token}"
        ),
    )
    return {
        "ok": True,
        "email": email,
        "accepted": bool(existing_user),
        "url": f"{settings.app_url}/invite/{raw_token}",
    }


@router.get("/projects/{project_id}/guest-links")
def list_guest_links(
    project_id: str, request: Request, db: Session = Depends(get_db)
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_manage()
    links = db.scalars(
        select(models.GuestInvite)
        .where(models.GuestInvite.project_id == project_id)
        .order_by(models.GuestInvite.created_at.desc())
    ).all()
    return [guest_invite_payload(db, item) for item in links]


@router.post("/projects/{project_id}/guest-links", status_code=201)
def create_guest_link(
    project_id: str,
    payload: GuestInviteCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_manage()
    if (
        (is_independent_contractor(access.user) or is_company_worker(access.user))
        and payload.kind == "worker"
    ):
        raise HTTPException(403, "Samodzielny majster nie wysyła linków wykonawcom")
    raw_token = random_token(36)
    worker = worker_profile_for_assignment(
        db, payload.worker_profile_id, access.user, access.project.workspace_id
    )
    email = optional_email(payload.email or (worker.email if worker else ""))
    label = payload.label.strip() or (worker.label if worker else "Gość")
    role = project_role_from_guest_permission(payload.permission)
    existing_user = (
        db.scalar(select(models.User).where(models.User.email == email))
        if email
        else None
    )
    if existing_user:
        member = db.scalar(
            select(models.ProjectMember).where(
                models.ProjectMember.project_id == project_id,
                models.ProjectMember.user_id == existing_user.id,
            )
        )
        if member:
            member.role = role
        else:
            db.add(
                models.ProjectMember(
                    project_id=project_id,
                    user_id=existing_user.id,
                    role=role,
                )
            )
    elif email:
        pending_project_invite = db.scalar(
            select(models.Invitation).where(
                models.Invitation.project_id == project_id,
                models.Invitation.email == email,
                models.Invitation.accepted_at.is_(None),
                models.Invitation.revoked_at.is_(None),
            )
        )
        if not pending_project_invite:
            db.add(
                models.Invitation(
                    project_id=project_id,
                    email=email,
                    role=role,
                    token_hash=hash_secret(random_token()),
                    invited_by_id=access.user.id,
                    expires_at=now() + timedelta(days=14),
                    accepted_at=None,
                )
            )
    item = models.GuestInvite(
        project_id=project_id,
        workspace_id=access.project.workspace_id,
        worker_profile_id=worker.id if worker else None,
        label=label,
        email=email,
        kind=payload.kind,
        permission=payload.permission,
        token_hash=hash_secret(raw_token),
        expires_at=(
            now() + timedelta(days=payload.expires_in_days)
            if payload.expires_in_days
            else None
        ),
        created_by_id=access.user.id,
    )
    if worker and not access.project.worker_profile_id:
        access.project.worker_profile_id = worker.id
    db.add(item)
    db.commit()
    url = f"{settings.app_url}/g/{raw_token}"
    if email:
        send_email(
            email,
            f"Link do zlecenia {access.project.name}",
            (
                f"Otwórz zlecenie {access.project.name} bez logowania:\n"
                f"{url}\n\n"
                "Jeśli będziesz korzystać stale, możesz też zalogować się tym "
                "adresem e-mail w Pan Majster."
            ),
        )
    return {
        "id": item.id,
        "label": item.label,
        "email": item.email,
        "kind": item.kind,
        "account_type": "account" if item.email else "link_only",
        "permission": item.permission,
        "url": url,
        "token": raw_token,
        "expires_at": serializers.iso(item.expires_at),
    }


@router.post("/projects/{project_id}/guest-links/{invite_id}/rotate")
def rotate_guest_link(
    project_id: str,
    invite_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_manage()
    item = db.get(models.GuestInvite, invite_id)
    if not item or item.project_id != project_id or item.kind != "worker":
        raise HTTPException(404, "Nie znaleziono linku wykonawcy")
    raw_token = random_token(36)
    item.token_hash = hash_secret(raw_token)
    item.revoked_at = None
    if item.expires_at:
        item.expires_at = now() + timedelta(days=30)
    db.commit()
    url = f"{settings.app_url}/g/{raw_token}"
    return {
        **guest_invite_payload(db, item),
        "url": url,
        "token": raw_token,
    }


@router.delete("/projects/{project_id}/guest-links/{invite_id}")
def revoke_guest_link(
    project_id: str,
    invite_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_manage()
    item = db.get(models.GuestInvite, invite_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "Nie znaleziono zaproszenia")
    item.revoked_at = now()
    db.commit()
    return {"ok": True}


def ensure_client_share(project: models.Project) -> str:
    if not project.client_share_token:
        project.client_share_token = random_token(30)
    return project.client_share_token


def client_link_payload(project: models.Project) -> dict:
    token = ensure_client_share(project)
    return {
        "active": project.client_share_active,
        "requires_pin": bool(project.client_share_pin_hash),
        "url": f"{settings.app_url}/c/{token}",
    }


@router.get("/projects/{project_id}/client-link")
def get_client_link(
    project_id: str, request: Request, db: Session = Depends(get_db)
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    payload = client_link_payload(access.project)
    db.commit()
    return payload


@router.patch("/projects/{project_id}/client-link")
def update_client_link(
    project_id: str,
    payload: ClientLinkUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_manage()
    if payload.rotate or not access.project.client_share_token:
        access.project.client_share_token = random_token(30)
    if payload.active is not None:
        access.project.client_share_active = payload.active
    if payload.remove_pin:
        access.project.client_share_pin_hash = None
    elif payload.pin:
        access.project.client_share_pin_hash = hash_secret(payload.pin)
    db.commit()
    return client_link_payload(access.project)


@router.patch("/projects/{project_id}/client-cover")
def update_project_client_cover(
    project_id: str,
    payload: ProjectClientCoverUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_manage()
    if payload.media_id is None:
        access.project.client_cover_media_id = None
        db.commit()
        return project_detail_data(db, access)

    asset = db.get(models.MediaAsset, payload.media_id)
    if not asset or asset.project_id != project_id:
        raise HTTPException(404, "Nie znaleziono zdjecia z tego zlecenia")
    if asset.kind != "image":
        raise HTTPException(400, "Zdjeciem glownym moze byc tylko obraz")
    access.project.client_cover_media_id = asset.id
    db.commit()
    return project_detail_data(db, access)


def public_project_by_token(db: Session, token: str) -> models.Project:
    project = db.scalar(
        select(models.Project).where(
            models.Project.client_share_token == token,
            models.Project.client_share_active.is_(True),
        )
    )
    if not project:
        raise HTTPException(404, "Link klienta jest nieaktywny")
    return project


PUBLIC_COMMENT_DEFAULTS = {
    "confirm_resolved": "Potwierdzam, problem został rozwiązany.",
    "still_open": "Problem nadal wymaga poprawki.",
    "suggest_solution": "Klient zasugerował rozwiązanie problemu.",
}


def public_media_payload(asset: models.MediaAsset, token: str) -> dict:
    data = serializers.media(asset)
    data["media_type"] = asset.kind
    data["url"] = f"/api/public/projects/{token}/media/{asset.id}"
    return data


def verify_project_pin(project: models.Project, pin: str | None) -> None:
    if project.client_share_pin_hash and (
        not pin or not verify_secret(pin, project.client_share_pin_hash)
    ):
        raise HTTPException(401, "Podaj prawidłowy PIN do zlecenia")


def public_entry_payload(item: models.Entry, token: str) -> dict:
    data = serializers.entry(item)
    data["media"] = [public_media_payload(asset, token) for asset in item.media]
    data["author"] = (
        {"name": item.author.name or "Wykonawca"} if item.author else None
    )
    data["author_label"] = item.author.name if item.author and item.author.name else "Wykonawca"
    return data


def load_public_entries(db: Session, project_id: str):
    return db.scalars(
        select(models.Entry)
        .options(
            selectinload(models.Entry.author),
            selectinload(models.Entry.stage),
            selectinload(models.Entry.media),
            selectinload(models.Entry.comments).selectinload(models.Comment.author),
        )
        .where(models.Entry.project_id == project_id)
        .order_by(
            models.Entry.occurred_at.asc(),
            models.Entry.created_at.asc(),
            models.Entry.id.asc(),
        )
    ).all()


def public_client_cover_media(
    db: Session, project: models.Project, entries: list[models.Entry], token: str
) -> dict | None:
    selected = (
        db.get(models.MediaAsset, project.client_cover_media_id)
        if project.client_cover_media_id
        else None
    )
    if selected and selected.project_id == project.id and selected.kind == "image":
        return public_media_payload(selected, token)

    images = [
        asset
        for entry_item in entries
        for asset in entry_item.media
        if asset.kind == "image"
    ]
    if not images:
        return None
    fallback = max(images, key=lambda asset: (asset.created_at, asset.id))
    return public_media_payload(fallback, token)


PUBLIC_PROJECT_REPORT_STATUSES = ("ready", "published")


def public_report_payload(item: models.Report, token: str) -> dict:
    data = serializers.report(item)
    if item.pdf_storage_key:
        data["pdf_url"] = f"/api/public/projects/{token}/reports/{item.id}/pdf"
    data["legacy_pdf_url"] = None
    return data


@router.get("/public/projects/{token}")
def public_project(
    token: str, pin: str | None = None, db: Session = Depends(get_db)
):
    project = public_project_by_token(db, token)
    if project.client_share_pin_hash and not pin:
        return {"requires_pin": True, "project": None}
    verify_project_pin(project, pin)
    entries = load_public_entries(db, project.id)
    reports = db.scalars(
        select(models.Report)
        .where(
            models.Report.project_id == project.id,
            models.Report.status.in_(PUBLIC_PROJECT_REPORT_STATUSES),
            models.Report.pdf_storage_key.isnot(None),
        )
        .order_by(models.Report.published_at.desc(), models.Report.created_at.desc())
    ).all()
    project_data = project_payload(db, project, details=True)
    project_data.pop("client_email", None)
    return {
        "requires_pin": bool(project.client_share_pin_hash),
        "project": project_data,
        "client_cover_media": public_client_cover_media(db, project, entries, token),
        "entries": [public_entry_payload(item, token) for item in entries],
        "reports": [public_report_payload(item, token) for item in reports],
    }


@router.post("/public/projects/{token}/entries/{entry_id}/comments", status_code=201)
def add_public_entry_comment(
    token: str,
    entry_id: str,
    payload: PublicCommentCreate,
    pin: str | None = None,
    db: Session = Depends(get_db),
):
    project = public_project_by_token(db, token)
    verify_project_pin(project, pin)
    entry_item = db.scalar(
        select(models.Entry)
        .options(
            selectinload(models.Entry.author),
            selectinload(models.Entry.stage),
            selectinload(models.Entry.media),
            selectinload(models.Entry.comments).selectinload(models.Comment.author),
        )
        .where(
            models.Entry.id == entry_id,
            models.Entry.project_id == project.id,
        )
    )
    if not entry_item:
        raise HTTPException(404, "Nie znaleziono wpisu w tym zleceniu")
    if payload.intent != "comment" and entry_item.kind != "problem":
        raise HTTPException(400, "Akcje problemu są dostępne tylko dla wpisu problemowego")
    body = payload.body.strip()
    if not body:
        if payload.intent == "comment":
            raise HTTPException(400, "Komentarz nie może być pusty")
        body = PUBLIC_COMMENT_DEFAULTS[payload.intent]
    comment_item = models.Comment(
        entry_id=entry_item.id,
        author_type="client",
        author_label="Klient",
        guest_label="Klient",
        intent=payload.intent,
        body=body,
    )
    db.add(comment_item)
    db.commit()
    db.expire_all()
    entry_item = db.scalar(
        select(models.Entry)
        .options(
            selectinload(models.Entry.author),
            selectinload(models.Entry.stage),
            selectinload(models.Entry.media),
            selectinload(models.Entry.comments).selectinload(models.Comment.author),
        )
        .where(models.Entry.id == entry_id, models.Entry.project_id == project.id)
    )
    if entry_item is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return public_entry_payload(entry_item, token)


@router.get("/public/projects/{token}/qr")
def public_project_qr(token: str, db: Session = Depends(get_db)):
    import io

    import qrcode
    from fastapi.responses import StreamingResponse

    public_project_by_token(db, token)
    output = io.BytesIO()
    qrcode.make(f"{settings.app_url}/c/{token}").save(output, format="PNG")
    output.seek(0)
    return StreamingResponse(output, media_type="image/png")


@router.get("/public/projects/{token}/media/{asset_id}")
def public_project_media(
    token: str,
    asset_id: str,
    pin: str | None = None,
    db: Session = Depends(get_db),
):
    project = public_project_by_token(db, token)
    verify_project_pin(project, pin)
    asset = db.get(models.MediaAsset, asset_id)
    if not asset or asset.project_id != project.id:
        raise HTTPException(404, "Nie znaleziono zdjęcia")
    return stored_file_response(asset.storage_key, asset.content_type)


@router.get("/public/projects/{token}/reports/{report_id}/pdf")
def public_project_report_pdf(
    token: str,
    report_id: str,
    pin: str | None = None,
    db: Session = Depends(get_db),
):
    project = public_project_by_token(db, token)
    verify_project_pin(project, pin)
    report = db.get(models.Report, report_id)
    if (
        not report
        or report.project_id != project.id
        or report.status not in PUBLIC_PROJECT_REPORT_STATUSES
        or not report.pdf_storage_key
    ):
        raise HTTPException(404, "Nie znaleziono raportu")
    return stored_file_response(
        report.pdf_storage_key, "application/pdf", f"{report.title}.pdf"
    )


@router.get("/guest/{token}")
def resolve_guest_link(token: str, db: Session = Depends(get_db)):
    item = db.scalar(
        select(models.GuestInvite).where(
            models.GuestInvite.token_hash == hash_secret(token),
            models.GuestInvite.revoked_at.is_(None),
        )
    )
    if not item or not active_date(item.expires_at):
        raise HTTPException(404, "Link jest nieaktywny lub wygasł")
    project = db.get(models.Project, item.project_id)
    if not project:
        raise HTTPException(404, "Projekt nie istnieje")
    return {
        "project_id": project.id,
        "project_name": project.name,
        "label": item.label,
        "email": item.email,
        "kind": item.kind,
        "account_type": "account" if item.email else "link_only",
        "permission": item.permission,
        "expires_at": serializers.iso(item.expires_at),
    }


def load_entries(db: Session, project_id: str):
    return db.scalars(
        select(models.Entry)
        .options(
            selectinload(models.Entry.author),
            selectinload(models.Entry.stage),
            selectinload(models.Entry.media),
            selectinload(models.Entry.comments).selectinload(models.Comment.author),
        )
        .where(models.Entry.project_id == project_id)
        .order_by(models.Entry.occurred_at.desc(), models.Entry.created_at.desc())
    ).all()


@router.get("/projects/{project_id}/entries")
def list_entries(project_id: str, request: Request, db: Session = Depends(get_db)):
    access = get_project_access(request, db, project_id)
    if not access.can_view_history():
        return []
    return [serializers.entry(item) for item in load_entries(db, project_id)]


@router.post("/projects/{project_id}/entries", status_code=201)
def create_entry(
    project_id: str,
    payload: EntryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id)
    access.require_add()
    if payload.stage_id:
        stage = db.get(models.ProjectStage, payload.stage_id)
        if not stage or stage.project_id != project_id:
            raise HTTPException(400, "Nieprawidłowy etap")
    stage_id = payload.stage_id or default_entry_stage_id(db, project_id)
    if payload.client_ref:
        existing = db.scalar(
            select(models.Entry).where(
                models.Entry.project_id == project_id,
                models.Entry.client_ref == payload.client_ref,
            )
        )
        if existing:
            return serializers.entry(existing)
    item = models.Entry(
        project_id=project_id,
        stage_id=stage_id,
        author_id=access.user.id if access.user else None,
        guest_label=access.guest.label if access.guest else None,
        kind=payload.kind,
        body=payload.body.strip(),
        transcript=payload.transcript.strip(),
        occurred_at=payload.occurred_at or now(),
        problem_status="open" if payload.kind == "problem" else None,
        client_ref=payload.client_ref,
    )
    db.add(item)
    if access.project.status == PROJECT_STATUS_ASSIGNED:
        access.project.status = PROJECT_STATUS_IN_PROGRESS
    # Full completed/reopen rules belong to step 5B; 5A only starts work on progress.
    members = db.scalars(
        select(models.ProjectMember).where(
            models.ProjectMember.project_id == project_id,
            models.ProjectMember.user_id != (access.user.id if access.user else ""),
        )
    ).all()
    for member in members:
        member_user = db.get(models.User, member.user_id)
        db.add(
            models.Notification(
                user_id=member.user_id,
                kind="problem" if payload.kind == "problem" else "entry",
                title=(
                    f"Nowy problem: {access.project.name}"
                    if payload.kind == "problem"
                    else f"Nowy wpis: {access.project.name}"
                ),
                body=payload.body[:300],
                data={"project_id": project_id},
            )
        )
        if member_user:
            db.add(
                models.Job(
                    job_type="send_email",
                    payload={
                        "to": member_user.email,
                        "subject": (
                            f"Problem w projekcie {access.project.name}"
                            if payload.kind == "problem"
                            else f"Nowy wpis w projekcie {access.project.name}"
                        ),
                        "text": (
                            f"W projekcie „{access.project.name}” pojawił się nowy wpis.\n\n"
                            f"{payload.body[:1000]}\n\nOtwórz: {settings.app_url}/app"
                        ),
                    },
                )
            )
    db.commit()
    db.refresh(item)
    return serializers.entry(item)


def entry_access(request: Request, db: Session, entry_id: str):
    item = db.get(models.Entry, entry_id)
    if not item:
        raise HTTPException(404, "Nie znaleziono wpisu")
    return item, get_project_access(request, db, item.project_id)


@router.patch("/entries/{entry_id}")
def update_entry(
    entry_id: str,
    payload: EntryUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    item, access = entry_access(request, db, entry_id)
    access.require_add()
    if (
        access.user
        and item.author_id != access.user.id
        and not access.can_manage()
    ):
        raise HTTPException(403, "Możesz edytować tylko własne wpisy")
    if payload.stage_id:
        stage = db.get(models.ProjectStage, payload.stage_id)
        if not stage or stage.project_id != item.project_id:
            raise HTTPException(400, "Nieprawidłowy etap")
    if payload.problem_status is not None and item.kind != "problem":
        raise HTTPException(400, "Status problemu można zmienić tylko dla wpisu problemowego")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    return serializers.entry(item)


@router.delete("/entries/{entry_id}")
def delete_entry(
    entry_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    item, access = entry_access(request, db, entry_id)
    if not access.user:
        raise HTTPException(403, "Brak uprawnień do usuwania dokumentacji")
    if item.author_id != access.user.id and not access.can_manage():
        raise HTTPException(403, "Możesz usuwać tylko własne wpisy")
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.post("/entries/{entry_id}/comments", status_code=201)
def add_comment(
    entry_id: str,
    payload: CommentCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    item, access = entry_access(request, db, entry_id)
    if not (access.can_view_history() or access.can_add()):
        raise HTTPException(403, "Brak dostępu do komentarzy")
    comment_item = models.Comment(
        entry_id=item.id,
        author_id=access.user.id if access.user else None,
        guest_label=access.guest.label if access.guest else None,
        author_type="user" if access.user else "guest",
        author_label=access.label,
        intent=payload.intent,
        body=payload.body.strip(),
    )
    db.add(comment_item)
    members = db.scalars(
        select(models.ProjectMember).where(
            models.ProjectMember.project_id == item.project_id,
            models.ProjectMember.user_id != (access.user.id if access.user else ""),
        )
    ).all()
    for member in members:
        member_user = db.get(models.User, member.user_id)
        db.add(
            models.Notification(
                user_id=member.user_id,
                kind="comment",
                title=f"Nowy komentarz: {access.project.name}",
                body=payload.body[:300],
                data={"project_id": item.project_id, "entry_id": item.id},
            )
        )
        if member_user:
            db.add(
                models.Job(
                    job_type="send_email",
                    payload={
                        "to": member_user.email,
                        "subject": f"Nowy komentarz: {access.project.name}",
                        "text": (
                            f"Nowy komentarz w projekcie „{access.project.name}”:\n\n"
                            f"{payload.body[:1000]}\n\nOtwórz: {settings.app_url}/app"
                        ),
                    },
                )
            )
    db.commit()
    db.refresh(comment_item)
    return serializers.comment(comment_item)


@router.post("/projects/{project_id}/transcribe")
def transcribe_recording(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id)
    access.require_add()
    if not settings.enable_server_transcription:
        raise HTTPException(503, "Transkrypcja backendowa jest wylaczona")
    content_type = (file.content_type or "audio/webm").lower()
    if not (
        content_type.startswith("audio/") or content_type.startswith("video/")
    ):
        raise HTTPException(415, "Prześlij nagranie głosowe")
    content = file.file.read()
    if not content:
        raise HTTPException(400, "Nagranie jest puste")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "Nagranie jest zbyt duże")
    try:
        text = transcribe_upload(file.filename or "nagranie.webm", content_type, content)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    return {"text": text}


@router.post("/entries/{entry_id}/media", status_code=201)
def upload_media(
    entry_id: str,
    request: Request,
    file: UploadFile = File(...),
    client_ref: str | None = Form(default=None),
    purpose: str = Form(default="attachment"),
    db: Session = Depends(get_db),
):
    entry_item, access = entry_access(request, db, entry_id)
    access.require_add()
    if client_ref:
        existing = db.scalar(
            select(models.MediaAsset).where(
                models.MediaAsset.project_id == entry_item.project_id,
                models.MediaAsset.client_ref == client_ref,
            )
        )
        if existing:
            return serializers.media(existing)

    content_type = (file.content_type or "application/octet-stream").lower()
    if content_type.startswith("image/"):
        kind = "image"
    elif content_type.startswith("audio/") or content_type.startswith("video/"):
        kind = "audio"
    else:
        raise HTTPException(415, "Dozwolone są zdjęcia i nagrania audio")

    asset = models.MediaAsset(
        id=models.uuid4(),
        project_id=entry_item.project_id,
        entry_id=entry_item.id,
        owner_user_id=access.user.id if access.user else None,
        kind=kind,
        purpose=(
            purpose
            if purpose in {"attachment", "voice_description", "voice_note"}
            else "attachment"
        ),
        original_name=(file.filename or "plik")[:260],
        content_type=content_type,
        size_bytes=0,
        sha256="",
        storage_provider=storage.provider,
        storage_key="pending",
        client_ref=client_ref,
        status="uploading",
    )
    db.add(asset)
    asset.storage_key = storage.media_key(
        entry_item.project_id, asset.id, asset.original_name
    )
    try:
        size, digest = storage.write_stream(
            asset.storage_key,
            file.file,
            max_bytes=settings.max_upload_mb * 1024 * 1024,
        )
        asset.size_bytes = size
        asset.sha256 = digest
        asset.status = "ready"
        if kind == "audio" and settings.enable_server_transcription:
            db.add(
                models.Job(
                    job_type="transcribe",
                    payload={"asset_id": asset.id, "entry_id": entry_item.id},
                )
            )
        db.commit()
    except ValueError as exc:
        db.rollback()
        storage.delete(asset.storage_key)
        raise HTTPException(413, str(exc))
    except Exception:
        db.rollback()
        storage.delete(asset.storage_key)
        raise
    return serializers.media(asset)


@router.get("/media/{asset_id}")
def get_media(asset_id: str, request: Request, db: Session = Depends(get_db)):
    asset = db.get(models.MediaAsset, asset_id)
    if not asset:
        raise HTTPException(404, "Nie znaleziono pliku")
    get_project_access(request, db, asset.project_id)
    return stored_file_response(
        asset.storage_key,
        asset.content_type,
        asset.original_name if asset.kind != "image" else None,
    )


@router.delete("/media/{asset_id}")
def delete_media(asset_id: str, request: Request, db: Session = Depends(get_db)):
    asset = db.get(models.MediaAsset, asset_id)
    if not asset:
        raise HTTPException(404, "Nie znaleziono pliku")
    access = get_project_access(request, db, asset.project_id, allow_guest=False)
    if asset.owner_user_id != access.user.id and not access.can_manage():
        raise HTTPException(403, "Brak uprawnień do usunięcia pliku")
    storage.delete(asset.storage_key)
    db.delete(asset)
    db.commit()
    return {"ok": True}


def require_project_pdf_access(access: ProjectAccess) -> None:
    if access.guest and not access.can_view_history():
        raise HTTPException(403, "Ten link nie ma dostępu do historii raportu")


def generated_report_title(report_type: str, report_date: date | None) -> str:
    if report_type == "daily":
        selected = report_date or now().date()
        return f"Raport dzienny - {selected.strftime('%d.%m.%Y')}"
    return f"Raport końcowy - {now().strftime('%d.%m.%Y')}"


def generated_report_period(
    report_type: str, report_date: date | None
) -> tuple[datetime | None, datetime | None, str | None]:
    if report_type != "daily":
        return None, now(), None
    selected = report_date or now().date()
    start = datetime.combine(selected, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(selected, datetime.max.time(), tzinfo=timezone.utc)
    return start, end, selected.isoformat()


def generated_report_request(payload: ReportCreate) -> tuple[str | None, date | None]:
    raw_type = (payload.model_extra or {}).get("type")
    if raw_type is None:
        return None, None
    if raw_type not in {"daily", "final"}:
        raise HTTPException(422, "Nieprawidlowy typ raportu PDF")
    raw_date = (payload.model_extra or {}).get("date")
    if raw_date in (None, ""):
        return raw_type, None
    if isinstance(raw_date, date):
        return raw_type, raw_date
    try:
        return raw_type, date.fromisoformat(str(raw_date))
    except ValueError:
        raise HTTPException(422, "Nieprawidlowa data raportu")


@router.get("/projects/{project_id}/report.pdf")
def get_project_report_pdf(
    project_id: str,
    request: Request,
    report_type: Literal["daily", "final"] = Query("daily", alias="type"),
    report_date: date | None = Query(None, alias="date"),
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=True)
    require_project_pdf_access(access)
    try:
        filename, pdf_bytes = render_project_report_pdf(
            db,
            access,
            report_type=report_type,
            report_date=report_date,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise report_pdf_generation_error(exc) from exc
    encoded_name = quote(filename.replace('"', ""))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
        },
    )


@router.get("/projects/{project_id}/reports")
def list_reports(project_id: str, request: Request, db: Session = Depends(get_db)):
    access = get_project_access(request, db, project_id, allow_guest=True)
    require_project_pdf_access(access)
    query = select(models.Report).where(models.Report.project_id == project_id)
    if not access.can_manage():
        query = query.where(models.Report.pdf_storage_key.isnot(None))
    items = db.scalars(query.order_by(models.Report.created_at.desc())).all()
    return [serializers.report(item) for item in items]


@router.post("/projects/{project_id}/reports", status_code=202)
def create_report(
    project_id: str,
    payload: ReportCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    generated_type, report_date = generated_report_request(payload)
    if generated_type:
        access = get_project_access(request, db, project_id, allow_guest=True)
        require_project_pdf_access(access)
        generation_lock = acquire_report_generation_lock(project_id)
        if generation_lock is None:
            raise HTTPException(
                409,
                "Raport jest już generowany, spróbuj za chwilę",
            )
        try:
            filename, pdf_bytes = render_project_report_pdf(
                db,
                access,
                report_type=generated_type,
                report_date=report_date,
            )
            period_from, period_to, report_date_value = generated_report_period(
                generated_type, report_date
            )
            report_id = models.uuid4()
            pdf_key = storage.report_key(project_id, report_id)
            created_by_id = access.user.id if access.user else access.project.created_by_id
            generated_by_label = access.label
            db.commit()
            storage.write_bytes(pdf_key, pdf_bytes)
            item = models.Report(
                id=report_id,
                project_id=project_id,
                created_by_id=created_by_id,
                title=generated_report_title(generated_type, report_date),
                report_type=generated_type,
                status="ready",
                content={
                    "generated_by_label": generated_by_label,
                    "filename": filename,
                    "report_date": report_date_value,
                    "snapshot": True,
                },
                period_from=period_from,
                period_to=period_to,
                published_at=now(),
                pdf_storage_key=pdf_key,
            )
            db.add(item)
            try:
                db.commit()
            except Exception:
                storage.delete(pdf_key)
                raise
            return serializers.report(item)
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise report_pdf_generation_error(exc) from exc
        finally:
            release_report_generation_lock(project_id, generation_lock)

    access = get_project_access(request, db, project_id, allow_guest=False)
    access.require_manage()
    if not payload.title:
        raise HTTPException(422, "Podaj tytuł raportu")
    item = models.Report(
        project_id=project_id,
        created_by_id=access.user.id,
        title=payload.title.strip(),
        report_type=payload.report_type,
        status="generating",
        period_from=payload.period_from,
        period_to=payload.period_to,
    )
    db.add(item)
    db.flush()
    db.add(
        models.Job(
            job_type="generate_report",
            payload={"report_id": item.id},
        )
    )
    db.commit()
    return serializers.report(item)


@router.get("/projects/{project_id}/reports/{report_id}.pdf")
def get_project_report_pdf_snapshot(
    project_id: str,
    report_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    access = get_project_access(request, db, project_id, allow_guest=True)
    require_project_pdf_access(access)
    item = db.get(models.Report, report_id)
    if not item or item.project_id != project_id:
        raise HTTPException(404, "Nie znaleziono raportu")
    if not item.pdf_storage_key:
        raise HTTPException(404, "Raport PDF nie jest jeszcze gotowy")
    return stored_file_response(
        item.pdf_storage_key,
        "application/pdf",
        f"{item.title}.pdf",
    )


def report_with_access(
    report_id: str, request: Request, db: Session, manage: bool = False
):
    item = db.get(models.Report, report_id)
    if not item:
        raise HTTPException(404, "Nie znaleziono raportu")
    access = get_project_access(request, db, item.project_id, allow_guest=not manage)
    if manage:
        access.require_manage()
    else:
        require_project_pdf_access(access)
    return item, access


@router.get("/reports/{report_id}")
def get_report(report_id: str, request: Request, db: Session = Depends(get_db)):
    item, _ = report_with_access(report_id, request, db)
    return serializers.report(item)


@router.patch("/reports/{report_id}")
def update_report(
    report_id: str,
    payload: ReportUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    item, _ = report_with_access(report_id, request, db, manage=True)
    if item.status == "published":
        raise HTTPException(409, "Opublikowanego raportu nie można edytować")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    item.status = "draft"
    db.commit()
    return serializers.report(item)


@router.post("/reports/{report_id}/regenerate", status_code=202)
def regenerate_report(
    report_id: str, request: Request, db: Session = Depends(get_db)
):
    item, _ = report_with_access(report_id, request, db, manage=True)
    item.status = "generating"
    db.add(
        models.Job(job_type="generate_report", payload={"report_id": item.id})
    )
    db.commit()
    return serializers.report(item)


@router.post("/reports/{report_id}/publish")
def publish_report(
    report_id: str,
    payload: ReportPublish,
    request: Request,
    db: Session = Depends(get_db),
):
    item, access = report_with_access(report_id, request, db, manage=True)
    if not item.content:
        raise HTTPException(409, "Poczekaj na wygenerowanie treści raportu")
    for old_share in db.scalars(
        select(models.ReportShare).where(models.ReportShare.report_id == item.id)
    ).all():
        old_share.active = False

    raw_token = random_token(36)
    share = models.ReportShare(
        report_id=item.id,
        token_hash=hash_secret(raw_token),
        pin_hash=hash_secret(payload.pin) if payload.pin else None,
        expires_at=(
            now() + timedelta(days=payload.expires_in_days)
            if payload.expires_in_days
            else None
        ),
    )
    db.add(share)
    item.status = "published"
    item.published_at = now()
    client_token = ensure_client_share(access.project)
    share_url = f"{settings.app_url}/c/{client_token}"
    pdf_key = storage.report_key(item.project_id, item.id)
    try:
        pdf_bytes = render_pdf(db, item, share_url)
        storage.write_bytes(pdf_key, pdf_bytes)
    except Exception as exc:
        db.rollback()
        raise report_pdf_generation_error(exc) from exc
    item.pdf_storage_key = pdf_key
    db.commit()
    return {
        "report": serializers.report(item),
        "url": share_url,
        "token": raw_token,
        "requires_pin": bool(payload.pin),
        "qr_url": f"/api/public/projects/{client_token}/qr",
        "pdf_url": f"/api/public/reports/{raw_token}/pdf",
        "client_url": share_url,
    }


@router.delete("/reports/{report_id}")
def delete_report(
    report_id: str, request: Request, db: Session = Depends(get_db)
):
    item, _ = report_with_access(report_id, request, db, manage=True)
    if item.pdf_storage_key:
        storage.delete(item.pdf_storage_key)
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.get("/reports/{report_id}/pdf")
def get_report_pdf(
    report_id: str, request: Request, db: Session = Depends(get_db)
):
    item, _ = report_with_access(report_id, request, db)
    if not item.pdf_storage_key:
        raise HTTPException(404, "Raport PDF nie jest jeszcze gotowy")
    return stored_file_response(
        item.pdf_storage_key, "application/pdf", f"{item.title}.pdf"
    )


def public_share(db: Session, raw_token: str) -> tuple[models.ReportShare, models.Report]:
    share = db.scalar(
        select(models.ReportShare).where(
            models.ReportShare.token_hash == hash_secret(raw_token),
            models.ReportShare.active.is_(True),
        )
    )
    if not share or not active_date(share.expires_at):
        raise HTTPException(404, "Link jest nieaktywny lub wygasł")
    report_item = db.get(models.Report, share.report_id)
    if not report_item:
        raise HTTPException(404, "Nie znaleziono raportu")
    return share, report_item


def verify_share_pin(share: models.ReportShare, pin: str | None):
    if share.pin_hash and (not pin or not verify_secret(pin, share.pin_hash)):
        raise HTTPException(401, "Raport wymaga prawidłowego kodu PIN")


@router.get("/public/reports/{token}")
def public_report(
    token: str, pin: str | None = None, db: Session = Depends(get_db)
):
    share, item = public_share(db, token)
    if share.pin_hash and not pin:
        return {"requires_pin": True, "report": None}
    verify_share_pin(share, pin)
    project = db.get(models.Project, item.project_id)
    return {
        "requires_pin": bool(share.pin_hash),
        "report": serializers.report(item),
        "project": project_payload(db, project, details=True),
    }


@router.get("/public/reports/{token}/pdf")
def public_report_pdf(
    token: str, pin: str | None = None, db: Session = Depends(get_db)
):
    share, item = public_share(db, token)
    verify_share_pin(share, pin)
    if not item.pdf_storage_key:
        raise HTTPException(404, "Brak pliku PDF")
    return stored_file_response(
        item.pdf_storage_key, "application/pdf", f"{item.title}.pdf"
    )


@router.get("/public/reports/{token}/qr")
def public_report_qr(token: str, db: Session = Depends(get_db)):
    import io

    import qrcode
    from fastapi.responses import StreamingResponse

    public_share(db, token)
    output = io.BytesIO()
    qrcode.make(f"{settings.app_url}/r/{token}").save(output, format="PNG")
    output.seek(0)
    return StreamingResponse(output, media_type="image/png")


@router.get("/public/reports/{token}/media/{asset_id}")
def public_report_media(
    token: str,
    asset_id: str,
    pin: str | None = None,
    db: Session = Depends(get_db),
):
    share, report_item = public_share(db, token)
    verify_share_pin(share, pin)
    media_ids: set[str] = set()
    for stage_group in (report_item.content or {}).get("stages", []):
        for entry_item in stage_group.get("entries", []):
            media_ids.update(entry_item.get("media_ids") or [])
    if asset_id not in media_ids:
        raise HTTPException(404, "Zdjęcie nie należy do tego raportu")
    asset = db.get(models.MediaAsset, asset_id)
    if (
        not asset
        or asset.project_id != report_item.project_id
        or asset.kind != "image"
    ):
        raise HTTPException(404, "Nie znaleziono zdjęcia")
    return stored_file_response(asset.storage_key, asset.content_type)


@router.get("/portfolio/{slug}")
def public_portfolio(slug: str, db: Session = Depends(get_db)):
    projects = db.scalars(
        select(models.Project).where(
            models.Project.portfolio_enabled.is_(True),
            models.Project.portfolio_slug == slug,
        )
    ).all()
    if not projects:
        raise HTTPException(404, "Nie znaleziono portfolio")
    result = []
    for item in projects:
        assets = db.scalars(
            select(models.MediaAsset)
            .where(
                models.MediaAsset.project_id == item.id,
                models.MediaAsset.kind == "image",
            )
            .order_by(models.MediaAsset.created_at.desc())
            .limit(12)
        ).all()
        result.append(
            {
                **project_payload(db, item),
                "images": [
                    {
                        "id": asset.id,
                        "url": f"/api/portfolio/{slug}/media/{asset.id}",
                    }
                    for asset in assets
                ],
            }
        )
    return {"slug": slug, "projects": result}


@router.get("/portfolio/{slug}/media/{asset_id}")
def public_portfolio_media(
    slug: str, asset_id: str, db: Session = Depends(get_db)
):
    asset = db.get(models.MediaAsset, asset_id)
    if not asset:
        raise HTTPException(404, "Nie znaleziono zdjęcia")
    project = db.get(models.Project, asset.project_id)
    if (
        not project
        or not project.portfolio_enabled
        or project.portfolio_slug != slug
        or asset.kind != "image"
    ):
        raise HTTPException(404, "Zdjęcie nie jest publiczne")
    return stored_file_response(asset.storage_key, asset.content_type)


@router.get("/notifications")
def notifications(
    user: models.User = Depends(require_user), db: Session = Depends(get_db)
):
    items = db.scalars(
        select(models.Notification)
        .where(models.Notification.user_id == user.id)
        .order_by(models.Notification.created_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "id": item.id,
            "kind": item.kind,
            "title": item.title,
            "body": item.body,
            "data": item.data,
            "read_at": serializers.iso(item.read_at),
            "created_at": serializers.iso(item.created_at),
        }
        for item in items
    ]


@router.post("/notifications/{notification_id}/read")
def read_notification(
    notification_id: str,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
):
    item = db.get(models.Notification, notification_id)
    if not item or item.user_id != user.id:
        raise HTTPException(404, "Nie znaleziono powiadomienia")
    item.read_at = now()
    db.commit()
    return {"ok": True}


def require_admin(user: models.User = Depends(require_user)) -> models.User:
    if not user.is_admin:
        raise HTTPException(403, "Panel dostępny tylko dla administratora")
    return user


@router.get("/admin/overview")
def admin_overview(
    _: models.User = Depends(require_admin), db: Session = Depends(get_db)
):
    users = db.scalars(select(models.User).order_by(models.User.created_at.desc())).all()
    jobs = db.scalars(
        select(models.Job).order_by(models.Job.created_at.desc()).limit(100)
    ).all()
    return {
        "counts": {
            "users": db.scalar(select(func.count(models.User.id))),
            "projects": db.scalar(select(func.count(models.Project.id))),
            "entries": db.scalar(select(func.count(models.Entry.id))),
            "media": db.scalar(select(func.count(models.MediaAsset.id))),
        },
        "users": [
            {
                **serializers.user(item),
                "created_at": serializers.iso(item.created_at),
            }
            for item in users
        ],
        "jobs": [
            {
                "id": item.id,
                "job_type": item.job_type,
                "status": item.status,
                "attempts": item.attempts,
                "last_error": item.last_error,
                "created_at": serializers.iso(item.created_at),
            }
            for item in jobs
        ],
    }


@router.post("/admin/users/{user_id}/beta")
def toggle_beta(
    user_id: str,
    active: bool = True,
    _: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(models.User, user_id)
    if not target:
        raise HTTPException(404, "Nie znaleziono użytkownika")
    entitlement = db.scalar(
        select(models.BetaEntitlement).where(
            models.BetaEntitlement.user_id == user_id
        )
    )
    if not entitlement:
        entitlement = models.BetaEntitlement(user_id=user_id, active=active)
        db.add(entitlement)
    else:
        entitlement.active = active
    db.commit()
    return {"ok": True, "active": entitlement.active}
