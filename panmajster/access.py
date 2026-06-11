from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import models
from .security import hash_secret


PROJECT_ROLE_LEVEL = {
    "viewer": 10,
    "contributor": 20,
    "manager": 30,
    "owner": 40,
}
GUEST_LEVEL = {"view": 10, "add": 20, "history": 20}


def now() -> datetime:
    return datetime.now(timezone.utc)


def active_date(value: datetime | None) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value > now()


def current_user(request: Request, db: Session, required: bool = True) -> models.User | None:
    raw_token = request.cookies.get("pm_session")
    if not raw_token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            raw_token = authorization[7:].strip()
    if not raw_token:
        if required:
            raise HTTPException(401, "Zaloguj się, aby kontynuować")
        return None

    session = db.scalar(
        select(models.UserSession).where(
            models.UserSession.token_hash == hash_secret(raw_token)
        )
    )
    if not session or not active_date(session.expires_at):
        if required:
            raise HTTPException(401, "Sesja wygasła")
        return None
    session.last_seen_at = now()
    return session.user


@dataclass
class ProjectAccess:
    project: models.Project
    user: models.User | None
    role: str | None = None
    guest: models.GuestInvite | None = None

    @property
    def label(self) -> str:
        if self.user:
            return self.user.name or self.user.email
        return self.guest.label if self.guest else "Gość"

    def can_view_history(self) -> bool:
        return bool(self.user or (self.guest and self.guest.permission in {"history", "view"}))

    def can_add(self) -> bool:
        if self.user:
            return PROJECT_ROLE_LEVEL.get(self.role or "", 0) >= 20
        return bool(self.guest and self.guest.permission in {"add", "history"})

    def can_manage(self) -> bool:
        return bool(
            self.user and PROJECT_ROLE_LEVEL.get(self.role or "", 0) >= 30
        )

    def require_add(self) -> None:
        if not self.can_add():
            raise HTTPException(403, "Brak uprawnień do dodawania wpisów")

    def require_manage(self) -> None:
        if not self.can_manage():
            raise HTTPException(403, "Brak uprawnień do zarządzania projektem")


def project_role(db: Session, project_id: str, user_id: str) -> str | None:
    member = db.scalar(
        select(models.ProjectMember).where(
            models.ProjectMember.project_id == project_id,
            models.ProjectMember.user_id == user_id,
        )
    )
    return member.role if member else None


def get_project_access(
    request: Request,
    db: Session,
    project_id: str,
    allow_guest: bool = True,
) -> ProjectAccess:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Nie znaleziono projektu")

    user = current_user(request, db, required=False)
    if user:
        role = project_role(db, project.id, user.id)
        if role:
            return ProjectAccess(project=project, user=user, role=role)

    if allow_guest:
        raw_token = request.headers.get("x-guest-token") or request.query_params.get(
            "guest_token"
        )
        if raw_token:
            guest = db.scalar(
                select(models.GuestInvite).where(
                    models.GuestInvite.project_id == project.id,
                    models.GuestInvite.token_hash == hash_secret(raw_token),
                    models.GuestInvite.revoked_at.is_(None),
                )
            )
            if guest and active_date(guest.expires_at):
                return ProjectAccess(project=project, user=None, guest=guest)

    raise HTTPException(403, "Brak dostępu do projektu")


def user_projects_query(user_id: str):
    return (
        select(models.Project, models.ProjectMember.role)
        .join(
            models.ProjectMember,
            models.ProjectMember.project_id == models.Project.id,
        )
        .where(models.ProjectMember.user_id == user_id)
        .order_by(models.Project.updated_at.desc())
    )


def can_manage_workspace(db: Session, workspace_id: str, user_id: str) -> bool:
    return (
        db.scalar(
            select(models.WorkspaceMember).where(
                models.WorkspaceMember.workspace_id == workspace_id,
                models.WorkspaceMember.user_id == user_id,
                models.WorkspaceMember.role.in_(["owner", "admin"]),
            )
        )
        is not None
    )


def find_pending_invitations(db: Session, email: str):
    return db.scalars(
        select(models.Invitation).where(
            models.Invitation.email == email,
            models.Invitation.accepted_at.is_(None),
            models.Invitation.revoked_at.is_(None),
            or_(
                models.Invitation.expires_at.is_(None),
                models.Invitation.expires_at > now(),
            ),
        )
    ).all()
