from __future__ import annotations

import argparse
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .db import SessionLocal, init_db
from .security import hash_secret, random_token
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
    "Wykonawcy Inwestora Demo",
}
STAGE_TITLES = STAGE_TEMPLATES["custom"]


@dataclass
class DemoResult:
    counts: dict[str, int]
    company_statuses: dict[str, int]
    independent_statuses: dict[str, int]
    investor_statuses: dict[str, int]
    guest_links: int
    client_links: int


def _dt(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 6, day, hour, 0, tzinfo=timezone.utc)


def _user(
    db: Session,
    email: str,
    name: str,
    profile_type: str,
    preferred_mode: str = "expanded",
) -> models.User:
    item = db.scalar(select(models.User).where(models.User.email == email))
    if not item:
        item = models.User(email=email)
        db.add(item)
        db.flush()
    item.name = name
    item.phone = ""
    item.profile_type = profile_type
    item.preferred_mode = preferred_mode
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
        item = models.WorkerProfile(
            owner_id=owner.id,
            workspace_id=workspace.id,
            label=label,
        )
        db.add(item)
        db.flush()
    item.profile_kind = profile_kind
    item.email = email
    item.phone = ""
    item.note = note
    item.active = active
    if linked_user:
        _workspace_member(db, workspace, linked_user, "member")
    return item


def _stage_statuses(status: str, active_position: int) -> list[str]:
    if status == "completed":
        return ["completed", "completed", "completed"]
    return [
        "completed" if index < active_position else "active" if index == active_position else "planned"
        for index in range(len(STAGE_TITLES))
    ]


