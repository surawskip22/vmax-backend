from __future__ import annotations

import os
from datetime import datetime, timedelta
import zoneinfo

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import Column, DateTime, Integer, String, create_engine, desc
from sqlalchemy.orm import Session, declarative_base, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 15})
else:
    engine = create_engine(DATABASE_URL, pool_size=20, max_overflow=10)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_now() -> datetime:
    try:
        return datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow() + timedelta(hours=2)


class GlobalSetting(Base):
    __tablename__ = "global_settings"
    id = Column(Integer, primary_key=True, index=True)
    setting_type = Column(String, index=True)
    value = Column(String)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    global_id = Column(String, unique=True, index=True)
    role = Column(String, default="EMPLOYEE")
    group_name = Column(String, default="Administracja")
    name = Column(String, unique=True, index=True)
    pin = Column(String)
    hire_date = Column(DateTime, default=get_now)


class Activity(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    color = Column(String, default="#0A84FF")


class Scanner(Base):
    __tablename__ = "scanners"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)


class Trolley(Base):
    __tablename__ = "trolleys"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)


class WorkLog(Base):
    __tablename__ = "work_logs"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    task_name = Column(String)
    start_time = Column(DateTime, default=get_now)
    end_time = Column(DateTime, nullable=True)
    date_str = Column(String, index=True)
    skaner = Column(String, nullable=True, default="")
    wozek = Column(String, nullable=True, default="")
    is_autoclosed = Column(Integer, default=0)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_root_admin(db: Session) -> tuple[str, str]:
    login_row = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").first()
    pass_row = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first()
    return (login_row.value if login_row else "Piotrek", pass_row.value if pass_row else "123")


def bootstrap_state() -> None:
    with SessionLocal() as db:
        bootstrap_flag = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "app_bootstrap_v1").first()
        if not bootstrap_flag:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(table.delete())
            db.commit()
            db.add(GlobalSetting(setting_type="app_bootstrap_v1", value="done"))
            db.add(GlobalSetting(setting_type="admin_login", value="Piotrek"))
            db.add(GlobalSetting(setting_type="admin_pass", value="123"))
            db.add(User(
                global_id="00001",
                role="SUPER_ADMIN",
                group_name="Administracja",
                name="Piotrek",
                pin="123",
                hire_date=get_now(),
            ))
            db.add(Activity(name="Praca", color="#0A84FF"))
            db.commit()
            return

        login_row = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").first()
        pass_row = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first()
        if not login_row:
            db.add(GlobalSetting(setting_type="admin_login", value="Piotrek"))
        else:
            login_row.value = "Piotrek"
        if not pass_row:
            db.add(GlobalSetting(setting_type="admin_pass", value="123"))
        else:
            pass_row.value = "123"

        admin = db.query(User).filter(User.name == "Piotrek").first()
        if not admin:
            db.add(User(
                global_id="00001",
                role="SUPER_ADMIN",
                group_name="Administracja",
                name="Piotrek",
                pin="123",
                hire_date=get_now(),
            ))
        else:
            admin.role = "SUPER_ADMIN"
            admin.pin = "123"
            admin.group_name = "Administracja"
            if not admin.global_id:
                admin.global_id = "00001"

        if not db.query(Activity).filter(Activity.name == "Praca").first():
            db.add(Activity(name="Praca", color="#0A84FF"))
        db.commit()


bootstrap_state()

app = FastAPI(title="V-Max Time Tracker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def html_path(filename: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def format_minutes(total_minutes: int) -> str:
    total_minutes = max(total_minutes, 0)
    return f"{total_minutes // 60}h {total_minutes % 60}m"


def duration_minutes(start: datetime, end: datetime | None) -> int:
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() / 60))


def user_payload(user: User) -> dict:
    return {
        "ok": True,
        "name": user.name,
        "role": user.role,
        "global_id": user.global_id or "",
        "group": user.group_name or "",
    }


def close_open_log(db: Session, active_log: WorkLog, when: datetime) -> None:
    active_log.end_time = when


def ensure_activity_name(db: Session, name: str | None) -> str:
    clean = (name or "").strip()
    if clean:
        return clean
    first_activity = db.query(Activity).order_by(Activity.name).first()
    return first_activity.name if first_activity else "Praca"


def get_user_history_payload(db: Session, username: str, month: str = "") -> dict:
    logs = db.query(WorkLog).filter(WorkLog.username == username).order_by(desc(WorkLog.id)).all()
    history = []
    current_task = None

    for log in logs:
        if month and not (log.date_str or "").startswith(month):
            continue

        mins = duration_minutes(log.start_time, log.end_time)
        history.append(
            {
                "data": log.date_str,
                "zadanie": log.task_name,
                "start": log.start_time.strftime("%H:%M") if log.start_time else "",
                "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa...",
                "czas": format_minutes(mins) if log.end_time else "-",
                "skaner": log.skaner or "",
                "wozek": log.wozek or "",
            }
        )

        if not log.end_time and current_task is None and log.task_name != "Zakonczenie pracy":
            current_task = {
                "name": log.task_name,
                "skaner": log.skaner or "",
                "wozek": log.wozek or "",
                "start_time": log.start_time.strftime("%H:%M") if log.start_time else "",
            }

    return {"hist": history, "currentTask": current_task}


