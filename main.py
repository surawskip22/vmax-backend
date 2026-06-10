from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import openpyxl
import zoneinfo
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import Column, DateTime, Integer, MetaData, String, create_engine, desc, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 20},
    )
else:
    engine = create_engine(DATABASE_URL, pool_size=20, max_overflow=10)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

SCHEMA_VERSION = "rejestrator-admin-worker-v1"
APP_VERSION = "rejestrator-2026-06-10.3"
ADMIN_ROLES = {"ADMIN", "SUPER_ADMIN", "MANAGER", "TEAM_LEADER"}
EXPORT_DIR = Path(tempfile.gettempdir()) / "rctp_exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def get_now() -> datetime:
    try:
        return datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow() + timedelta(hours=2)


class GlobalSetting(Base):
    __tablename__ = "global_settings"
    id = Column(Integer, primary_key=True, index=True)
    setting_type = Column(String, unique=True, index=True)
    value = Column(String)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    global_id = Column(String, unique=True, index=True)
    role = Column(String, default="EMPLOYEE")
    name = Column(String, unique=True, index=True)
    pin = Column(String)
    hire_date = Column(DateTime, default=get_now)
    notes = Column(String, default="")


class Activity(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    color = Column(String, default="#0b57d0")


class WorkLog(Base):
    __tablename__ = "work_logs"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    task_name = Column(String)
    start_time = Column(DateTime, default=get_now)
    end_time = Column(DateTime, nullable=True)
    date_str = Column(String, index=True)
    is_autoclosed = Column(Integer, default=0)


class Productivity(Base):
    __tablename__ = "productivity"
    id = Column(Integer, primary_key=True, index=True)
    date_str = Column(String, index=True)
    username = Column(String, index=True)
    paczki = Column(Integer, default=0)
    produkty = Column(Integer, default=0)
    mins = Column(Integer, default=0)


class Problem(Base):
    __tablename__ = "problems"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    description = Column(String)
    timestamp = Column(DateTime, default=get_now)
    is_resolved = Column(Integer, default=0)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    sender = Column(String, default="Piotrek")
    receiver = Column(String, index=True)
    content = Column(String)
    timestamp = Column(DateTime, default=get_now)
    is_read = Column(Integer, default=0)
    reply = Column(String, nullable=True)
    is_archived = Column(Integer, default=0)


class AlertDismiss(Base):
    __tablename__ = "alert_dismiss"
    id = Column(Integer, primary_key=True, index=True)
    alert_key = Column(String, unique=True, index=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def format_minutes(total_minutes: int) -> str:
    total_minutes = max(int(total_minutes or 0), 0)
    return f"{total_minutes // 60}h {total_minutes % 60}m"


def duration_minutes(start: datetime | None, end: datetime | None) -> int:
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() / 60))


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(GlobalSetting).filter(GlobalSetting.setting_type == key).first()
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(GlobalSetting).filter(GlobalSetting.setting_type == key).first()
    if row:
        row.value = value
    else:
        db.add(GlobalSetting(setting_type=key, value=value))


def get_root_admin(db: Session) -> tuple[str, str]:
    return (
        get_setting(db, "admin_login", "Piotrek") or "Piotrek",
        get_setting(db, "admin_pass", "123") or "123",
    )


def generate_global_id(db: Session) -> str:
    max_id = 0
    for user in db.query(User).all():
        if user.global_id and str(user.global_id).isdigit():
            max_id = max(max_id, int(user.global_id))
    return f"{max_id + 1:05d}"


def raw_schema_version() -> str:
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT value FROM global_settings "
                    "WHERE setting_type = 'schema_version' LIMIT 1"
                )
            ).first()
            return str(row[0]) if row else ""
    except Exception:
        return ""


