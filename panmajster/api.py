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
    is_company_worker,
    is_independent_contractor,
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
