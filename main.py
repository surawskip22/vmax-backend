from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import zoneinfo
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import Column, DateTime, Integer, MetaData, String, create_engine, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 20})
else:
    engine = create_engine(DATABASE_URL, pool_size=20, max_overflow=10)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
BOOTSTRAP_VERSION = "rejestrator-v1"


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
    hire_date = Column(DateTime, default=lambda: get_now())
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
    start_time = Column(DateTime, default=lambda: get_now())
    end_time = Column(DateTime, nullable=True)
    date_str = Column(String, index=True)
    is_autoclosed = Column(Integer, default=0)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    sender = Column(String, default="ADMIN")
    receiver = Column(String, index=True)
    content = Column(String)
    timestamp = Column(DateTime, default=lambda: get_now())
    is_read = Column(Integer, default=0)
    reply = Column(String, nullable=True)
    is_archived = Column(Integer, default=0)


class Problem(Base):
    __tablename__ = "problems"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    description = Column(String)
    timestamp = Column(DateTime, default=lambda: get_now())
    is_resolved = Column(Integer, default=0)


def get_now() -> datetime:
    try:
        return datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow() + timedelta(hours=2)


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def format_minutes(total_minutes: int) -> str:
    total_minutes = max(int(total_minutes or 0), 0)
    return f"{total_minutes // 60}h {total_minutes % 60}m"


def calc_minutes(start: datetime | None, end: datetime | None) -> int:
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() / 60))