def reset_database() -> None:
    metadata = MetaData()
    metadata.reflect(bind=engine)
    metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def ensure_seed_data(db: Session, reset_defaults: bool) -> None:
    set_setting(db, "schema_version", SCHEMA_VERSION)
    if reset_defaults or not get_setting(db, "admin_login"):
        set_setting(db, "admin_login", "Piotrek")
    if reset_defaults or not get_setting(db, "admin_pass"):
        set_setting(db, "admin_pass", "123")

    admin = db.query(User).filter(User.name == "Piotrek").first()
    if not admin:
        admin = User(
            global_id="00001",
            role="SUPER_ADMIN",
            name="Piotrek",
            pin="123",
            hire_date=get_now(),
        )
        db.add(admin)
    else:
        admin.role = "SUPER_ADMIN"
        if reset_defaults:
            admin.pin = "123"
        admin.global_id = admin.global_id or "00001"

    worker = db.query(User).filter(User.name == "pracownik").first()
    if not worker:
        db.add(
            User(
                global_id="00002",
                role="EMPLOYEE",
                name="pracownik",
                pin="123",
                hire_date=get_now(),
            )
        )
    else:
        worker.role = "EMPLOYEE"
        if reset_defaults:
            worker.pin = "123"
        worker.global_id = worker.global_id or "00002"

    defaults = [
        ("Praca", "#0b57d0"),
        ("Przerwa", "#f29900"),
        ("Pakowanie Paczek", "#188038"),
        ("Inne", "#8e24aa"),
    ]
    for name, color in defaults:
        if not db.query(Activity).filter(Activity.name == name).first():
            db.add(Activity(name=name, color=color))


def bootstrap_database() -> None:
    reset_defaults = raw_schema_version() != SCHEMA_VERSION
    if reset_defaults:
        reset_database()
    else:
        Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        ensure_seed_data(db, reset_defaults)
        db.commit()


bootstrap_database()

app = FastAPI(title="Rejestrator Czasu Pracy")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_response_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-App-Version"] = APP_VERSION
    return response


def html_path(filename: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def user_payload(user: User) -> dict[str, Any]:
    return {
        "ok": True,
        "name": user.name,
        "role": user.role,
        "global_id": user.global_id or "",
        "is_admin": user.role in ADMIN_ROLES,
    }


def get_user(db: Session, username: str) -> User:
    user = db.query(User).filter(User.name == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Nie znaleziono konta.")
    return user


def require_employee(db: Session, username: str) -> User:
    user = get_user(db, username)
    if user.role in ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Konto administratora nie rejestruje czasu pracy.",
        )
    return user


def ensure_activity(db: Session, name: str) -> str:
    task_name = clean_text(name)
    if task_name:
        return task_name
    first = db.query(Activity).order_by(Activity.name.asc()).first()
    return first.name if first else "Praca"


def history_payload(db: Session, username: str, month: str = "") -> dict[str, Any]:
    query = db.query(WorkLog).filter(WorkLog.username == username)
    if month:
        query = query.filter(WorkLog.date_str.startswith(month))
    logs = query.order_by(desc(WorkLog.start_time), desc(WorkLog.id)).limit(500).all()

    history = []
    current_task = None
    for log in logs:
        history.append(
            {
                "data": log.date_str,
                "zadanie": log.task_name,
                "start": log.start_time.strftime("%H:%M") if log.start_time else "",
                "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa...",
                "czas": (
                    format_minutes(duration_minutes(log.start_time, log.end_time))
                    if log.end_time
                    else "-"
                ),
            }
        )
        if not log.end_time and current_task is None:
            current_task = {
                "name": log.task_name,
                "start_time": log.start_time.strftime("%H:%M") if log.start_time else "",
            }
    return {"hist": history, "currentTask": current_task}


def close_active_log(db: Session, username: str, when: datetime) -> None:
    active = (
        db.query(WorkLog)
        .filter(WorkLog.username == username, WorkLog.end_time.is_(None))
        .order_by(desc(WorkLog.id))
        .first()
    )
    if active:
        active.end_time = when


def create_stop_marker(db: Session, username: str, when: datetime) -> None:
    db.add(
        WorkLog(
            username=username,
            task_name="Zakończenie pracy",
            start_time=when,
            end_time=when,
            date_str=when.strftime("%Y-%m-%d"),
        )
    )


@app.get("/")
def root_page():
    return FileResponse(html_path("index.html"))


@app.get("/rejestrator")
def rejestrator_page():
    return FileResponse(html_path("index.html"))


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "app": "Rejestrator Czasu Pracy",
        "version": APP_VERSION,
    }


