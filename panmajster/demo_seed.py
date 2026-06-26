from __future__ import annotations

import argparse
import base64
import hashlib
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .db import SessionLocal, init_db
from .security import hash_secret, random_token
from .storage import storage
from .templates import STAGE_TEMPLATES


DEMO_PASSWORD = "test1234"
DEMO_EMAILS = {
    "szef@majster.pl",
    "inwestor@majster.pl",
    "samodzielny@majster.pl",
    "pracownik@majster.pl",
    "pracownik2@majster.pl",
}
DEMO_WORKSPACE_NAMES = {
    "Firma Remontowo-Budowlana Majster Demo",
    "MajsterPro Warszawa",
    "Wykonawcy Inwestora Demo",
}
STAGE_TITLES = STAGE_TEMPLATES["custom"]
DEMO_IMAGE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mP8z8AARQAFAQHq"
    "6XcAAAAASUVORK5CYII="
)
DEMO_AUDIO_BYTES = b"pan-majster-demo-audio"


@dataclass
class DemoResult:
    counts: dict[str, int]
    company_statuses: dict[str, int]
    independent_statuses: dict[str, int]
    investor_statuses: dict[str, int]
    guest_links: int
    client_links: int


def _dt(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 6, max(1, min(28, day)), hour, 0, tzinfo=timezone.utc)


def _user(
    db: Session,
    email: str,
    name: str,
    profile_type: str,
    preferred_mode: str = "expanded",
    public_profile_name: str = "",
) -> models.User:
    item = db.scalar(select(models.User).where(models.User.email == email))
    if not item:
        item = models.User(email=email)
        db.add(item)
        db.flush()
    item.name = name
    item.phone = "+48 600 000 000" if profile_type in {"company_worker", "independent_contractor"} else ""
    item.profile_type = profile_type
    item.preferred_mode = preferred_mode
    item.public_profile_name = public_profile_name
    item.password_hash = hash_secret(DEMO_PASSWORD)
    entitlement = db.scalar(
        select(models.BetaEntitlement).where(models.BetaEntitlement.user_id == item.id)
    )
    if not entitlement:
        db.add(models.BetaEntitlement(user_id=item.id, active=True, note="demo seed"))
    else:
        entitlement.active = True
        entitlement.note = "demo seed"
    return item


def _workspace_member(
    db: Session, workspace: models.Workspace, user: models.User, role: str
) -> None:
    member = db.scalar(
        select(models.WorkspaceMember).where(
            models.WorkspaceMember.workspace_id == workspace.id,
            models.WorkspaceMember.user_id == user.id,
        )
    )
    if member:
        member.role = role
    else:
        db.add(
            models.WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role=role,
            )
        )


def _workspace(
    db: Session,
    owner: models.User,
    name: str,
    kind: str,
    description: str,
) -> models.Workspace:
    item = db.scalar(
        select(models.Workspace).where(
            models.Workspace.owner_id == owner.id,
            models.Workspace.name == name,
            models.Workspace.kind == kind,
        )
    )
    if not item:
        item = models.Workspace(owner_id=owner.id, name=name, kind=kind)
        db.add(item)
        db.flush()
    item.description = description
    item.phone = ""
    item.address = ""
    _workspace_member(db, item, owner, "owner")
    return item


def _worker(
    db: Session,
    owner: models.User,
    workspace: models.Workspace,
    label: str,
    note: str,
    profile_kind: str = "craftsman",
    email: str = "",
    active: bool = True,
    linked_user: models.User | None = None,
) -> models.WorkerProfile:
    item = db.scalar(
        select(models.WorkerProfile).where(
            models.WorkerProfile.workspace_id == workspace.id,
            models.WorkerProfile.label == label,
        )
    )
    if not item:
        item = models.WorkerProfile(owner_id=owner.id, workspace_id=workspace.id, label=label)
        db.add(item)
        db.flush()
    item.profile_kind = profile_kind
    item.email = email
    item.phone = "+48 600 000 000" if email else ""
    item.note = note
    item.active = active
    if linked_user:
        _workspace_member(db, workspace, linked_user, "member")
    return item


