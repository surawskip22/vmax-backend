from collections.abc import Generator
import re

from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


settings = get_settings()
is_postgres = settings.normalized_database_url.startswith("postgresql")
database_schema = settings.database_schema if is_postgres else None


class Base(DeclarativeBase):
    metadata = MetaData(schema=database_schema)


connect_args = (
    {"check_same_thread": False, "timeout": 30}
    if settings.normalized_database_url.startswith("sqlite")
    else {}
)
engine = create_engine(
    settings.normalized_database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_database_schema(connection) -> None:
    if not database_schema:
        return
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database_schema):
        raise ValueError("Nieprawidłowa nazwa schematu bazy danych")
    quoted = connection.dialect.identifier_preparer.quote(database_schema)
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401
    from .security import hash_secret

    with engine.begin() as connection:
        ensure_database_schema(connection)
        Base.metadata.create_all(bind=connection)
        if not settings.is_production:
            db = Session(bind=connection)
            seed_development_accounts(db, models, hash_secret)


def seed_development_accounts(db: Session, models, hash_secret) -> None:
    accounts = [
        ("szef@majster.pl", "Szef Firmy Testowej", "company_owner"),
        ("inwestor@majster.pl", "Inwestor Testowy", "investor"),
        ("samodzielny@majster.pl", "Samodzielny Majster", "independent_contractor"),
        ("pracownik@majster.pl", "Pracownik Firmy Testowej", "company_worker"),
    ]
    users = {}
    for email, name, profile_type in accounts:
        user = db.scalar(select(models.User).where(models.User.email == email))
        if not user:
            user = models.User(
                email=email,
                name=name,
                phone="",
                profile_type=profile_type,
                preferred_mode="expanded",
                password_hash=hash_secret("test1234"),
            )
            db.add(user)
            db.flush()
        else:
            if user.profile_type != profile_type:
                user.profile_type = profile_type
            if not user.preferred_mode:
                user.preferred_mode = "expanded"
            if not user.password_hash:
                user.password_hash = hash_secret("test1234")
        users[email] = user
        entitlement = db.scalar(
            select(models.BetaEntitlement).where(
                models.BetaEntitlement.user_id == user.id
            )
        )
        if not entitlement:
            db.add(models.BetaEntitlement(user_id=user.id, active=True, note="local seed"))

    owner = users["szef@majster.pl"]
    worker = users["pracownik@majster.pl"]
    workspace = db.scalar(
        select(models.Workspace).where(
            models.Workspace.owner_id == owner.id,
            models.Workspace.kind == "company",
        )
    )
    if not workspace:
        workspace = models.Workspace(
            name="Firma testowa Szefa",
            kind="company",
            owner_id=owner.id,
            description="Lokalna firma testowa do pracy nad Pan Majster",
        )
        db.add(workspace)
        db.flush()
    owner_membership = db.scalar(
        select(models.WorkspaceMember).where(
            models.WorkspaceMember.workspace_id == workspace.id,
            models.WorkspaceMember.user_id == owner.id,
        )
    )
    if not owner_membership:
        db.add(
            models.WorkspaceMember(
                workspace_id=workspace.id,
                user_id=owner.id,
                role="owner",
            )
        )
    worker_membership = db.scalar(
        select(models.WorkspaceMember).where(
            models.WorkspaceMember.workspace_id == workspace.id,
            models.WorkspaceMember.user_id == worker.id,
        )
    )
    if not worker_membership:
        db.add(
            models.WorkspaceMember(
                workspace_id=workspace.id,
                user_id=worker.id,
                role="member",
            )
        )
    worker_profile = db.scalar(
        select(models.WorkerProfile).where(
            models.WorkerProfile.workspace_id == workspace.id,
            models.WorkerProfile.email == worker.email,
        )
    )
    if not worker_profile:
        db.add(
            models.WorkerProfile(
                owner_id=owner.id,
                workspace_id=workspace.id,
                label="Pracownik Firmy Testowej",
                profile_kind="craftsman",
                email=worker.email,
                phone="",
                note="Lokalny seed: Majster - czlonek firmy",
            )
        )
    db.flush()
