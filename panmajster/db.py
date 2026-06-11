from collections.abc import Generator
import re

from sqlalchemy import MetaData, create_engine, text
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

    with engine.begin() as connection:
        ensure_database_schema(connection)
        Base.metadata.create_all(bind=connection)