def _stage_statuses(project_status: str, active_position: int) -> list[str]:
    if project_status == "completed":
        return ["completed", "completed", "completed"]
    return [
        "completed" if index < active_position else "active" if index == active_position else "planned"
        for index in range(len(STAGE_TITLES))
    ]


def _project_member(db: Session, project: models.Project, user: models.User, role: str) -> None:
    member = db.scalar(
        select(models.ProjectMember).where(
            models.ProjectMember.project_id == project.id,
            models.ProjectMember.user_id == user.id,
        )
    )
    if member:
        member.role = role
    else:
        db.add(models.ProjectMember(project_id=project.id, user_id=user.id, role=role))


def _add_demo_media(
    db: Session,
    project: models.Project,
    entry: models.Entry,
    owner: models.User | None,
    kind: str,
    original_name: str,
    content_type: str,
    content: bytes,
) -> None:
    asset_id = str(uuid4())
    storage_key = storage.media_key(project.id, asset_id, original_name)
    if storage.provider == "database":
        size = len(content)
        digest = hashlib.sha256(content).hexdigest()
        db.add(
            models.StoredBlob(
                storage_key=storage_key,
                content=content,
                size_bytes=size,
                sha256=digest,
            )
        )
    else:
        size, digest = storage.write_bytes(storage_key, content)
    db.add(
        models.MediaAsset(
            id=asset_id,
            project_id=project.id,
            entry_id=entry.id,
            owner_user_id=owner.id if owner else None,
            kind=kind,
            purpose="attachment",
            original_name=original_name,
            content_type=content_type,
            size_bytes=size,
            sha256=digest,
            storage_provider=storage.provider,
            storage_key=storage_key,
            status="ready",
        )
    )


def _entry_pack(prefix: str, include_problem: bool = False) -> list[tuple[int, int, str, str, str | None]]:
    entries = [
        (0, 0, f"{prefix}: dokumentacja startowa i przygotowanie miejsca pracy.", "update", None),
        (2, 1, f"{prefix}: wykonano kolejny zakres prac zgodnie z ustaleniami.", "update", None),
    ]
    if include_problem:
        entries.append(
            (3, 1, f"{prefix}: zgłoszono problem do wyjaśnienia z klientem.", "problem", "open")
        )
    entries.append((5, 2, f"{prefix}: podsumowanie i odbiór zakresu prac.", "update", None))
    return entries