def _project_member(
    db: Session, project: models.Project, user: models.User, role: str
) -> None:
    member = db.scalar(
        select(models.ProjectMember).where(
            models.ProjectMember.project_id == project.id,
            models.ProjectMember.user_id == user.id,
        )
    )
    if member:
        member.role = role
    else:
        db.add(
            models.ProjectMember(
                project_id=project.id,
                user_id=user.id,
                role=role,
            )
        )


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
    template: str = "custom",
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
        template=template,
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
    stage_by_position = {
        stage.position: stage
        for stage in db.scalars(
            select(models.ProjectStage).where(models.ProjectStage.project_id == project.id)
        )
    }
    for offset, stage_pos, body, kind, problem_status in entries or []:
        db.add(
            models.Entry(
                project_id=project.id,
                stage_id=stage_by_position[stage_pos].id,
                author_id=worker_user.id if worker_user else owner.id,
                guest_label=None if worker_user else (worker.label if worker and not worker.email else None),
                kind=kind,
                body=body,
                occurred_at=_dt(max(1, min(28, planned_start.day + offset)), 10 + offset),
                problem_status=problem_status,
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
        db.scalars(
            select(models.Report.id).where(
                models.Report.project_id.in_(project_ids) if project_ids else False
            )
        )
    )
    entry_ids = set(
        db.scalars(
            select(models.Entry.id).where(
                models.Entry.project_id.in_(project_ids) if project_ids else False
            )
        )
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
    if workspace_ids:
        db.execute(delete(models.GuestInvite).where(models.GuestInvite.workspace_id.in_(workspace_ids)))
        db.execute(delete(models.Invitation).where(models.Invitation.workspace_id.in_(workspace_ids)))
    if worker_ids:
        db.execute(delete(models.GuestInvite).where(models.GuestInvite.worker_profile_id.in_(worker_ids)))
        db.execute(delete(models.WorkerProfile).where(models.WorkerProfile.id.in_(worker_ids)))
    if project_ids:
        db.execute(delete(models.Project).where(models.Project.id.in_(project_ids)))
    if workspace_ids:
        db.execute(delete(models.WorkspaceMember).where(models.WorkspaceMember.workspace_id.in_(workspace_ids)))
        db.execute(delete(models.Workspace).where(models.Workspace.id.in_(workspace_ids)))
    if user_ids:
        db.execute(delete(models.Notification).where(models.Notification.user_id.in_(user_ids)))
        db.execute(delete(models.UserSession).where(models.UserSession.user_id.in_(user_ids)))
        db.execute(delete(models.BetaEntitlement).where(models.BetaEntitlement.user_id.in_(user_ids)))
        db.execute(delete(models.WorkspaceMember).where(models.WorkspaceMember.user_id.in_(user_ids)))
        db.execute(delete(models.ProjectMember).where(models.ProjectMember.user_id.in_(user_ids)))
        db.execute(delete(models.OtpCode).where(models.OtpCode.email.in_(DEMO_EMAILS)))
        db.execute(delete(models.User).where(models.User.id.in_(user_ids)))
    db.flush()


def _entry_pack(prefix: str, include_problem: bool = False) -> list[tuple[int, int, str, str, str | None]]:
    entries = [
        (0, 0, f"{prefix}: Raport okresowy #1 - dokumentacja przed pracami.", "update", None),
        (2, 1, f"{prefix}: Raport okresowy #2 - postęp prac.", "update", None),
    ]
    if include_problem:
        entries.append((3, 1, f"{prefix}: Zgłoszono problem z dostępnością materiału.", "problem", "resolved"))
    entries.append((5, 2, f"{prefix}: Raport końcowy - odbiór prac.", "update", None))
    return entries


def seed_demo_data(db: Session, reset: bool = False, yes: bool = False) -> DemoResult:
    settings = get_settings()
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
    )
    worker1_user = _user(db, "pracownik@majster.pl", "Paweł Glazurnik", "company_worker")
    worker2_user = _user(db, "pracownik2@majster.pl", "Marek Hydraulik", "company_worker")

    company = _workspace(
        db,
        owner,
        "Firma Remontowo-Budowlana Majster Demo",
        "company",
        "Demo: firma z majstrami, ekipami, link-only wykonawcami i zleceniami.",
    )
    investor_space = _workspace(
        db,
        investor,
        "Wykonawcy Inwestora Demo",
        "personal",
        "Demo: lista zewnętrznych wykonawców inwestora.",
    )

    w_pawel = _worker(db, owner, company, "Paweł Glazurnik", "glazurnik, łazienki, wykończenia", email="pracownik@majster.pl", linked_user=worker1_user)
    w_marek = _worker(db, owner, company, "Marek Hydraulik", "hydraulik, instalacje, biały montaż", email="pracownik2@majster.pl", linked_user=worker2_user)
    w_lazienki = _worker(db, owner, company, "Ekipa Łazienkowa Alfa", "łazienki, płytki, biały montaż", "crew", "ekipa.lazienki@majster.pl")
    w_elektryka = _worker(db, owner, company, "Ekipa Elektryczna Volt", "elektryka, oświetlenie, pomiary", "crew", "ekipa.elektryczna@majster.pl")
    w_ogrod = _worker(db, owner, company, "Ekipa Ogrodowa Bez Maila", "ogrody, tarasy, kostka", "crew")
    w_wykonczenia = _worker(db, owner, company, "Ekipa Wykończeniowa Bez Maila", "malowanie, szpachlowanie, wykończenia", "crew")
    w_mietek = _worker(db, owner, company, "Mietek Złota Rączka", "naprawy, hydraulika, drobne prace")
    _worker(db, owner, company, "Staszek Malarz Nieaktywny", "malowanie, tapetowanie", active=False)

    inv_workers = {
        "max": _worker(db, investor, investor_space, "Max-Pol Remonty", "remonty, wykończenia", "crew"),
        "ogrody": _worker(db, investor, investor_space, "Zielone Ogrody", "ogrody, tarasy, pielęgnacja", "crew"),
        "clean": _worker(db, investor, investor_space, "CleanPro Sprzątanie", "sprzątanie po remoncie, porządki", "crew"),
        "jan": _worker(db, investor, investor_space, "Jan Hydraulik", "hydraulik, awarie"),
        "elektro": _worker(db, investor, investor_space, "ElektroFix", "elektryka, oświetlenie"),
    }

    company_specs = [
        ("Remont łazienki - Wilanów", "in_progress", w_lazienki, None, "Anna Kowalska", "ul. Sejmu Czteroletniego 12, Warszawa", date(2026, 6, 10), date(2026, 6, 28), "18500", _entry_pack("Łazienka Wilanów", True), False),
        ("Instalacja elektryczna - Białołęka", "in_progress", w_elektryka, None, "Jan Zieliński", "ul. Modlińska 44, Warszawa", date(2026, 6, 12), date(2026, 6, 30), "14200", _entry_pack("Elektryka Białołęka"), False),
        ("Montaż WC i biały montaż - Ursus", "assigned", w_marek, worker2_user, "Piotr Nowak", "ul. Dzieci Warszawy 27, Warszawa", date(2026, 6, 20), date(2026, 6, 24), "4200", [], False),
        ("Taras i ogród - Piaseczno", "in_progress", w_ogrod, None, "Maria Wiśniewska", "ul. Ogrodowa 5, Piaseczno", date(2026, 6, 8), date(2026, 7, 4), "24300", _entry_pack("Taras Piaseczno"), True),
        ("Malowanie mieszkania - Praga", "in_progress", w_wykonczenia, None, "Kamil Wójcik", "ul. Targowa 80, Warszawa", date(2026, 6, 14), date(2026, 6, 25), "9800", _entry_pack("Malowanie Praga", True), True),
        ("Remont kuchni - Bemowo", "completed", w_pawel, worker1_user, "Ewa Maj", "ul. Powstańców Śląskich 10, Warszawa", date(2026, 5, 2), date(2026, 5, 18), "26500", _entry_pack("Kuchnia Bemowo"), False),
        ("Wykończenie mieszkania - Mokotów", "completed", w_lazienki, None, "Michał Lis", "ul. Puławska 118, Warszawa", date(2026, 4, 8), date(2026, 5, 10), "78000", _entry_pack("Mokotów wykończenie"), False),
        ("Wymiana okien - Żoliborz", "completed", w_mietek, None, "Karolina Brzezińska", "ul. Słowackiego 7, Warszawa", date(2026, 5, 20), date(2026, 5, 22), "11900", _entry_pack("Okna Żoliborz"), True),
        ("Naprawa hydrauliczna - Wola", "completed", w_marek, worker2_user, "Tomasz Król", "ul. Okopowa 33, Warszawa", date(2026, 5, 27), date(2026, 5, 28), "1800", _entry_pack("Hydraulika Wola"), False),
        ("Oświetlenie ogrodu - Józefosław", "completed", w_elektryka, None, "Olga Szymańska", "ul. Geodetów 15, Józefosław", date(2026, 4, 21), date(2026, 4, 29), "16800", _entry_pack("Ogród Józefosław"), False),
    ]
    for spec in company_specs:
        _project(db, owner, spec[0], spec[1], company, spec[2], spec[4], spec[5], spec[6], spec[7], 3, spec[8], f"Demo firmowe: {spec[0]}", active_stage=1 if spec[1] != "completed" else 2, entries=spec[9], worker_user=spec[3], guest_link=spec[10])

    independent_specs = [
        ("Łazienka 6 m2 - klient Nowak", "completed", date(2026, 4, 1), date(2026, 4, 16), "15500", _entry_pack("Łazienka Nowak", True)),
        ("Taras drewniany - Piaseczno", "completed", date(2026, 4, 20), date(2026, 5, 6), "22000", _entry_pack("Taras drewniany")),
        ("Montaż kuchni - Ursynów", "in_progress", date(2026, 6, 11), date(2026, 6, 27), "17800", _entry_pack("Kuchnia Ursynów")),
        ("Malowanie salonu - Mokotów", "in_progress", date(2026, 6, 15), date(2026, 6, 20), "3600", _entry_pack("Salon Mokotów")),
        ("Naprawa przecieku - Wola", "assigned", date(2026, 6, 22), date(2026, 6, 22), "900", []),
        ("Biały montaż - Wilanów", "completed", date(2026, 5, 8), date(2026, 5, 9), "2400", _entry_pack("Biały montaż")),
        ("Szybka naprawa hydrauliki - Bielany", "completed", date(2026, 5, 15), date(2026, 5, 15), "650", _entry_pack("Hydraulika Bielany")),
    ]
    for name, status, start, end, amount, entries in independent_specs:
        _project(db, independent, name, status, None, None, "Klient demo", "Warszawa", start, end, 1, amount, f"Demo samodzielnego majstra: {name}", active_stage=1 if status != "completed" else 2, entries=entries)

    investor_specs = [
        ("Remont mieszkania - Warszawa", "in_progress", inv_workers["max"], date(2026, 6, 7), date(2026, 7, 5), "120000", _entry_pack("Remont inwestora", True)),
        ("Ogród i taras - dom pod Warszawą", "in_progress", inv_workers["ogrody"], date(2026, 6, 5), date(2026, 7, 18), "45000", _entry_pack("Ogród inwestora")),
        ("Sprzątanie po remoncie - apartament", "assigned", inv_workers["clean"], date(2026, 7, 6), date(2026, 7, 7), "3200", []),
        ("Montaż kuchni - inwestycja prywatna", "completed", inv_workers["max"], date(2026, 3, 4), date(2026, 3, 25), "34000", _entry_pack("Kuchnia inwestora")),
        ("Awaria hydrauliczna - łazienka", "completed", inv_workers["jan"], date(2026, 5, 13), date(2026, 5, 13), "1100", _entry_pack("Awaria łazienka")),
        ("Oświetlenie ogrodu", "completed", inv_workers["elektro"], date(2026, 4, 6), date(2026, 4, 14), "8900", _entry_pack("Oświetlenie inwestor")),
        ("Porządkowanie działki", "completed", inv_workers["ogrody"], date(2026, 5, 1), date(2026, 5, 3), "2700", _entry_pack("Działka")),
        ("Sprzątanie garażu i piwnicy", "completed", inv_workers["clean"], date(2026, 5, 9), date(2026, 5, 10), "1600", _entry_pack("Garaż piwnica")),
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