def html_path(filename: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
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


def ensure_root_user(db: Session) -> User:
    login, password = get_root_admin(db)
    root_user = db.query(User).filter(User.name == login).first()
    if not root_user:
        root_user = User(
            global_id="00001",
            role="SUPER_ADMIN",
            name=login,
            pin=password,
            hire_date=get_now(),
        )
        db.add(root_user)
    else:
        root_user.role = "SUPER_ADMIN"
        root_user.pin = password
        if not root_user.global_id:
            root_user.global_id = "00001"
    return root_user


def ensure_default_activities(db: Session) -> None:
    defaults = ["Praca", "Przerwa", "Inne"]
    for name in defaults:
        if not db.query(Activity).filter(Activity.name == name).first():
            db.add(Activity(name=name, color="#0b57d0"))


def reset_database() -> None:
    metadata = MetaData()
    metadata.reflect(bind=engine)
    metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def bootstrap_database() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        version = get_setting(db, "schema_version")
    if version != BOOTSTRAP_VERSION:
        reset_database()
        with SessionLocal() as db:
            set_setting(db, "schema_version", BOOTSTRAP_VERSION)
            set_setting(db, "admin_login", "Piotrek")
            set_setting(db, "admin_pass", "123")
            ensure_root_user(db)
            ensure_default_activities(db)
            db.commit()
        return

    with SessionLocal() as db:
        set_setting(db, "schema_version", BOOTSTRAP_VERSION)
        ensure_root_user(db)
        ensure_default_activities(db)
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
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def user_payload(user: User | None, db: Session | None = None) -> dict[str, Any]:
    if not user:
        return {}
    root_login, _ = get_root_admin(db) if db is not None else ("Piotrek", "123")
    return {
        "ok": True,
        "name": user.name,
        "role": user.role,
        "global_id": user.global_id or "",
        "is_root": user.name == root_login,
    }


def get_history_payload(db: Session, username: str, month: str = "") -> dict[str, Any]:
    query = db.query(WorkLog).filter(WorkLog.username == username)
    if month:
        query = query.filter(WorkLog.date_str.startswith(month))
    logs = query.order_by(desc(WorkLog.start_time), desc(WorkLog.id)).all()

    history = []
    current_task = None
    for log in logs:
        total_mins = calc_minutes(log.start_time, log.end_time)
        history.append(
            {
                "data": log.date_str,
                "zadanie": log.task_name,
                "start": log.start_time.strftime("%H:%M") if log.start_time else "",
                "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa...",
                "czas": format_minutes(total_mins) if log.end_time else "-",
            }
        )
        if current_task is None and log.end_time is None and log.task_name != "Koniec pracy":
            current_task = {
                "name": log.task_name,
                "start_time": log.start_time.strftime("%H:%M") if log.start_time else "",
            }

    return {"hist": history, "currentTask": current_task}


def close_active_log(db: Session, username: str, when: datetime | None = None) -> None:
    active_log = db.query(WorkLog).filter(WorkLog.username == username, WorkLog.end_time.is_(None)).first()
    if active_log:
        active_log.end_time = when or get_now()


def create_stop_marker(db: Session, username: str, when: datetime | None = None) -> None:
    moment = when or get_now()
    db.add(
        WorkLog(
            username=username,
            task_name="Koniec pracy",
            start_time=moment,
            end_time=moment,
            date_str=moment.strftime("%Y-%m-%d"),
        )
    )


def ensure_activity_name(db: Session, value: Any) -> str:
    task_name = clean_text(value)
    if task_name:
        return task_name
    first_activity = db.query(Activity).order_by(Activity.name.asc()).first()
    return first_activity.name if first_activity else "Praca"


@app.get("/")
def root_page():
    return FileResponse(html_path("index.html"))


@app.get("/rejestrator")
def rejestrator_page():
    return FileResponse(html_path("index.html"))


@app.get("/vmax")
def legacy_vmax():
    return RedirectResponse("/", status_code=302)


@app.get("/planner")
def legacy_planner():
    return RedirectResponse("/", status_code=302)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/public")
def public_data(db: Session = Depends(get_db)):
    employees = [u.name for u in db.query(User).order_by(User.name.asc()).all()]
    return {"employees": employees}


@app.get("/api/config")
def get_config(db: Session = Depends(get_db)):
    root_login, _ = get_root_admin(db)
    employees = [u.name for u in db.query(User).order_by(User.name.asc()).all()]
    activities = [a.name for a in db.query(Activity).order_by(Activity.name.asc()).all()]
    return {
        "admin": {"login": root_login},
        "employees": employees,
        "activities": activities,
    }


@app.post("/api/auth/login")
def login(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    pin = clean_text(req.get("pin"))
    root_login, root_pass = get_root_admin(db)

    if username == root_login and pin == root_pass:
        root_user = ensure_root_user(db)
        db.commit()
        return user_payload(root_user, db)

    user = db.query(User).filter(User.name == username, User.pin == pin).first()
    if not user:
        raise HTTPException(status_code=401, detail="Bledny login lub PIN.")
    return user_payload(user, db)


@app.post("/api/auth/change-pin")
def change_pin(req: dict, db: Session = Depends(get_db)):
    name = clean_text(req.get("name"))
    old_pin = clean_text(req.get("oldPin"))
    new_pin = clean_text(req.get("newPin"))
    is_admin = bool(req.get("isAdmin"))
    root_login, root_pass = get_root_admin(db)

    if not name or not new_pin:
        return {"ok": False, "msg": "Brak danych do zmiany PIN-u."}

    if name == root_login:
        if not is_admin and old_pin != root_pass:
            return {"ok": False, "msg": "Stary PIN jest niepoprawny."}
        set_setting(db, "admin_pass", new_pin)
        root_user = ensure_root_user(db)
        root_user.pin = new_pin
        db.commit()
        return {"ok": True, "msg": "Zmieniono PIN administratora."}

    user = db.query(User).filter(User.name == name).first()
    if not user:
        return {"ok": False, "msg": "Nie znaleziono pracownika."}
    if not is_admin and user.pin != old_pin:
        return {"ok": False, "msg": "Stary PIN jest niepoprawny."}
    user.pin = new_pin
    db.commit()
    return {"ok": True, "msg": "PIN zostal zmieniony."}


@app.post("/api/user/messages/unread")
def unread_messages(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    if not username:
        return []
    msgs = (
        db.query(Message)
        .filter(Message.receiver == username, Message.is_read == 0, Message.is_archived == 0)
        .order_by(Message.timestamp.asc())
        .all()
    )
    return [
        {
            "id": msg.id,
            "content": msg.content,
            "sender": msg.sender,
            "time": msg.timestamp.strftime("%H:%M"),
        }
        for msg in msgs
    ]


@app.post("/api/user/messages/reply")
def reply_message(req: dict, db: Session = Depends(get_db)):
    msg_id = req.get("msg_id")
    reply = clean_text(req.get("reply"))
    msg = db.query(Message).filter(Message.id == msg_id).first()
    if not msg:
        return False
    msg.is_read = 1
    msg.reply = reply or None
    db.commit()
    return True


@app.post("/api/user/problem")
def report_problem(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    description = clean_text(req.get("description"))
    if not username or not description:
        return {"ok": False}
    db.add(Problem(username=username, description=description))
    db.commit()
    return {"ok": True}


@app.post("/api/user/history")
def user_history(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    month = clean_text(req.get("month"))
    if not username:
        return {"hist": [], "currentTask": None}
    return get_history_payload(db, username, month)


@app.post("/api/user/action")
def user_action(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    action_type = clean_text(req.get("type")).upper()
    task_name = ensure_activity_name(db, req.get("task"))

    if not username:
        raise HTTPException(status_code=400, detail="Brak nazwy uzytkownika.")

    now = get_now()
    today = now.strftime("%Y-%m-%d")
    active_log = db.query(WorkLog).filter(WorkLog.username == username, WorkLog.end_time.is_(None)).first()

    if action_type in {"START", "TASK"}:
        if active_log:
            active_log.end_time = now
        db.add(
            WorkLog(
                username=username,
                task_name=task_name,
                start_time=now,
                date_str=today,
            )
        )
    elif action_type == "STOP":
        if active_log:
            active_log.end_time = now
        create_stop_marker(db, username, now)
    else:
        raise HTTPException(status_code=400, detail="Nieznany typ akcji.")

    db.commit()
    return get_history_payload(db, username, clean_text(req.get("month")))


@app.get("/api/admin/workers")
def admin_workers(db: Session = Depends(get_db)):
    root_login, _ = get_root_admin(db)
    workers = []
    for user in db.query(User).order_by(User.role.desc(), User.name.asc()).all():
        workers.append(
            {
                "name": user.name,
                "role": user.role,
                "global_id": user.global_id or "",
                "is_root": user.name == root_login,
            }
        )
    return workers


@app.post("/api/admin/workers/add")
def add_worker(req: dict, db: Session = Depends(get_db)):
    name = clean_text(req.get("name"))
    pin = clean_text(req.get("pin"))
    role = clean_text(req.get("role")) or "EMPLOYEE"
    root_login, _ = get_root_admin(db)

    if not name or not pin:
        return {"ok": False, "msg": "Podaj login i PIN."}
    if name == root_login:
        return {"ok": False, "msg": "To konto jest zarezerwowane dla administratora."}
    if db.query(User).filter(User.name == name).first():
        return {"ok": False, "msg": "Pracownik juz istnieje."}

    next_id = 1
    for user in db.query(User).all():
        if user.global_id and str(user.global_id).isdigit():
            next_id = max(next_id, int(user.global_id) + 1)

    db.add(
        User(
            global_id=f"{next_id:05d}",
            role=role if role != "SUPER_ADMIN" else "EMPLOYEE",
            name=name,
            pin=pin,
            hire_date=get_now(),
        )
    )
    db.commit()
    return {"ok": True}


@app.post("/api/admin/workers/delete")
def delete_worker(req: dict, db: Session = Depends(get_db)):
    name = clean_text(req.get("name"))
    root_login, _ = get_root_admin(db)
    if not name:
        return {"ok": False, "msg": "Brak pracownika."}
    if name == root_login:
        return {"ok": False, "msg": "Konta administratora nie mozna usunac."}

    active_log = db.query(WorkLog).filter(WorkLog.username == name, WorkLog.end_time.is_(None)).first()
    if active_log:
        active_log.end_time = get_now()

    db.query(Message).filter((Message.sender == name) | (Message.receiver == name)).delete(synchronize_session=False)
    db.query(Problem).filter(Problem.username == name).delete(synchronize_session=False)
    db.query(User).filter(User.name == name).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/workers/pin")
def admin_worker_pin(req: dict, db: Session = Depends(get_db)):
    name = clean_text(req.get("name"))
    pin = clean_text(req.get("pin"))
    root_login, _ = get_root_admin(db)
    if not name or not pin:
        return {"ok": False, "msg": "Brak danych."}
    if name == root_login:
        return {"ok": False, "msg": "PIN administratora zmieniaj przez panel naglowka."}
    user = db.query(User).filter(User.name == name).first()
    if not user:
        return {"ok": False, "msg": "Pracownik nie istnieje."}
    user.pin = pin
    db.commit()
    return {"ok": True}


@app.get("/api/admin/activities")
def admin_activities(db: Session = Depends(get_db)):
    return [
        {"name": activity.name, "color": activity.color or "#0b57d0"}
        for activity in db.query(Activity).order_by(Activity.name.asc()).all()
    ]


@app.post("/api/admin/activities/add")
def add_activity(req: dict, db: Session = Depends(get_db)):
    name = clean_text(req.get("name"))
    color = clean_text(req.get("color")) or "#0b57d0"
    if not name:
        return {"ok": False, "msg": "Podaj nazwe czynnosci."}
    if db.query(Activity).filter(Activity.name == name).first():
        return {"ok": False, "msg": "Taka czynność juz istnieje."}
    db.add(Activity(name=name, color=color))
    db.commit()
    return {"ok": True}


@app.post("/api/admin/activities/delete")
def delete_activity(req: dict, db: Session = Depends(get_db)):
    name = clean_text(req.get("name"))
    if not name:
        return {"ok": False, "msg": "Brak czynnosci."}
    activities = db.query(Activity).all()
    if len(activities) <= 1:
        return {"ok": False, "msg": "Musi zostac przynajmniej jedna czynność."}
    db.query(Activity).filter(Activity.name == name).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


@app.get("/api/admin/active-sessions")
def active_sessions(db: Session = Depends(get_db)):
    sessions: dict[str, list[dict[str, Any]]] = {}
    active_logs = db.query(WorkLog).filter(WorkLog.end_time.is_(None)).order_by(WorkLog.start_time.asc()).all()
    for log in active_logs:
        sessions.setdefault(log.task_name, []).append(
            {
                "user": log.username,
                "start": log.start_time.strftime("%H:%M") if log.start_time else "",
            }
        )
    return sessions


@app.post("/api/admin/live-session/close")
def close_live_session(req: dict, db: Session = Depends(get_db)):
    username = clean_text(req.get("username"))
    if not username:
        return {"ok": False}
    active_log = db.query(WorkLog).filter(WorkLog.username == username, WorkLog.end_time.is_(None)).first()
    if active_log:
        active_log.end_time = get_now()
        create_stop_marker(db, username, active_log.end_time)
        db.commit()
    return {"ok": True}


@app.post("/api/admin/live-session/close-all")
def close_all_live_sessions(db: Session = Depends(get_db)):
    now = get_now()
    active_logs = db.query(WorkLog).filter(WorkLog.end_time.is_(None)).all()
    for active_log in active_logs:
        active_log.end_time = now
        create_stop_marker(db, active_log.username, now)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/messages/send")
def send_admin_message(req: dict, db: Session = Depends(get_db)):
    receivers = req.get("receivers", [])
    content = clean_text(req.get("content"))
    if not content:
        return {"ok": False, "msg": "Brak tresci komunikatu."}

    employees = [u.name for u in db.query(User).filter(User.role != "SUPER_ADMIN").all()]
    if not receivers:
        receivers = employees
    if "ALL" in receivers:
        receivers = employees

    root_login, _ = get_root_admin(db)
    sent = 0
    for receiver in receivers:
        receiver_name = clean_text(receiver)
        if not receiver_name:
            continue
        if receiver_name not in employees:
            continue
        db.add(Message(sender=root_login, receiver=receiver_name, content=content))
        sent += 1
    db.commit()
    return {"ok": True, "sent": sent}


@app.get("/api/admin/alerts")
def admin_alerts(db: Session = Depends(get_db)):
    items: list[dict[str, Any]] = []
    for problem in db.query(Problem).filter(Problem.is_resolved == 0).order_by(Problem.timestamp.desc()).all():
        items.append(
            {
                "type": "problem",
                "id": problem.id,
                "date": problem.timestamp.strftime("%Y-%m-%d"),
                "text": f"Problem od {problem.username}: {problem.description}",
            }
        )
    for message in db.query(Message).filter(Message.reply.isnot(None), Message.is_archived == 0).order_by(Message.timestamp.desc()).all():
        items.append(
            {
                "type": "reply",
                "id": message.id,
                "date": message.timestamp.strftime("%Y-%m-%d"),
                "text": f"Odpowiedź od {message.receiver}: {message.reply}",
            }
        )
    return items


@app.post("/api/admin/alerts/resolve")
def resolve_alert(req: dict, db: Session = Depends(get_db)):
    alert_type = clean_text(req.get("type"))
    alert_id = req.get("id")
    if alert_type == "problem":
        problem = db.query(Problem).filter(Problem.id == alert_id).first()
        if problem:
            problem.is_resolved = 1
    elif alert_type == "reply":
        message = db.query(Message).filter(Message.id == alert_id).first()
        if message:
            message.is_archived = 1
    db.commit()
    return {"ok": True}


@app.get("/api/admin/summary")
def admin_summary(db: Session = Depends(get_db)):
    root_login, _ = get_root_admin(db)
    return {
        "admin": root_login,
        "employees": [u.name for u in db.query(User).order_by(User.name.asc()).all()],
        "activities": [a.name for a in db.query(Activity).order_by(Activity.name.asc()).all()],
        "activeSessions": len(db.query(WorkLog).filter(WorkLog.end_time.is_(None)).all()),
        "openProblems": db.query(Problem).filter(Problem.is_resolved == 0).count(),
    }