def _project(
    db: Session,
    owner: models.User,
    name: str,
    status: str,
    workspace: models.Workspace | None,
    worker: models.WorkerProfile | None,
    client_name: str,
    address: str,
    planned_start: date,
    planned_end: date,
    uncertainty_days: int,
    amount: str,
    description: str,
    active_stage: int = 1,
    entries: list[tuple[int, int, str, str, str | None]] | None = None,
    worker_user: models.User | None = None,
    guest_link: bool = False,
) -> models.Project:
    project = models.Project(
        workspace_id=workspace.id if workspace else None,
        worker_profile_id=worker.id if worker else None,
        created_by_id=owner.id,
        name=name,
        client_name=client_name,
        client_email="",
        address=address,
        description=description,
        status=status,
        template="custom",
        planned_start_date=planned_start,
        planned_end_date=planned_end,
        schedule_uncertainty_days=uncertainty_days,
        contract_amount=Decimal(amount),
        contract_currency="PLN",
        started_at=_dt(planned_start.day) if status != "assigned" else None,
        finished_at=_dt(planned_end.day, 16) if status == "completed" else None,
        client_share_token=random_token(30),
        client_share_active=True,
    )
    db.add(project)
    db.flush()
    _project_member(db, project, owner, "owner")
    if worker_user:
        _project_member(db, project, worker_user, "contributor")

    for position, title in enumerate(STAGE_TITLES):
        db.add(
            models.ProjectStage(
                project_id=project.id,
                title=title,
                position=position,
                status=_stage_statuses(status, active_stage)[position],
            )
        )
    db.flush()
    stages = {
        stage.position: stage
        for stage in db.scalars(select(models.ProjectStage).where(models.ProjectStage.project_id == project.id))
    }
    for index, (offset, stage_pos, body, kind, problem_status) in enumerate(entries or []):
        entry = models.Entry(
            project_id=project.id,
            stage_id=stages[stage_pos].id,
            author_id=worker_user.id if worker_user else owner.id,
            guest_label=None if worker_user else (worker.label if worker and not worker.email else None),
            kind=kind,
            body=body,
            transcript=body if index == 1 else "",
            occurred_at=_dt(planned_start.day + offset, 10 + offset),
            problem_status=problem_status,
        )
        db.add(entry)
        db.flush()
        if index == 0:
            _add_demo_media(db, project, entry, worker_user or owner, "image", "demo-zdjecie.png", "image/png", DEMO_IMAGE_BYTES)
        if index == 1:
            _add_demo_media(db, project, entry, worker_user or owner, "audio", "demo-audio.webm", "audio/webm", DEMO_AUDIO_BYTES)
        if kind == "problem":
            db.add(
                models.Comment(
                    entry_id=entry.id,
                    author_id=owner.id,
                    author_type="user",
                    author_label=owner.name,
                    intent="comment",
                    body="Dzięki za zgłoszenie, sprawdzimy to przed kolejną wizytą.",
                )
            )

    if guest_link and worker:
        db.add(
            models.GuestInvite(
                project_id=project.id,
                workspace_id=workspace.id if workspace else None,
                worker_profile_id=worker.id,
                label=worker.label,
                email=worker.email,
                kind="worker",
                permission="history",
                token_hash=hash_secret(random_token(36)),
                expires_at=None,
                created_by_id=owner.id,
            )
        )
    return project