@app.get("/")
def root_page():
    return FileResponse(html_path("vmax.html"))


@app.get("/vmax")
def vmax_page():
    return FileResponse(html_path("vmax.html"))


@app.get("/planner")
def planner_removed():
    raise HTTPException(status_code=404, detail="Planner is disabled in this version.")


@app.get("/api/public")
def public_data(db: Session = Depends(get_db)):
    employees = [u.name for u in db.query(User).order_by(User.name).all()]
    root_login, _ = get_root_admin(db)
    if root_login not in employees:
        employees.insert(0, root_login)
    return {"employees": employees}


@app.post("/api/auth/login")
def auth_login(req: dict, db: Session = Depends(get_db)):
    username = str(req.get("username", "")).strip()
    pin = str(req.get("pin", "")).strip()
    root_login, root_pass = get_root_admin(db)

    if username == root_login and pin == root_pass:
        user = db.query(User).filter(User.name == root_login).first()
        return user_payload(user) if user else {"ok": True, "name": root_login, "role": "SUPER_ADMIN", "global_id": "00001", "group": "Administracja"}

    user = db.query(User).filter(User.name == username, User.pin == pin).first()
    if not user:
        raise HTTPException(status_code=401, detail="Bledny login lub PIN.")
    return user_payload(user)


@app.post("/api/auth/change-pin")
def change_pin(req: dict, db: Session = Depends(get_db)):
    name = str(req.get("name", "")).strip()
    old_pin = str(req.get("oldPin", "")).strip()
    new_pin = str(req.get("newPin", "")).strip()
    is_admin = bool(req.get("isAdmin"))
    root_login, root_pass = get_root_admin(db)

    if not new_pin:
        return {"ok": False, "msg": "Nowy PIN jest pusty."}

    if name == root_login:
        if not is_admin and old_pin != root_pass:
            return {"ok": False, "msg": "Stary PIN jest niepoprawny."}
        login_row = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").first()
        pass_row = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first()
        if login_row:
            login_row.value = root_login
        if pass_row:
            pass_row.value = new_pin
        user = db.query(User).filter(User.name == root_login).first()
        if user:
            user.pin = new_pin
        db.commit()
        return {"ok": True, "msg": "PIN administratora zostal zmieniony."}

    user = db.query(User).filter(User.name == name).first()
    if not user:
        return {"ok": False, "msg": "Nie znaleziono uzytkownika."}
    if not is_admin and user.pin != old_pin:
        return {"ok": False, "msg": "Stary PIN jest niepoprawny."}
    user.pin = new_pin
    db.commit()
    return {"ok": True, "msg": "PIN zostal zmieniony."}


@app.get("/api/config")
def get_config(db: Session = Depends(get_db)):
    root_login, root_pass = get_root_admin(db)
    admins = {u.name: u.pin for u in db.query(User).filter(User.role.in_(["ADMIN", "SUPER_ADMIN", "MANAGER", "TEAM_LEADER"])).all()}
    admins[root_login] = root_pass
    employees = [u.name for u in db.query(User).order_by(User.name).all()]
    activities = [a.name for a in db.query(Activity).order_by(Activity.name).all()]
    if "Praca" not in activities:
        activities.insert(0, "Praca")
    scanners = [s.name for s in db.query(Scanner).order_by(Scanner.name).all()]
    trolleys = [t.name for t in db.query(Trolley).order_by(Trolley.name).all()]
    active_logs = db.query(WorkLog).filter(WorkLog.end_time.is_(None)).all()
    return {
        "admins": admins,
        "pracownicy": employees,
        "aktywnosci": activities,
        "skanery": scanners,
        "wozki": trolleys,
        "zajete_skanery": [log.skaner for log in active_logs if log.skaner],
        "zajete_wozki": [log.wozek for log in active_logs if log.wozek],
    }


@app.post("/api/user/history")
def user_history(req: dict, db: Session = Depends(get_db)):
    username = str(req.get("username", "")).strip()
    month = str(req.get("month", "")).strip()
    return get_user_history_payload(db, username, month)