@app.get("/api/public")
def public_data(db: Session = Depends(get_db)):
    return {
        "employees": [
            user.name for user in db.query(User).order_by(User.name.asc()).all()
        ]
    }


@app.get("/api/config")
def config(db: Session = Depends(get_db)):
    admins = {
        user.name: user.pin
        for user in db.query(User)
        .filter(User.role.in_(list(ADMIN_ROLES)))
        .order_by(User.name.asc())
        .all()
    }
    employees = [
        user.name
        for user in db.query(User)
        .filter(~User.role.in_(list(ADMIN_ROLES)))
        .order_by(User.name.asc())
        .all()
    ]
    activities = [
        activity.name for activity in db.query(Activity).order_by(Activity.name.asc()).all()
    ]
    return {
        "admins": admins,
        "pracownicy": employees,
        "aktywnosci": activities,
    }


@app.post("/api/auth/login")
def login(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    pin = clean_text(req.get("pin"))
    user = db.query(User).filter(User.name == username, User.pin == pin).first()
    if not user:
        raise HTTPException(status_code=401, detail="Błędny login lub PIN.")
    return user_payload(user)


@app.post("/api/auth/change-pin")
@app.post("/api/user/change-pin")
def change_pin(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("name") or req.get("username"))
    old_pin = clean_text(req.get("oldPin"))
    new_pin = clean_text(req.get("newPin"))
    user = get_user(db, username)

    if not new_pin:
        return {"ok": False, "msg": "Nowy PIN jest pusty."}
    if user.role not in ADMIN_ROLES and old_pin and user.pin != old_pin:
        return {"ok": False, "msg": "Stary PIN jest niepoprawny."}

    user.pin = new_pin
    if user.name == "Piotrek":
        set_setting(db, "admin_pass", new_pin)
    db.commit()
    return {"ok": True, "msg": "PIN został zmieniony."}


@app.post("/api/user/history")
def user_history(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    require_employee(db, username)
    return history_payload(db, username, clean_text(req.get("month")))


@app.post("/api/user/action")
def user_action(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    require_employee(db, username)
    action_type = clean_text(req.get("type")).upper()
    now = get_now()

    if action_type not in {"START", "TASK", "STOP"}:
        raise HTTPException(status_code=400, detail="Nieznana akcja.")

    close_active_log(db, username, now)
    if action_type in {"START", "TASK"}:
        db.add(
            WorkLog(
                username=username,
                task_name=ensure_activity(db, req.get("task")),
                start_time=now,
                date_str=now.strftime("%Y-%m-%d"),
            )
        )
    else:
        create_stop_marker(db, username, now)

    db.commit()
    return history_payload(db, username, clean_text(req.get("month")))


@app.post("/api/user/correct-task")
def correct_task(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    require_employee(db, username)
    last_log = (
        db.query(WorkLog)
        .filter(
            WorkLog.username == username,
            WorkLog.task_name != "Zakończenie pracy",
        )
        .order_by(desc(WorkLog.id))
        .first()
    )
    if not last_log:
        return {"ok": False, "msg": "Brak wpisu do poprawy."}
    last_log.task_name = ensure_activity(db, req.get("task"))
    db.commit()
    return history_payload(db, username, clean_text(req.get("month")))


@app.post("/api/user/problem")
def report_problem(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    description = clean_text(req.get("description"))
    require_employee(db, username)
    if not description:
        return {"ok": False, "msg": "Opis jest pusty."}
    db.add(Problem(username=username, description=description))
    db.commit()
    return {"ok": True}


@app.post("/api/user/messages/unread")
def unread_messages(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    messages = (
        db.query(Message)
        .filter(Message.receiver == username, Message.is_read == 0)
        .order_by(Message.timestamp.asc())
        .all()
    )
    return [
        {
            "id": message.id,
            "sender": message.sender,
            "content": message.content,
            "time": message.timestamp.strftime("%H:%M"),
        }
        for message in messages
    ]


@app.post("/api/user/messages/reply")
def reply_message(req: dict, db: Session = Depends(get_db)):
    message = db.query(Message).filter(Message.id == req.get("msg_id")).first()
    if not message:
        return {"ok": False}
    message.is_read = 1
    message.reply = clean_text(req.get("reply"))
    db.commit()
    return {"ok": True}


@app.get("/api/admin/active-sessions")
def active_sessions(db: Session = Depends(get_db)):
    sessions: dict[str, list[dict[str, str]]] = {}
    logs = (
        db.query(WorkLog)
        .filter(WorkLog.end_time.is_(None))
        .order_by(WorkLog.task_name.asc(), WorkLog.start_time.asc())
        .all()
    )
    for log in logs:
        sessions.setdefault(log.task_name, []).append(
            {
                "user": log.username,
                "start": log.start_time.strftime("%H:%M") if log.start_time else "",
            }
        )
    return sessions


@app.post("/api/admin/messages/send")
def send_admin_message(req: dict, db: Session = Depends(get_db)):
    receivers = req.get("receivers") or []
    content = clean_text(req.get("content"))
    if not content:
        return {"ok": False, "msg": "Wiadomość jest pusta."}

    employee_names = {
        user.name
        for user in db.query(User).filter(~User.role.in_(list(ADMIN_ROLES))).all()
    }
    if "ALL" in receivers:
        receivers = sorted(employee_names)

    sent = 0
    for receiver in dict.fromkeys(receivers):
        if receiver not in employee_names:
            continue
        db.add(Message(sender="Piotrek", receiver=receiver, content=content))
        sent += 1
    db.commit()
    return {"ok": True, "sent": sent}


@app.post("/api/admin/live-session/close")
def close_live_session(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    now = get_now()
    close_active_log(db, username, now)
    create_stop_marker(db, username, now)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/live-session/close-all")
def close_all_live_sessions(db: Session = Depends(get_db)):
    now = get_now()
    active_users = [
        row.username
        for row in db.query(WorkLog).filter(WorkLog.end_time.is_(None)).all()
    ]
    for username in active_users:
        close_active_log(db, username, now)
        create_stop_marker(db, username, now)
    db.commit()
    return {"ok": True, "closed": len(active_users)}


@app.post("/api/admin/live-session/edit")
def edit_live_session(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    active = (
        db.query(WorkLog)
        .filter(WorkLog.username == username, WorkLog.end_time.is_(None))
        .order_by(desc(WorkLog.id))
        .first()
    )
    if not active:
        return {"ok": False, "msg": "Sesja nie jest aktywna."}

    active.task_name = ensure_activity(db, req.get("task"))
    start_time = clean_text(req.get("start_time"))
    if start_time:
        active.start_time = datetime.strptime(
            f"{active.date_str} {start_time}",
            "%Y-%m-%d %H:%M",
        )
    db.commit()
    return {"ok": True}


@app.get("/api/admin/alerts")
def admin_alerts(db: Session = Depends(get_db)):
    alerts = []
    replies = (
        db.query(Message)
        .filter(
            Message.is_read == 1,
            Message.reply.is_not(None),
            Message.is_archived == 0,
        )
        .order_by(desc(Message.timestamp))
        .all()
    )
    for reply in replies:
        alerts.append(
            {
                "type": "msg",
                "id": reply.id,
                "date": reply.timestamp.strftime("%Y-%m-%d"),
                "text": f"{reply.receiver} odpisał: {reply.reply}",
            }
        )

    problems = (
        db.query(Problem)
        .filter(Problem.is_resolved == 0)
        .order_by(desc(Problem.timestamp))
        .all()
    )
    for problem in problems:
        alerts.append(
            {
                "type": "prob",
                "id": problem.id,
                "date": problem.timestamp.strftime("%Y-%m-%d"),
                "text": f"PROBLEM ({problem.username}): {problem.description}",
            }
        )

    dismissed = {
        row.alert_key for row in db.query(AlertDismiss).all()
    }
    today = get_now().strftime("%Y-%m-%d")
    today_logs = db.query(WorkLog).filter(WorkLog.date_str == today).all()
    for username in {log.username for log in today_logs}:
        user_logs = [log for log in today_logs if log.username == username]
        break_minutes = sum(
            duration_minutes(log.start_time, log.end_time)
            for log in user_logs
            if "Przerwa" in log.task_name and log.end_time
        )
        finished = any(
            log.task_name == "Zakończenie pracy" and log.end_time
            for log in user_logs
        )
        if finished and break_minutes == 0:
            key = f"sys_nobreak_{username}_{today}"
            if key not in dismissed:
                alerts.append(
                    {
                        "type": "sys",
                        "id": key,
                        "date": today,
                        "text": f"{username}: zakończył pracę bez przerwy.",
                    }
                )
        if break_minutes > 40:
            key = f"sys_longbreak_{username}_{today}"
            if key not in dismissed:
                alerts.append(
                    {
                        "type": "sys",
                        "id": key,
                        "date": today,
                        "text": f"{username}: przerwa trwała {break_minutes} min.",
                    }
                )
    return alerts


@app.post("/api/admin/alerts/dismiss-all")
def dismiss_alerts(req: dict, db: Session = Depends(get_db)):
    for alert in req.get("alerts") or []:
        alert_type = clean_text(alert.get("type"))
        alert_id = alert.get("id")
        if alert_type == "prob":
            problem = db.query(Problem).filter(Problem.id == alert_id).first()
            if problem:
                problem.is_resolved = 1
        elif alert_type == "msg":
            message = db.query(Message).filter(Message.id == alert_id).first()
            if message:
                message.is_archived = 1
        elif alert_type == "sys":
            key = clean_text(alert_id)
            if key and not db.query(AlertDismiss).filter(AlertDismiss.alert_key == key).first():
                db.add(AlertDismiss(alert_key=key))
    db.commit()
    return {"ok": True}


@app.post("/api/admin/reports")
def admin_reports(req: dict, db: Session = Depends(get_db)):
    date_from = clean_text(req.get("d1"))
    date_to = clean_text(req.get("d2"))
    username = clean_text(req.get("user"))
    query = db.query(WorkLog)
    if date_from:
        query = query.filter(WorkLog.date_str >= date_from)
    if date_to:
        query = query.filter(WorkLog.date_str <= date_to)
    if username and username != "Wszyscy":
        query = query.filter(WorkLog.username == username)

    result: dict[str, dict[str, Any]] = {}
    for log in query.order_by(WorkLog.username.asc(), WorkLog.start_time.asc()).all():
        minutes = duration_minutes(log.start_time, log.end_time)
        bucket = result.setdefault(log.username, {"total": 0, "logi": []})
        bucket["total"] += minutes
        bucket["logi"].append(
            {
                "zadanie": log.task_name,
                "data": log.date_str,
                "start": log.start_time.strftime("%H:%M") if log.start_time else "",
                "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa",
                "czas": format_minutes(minutes) if log.end_time else "-",
            }
        )
    for bucket in result.values():
        bucket["totalStr"] = format_minutes(bucket.pop("total"))
    return result


@app.post("/api/admin/productivity")
def admin_productivity(req: dict, db: Session = Depends(get_db)):
    date_from = clean_text(req.get("d1"))
    date_to = clean_text(req.get("d2"))
    query = db.query(WorkLog)
    if date_from:
        query = query.filter(WorkLog.date_str >= date_from)
    if date_to:
        query = query.filter(WorkLog.date_str <= date_to)

    chart_data: dict[str, int] = {}
    worker_details: dict[str, dict[str, int]] = {}
    task_workers: dict[str, set[str]] = {}
    for log in query.all():
        if not log.end_time or log.task_name == "Zakończenie pracy":
            continue
        minutes = duration_minutes(log.start_time, log.end_time)
        chart_data[log.task_name] = chart_data.get(log.task_name, 0) + minutes
        worker_details.setdefault(log.username, {})
        worker_details[log.username][log.task_name] = (
            worker_details[log.username].get(log.task_name, 0) + minutes
        )
        task_workers.setdefault(log.task_name, set()).add(log.username)

    packing_stats: dict[str, dict[str, int]] = {}
    prod_query = db.query(Productivity)
    if date_from:
        prod_query = prod_query.filter(Productivity.date_str >= date_from)
    if date_to:
        prod_query = prod_query.filter(Productivity.date_str <= date_to)
    for row in prod_query.all():
        stat = packing_stats.setdefault(
            row.username,
            {"paczki": 0, "produkty": 0, "mins": 0},
        )
        stat["paczki"] += row.paczki or 0
        stat["produkty"] += row.produkty or 0
        stat["mins"] += row.mins or 0

    return {
        "chartData": chart_data,
        "headcount": {task: len(users) for task, users in task_workers.items()},
        "workerDetails": worker_details,
        "packingStats": packing_stats,
    }


@app.post("/api/admin/save-productivity")
def save_productivity(req: dict, db: Session = Depends(get_db)):
    date_str = clean_text(req.get("date"))
    for update in req.get("updates") or []:
        username = clean_text(update.get("worker"))
        row = (
            db.query(Productivity)
            .filter(
                Productivity.date_str == date_str,
                Productivity.username == username,
            )
            .first()
        )
        if not row:
            row = Productivity(date_str=date_str, username=username)
            db.add(row)
        row.paczki = int(update.get("paczki") or 0)
        row.produkty = int(update.get("produkty") or 0)
        row.mins = int(float(update.get("mins") or 0))
    db.commit()
    return {"ok": True}


@app.post("/api/admin/edit-logs")
def edit_logs(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    date_str = clean_text(req.get("date"))
    logs = (
        db.query(WorkLog)
        .filter(WorkLog.username == username, WorkLog.date_str == date_str)
        .order_by(WorkLog.start_time.asc())
        .all()
    )
    return [
        {
            "id": log.id,
            "zadanie": log.task_name,
            "start": log.start_time.strftime("%H:%M") if log.start_time else "",
            "koniec": log.end_time.strftime("%H:%M") if log.end_time else "",
        }
        for log in logs
    ]


@app.post("/api/admin/update-batch")
def update_batch(req: dict, db: Session = Depends(get_db)):
    date_str = clean_text(req.get("date"))
    for update in req.get("updates") or []:
        log = db.query(WorkLog).filter(WorkLog.id == update.get("id")).first()
        if not log:
            continue
        log.task_name = clean_text(update.get("task")) or log.task_name
        start = clean_text(update.get("start"))
        end = clean_text(update.get("end"))
        if start:
            log.start_time = datetime.strptime(
                f"{date_str} {start}",
                "%Y-%m-%d %H:%M",
            )
        log.end_time = (
            datetime.strptime(f"{date_str} {end}", "%Y-%m-%d %H:%M")
            if end
            else None
        )
    db.commit()
    return {"ok": True}


def export_rows(db: Session, export_type: str, date_from: str, date_to: str) -> tuple[list[str], list[list[Any]]]:
    if export_type == "PROD":
        query = db.query(Productivity)
        if date_from:
            query = query.filter(Productivity.date_str >= date_from)
        if date_to:
            query = query.filter(Productivity.date_str <= date_to)
        rows = [
            [row.date_str, row.username, row.paczki, row.produkty, row.mins]
            for row in query.order_by(Productivity.date_str, Productivity.username).all()
        ]
        return ["Data", "Pracownik", "Paczki", "Produkty", "Minuty"], rows

    if export_type == "HR":
        users = (
            db.query(User)
            .filter(~User.role.in_(list(ADMIN_ROLES)))
            .order_by(User.name.asc())
            .all()
        )
        return (
            ["ID", "Pracownik", "Rola", "Data zatrudnienia", "Notatki"],
            [
                [
                    user.global_id or "",
                    user.name,
                    user.role,
                    user.hire_date.strftime("%Y-%m-%d") if user.hire_date else "",
                    user.notes or "",
                ]
                for user in users
            ],
        )

    query = db.query(WorkLog)
    if date_from:
        query = query.filter(WorkLog.date_str >= date_from)
    if date_to:
        query = query.filter(WorkLog.date_str <= date_to)
    rows = [
        [
            log.id,
            log.username,
            log.date_str,
            log.task_name,
            log.start_time.strftime("%H:%M") if log.start_time else "",
            log.end_time.strftime("%H:%M") if log.end_time else "",
            duration_minutes(log.start_time, log.end_time),
        ]
        for log in query.order_by(WorkLog.date_str, WorkLog.username, WorkLog.start_time).all()
    ]
    return ["ID", "Pracownik", "Data", "Aktywność", "Start", "Koniec", "Minuty"], rows


def save_csv_file(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(headers)
        writer.writerows(rows)


def save_xlsx_file(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Rejestrator"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(
            max(len(str(cell.value or "")) for cell in column) + 2,
            45,
        )
    workbook.save(path)


@app.post("/api/admin/export")
def admin_export(req: dict, db: Session = Depends(get_db)):
    export_type = clean_text(req.get("export_type")).upper() or "FULL"
    file_format = clean_text(req.get("format")).lower() or "csv"
    date_from = clean_text(req.get("d1"))
    date_to = clean_text(req.get("d2"))
    token = uuid.uuid4().hex[:10]

    if export_type == "BACKUP" and file_format == "json":
        path = EXPORT_DIR / f"backup_{token}.json"
        data = {
            "generated_at": get_now().isoformat(),
            "users": [
                {
                    "global_id": user.global_id,
                    "name": user.name,
                    "role": user.role,
                    "pin": user.pin,
                }
                for user in db.query(User).all()
            ],
            "activities": [
                {"name": activity.name, "color": activity.color}
                for activity in db.query(Activity).all()
            ],
            "work_logs": [
                {
                    "id": log.id,
                    "username": log.username,
                    "task_name": log.task_name,
                    "start_time": log.start_time.isoformat() if log.start_time else None,
                    "end_time": log.end_time.isoformat() if log.end_time else None,
                    "date_str": log.date_str,
                }
                for log in db.query(WorkLog).all()
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        headers, rows = export_rows(
            db,
            "FULL" if export_type == "BACKUP" else export_type,
            date_from,
            date_to,
        )
        extension = "xlsx" if file_format in {"xls", "xlsx"} else "csv"
        path = EXPORT_DIR / f"{export_type.lower()}_{token}.{extension}"
        if extension == "xlsx":
            save_xlsx_file(path, headers, rows)
        else:
            save_csv_file(path, headers, rows)

    return {"ok": True, "url": f"/api/download/{path.name}"}


@app.get("/api/download/{filename}")
def download_export(filename: str):
    safe_name = os.path.basename(filename)
    path = EXPORT_DIR / safe_name
    if not path.exists() or path.parent != EXPORT_DIR:
        raise HTTPException(status_code=404, detail="Plik nie istnieje.")
    return FileResponse(path, filename=safe_name)


@app.post("/api/admin/db")
def admin_database(req: dict, db: Session = Depends(get_db)):
    item_type = clean_text(req.get("type")).upper()
    action = clean_text(req.get("action")).upper()
    name = clean_text(req.get("name"))
    value = clean_text(req.get("val"))

    if item_type in {"ADMIN", "EMPLOYEE"}:
        user = db.query(User).filter(User.name == name).first()
        if action == "ADD":
            if user:
                return {"ok": False, "msg": "Takie konto już istnieje."}
            db.add(
                User(
                    global_id=generate_global_id(db),
                    role="ADMIN" if item_type == "ADMIN" else "EMPLOYEE",
                    name=name,
                    pin=value or "123",
                    hire_date=get_now(),
                )
            )
        elif action == "DELETE":
            if name == "Piotrek":
                return {"ok": False, "msg": "Nie można usunąć głównego administratora."}
            if user:
                db.delete(user)
        elif action == "EDIT_PIN":
            if not user:
                return {"ok": False, "msg": "Nie znaleziono konta."}
            if not value:
                return {"ok": False, "msg": "PIN jest pusty."}
            user.pin = value
            if name == "Piotrek":
                set_setting(db, "admin_pass", value)

    elif item_type == "ACTIVITY":
        activity = db.query(Activity).filter(Activity.name == name).first()
        if action == "ADD":
            if activity:
                return {"ok": False, "msg": "Taka aktywność już istnieje."}
            db.add(Activity(name=name, color="#0b57d0"))
        elif action == "DELETE":
            if activity:
                db.delete(activity)
    else:
        return {"ok": False, "msg": "Nieobsługiwany typ danych."}

    try:
        db.commit()
        return {"ok": True}
    except IntegrityError:
        db.rollback()
        return {"ok": False, "msg": "Nie udało się zapisać danych."}