def _reset_demo_data(db: Session) -> None:
    demo_users = db.scalars(select(models.User).where(models.User.email.in_(DEMO_EMAILS))).all()
    user_ids = {item.id for item in demo_users}
    workspace_ids = set(
        db.scalars(
            select(models.Workspace.id).where(
                or_(
                    models.Workspace.owner_id.in_(user_ids) if user_ids else False,
                    models.Workspace.name.in_(DEMO_WORKSPACE_NAMES),
                )
            )
        )
    )
    worker_ids = set(
        db.scalars(
            select(models.WorkerProfile.id).where(
                or_(
                    models.WorkerProfile.owner_id.in_(user_ids) if user_ids else False,
                    models.WorkerProfile.workspace_id.in_(workspace_ids) if workspace_ids else False,
                    models.WorkerProfile.email.in_(DEMO_EMAILS),
                )
            )
        )
    )
    project_ids = set(
        db.scalars(
            select(models.Project.id).where(
                or_(
                    models.Project.created_by_id.in_(user_ids) if user_ids else False,
                    models.Project.workspace_id.in_(workspace_ids) if workspace_ids else False,
                    models.Project.worker_profile_id.in_(worker_ids) if worker_ids else False,
                )
            )
        )
    )
    report_ids = set(
        db.scalars(select(models.Report.id).where(models.Report.project_id.in_(project_ids) if project_ids else False))
    )
    entry_ids = set(
        db.scalars(select(models.Entry.id).where(models.Entry.project_id.in_(project_ids) if project_ids else False))
    )
    media_keys = set(
        db.scalars(
            select(models.MediaAsset.storage_key).where(
                models.MediaAsset.project_id.in_(project_ids) if project_ids else False
            )
        )
    )

    if report_ids:
        db.execute(delete(models.ReportShare).where(models.ReportShare.report_id.in_(report_ids)))
    if project_ids:
        db.execute(delete(models.Report).where(models.Report.project_id.in_(project_ids)))
        db.execute(delete(models.GuestInvite).where(models.GuestInvite.project_id.in_(project_ids)))
        db.execute(delete(models.Invitation).where(models.Invitation.project_id.in_(project_ids)))
        db.execute(delete(models.ProjectMember).where(models.ProjectMember.project_id.in_(project_ids)))
        db.execute(delete(models.ProjectStage).where(models.ProjectStage.project_id.in_(project_ids)))
        db.execute(delete(models.MediaAsset).where(models.MediaAsset.project_id.in_(project_ids)))
    if entry_ids:
        db.execute(delete(models.Comment).where(models.Comment.entry_id.in_(entry_ids)))
        db.execute(delete(models.Entry).where(models.Entry.id.in_(entry_ids)))
    if media_keys:
        db.execute(delete(models.StoredBlob).where(models.StoredBlob.storage_key.in_(media_keys)))
        if storage.provider != "database":
            for key in media_keys:
                try:
                    storage.delete(key)
                except Exception:
                    pass
    if workspace_ids:
        db.execute(delete(models.GuestInvite).where(models.GuestInvite.workspace_id.in_(workspace_ids)))
        db.execute(delete(models.Invitation).where(models.Invitation.workspace_id.in_(workspace_ids)))
        db.execute(delete(models.WorkspaceMember).where(models.WorkspaceMember.workspace_id.in_(workspace_ids)))
    if worker_ids:
        db.execute(delete(models.GuestInvite).where(models.GuestInvite.worker_profile_id.in_(worker_ids)))
        db.execute(delete(models.WorkerProfile).where(models.WorkerProfile.id.in_(worker_ids)))
    if project_ids:
        db.execute(delete(models.Project).where(models.Project.id.in_(project_ids)))
    if workspace_ids:
        db.execute(delete(models.Workspace).where(models.Workspace.id.in_(workspace_ids)))
    if user_ids:
        db.execute(delete(models.Notification).where(models.Notification.user_id.in_(user_ids)))
        db.execute(delete(models.ProjectMember).where(models.ProjectMember.user_id.in_(user_ids)))
        db.execute(delete(models.WorkspaceMember).where(models.WorkspaceMember.user_id.in_(user_ids)))
        db.execute(delete(models.OtpCode).where(models.OtpCode.email.in_(DEMO_EMAILS)))
    db.flush()