@app.post("/api/user/action")
def user_action(req: dict, db: Session = Depends(get_db)):
    username = str(req.get("username", "")).strip()
    action_type = str(req.get("type", "")).strip().upper()
    task_name = ensure_activity_name(db, req.get("task"))
    skaner = str(req.get("skaner", "")).strip()
    wozek = str(req.get("wozek", "")).strip()

    if not username:
        raise HTTPException(status_code=400, detail="Brak nazwy uzytkownika.")

    now = get_now()
    date_str = now.strftime("%Y-%m-%d")
    active_log = db.query(WorkLog).filter(WorkLog.username == username, WorkLog.end_time.is_(None)).first()

    if action_type in {"START", "TASK"}:
        if skaner and db.query(WorkLog).filter(WorkLog.end_time.is_(None), WorkLog.skaner == skaner, WorkLog.username != username).first():
            raise HTTPException(status_code=400, detail="Skaner jest juz zajety.")
        if wozek and db.query(WorkLog).filter(WorkLog.end_time.is_(None), WorkLog.wozek == wozek, WorkLog.username != username).first():
            raise HTTPException(status_code=400, detail="Wozek jest juz zajety.")

    if action_type == "STOP":
        if active_log:
            close_open_log(db, active_log, now)
        db.add(WorkLog(
            username=username,
            task_name="Zakonczenie pracy",
            start_time=now,
            end_time=now,
            date_str=date_str,
        ))
        db.commit()
        return get_user_history_payload(db, username, str(req.get("month", "")).strip())

    if active_log:
        close_open_log(db, active_log, now)

    db.add(WorkLog(
        username=username,
        task_name=task_name,
        start_time=now,
        date_str=date_str,
        skaner=skaner,
        wozek=wozek,
    ))
    db.commit()
    return get_user_history_payload(db, username, str(req.get("month", "")).strip())


@app.post("/api/user/equipment")
def update_equipment(req: dict, db: Session = Depends(get_db)):
    username = str(req.get("username", "")).strip()
    skaner = str(req.get("skaner", "")).strip()
    wozek = str(req.get("wozek", "")).strip()
    active_log = db.query(WorkLog).filter(WorkLog.username == username, WorkLog.end_time.is_(None)).first()
    if not active_log:
        return get_user_history_payload(db, username, str(req.get("month", "")).strip())
    if skaner and db.query(WorkLog).filter(WorkLog.end_time.is_(None), WorkLog.skaner == skaner, WorkLog.username != username).first():
        raise HTTPException(status_code=400, detail="Skaner jest juz zajety.")
    if wozek and db.query(WorkLog).filter(WorkLog.end_time.is_(None), WorkLog.wozek == wozek, WorkLog.username != username).first():
        raise HTTPException(status_code=400, detail="Wozek jest juz zajety.")
    active_log.skaner = skaner
    active_log.wozek = wozek
    db.commit()
    return get_user_history_payload(db, username, str(req.get("month", "")).strip())


@app.post("/api/user/correct-task")
def correct_task(req: dict, db: Session = Depends(get_db)):
    username = str(req.get("username", "")).strip()
    task_name = ensure_activity_name(db, req.get("task"))
    last_log = db.query(WorkLog).filter(WorkLog.username == username, WorkLog.task_name != "Zakonczenie pracy").order_by(desc(WorkLog.id)).first()
    if not last_log:
        return False
    last_log.task_name = task_name
    db.commit()
    return get_user_history_payload(db, username, str(req.get("month", "")).strip())


@app.get("/api/admin/active-sessions")
def active_sessions(db: Session = Depends(get_db)):
    sessions: dict[str, list[dict]] = {}
    for log in db.query(WorkLog).filter(WorkLog.end_time.is_(None)).order_by(WorkLog.start_time.asc()).all():
        sessions.setdefault(log.task_name, []).append(
            {
                "user": log.username,
                "skaner": log.skaner or "",
                "wozek": log.wozek or "",
                "start": log.start_time.strftime("%H:%M") if log.start_time else "",
            }
        )
    return sessions


@app.post("/api/admin/live-session/close")
def close_live_session(req: dict, db: Session = Depends(get_db)):
    username = str(req.get("username", "")).strip()
    active = db.query(WorkLog).filter(WorkLog.username == username, WorkLog.end_time.is_(None)).first()
    if active:
        now = get_now()
        active.end_time = now
        db.add(WorkLog(
            username=username,
            task_name="Zakonczenie pracy",
            start_time=now,
            end_time=now,
            date_str=now.strftime("%Y-%m-%d"),
        ))
        db.commit()
    return True


@app.post("/api/admin/live-session/close-all")
def close_all_live_sessions(db: Session = Depends(get_db)):
    now = get_now()
    for active in db.query(WorkLog).filter(WorkLog.end_time.is_(None)).all():
        active.end_time = now
        db.add(WorkLog(
            username=active.username,
            task_name="Zakonczenie pracy",
            start_time=now,
            end_time=now,
            date_str=now.strftime("%Y-%m-%d"),
        ))
    db.commit()
    return True


@app.get("/api/health")
def health():
    return {"ok": True}