def seed_demo_data(db: Session, reset: bool = False, yes: bool = False) -> DemoResult:
    settings = get_settings()
    init_db()
    if reset and not yes:
        raise RuntimeError("Reset demo wymaga flagi --yes.")
    if reset and settings.is_production and os.getenv("PANMAJSTER_ALLOW_DEMO_RESET") != "1":
        raise RuntimeError("Reset demo w production wymaga PANMAJSTER_ALLOW_DEMO_RESET=1.")
    if reset:
        _reset_demo_data(db)

    owner = _user(db, "szef@majster.pl", "Szef Firmy Testowej", "company_owner")
    investor = _user(db, "inwestor@majster.pl", "Inwestor Testowy", "investor")
    independent = _user(
        db,
        "samodzielny@majster.pl",
        "Samodzielny Majster Testowy",
        "independent_contractor",
        public_profile_name="Remonty Kowalski Warszawa",
    )
    worker1_user = _user(db, "pracownik@majster.pl", "Paweł Glazurnik", "company_worker", "field")
    worker2_user = _user(db, "pracownik2@majster.pl", "Marek Hydraulik", "company_worker", "field")

    company = _workspace(db, owner, "MajsterPro Warszawa", "company", "Demo: firma z majstrami, ekipami i link-only wykonawcami.")
    investor_space = _workspace(db, investor, "Wykonawcy Inwestora Demo", "personal", "Demo: zewnętrzni wykonawcy inwestora.")

    w_pawel = _worker(db, owner, company, "Paweł Glazurnik", "glazurnik, łazienki, wykończenia", email="pracownik@majster.pl", linked_user=worker1_user)
    w_marek = _worker(db, owner, company, "Marek Hydraulik", "hydraulika, instalacje, biały montaż", email="pracownik2@majster.pl", linked_user=worker2_user)
    w_link_glaz = _worker(db, owner, company, "Ekipa Glazurnicza Link", "glazura, łazienki, biały montaż", "crew")
    w_link_elektryk = _worker(db, owner, company, "Elektryk Link", "elektryka, oświetlenie, pomiary")
    w_link_hydraulik = _worker(db, owner, company, "Hydraulik Link", "awarie, instalacje wodne")
    _worker(db, owner, company, "Malarz Nieaktywny Demo", "malowanie, tapetowanie", active=False)

    inv_workers = {
        "max": _worker(db, investor, investor_space, "Max-Pol Remonty", "remonty, wykończenia", "crew"),
        "garden": _worker(db, investor, investor_space, "Zielone Ogrody", "ogrody, tarasy, pielęgnacja", "crew"),
        "clean": _worker(db, investor, investor_space, "CleanPro Sprzątanie", "sprzątanie po remoncie", "crew"),
        "jan": _worker(db, investor, investor_space, "Jan Hydraulik", "hydraulik, awarie"),
    }

    company_specs = [
        ("Wykończenie mieszkania deweloperskiego — 64 m²", "in_progress", w_pawel, worker1_user, "Anna Maj", "ul. Bukowińska 12, Warszawa", date(2026, 6, 18), date(2026, 7, 8), "68500", _entry_pack("Wykończenie mieszkania", True), False),
        ("Awaria instalacji wodnej", "in_progress", w_marek, worker2_user, "Duża Pepa", "Milanowska 18, Warszawa", date(2026, 6, 20), date(2026, 6, 24), "4200", _entry_pack("Awaria instalacji", True), False),
        ("Remont klatki schodowej", "assigned", w_link_glaz, None, "Wspólnota Demo", "ul. Wspólna 7, Warszawa", date(2026, 6, 26), date(2026, 7, 12), "28000", [], True),
        ("Montaż oświetlenia LED", "completed", w_link_elektryk, None, "Biuro Demo", "ul. Prosta 10, Warszawa", date(2026, 5, 3), date(2026, 5, 9), "9600", _entry_pack("Oświetlenie LED"), True),
        ("Przegląd hydrauliczny lokalu", "completed", w_link_hydraulik, None, "Lokal Demo", "ul. Mokra 2, Warszawa", date(2026, 5, 14), date(2026, 5, 15), "1800", _entry_pack("Przegląd hydrauliczny"), True),
    ]
    for spec in company_specs:
        _project(db, owner, spec[0], spec[1], company, spec[2], spec[4], spec[5], spec[6], spec[7], 3, spec[8], f"Demo firmowe: {spec[0]}", active_stage=1 if spec[1] != "completed" else 2, entries=spec[9], worker_user=spec[3], guest_link=spec[10])

    independent_specs = [
        ("Remont łazienki — Mokotów", "completed", "Arni", "Działka", date(2026, 4, 1), date(2026, 4, 20), "12500", _entry_pack("Remont łazienki", True)),
        ("Montaż kuchni na wymiar — Ursynów", "in_progress", "Klient demo", "ul. Alternatywy 4, Warszawa", date(2026, 6, 11), date(2026, 6, 27), "17800", _entry_pack("Kuchnia na wymiar")),
        ("Malowanie mieszkania 52 m² — Wola", "assigned", "Pani Ewa", "ul. Sienna 20, Warszawa", date(2026, 6, 25), date(2026, 6, 28), "3600", []),
        ("Drobne poprawki po remoncie", "completed", "Klient techniczny", "ul. Testowa 5, Warszawa", date(2026, 5, 8), date(2026, 5, 9), "900", _entry_pack("Poprawki po remoncie")),
    ]
    for name, status, client, address, start, end, amount, entries in independent_specs:
        _project(db, independent, name, status, None, None, client, address, start, end, 1, amount, f"Demo samodzielnego majstra: {name}", active_stage=1 if status != "completed" else 2, entries=entries)

    investor_specs = [
        ("Remont mieszkania pod wynajem", "in_progress", inv_workers["max"], date(2026, 6, 7), date(2026, 7, 5), "120000", _entry_pack("Remont inwestora", True)),
        ("Ogród przy domu", "in_progress", inv_workers["garden"], date(2026, 6, 5), date(2026, 7, 18), "45000", _entry_pack("Ogród inwestora")),
        ("Naprawa po zalaniu", "assigned", inv_workers["jan"], date(2026, 7, 6), date(2026, 7, 8), "5200", []),
        ("Sprzątanie po remoncie apartamentu", "completed", inv_workers["clean"], date(2026, 5, 4), date(2026, 5, 6), "3200", _entry_pack("Sprzątanie inwestora")),
    ]
    for name, status, worker, start, end, amount, entries in investor_specs:
        _project(db, investor, name, status, investor_space, worker, "Inwestor prywatny", "Warszawa", start, end, 4, amount, f"Demo inwestora: {name}", active_stage=1 if status != "completed" else 2, entries=entries)

    db.commit()
    return demo_counts(db)


def _status_counts(db: Session, user_email: str) -> dict[str, int]:
    user = db.scalar(select(models.User).where(models.User.email == user_email))
    if not user:
        return {}
    rows = db.execute(
        select(models.Project.status, func.count(models.Project.id))
        .where(models.Project.created_by_id == user.id)
        .group_by(models.Project.status)
    ).all()
    return dict(rows)


def _table_count(db: Session, model) -> int:
    return int(db.scalar(select(func.count(model.id))) or 0)


def demo_counts(db: Session) -> DemoResult:
    counts = {
        "users": _table_count(db, models.User),
        "workspaces": _table_count(db, models.Workspace),
        "workspace_members": _table_count(db, models.WorkspaceMember),
        "worker_profiles": _table_count(db, models.WorkerProfile),
        "projects": _table_count(db, models.Project),
        "project_members": _table_count(db, models.ProjectMember),
        "entries": _table_count(db, models.Entry),
        "media_assets": _table_count(db, models.MediaAsset),
        "guest_invites": _table_count(db, models.GuestInvite),
        "reports": _table_count(db, models.Report),
        "report_shares": _table_count(db, models.ReportShare),
    }
    projects = db.scalars(select(models.Project)).all()
    client_links = sum(1 for project in projects if project.client_share_token and project.client_share_active)
    return DemoResult(
        counts=counts,
        company_statuses=_status_counts(db, "szef@majster.pl"),
        independent_statuses=_status_counts(db, "samodzielny@majster.pl"),
        investor_statuses=_status_counts(db, "inwestor@majster.pl"),
        guest_links=counts["guest_invites"],
        client_links=client_links,
    )


def _format_result(result: DemoResult) -> str:
    lines = ["Demo seed complete.", "Counts:"]
    for key, value in result.counts.items():
        lines.append(f"- {key}: {value}")
    lines.append(f"- public_client_links: {result.client_links}")
    lines.append(f"- guest_link_only_links: {result.guest_links}")
    lines.append(f"Company statuses: {dict(Counter(result.company_statuses))}")
    lines.append(f"Independent statuses: {dict(Counter(result.independent_statuses))}")
    lines.append(f"Investor statuses: {dict(Counter(result.investor_statuses))}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset and seed Pan Majster demo data.")
    parser.add_argument("--reset", action="store_true", help="Remove existing demo data before seeding.")
    parser.add_argument("--yes", action="store_true", help="Required confirmation for destructive reset.")
    args = parser.parse_args()
    init_db()
    with SessionLocal() as db:
        result = seed_demo_data(db, reset=args.reset, yes=args.yes)
        print(_format_result(result))


if __name__ == "__main__":
    main()
