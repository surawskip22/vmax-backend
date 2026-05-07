from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc, text, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import os
import json
import uuid
import random
import io
import re  # <--- TUTAJ JEST ZGUBIONY IMPORT!
from datetime import datetime, timedelta
import zoneinfo
import openpyxl

# ==========================================
# 1. KONFIGURACJA BAZY DANYCH (HIGH-CONCURRENCY)
# ==========================================
db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

if "sqlite" in db_url:
    engine = create_engine(db_url, connect_args={"check_same_thread": False, "timeout": 15})
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
else:
    engine = create_engine(db_url, pool_size=50, max_overflow=20)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_now():
    try: 
        return datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)
    except Exception: 
        return datetime.utcnow() + timedelta(hours=2)

def clamp_rating(value, default=3):
    try:
        rating = int(value)
    except Exception:
        return default
    if rating > 4:
        return 4
    if rating < 1:
        return 1
    return rating

def normalize_task_ratings(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else {}
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return {}
    clean = {}
    for task, rating in raw.items():
        task_name = str(task).strip()
        if task_name:
            clean[task_name] = clamp_rating(rating)
    return clean

def parse_date_input(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except Exception:
        return None

def date_to_str(value):
    return value.strftime("%Y-%m-%d") if value else ""

def is_truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on", "tak")

# ==========================================
# 2. STRUKTURA BAZY DANYCH (ERP + VMAX)
# ==========================================
class UserGroup(Base):
    __tablename__ = "user_groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    master_group = Column(String, default="Bez master grupy")
    emp_type = Column(String) 
    allowed_activities = Column(String, default="[]") 
    is_flexible = Column(Integer, default=0)

class MasterGroup(Base):
    __tablename__ = "master_groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)

class GroupAccess(Base):
    __tablename__ = "group_access"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    group_name = Column(String, index=True)
    access_role = Column(String, default="MANAGE")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    global_id = Column(String, unique=True, index=True) 
    role = Column(String, default="EMPLOYEE") 
    group_name = Column(String, default="Nieprzypisani") 
    name = Column(String, unique=True, index=True)
    pin = Column(String)
    hire_date = Column(DateTime, default=get_now)
    last_eval_date = Column(DateTime, nullable=True)
    next_eval_date = Column(DateTime, nullable=True)
    eval_count = Column(Integer, default=0)
    notes = Column(String, default="")

class EvaluationLog(Base):
    __tablename__ = "evaluation_logs"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    eval_date = Column(DateTime, default=get_now)
    rating = Column(Integer, default=3) 
    notes_snapshot = Column(String)
    task_ratings = Column(String, default="{}")

class GlobalSetting(Base):
    __tablename__ = "global_settings"
    id = Column(Integer, primary_key=True, index=True)
    setting_type = Column(String, index=True) 
    value = Column(String)

class Competence(Base):
    __tablename__ = "competences"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    activity = Column(String)
    rating = Column(Integer, default=2)

class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    date_str = Column(String, index=True)
    activity = Column(String)
    hours = Column(String)
    is_override = Column(Integer, default=0)

class DailyCapacity(Base):
    __tablename__ = "daily_capacity"
    id = Column(Integer, primary_key=True, index=True)
    date_str = Column(String, index=True)
    activity = Column(String)
    required_count = Column(Integer, default=0)

class Request(Base):
    __tablename__ = "requests"
    id = Column(String, primary_key=True, index=True)
    username = Column(String, index=True)
    date_str = Column(String, index=True)
    req_type = Column(String) 
    hours = Column(String)
    status = Column(String, default="Oczekuje") 
    timestamp = Column(DateTime, default=get_now)

class Activity(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)
    color = Column(String, default="#0A84FF") 

# --- V-MAX Tabele ---
class Scanner(Base):
    __tablename__ = "scanners"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)

class Trolley(Base):
    __tablename__ = "trolleys"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)

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
    sender = Column(String, default="ADMIN")
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

Base.metadata.create_all(bind=engine)

def generate_global_id(db: Session):
    users = db.query(User).all()
    max_id = 0
    for u in users:
        if u.global_id and str(u.global_id).isdigit():
            max_id = max(max_id, int(u.global_id))
    return f"{max_id + 1:05d}"

# MIGRACJE BEZPIECZNE
with engine.connect() as conn:
    try: conn.execute(text("ALTER TABLE evaluation_logs ADD COLUMN rating INTEGER DEFAULT 3")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE evaluation_logs ADD COLUMN task_ratings VARCHAR DEFAULT '{}'")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE users ADD COLUMN hire_date TIMESTAMP")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE users ADD COLUMN last_eval_date TIMESTAMP")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE users ADD COLUMN next_eval_date TIMESTAMP")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE users ADD COLUMN eval_count INTEGER DEFAULT 0")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE users ADD COLUMN notes VARCHAR DEFAULT ''")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE users ADD COLUMN global_id VARCHAR")); conn.commit()
    except: conn.rollback()
    try: 
        conn.execute(text("ALTER TABLE users ADD COLUMN group_name VARCHAR"))
        conn.commit()
        conn.execute(text("UPDATE users SET group_name = 'Nieprzypisani' WHERE group_name IS NULL"))
        conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE schedules ADD COLUMN is_override INTEGER DEFAULT 0")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE user_groups ADD COLUMN is_flexible INTEGER DEFAULT 0")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE user_groups ADD COLUMN master_group VARCHAR DEFAULT 'Bez master grupy'")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE activities ADD COLUMN color VARCHAR DEFAULT '#0A84FF'")); conn.commit()
    except: conn.rollback()
    try:
        conn.execute(text("UPDATE competences SET rating = 4 WHERE rating > 4"))
        conn.execute(text("UPDATE evaluation_logs SET rating = 4 WHERE rating > 4"))
        conn.commit()
    except: conn.rollback()

with SessionLocal() as db:
    users_to_fix = db.query(User).filter(User.hire_date == None).all()
    for u in users_to_fix: u.hire_date = get_now() - timedelta(days=90)
    for c in db.query(Competence).all():
        c.rating = clamp_rating(c.rating, default=2)
    for e in db.query(EvaluationLog).all():
        e.rating = clamp_rating(e.rating)
        e.task_ratings = json.dumps(normalize_task_ratings(e.task_ratings), ensure_ascii=False)
    db.commit()

    if not db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").first():
        db.add(GlobalSetting(setting_type="admin_login", value="ADMIN"))
    if not db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first():
        db.add(GlobalSetting(setting_type="admin_pass", value="admin"))

    default_master_groups = ["Bez master grupy", "Magazyn"]
    for mg in default_master_groups:
        if not db.query(MasterGroup).filter(MasterGroup.name == mg).first():
            db.add(MasterGroup(name=mg))

    default_groups = [
        {"name": "Nieprzypisani", "type": "Stały", "flex": 0, "master": "Bez master grupy"},
        {"name": "Magazyn osoby stałe", "type": "Stały", "flex": 0, "master": "Magazyn"},
        {"name": "Magazyn osoby dodatkowe", "type": "Dodatkowy", "flex": 1, "master": "Magazyn"},
        {"name": "Hydry", "type": "Dodatkowy", "flex": 1, "master": "Magazyn"}
    ]
    for g in default_groups:
        if not db.query(UserGroup).filter(UserGroup.name == g["name"]).first():
            db.add(UserGroup(name=g["name"], emp_type=g["type"], master_group=g["master"], allowed_activities="[]", is_flexible=g["flex"]))
    
    all_users = db.query(User).all()
    for u in all_users:
        if not u.global_id or not re.match(r'^[0-9]{5}$', str(u.global_id)):
            u.global_id = generate_global_id(db)
    db.commit()

app = FastAPI(title="WMS Enterprise Platform")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def get_root_admin(db: Session):
    l = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").first()
    p = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first()
    return l.value if l else "ADMIN", p.value if p else "admin"

def calc_mins(start: datetime, end: datetime):
    if not start or not end: return 0
    return int((end - start).total_seconds() / 60)

def format_dur(mins: int):
    if mins < 0: mins = 0
    return f"{mins//60}h {mins%60}m"

def find_excel_file(filename="imiona i nazwiska.xlsx"):
    candidates = [
        os.path.join(os.getcwd(), filename),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), filename),
        filename
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def read_people_from_excel(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    imported = []
    seen = set()
    for col in range(1, ws.max_column + 1):
        group_name = str(ws.cell(row=1, column=col).value or "").strip()
        if not group_name:
            continue
        for row in range(2, ws.max_row + 1):
            raw_name = ws.cell(row=row, column=col).value
            if not raw_name:
                continue
            emp_name = re.sub(r"\s+", " ", str(raw_name)).strip()
            if not emp_name or emp_name.lower() in seen:
                continue
            seen.add(emp_name.lower())
            imported.append({"name": emp_name, "group": group_name})
    return imported

def reset_employee_data(db: Session, include_vmax_logs=False):
    db.query(Schedule).delete()
    db.query(Request).delete()
    db.query(EvaluationLog).delete()
    db.query(Competence).delete()
    db.query(DailyCapacity).delete()
    db.query(AlertDismiss).delete()
    if include_vmax_logs:
        db.query(WorkLog).delete()
        db.query(Productivity).delete()
        db.query(Problem).delete()
        db.query(Message).delete()

# ==========================================
# 3. SYMULATOR HR
# ==========================================
@app.post("/api/admin/simulate")
def run_simulation(db: Session = Depends(get_db)):
    start_date = datetime(2026, 1, 1).date()
    end_date = datetime(2026, 5, 15).date()
    
    db.query(Schedule).filter(Schedule.date_str >= "2026-01-01", Schedule.date_str <= "2026-05-15").delete()
    users = db.query(User).filter(User.role == "EMPLOYEE").all()
    groups = {g.name: json.loads(g.allowed_activities) if g.allowed_activities else [] for g in db.query(UserGroup).all()}
    all_activities = [a.name for a in db.query(Activity).all()]
    
    db.query(Competence).delete() 
    
    for u in users:
        u.hire_date = datetime(2025, 12, 1) 
        u.eval_count = 2 
        u.last_eval_date = datetime(2026, 2, 15) 
        
        acts = groups.get(u.group_name, all_activities)
        if not acts: acts = all_activities
            
        for a in acts:
            db.add(Competence(username=u.name, activity=a, rating=3))
            
        curr_d = start_date
        while curr_d <= end_date:
            d_str = curr_d.strftime("%Y-%m-%d")
            if curr_d.weekday() >= 5:
                db.add(Schedule(username=u.name, date_str=d_str, activity="Wolne 🏠", hours="", is_override=1))
            else:
                rand_val = random.randint(1, 100)
                if rand_val <= 5: db.add(Schedule(username=u.name, date_str=d_str, activity="Chory 🤒", hours="", is_override=1))
                elif rand_val <= 10: db.add(Schedule(username=u.name, date_str=d_str, activity="Urlop 🌴", hours="", is_override=1))
                elif rand_val <= 12: db.add(Schedule(username=u.name, date_str=d_str, activity="No Show ❌", hours="", is_override=1))
                else:
                    if acts:
                        assigned_act = random.choice(acts)
                        db.add(Schedule(username=u.name, date_str=d_str, activity=assigned_act, hours="07:00-15:00", is_override=1))
            curr_d += timedelta(days=1)
            
    db.commit()
    return {"ok": True, "msg": "Zakończono generowanie historii pracy. Pracownicy otrzymali alerty HR o ocenie (3 mc)."}

# ==========================================
# 4. MODUŁ 1: PLANNER I HR
# ==========================================
@app.get("/")
def serve_vmax():
    return FileResponse("index.html")

@app.get("/planner")
def serve_planner():
    if os.path.exists("planner.html"): return FileResponse("planner.html")
    return HTMLResponse("<h1>Brak pliku planner.html</h1>", status_code=404)

@app.get("/api/public")
def get_public_data(db: Session = Depends(get_db)):
    employees = [u.name for u in db.query(User).all()]
    root_login, _ = get_root_admin(db)
    if root_login not in employees: employees.append(root_login)
    return {"employees": employees}

@app.post("/api/auth/login")
def login(req: dict, db: Session = Depends(get_db)):
    u, p, r = str(req.get("username", "")).strip(), str(req.get("pin", "")).strip(), req.get("role", "EMPLOYEE")
    root_login, root_pass = get_root_admin(db)
    
    if r == "ADMIN":
        if u == root_login and p == root_pass: return {"ok": True, "name": root_login, "role": "SUPER_ADMIN"}
        user = db.query(User).filter(User.name == u, User.pin == p).first()
        if user and user.role in ["ADMIN", "MANAGER", "TEAM_LEADER", "SUPER_ADMIN"]: return {"ok": True, "name": user.name, "role": user.role}
        raise HTTPException(status_code=401, detail="Błędny Login/PIN lub brak uprawnień!")
    else:
        if u == root_login and p == root_pass: return {"ok": True, "name": root_login, "role": "SUPER_ADMIN"}
        user = db.query(User).filter(User.name == u, User.pin == p).first()
        if user: return {"ok": True, "name": user.name, "role": user.role}
        raise HTTPException(status_code=401, detail="Błędny PIN!")

@app.post("/api/auth/change-pin")
def change_pin(req: dict, db: Session = Depends(get_db)):
    name, oldP, newP, is_admin = req.get("name"), req.get("oldPin"), req.get("newPin"), req.get("isAdmin")
    root_login, root_pass = get_root_admin(db)
    
    if name == root_login:
        if not is_admin and oldP != root_pass: return {"ok": False, "msg": "Stare hasło jest błędne"}
        p_setting = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first()
        if p_setting: p_setting.value = newP
        db.commit()
        return {"ok": True, "msg": "Hasło Głównego Admina zostało zmienione!"}

    user = db.query(User).filter(User.name == name).first()
    if not user: return {"ok": False, "msg": "Nie znaleziono użytkownika"}
    if not is_admin and user.pin != oldP: return {"ok": False, "msg": "Stary PIN jest błędny"}
    user.pin = newP
    db.commit()
    return {"ok": True, "msg": "Hasło/PIN zostało zmienione!"}

@app.post("/api/emp/dashboard")
def get_emp_dash(req: dict, db: Session = Depends(get_db)):
    name, year, month = req.get("name"), int(req.get("year")), int(req.get("month"))
    schedules = db.query(Schedule).filter(Schedule.username == name).all()
    requests = db.query(Request).filter(Request.username == name).all()
    shifts = [s.value for s in db.query(GlobalSetting).filter(GlobalSetting.setting_type == "shift").all()]
    if not shifts: shifts = ["07:00-15:00", "08:00-16:00"]
    
    db_acts = db.query(Activity).all()
    activityColors = {a.name: a.color for a in db_acts}
    activityColors["Chory 🤒"] = "#FF3B30"; activityColors["Urlop 🌴"] = "#FFCC00"
    activityColors["Wolne 🏠"] = "#8E8E93"; activityColors["No Show ❌"] = "#8B0000"
    
    user_db = db.query(User).filter(User.name == name).first()
    allowed_acts = []
    if user_db:
        group_db = db.query(UserGroup).filter(UserGroup.name == user_db.group_name).first()
        if group_db and group_db.allowed_activities: allowed_acts = json.loads(group_db.allowed_activities)
            
    activities = [a.name for a in db_acts if a.name in allowed_acts] if allowed_acts else [a.name for a in db_acts]
    plan_map = {s.date_str: s for s in schedules}
    req_map = {r.date_str: r for r in requests}
    
    next_month = month + 1 if month < 11 else 0
    next_year = year if month < 11 else year + 1
    days_in_month = (datetime(next_year, next_month + 1, 1) - timedelta(days=1)).day
    
    schedule_list = []
    for d in range(1, days_in_month + 1):
        date_str = f"{year}-{month+1:02d}-{d:02d}"
        iso_date = datetime(year, month + 1, d).isoformat()
        p = plan_map.get(date_str)
        r = req_map.get(date_str)
        req_obj = {"type": r.req_type, "hrs": r.hours, "status": r.status} if r else None
        schedule_list.append({"date": iso_date, "date_key": date_str, "act": p.activity if p and p.activity else "Brak planu", "hrs": p.hours if p else "", "req": req_obj, "override": p.is_override if p else 0})
        
    return {"schedule": schedule_list, "shifts": shifts, "activities": activities, "activityColors": activityColors}

@app.post("/api/emp/request-batch")
def submit_request_batch(req: dict, db: Session = Depends(get_db)):
    name, updates = req.get("name"), req.get("updates", [])
    root_login, _ = get_root_admin(db)
    if name == root_login:
        return {"ok": False, "msg": "Konto root nie wysyla dyspozycji pracowniczych."}
    user_db = db.query(User).filter(User.name == name).first()
    if not user_db:
        return {"ok": False, "msg": "Blad uzytkownika"}
    allowed_req_types = ["Dostępny", "Urlop 🌴", "Chory 🤒", "Wolne 🏠", "Wyczyść"]
    
    for upd in updates:
        d_str, r_type, hrs = upd["date"], upd["act"], upd["hrs"]
        if r_type not in allowed_req_types:
            continue
        if r_type == "Wyczyść":
            db.query(Request).filter(Request.username == name, Request.date_str == d_str, Request.status == "Oczekuje").delete()
        else:
            existing = db.query(Request).filter(Request.username == name, Request.date_str == d_str, Request.status == "Oczekuje").first()
            if existing:
                existing.req_type, existing.hours, existing.timestamp = r_type, hrs, get_now()
            else: db.add(Request(id=str(uuid.uuid4())[:8], username=name, date_str=d_str, req_type=r_type, hours=hrs, status="Oczekuje"))
                
    db.commit()
    return {"ok": True, "msg": "Dyspozycje wyslane do akceptacji."}

@app.get("/api/admin/data")
def get_admin_data(db: Session = Depends(get_db)):
    db_users = db.query(User).order_by(User.global_id).all()
    employees = [{
        "id": u.global_id,
        "name": u.name,
        "group": u.group_name,
        "role": u.role,
        "hire_date": date_to_str(u.hire_date),
        "last_eval_date": date_to_str(u.last_eval_date),
        "next_eval_date": date_to_str(u.next_eval_date),
        "eval_count": u.eval_count or 0
    } for u in db_users]
    master_groups = [m.name for m in db.query(MasterGroup).order_by(MasterGroup.name).all()]
    groups = [{
        "name": g.name,
        "master": g.master_group or "Bez master grupy",
        "type": g.emp_type,
        "is_flexible": g.is_flexible,
        "activities": json.loads(g.allowed_activities) if g.allowed_activities else []
    } for g in db.query(UserGroup).all()]
    access_rows = db.query(GroupAccess).all()
    access_map = {}
    for a in access_rows:
        access_map.setdefault(a.username, []).append(a.group_name)
    
    db_acts = db.query(Activity).all()
    activityColors = {a.name: a.color for a in db_acts}
    activityColors["Chory 🤒"] = "#FF3B30"; activityColors["Urlop 🌴"] = "#FFCC00"
    activityColors["Wolne 🏠"] = "#8E8E93"; activityColors["No Show ❌"] = "#8B0000"

    activities = [a.name for a in db_acts]
    planner_activities = list(set(activities + ["Chory 🤒", "Urlop 🌴", "Wolne 🏠", "No Show ❌"]))
    shifts = [s.value for s in db.query(GlobalSetting).filter(GlobalSetting.setting_type == "shift").all()]
    if not shifts: shifts = ["07:00-15:00", "08:00-16:00"]
    
    comps = db.query(Competence).all()
    ratings_map = {f"{c.username}_{c.activity}": clamp_rating(c.rating, default=2) for c in comps}
    schedules = db.query(Schedule).all()
    plan_map = {f"{s.username}_{s.date_str}": f"{s.activity}||{s.hours}||{s.is_override}" for s in schedules}
    capacities = db.query(DailyCapacity).all()
    capacity_map = {f"{c.date_str}_{c.activity}": c.required_count for c in capacities}
    
    reqs = db.query(Request).all()
    alerts, avail_map, request_map = [], {}, {}
    for r in reversed(reqs):
        request_map[f"{r.username}_{r.date_str}"] = {"type": r.req_type, "hrs": r.hours, "status": r.status}
        if r.status == "Oczekuje": alerts.append({"id": r.id, "name": r.username, "date": r.date_str, "type": r.req_type, "hrs": r.hours, "ts": r.timestamp.strftime("%Y-%m-%d %H:%M")})
        if r.req_type == "Dostępny" and r.status in ["Oczekuje", "Zatwierdzono"]:
            avail_map[f"{r.username}_{r.date_str}"] = r.hours

    now = get_now()
    hr_alerts = []
    for u in db_users:
        if not u.hire_date or u.role == "SUPER_ADMIN": continue
        days_since_hire = (now - u.hire_date).days
        if u.eval_count == 0 and days_since_hire >= 14: hr_alerts.append({"id": f"hr_{u.name}_0", "name": u.name, "date": now.strftime("%Y-%m-%d"), "type": "Ocena (14 dni)", "hrs": "", "ts": now.strftime("%Y-%m-%d %H:%M")})
        elif u.eval_count == 1 and days_since_hire >= 45: hr_alerts.append({"id": f"hr_{u.name}_1", "name": u.name, "date": now.strftime("%Y-%m-%d"), "type": "Ocena (1.5 mc)", "hrs": "", "ts": now.strftime("%Y-%m-%d %H:%M")})
        elif u.eval_count >= 2:
            if u.next_eval_date:
                if u.next_eval_date.date() <= now.date():
                    hr_alerts.append({"id": f"hr_{u.name}_{u.eval_count}", "name": u.name, "date": now.strftime("%Y-%m-%d"), "type": "Ocena Okresowa (termin)", "hrs": "", "ts": now.strftime("%Y-%m-%d %H:%M")})
            elif u.last_eval_date and (now - u.last_eval_date).days >= 90:
                hr_alerts.append({"id": f"hr_{u.name}_{u.eval_count}", "name": u.name, "date": now.strftime("%Y-%m-%d"), "type": "Ocena Okresowa (3 mc)", "hrs": "", "ts": now.strftime("%Y-%m-%d %H:%M")})

    root_login, _ = get_root_admin(db)
    return {"employees": employees, "groups": groups, "masterGroups": master_groups, "groupAccess": access_map, "activityNames": activities, "plannerActivities": planner_activities, "activityColors": activityColors, "shifts": shifts, "ratingsMap": ratings_map, "planMap": plan_map, "requestMap": request_map, "alerts": alerts, "hrAlerts": hr_alerts, "availMap": avail_map, "capacityMap": capacity_map, "rootAdmin": root_login}

@app.post("/api/admin/employee/hr_details")
def get_hr_details(req: dict, db: Session = Depends(get_db)):
    username, d_from_str, d_to_str = req.get("username"), req.get("date_from"), req.get("date_to")
    if not username: return {"ok": False}
    user_db = db.query(User).filter(User.name == username).first()
    if not user_db: return {"ok": False, "msg": "Pracownik nie istnieje."}
    notes = user_db.notes if user_db and user_db.notes else ""
    now = get_now()
    d_from = datetime.strptime(d_from_str, "%Y-%m-%d") if d_from_str else (now - timedelta(days=30))
    d_to = datetime.strptime(d_to_str, "%Y-%m-%d") if d_to_str else now

    tracked_acts = ["Chory 🤒", "Urlop 🌴", "Wolne 🏠", "No Show ❌"]
    schedules = db.query(Schedule).filter(Schedule.username == username, Schedule.date_str >= d_from.strftime("%Y-%m-%d"), Schedule.date_str <= d_to.strftime("%Y-%m-%d")).all()
    stats = {"Praca": 0, "Chory 🤒": 0, "Urlop 🌴": 0, "Wolne 🏠": 0, "No Show ❌": 0}
    for s in schedules:
        if s.activity in stats: stats[s.activity] += 1
        elif s.activity and s.activity not in ["Brak planu", "Wyczyść"]: stats["Praca"] += 1
        
    eval_logs = db.query(EvaluationLog).filter(EvaluationLog.username == username).order_by(desc(EvaluationLog.eval_date)).all()
    history = [{
        "id": l.id,
        "date": l.eval_date.strftime("%Y-%m-%d"),
        "rating": clamp_rating(l.rating),
        "notes": l.notes_snapshot,
        "task_ratings": normalize_task_ratings(l.task_ratings)
    } for l in eval_logs]
    return {"ok": True, "stats": stats, "notes": notes, "next_eval_date": date_to_str(user_db.next_eval_date), "history": history}

@app.post("/api/admin/employee/eval_details")
def get_eval_details(req: dict, db: Session = Depends(get_db)):
    try:
        eval_id = int(req.get("eval_id"))
    except Exception:
        return {"ok": False, "msg": "Nieprawidlowe ID oceny."}
    eval_log = db.query(EvaluationLog).filter(EvaluationLog.id == eval_id).first()
    if not eval_log: return {"ok": False}
    
    prev_eval = db.query(EvaluationLog).filter(EvaluationLog.username == eval_log.username, EvaluationLog.eval_date < eval_log.eval_date).order_by(desc(EvaluationLog.eval_date)).first()
    user = db.query(User).filter(User.name == eval_log.username).first()
    start_date = prev_eval.eval_date if prev_eval else (user.hire_date if user and user.hire_date else datetime(2020, 1, 1))
    end_date = eval_log.eval_date
    
    schedules = db.query(Schedule).filter(Schedule.username == eval_log.username, Schedule.date_str >= start_date.strftime("%Y-%m-%d"), Schedule.date_str <= end_date.strftime("%Y-%m-%d")).all()
    stats = {"Praca": 0, "Chory 🤒": 0, "Urlop 🌴": 0, "Wolne 🏠": 0, "No Show ❌": 0}
    for s in schedules:
        if s.activity in stats: stats[s.activity] += 1
        elif s.activity and s.activity not in ["Brak planu", "Wyczyść"]: stats["Praca"] += 1
        
    return {
        "ok": True,
        "period": f"{start_date.strftime('%Y-%m-%d')} do {end_date.strftime('%Y-%m-%d')}",
        "stats": stats,
        "rating": clamp_rating(eval_log.rating),
        "notes": eval_log.notes_snapshot,
        "task_ratings": normalize_task_ratings(eval_log.task_ratings)
    }

@app.post("/api/admin/employee/hr_save")
def save_hr_details(req: dict, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.name == req.get("username")).first()
    if not user:
        return {"ok": False, "msg": "Pracownik nie istnieje."}

    is_eval = is_truthy(req.get("is_evaluation"))
    task_ratings = normalize_task_ratings(req.get("task_ratings", {}))
    overall_rating = clamp_rating(req.get("rating", 3))
    next_eval_date = parse_date_input(req.get("next_eval_date"))

    user.notes = req.get("notes", "")
    if "next_eval_date" in req:
        user.next_eval_date = next_eval_date

    for act, rating in task_ratings.items():
        comp = db.query(Competence).filter(Competence.username == user.name, Competence.activity == act).first()
        if not comp:
            comp = Competence(username=user.name, activity=act)
            db.add(comp)
        comp.rating = rating

    if is_eval:
        now = get_now()
        user.eval_count = (user.eval_count or 0) + 1
        user.last_eval_date = now
        if not user.next_eval_date:
            user.next_eval_date = now + timedelta(days=90)
        db.add(EvaluationLog(
            username=user.name,
            rating=overall_rating,
            notes_snapshot=user.notes,
            task_ratings=json.dumps(task_ratings, ensure_ascii=False)
        ))
    db.commit()
    return {"ok": True, "msg": "Zapisano ewaluacje i zaktualizowano alerty!" if is_eval else "Zapisano notatki HR."}

@app.post("/api/admin/capacity/save")
def save_capacity(req: dict, db: Session = Depends(get_db)):
    d_str = req.get("date")
    for act, count in req.get("capacities", {}).items():
        cap = db.query(DailyCapacity).filter(DailyCapacity.date_str == d_str, DailyCapacity.activity == act).first()
        if not cap:
            cap = DailyCapacity(date_str=d_str, activity=act)
            db.add(cap)
        cap.required_count = int(count)
    db.commit()
    return {"ok": True}

@app.post("/api/admin/employee/group")
def change_emp_group(req: dict, db: Session = Depends(get_db)):
    emp_name = req.get("name")
    group_name = req.get("group") or "Nieprzypisani"
    user = db.query(User).filter(User.name == emp_name).first()
    if not user:
        return {"ok": False, "msg": "Pracownik nie istnieje."}

    root_login, _ = get_root_admin(db)
    if user.name == root_login or user.role == "SUPER_ADMIN":
        return {"ok": False, "msg": "Konta administratora nie mozna przenosic jako pracownika."}

    group = db.query(UserGroup).filter(UserGroup.name == group_name).first()
    if not group:
        return {"ok": False, "msg": "Grupa nie istnieje."}

    user.group_name = group_name
    db.commit()
    return {"ok": True, "msg": "Zmieniono grupe pracownika."}

@app.post("/api/admin/alerts/resolve")
def resolve_alert(req: dict, db: Session = Depends(get_db)):
    a_id, stat, name, date_str, a_type, hrs = req.get("id"), req.get("status"), req.get("name"), req.get("date"), req.get("type"), req.get("hrs")
    if "hr_" in str(a_id): return {"ok": False, "msg": "Alerty HR zamykają się po zapisaniu Oceny w Karcie HR!"}
    db_req = db.query(Request).filter(Request.id == a_id).first()
    if db_req:
        db_req.status = stat
        if stat == "Zatwierdzono" and a_type != "Dostępny":
            sched = db.query(Schedule).filter(Schedule.username == name, Schedule.date_str == date_str).first()
            if not sched:
                sched = Schedule(username=name, date_str=date_str)
                db.add(sched)
            sched.activity = a_type if a_type != "Wyczyść" else ""
            sched.hours = hrs if a_type != "Wyczyść" else ""
            sched.is_override = 1 
        db.commit()
    return {"ok": True, "msg": "Wniosek rozpatrzony!"}

@app.post("/api/admin/alerts/resolve-mass")
def resolve_mass_alerts(req: dict, db: Session = Depends(get_db)):
    stat, ids = req.get("status", "Zatwierdzono"), req.get("ids", [])
    filtered_ids = [i for i in ids if "hr_" not in str(i)] 
    reqs = db.query(Request).filter(Request.id.in_(filtered_ids)).all()
    for db_req in reqs:
        db_req.status = stat
        if stat == "Zatwierdzono" and db_req.req_type != "Dostępny":
            sched = db.query(Schedule).filter(Schedule.username == db_req.username, Schedule.date_str == db_req.date_str).first()
            if not sched:
                sched = Schedule(username=db_req.username, date_str=db_req.date_str)
                db.add(sched)
            sched.activity = db_req.req_type if db_req.req_type != "Wyczyść" else ""
            sched.hours = db_req.hours if db_req.req_type != "Wyczyść" else ""
            sched.is_override = 1
    db.commit()
    hr_skipped = len(ids) - len(filtered_ids)
    msg = f"Przetworzono {len(reqs)} wniosków grafiku!"
    if hr_skipped > 0: msg += f" (Pominięto {hr_skipped} alertów HR)"
    return {"ok": True, "msg": msg}

@app.post("/api/admin/matrix/update")
def update_matrix(req: dict, db: Session = Depends(get_db)):
    name, act, rating = req.get("name"), req.get("activity"), req.get("rating")
    if not name or not act:
        return {"ok": False, "msg": "Brak pracownika lub zadania."}
    if not db.query(User).filter(User.name == name).first():
        return {"ok": False, "msg": "Pracownik nie istnieje."}
    comp = db.query(Competence).filter(Competence.username == name, Competence.activity == act).first()
    if not comp:
        comp = Competence(username=name, activity=act)
        db.add(comp)
    comp.rating = clamp_rating(rating, default=2)
    db.commit()
    return {"ok": True}

@app.post("/api/admin/planner/save")
def save_planner(req: dict, db: Session = Depends(get_db)):
    names, start, end, act, hrs = req.get("names", []), req.get("start"), req.get("end"), req.get("activity"), req.get("hours", "")
    start_dt = datetime.strptime(start, "%Y-%m-%d") if start else None
    end_dt = datetime.strptime(end, "%Y-%m-%d") if end else None
    for n in names:
        if not start_dt:
            date_query = start 
            sched = db.query(Schedule).filter(Schedule.username == n, Schedule.date_str == date_query).first()
            if not sched:
                sched = Schedule(username=n, date_str=date_query)
                db.add(sched)
            sched.activity = act; sched.hours = hrs; sched.is_override = 1 
        else:
            curr = start_dt
            while curr <= end_dt:
                date_query = curr.strftime("%Y-%m-%d")
                sched = db.query(Schedule).filter(Schedule.username == n, Schedule.date_str == date_query).first()
                if not sched:
                    sched = Schedule(username=n, date_str=date_query)
                    db.add(sched)
                sched.activity = act; sched.hours = hrs; sched.is_override = 1 
                curr += timedelta(days=1)
    db.commit()
    return {"ok": True}

@app.post("/api/admin/planner/copy-day")
def copy_daily(req: dict, db: Session = Depends(get_db)):
    src_date, t_start, t_end = req.get("sourceDate"), req.get("targetStart"), req.get("targetEnd")
    src_query = datetime.strptime(src_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    source_schedules = db.query(Schedule).filter(Schedule.date_str == src_query).all()
    if not source_schedules: return {"ok": True}
    curr = datetime.strptime(t_start, "%Y-%m-%d")
    end_dt = datetime.strptime(t_end, "%Y-%m-%d")
    while curr <= end_dt:
        t_query = curr.strftime("%Y-%m-%d")
        for s in source_schedules:
            if s.activity and s.activity != "Wyczyść":
                target_s = db.query(Schedule).filter(Schedule.username == s.username, Schedule.date_str == t_query).first()
                if not target_s:
                    target_s = Schedule(username=s.username, date_str=t_query)
                    db.add(target_s)
                target_s.activity = s.activity; target_s.hours = s.hours; target_s.is_override = 1
        curr += timedelta(days=1)
    db.commit()
    return {"ok": True}

@app.get("/api/admin/backup/db")
def download_db(db: Session = Depends(get_db)):
    data = {
        "users": [{"name": u.name, "role": u.role, "group": u.group_name} for u in db.query(User).all()],
        "schedules": [{"username": s.username, "date": s.date_str, "act": s.activity, "hrs": s.hours} for s in db.query(Schedule).all()],
        "evaluations": [{"username": e.username, "date": e.eval_date.strftime("%Y-%m-%d"), "rating": clamp_rating(e.rating), "task_ratings": normalize_task_ratings(e.task_ratings)} for e in db.query(EvaluationLog).all()]
    }
    json_data = json.dumps(data, ensure_ascii=False, indent=2)
    return StreamingResponse(io.StringIO(json_data), media_type="application/json", headers={"Content-Disposition": f"attachment; filename=WMS_Backup_{get_now().strftime('%Y%m%d')}.json"})

@app.post("/api/admin/import-excel")
def import_excel(req: dict, db: Session = Depends(get_db)):
    if req.get("confirm") != "RESET":
        return {"ok": False, "msg": "Import wymaga potwierdzenia confirm=RESET."}

    path = find_excel_file(req.get("filename") or "imiona i nazwiska.xlsx")
    if not path:
        return {"ok": False, "msg": "Nie znaleziono pliku imiona i nazwiska.xlsx na serwerze."}

    rows = read_people_from_excel(path)
    if not rows:
        return {"ok": False, "msg": "Excel nie zawiera pracownikow do importu."}

    try:
        reset_employee_data(db, include_vmax_logs=is_truthy(req.get("include_vmax_logs")))
        db.query(GroupAccess).delete()
        db.query(UserGroup).delete()
        db.query(MasterGroup).delete()
        db.add(MasterGroup(name="Bez master grupy"))
        if not db.query(MasterGroup).filter(MasterGroup.name == "Magazyn").first():
            db.add(MasterGroup(name="Magazyn"))
        db.add(UserGroup(name="Nieprzypisani", master_group="Bez master grupy", emp_type="Stały", allowed_activities="[]", is_flexible=0))
        imported_groups = []
        for row in rows:
            group_name = row["group"]
            if group_name not in imported_groups:
                imported_groups.append(group_name)
            if not db.query(UserGroup).filter(UserGroup.name == group_name).first():
                db.add(UserGroup(name=group_name, master_group="Magazyn", emp_type="Stały", allowed_activities="[]", is_flexible=0))

        privileged_roles = ["ADMIN", "MANAGER", "TEAM_LEADER", "SUPER_ADMIN"]
        db.query(User).filter(~User.role.in_(privileged_roles)).delete(synchronize_session=False)

        for row in rows:
            existing = db.query(User).filter(User.name == row["name"]).first()
            if existing:
                existing.group_name = row["group"]
                if not existing.global_id:
                    existing.global_id = generate_global_id(db)
            else:
                db.add(User(global_id=generate_global_id(db), name=row["name"], pin="1111", role="EMPLOYEE", group_name=row["group"], hire_date=get_now(), eval_count=0))

        db.commit()
        return {"ok": True, "msg": f"Zaimportowano {len(rows)} pracownikow i {len(imported_groups)} grup z Excela.", "employees": len(rows), "groups": imported_groups}
    except Exception as e:
        db.rollback()
        return {"ok": False, "msg": str(e)}

@app.post("/api/admin/settings")
def cms_settings(req: dict, db: Session = Depends(get_db)):
    action, t, val = req.get("action"), req.get("type"), req.get("value")
    try:
        if action == "ADD":
            if t == "employee": 
                if db.query(User).filter(User.name == val).first(): return {"ok": False, "msg": "Użytkownik już istnieje!"}
                db.add(User(global_id=generate_global_id(db), name=val, pin="1111", role=req.get("role", "EMPLOYEE"), group_name="Nieprzypisani", hire_date=get_now(), eval_count=0))
            elif t == "shift": db.add(GlobalSetting(setting_type="shift", value=val))
            elif t == "activity": 
                act = db.query(Activity).filter(Activity.name == val).first()
                if act: act.color = req.get("color", "#0A84FF")
                else: db.add(Activity(name=val, color=req.get("color", "#0A84FF")))
            elif t == "master_group":
                if val and not db.query(MasterGroup).filter(MasterGroup.name == val).first():
                    db.add(MasterGroup(name=val))
            elif t == "group": 
                if not db.query(UserGroup).filter(UserGroup.name == val).first():
                    master_name = req.get("master_group") or "Bez master grupy"
                    if not db.query(MasterGroup).filter(MasterGroup.name == master_name).first():
                        db.add(MasterGroup(name=master_name))
                    db.add(UserGroup(name=val, emp_type=req.get("emp_type", "Stały"), master_group=master_name, is_flexible=req.get("is_flexible", 0), allowed_activities="[]"))
        elif action == "EDIT_USER_ROLE":
            user = db.query(User).filter(User.name == val).first()
            if user: user.role = req.get("role", "EMPLOYEE")
        elif action == "EDIT_ACTIVITY_COLOR":
            act = db.query(Activity).filter(Activity.name == val).first()
            if act: act.color = req.get("color", "#0A84FF")
        elif action == "EDIT_GROUP_FLEX":
            grp = db.query(UserGroup).filter(UserGroup.name == val).first()
            if grp: grp.is_flexible = req.get("is_flexible", 0)
        elif action == "EDIT_GROUP_MASTER":
            grp = db.query(UserGroup).filter(UserGroup.name == val).first()
            master_name = req.get("master_group") or "Bez master grupy"
            if grp:
                if not db.query(MasterGroup).filter(MasterGroup.name == master_name).first():
                    db.add(MasterGroup(name=master_name))
                grp.master_group = master_name
        elif action == "UPDATE_GROUP_ACCESS":
            db.query(GroupAccess).filter(GroupAccess.username == val).delete()
            for group_name in req.get("groups", []):
                if db.query(UserGroup).filter(UserGroup.name == group_name).first():
                    db.add(GroupAccess(username=val, group_name=group_name, access_role="MANAGE"))
        elif action == "UPDATE_GROUP_ACTS":
            grp = db.query(UserGroup).filter(UserGroup.name == val).first()
            if grp: grp.allowed_activities = json.dumps(req.get("activities", []))
        elif action == "DELETE":
            if t == "employee": 
                db.query(User).filter(User.name == val).delete()
                db.query(EvaluationLog).filter(EvaluationLog.username == val).delete() 
            elif t == "shift": db.query(GlobalSetting).filter(GlobalSetting.setting_type == "shift", GlobalSetting.value == val).delete()
            elif t == "activity": 
                db.query(Activity).filter(Activity.name == val).delete()
                db.query(Competence).filter(Competence.activity == val).delete()
                db.query(DailyCapacity).filter(DailyCapacity.activity == val).delete()
            elif t == "master_group":
                if val != "Bez master grupy":
                    db.query(MasterGroup).filter(MasterGroup.name == val).delete()
                    db.query(UserGroup).filter(UserGroup.master_group == val).update({"master_group": "Bez master grupy"})
            elif t == "group":
                db.query(UserGroup).filter(UserGroup.name == val).delete()
                db.query(GroupAccess).filter(GroupAccess.group_name == val).delete()
                db.query(User).filter(User.group_name == val).update({"group_name": "Nieprzypisani"})
        elif action == "EDIT_ROOT_ADMIN":
            l = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").first()
            p = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first()
            if l and req.get("login"): l.value = req.get("login")
            if p and req.get("pass"): p.value = req.get("pass")
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "msg": str(e)}

# ==========================================
# 5. MODUŁ 2: V-MAX (CZAS PRACY I SKANERY)
# ==========================================
@app.get("/api/config")
def get_vmax_config(db: Session = Depends(get_db)):
    now = get_now()
    stale_logs = db.query(WorkLog).filter(WorkLog.end_time == None).all()
    for log in stale_logs:
        if (now - log.start_time).total_seconds() > 15 * 3600:
            log.end_time = log.start_time + timedelta(hours=15)
            log.is_autoclosed = 1
    db.commit()
    admins = {u.name: u.pin for u in db.query(User).filter(User.role.in_(["ADMIN", "SUPER_ADMIN", "MANAGER"])).all()}
    employees = [u.name for u in db.query(User).all()]
    activities = [a.name for a in db.query(Activity).all()]
    scanners = [s.name for s in db.query(Scanner).all()]
    trolleys = [t.name for t in db.query(Trolley).all()]
    active_logs = db.query(WorkLog).filter(WorkLog.end_time == None).all()
    return {
        "admins": admins, "pracownicy": employees, "aktywnosci": activities, "skanery": scanners, "wozki": trolleys,
        "zajete_skanery": [log.skaner for log in active_logs if log.skaner], 
        "zajete_wozki": [log.wozek for log in active_logs if log.wozek]
    }

@app.post("/api/user/history")
def get_user_history(req: dict, db: Session = Depends(get_db)):
    user = req.get("username")
    month = req.get("month", "")
    logs = db.query(WorkLog).filter(WorkLog.username == user).order_by(desc(WorkLog.id)).limit(100).all()
    hist_data, current_task = [], None
    for log in logs:
        if not month or log.date_str.startswith(month):
            czas_str = format_dur(calc_mins(log.start_time, log.end_time)) if log.end_time else "-"
            hist_data.append({"data": log.date_str, "zadanie": log.task_name, "start": log.start_time.strftime("%H:%M"), "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa...", "czas": czas_str, "skaner": log.skaner, "wozek": log.wozek})
        if not log.end_time and not current_task and log.task_name != "Zakończenie pracy":
            current_task = {"name": log.task_name, "skaner": log.skaner, "wozek": log.wozek, "start_time": log.start_time.strftime("%H:%M")}
    return {"hist": hist_data, "currentTask": current_task}

@app.post("/api/user/action")
def user_action(req: dict, db: Session = Depends(get_db)):
    user, act_type, task = str(req.get("username", "")).strip(), req.get("type"), req.get("task")
    skaner, wozek = str(req.get("skaner", "")).strip(), str(req.get("wozek", "")).strip()
    now = get_now()
    date_str = now.strftime("%Y-%m-%d")
    
    if act_type in ["START", "TASK"]:
        if skaner and db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.skaner == skaner, WorkLog.username != user).first(): raise HTTPException(status_code=400, detail="Skaner zajęty przez kogoś innego!")
        if wozek and db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.wozek == wozek, WorkLog.username != user).first(): raise HTTPException(status_code=400, detail="Wózek zajęty przez kogoś innego!")

    active_log = db.query(WorkLog).filter(WorkLog.username == user, WorkLog.end_time == None).first()
    if active_log and act_type == "TASK":
        if not skaner: skaner = active_log.skaner
        if not wozek: wozek = active_log.wozek

    if active_log:
        active_log.end_time = now
        db.commit()
        
    if act_type in ["START", "TASK"]: db.add(WorkLog(username=user, task_name=task, start_time=now, date_str=date_str, skaner=skaner, wozek=wozek))
    elif act_type == "STOP": db.add(WorkLog(username=user, task_name="Zakończenie pracy", start_time=now, end_time=now, date_str=date_str))
    db.commit()
    return get_user_history({"username": user}, db)

@app.post("/api/user/equipment")
def update_equipment(req: dict, db: Session = Depends(get_db)):
    user, skaner, wozek = str(req.get("username", "")).strip(), str(req.get("skaner", "")).strip(), str(req.get("wozek", "")).strip()
    if skaner and db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.skaner == skaner, WorkLog.username != user).first(): raise HTTPException(status_code=400, detail="Skaner jest już w użyciu!")
    if wozek and db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.wozek == wozek, WorkLog.username != user).first(): raise HTTPException(status_code=400, detail="Wózek jest już w użyciu!")
    active_log = db.query(WorkLog).filter(WorkLog.username == user, WorkLog.end_time == None).first()
    if active_log:
        active_log.skaner, active_log.wozek = skaner, wozek
        db.commit()
    return get_user_history({"username": user}, db)

@app.post("/api/user/correct-task")
def correct_task(req: dict, db: Session = Depends(get_db)):
    last_log = db.query(WorkLog).filter(WorkLog.username == req.get("username"), WorkLog.task_name != "Zakończenie pracy").order_by(desc(WorkLog.id)).first()
    if last_log:
        last_log.task_name = req.get("task")
        db.commit()
        return get_user_history({"username": req.get("username")}, db)
    return False

@app.post("/api/user/problem")
def report_problem(req: dict, db: Session = Depends(get_db)):
    user, desc = req.get("username"), req.get("description")
    if user and desc:
        db.add(Problem(username=user, description=desc))
        db.commit()
        return True
    return False

@app.post("/api/user/messages/unread")
def check_unread_messages(req: dict, db: Session = Depends(get_db)):
    msgs = db.query(Message).filter(Message.receiver == req.get("username"), Message.is_read == 0).all()
    return [{"id": m.id, "content": m.content, "time": m.timestamp.strftime("%H:%M")} for m in msgs]

@app.post("/api/user/messages/reply")
def reply_message(req: dict, db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == req.get("msg_id")).first()
    if msg:
        msg.is_read = 1
        msg.reply = req.get("reply")
        db.commit()
        return True
    return False

@app.get("/api/admin/active-sessions")
def get_active_sessions(db: Session = Depends(get_db)):
    active_logs = db.query(WorkLog).filter(WorkLog.end_time == None).all()
    sessions = {}
    for log in active_logs:
        task = log.task_name
        if task not in sessions: sessions[task] = []
        sessions[task].append({"user": log.username, "skaner": log.skaner, "wozek": log.wozek, "start": log.start_time.strftime("%H:%M")})
    return sessions

@app.post("/api/admin/messages/send")
def send_admin_message(req: dict, db: Session = Depends(get_db)):
    for r in req.get("receivers", []): db.add(Message(receiver=r, content=req.get("content")))
    db.commit()
    return True

@app.post("/api/admin/live-session/close")
def close_live_session(req: dict, db: Session = Depends(get_db)):
    user, now = req.get("username"), get_now()
    active = db.query(WorkLog).filter(WorkLog.username == user, WorkLog.end_time == None).first()
    if active:
        active.end_time = now
        db.add(WorkLog(username=user, task_name="Zakończenie pracy", start_time=now, end_time=now, date_str=now.strftime("%Y-%m-%d")))
        db.commit()
    return True

@app.post("/api/admin/live-session/close-all")
def close_all_live_sessions(db: Session = Depends(get_db)):
    now = get_now()
    for active in db.query(WorkLog).filter(WorkLog.end_time == None).all():
        active.end_time = now
        db.add(WorkLog(username=active.username, task_name="Zakończenie pracy", start_time=now, end_time=now, date_str=now.strftime("%Y-%m-%d")))
    db.commit()
    return True

@app.post("/api/admin/live-session/edit")
def edit_live_session(req: dict, db: Session = Depends(get_db)):
    active = db.query(WorkLog).filter(WorkLog.username == req.get("username"), WorkLog.end_time == None).first()
    if active:
        active.task_name = req.get("task")
        active.skaner = req.get("skaner", "")
        active.wozek = req.get("wozek", "")
        if req.get("start_time"):
            active.start_time = datetime.strptime(f"{active.date_str} {req.get('start_time')}", "%Y-%m-%d %H:%M")
        db.commit()
    return True

@app.get("/api/admin/alerts")
def get_vmax_alerts(db: Session = Depends(get_db)):
    today = get_now().strftime("%Y-%m-%d")
    logs = db.query(WorkLog).filter(WorkLog.date_str == today).all()
    alerts = []
    replies = db.query(Message).filter(Message.is_read == 1, Message.reply != None, Message.is_archived == 0).all()
    for r in replies: alerts.append({"type": "msg", "id": r.id, "date": r.timestamp.strftime("%Y-%m-%d"), "text": f"✉️ <b>{r.receiver}</b> odpisał: <i>{r.reply}</i>"})
    probs = db.query(Problem).filter(Problem.is_resolved == 0).all()
    for p in probs: alerts.append({"type": "prob", "id": p.id, "date": p.timestamp.strftime("%Y-%m-%d"), "text": f"⚠️ PROBLEM ({p.username}): {p.description}"})
    dismissed = [d.alert_key for d in db.query(AlertDismiss).all()]
    for u in set([l.username for l in logs if l.is_autoclosed == 1]):
        key = f"sys_auto_{u}_{today}"
        if key not in dismissed: alerts.append({"type": "sys", "id": key, "date": today, "text": f"🔴 {u}: System zamknął sesję (brak aktywności >15h)."})
    for u in set([l.username for l in logs]):
        u_logs = [l for l in logs if l.username == u]
        break_mins = sum([calc_mins(l.start_time, l.end_time) for l in u_logs if "Przerwa" in l.task_name and l.end_time])
        has_finished = any([l.end_time and l.task_name == "Zakończenie pracy" for l in u_logs])
        if break_mins == 0 and has_finished: 
            key = f"sys_nobreak_{u}_{today}"
            if key not in dismissed: alerts.append({"type": "sys", "id": key, "date": today, "text": f"⚠️ {u}: Zakończył pracę bez przerwy."})
        elif break_mins > 40: 
            key = f"sys_longbreak_{u}_{today}"
            if key not in dismissed: alerts.append({"type": "sys", "id": key, "date": today, "text": f"⏱️ {u}: Przekroczono limit przerwy ({break_mins} min)."})
    return alerts

@app.post("/api/admin/alerts/dismiss-all")
def dismiss_all_vmax_alerts(req: dict, db: Session = Depends(get_db)):
    for a in req.get("alerts", []):
        a_type, a_id = a.get("type"), a.get("id")
        if a_type == "prob":
            p = db.query(Problem).filter(Problem.id == a_id).first()
            if p: p.is_resolved = 1
        elif a_type == "msg":
            m = db.query(Message).filter(Message.id == a_id).first()
            if m: m.is_archived = 1
        elif a_type == "sys":
            if not db.query(AlertDismiss).filter(AlertDismiss.alert_key == a_id).first():
                db.add(AlertDismiss(alert_key=a_id))
    db.commit()
    return True

@app.post("/api/admin/reports")
def get_admin_reports_json(req: dict, db: Session = Depends(get_db)):
    d1, d2, u_filter = req.get("d1"), req.get("d2"), req.get("user")
    query = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2)
    if u_filter and u_filter != "Wszyscy": query = query.filter(WorkLog.username == u_filter)
    raport = {}
    for log in query.order_by(desc(WorkLog.start_time)).all():
        u = log.username
        if u not in raport: raport[u] = {"totalMins": 0, "logi": []}
        mins = calc_mins(log.start_time, log.end_time) if log.end_time else 0
        raport[u]["totalMins"] += mins
        raport[u]["logi"].append({"zadanie": log.task_name, "data": log.date_str, "start": log.start_time.strftime("%H:%M"), "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa", "czas": format_dur(mins) if log.end_time else "-", "skaner": log.skaner, "wozek": log.wozek})
    for u in raport: raport[u]["totalStr"] = format_dur(raport[u]["totalMins"])
    return raport

@app.post("/api/admin/productivity")
def get_productivity_json(req: dict, db: Session = Depends(get_db)):
    d1, d2 = req.get("d1"), req.get("d2")
    chartData, headcount, workerDetails, packingStats = {}, {}, {}, {}
    for log in db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2).all():
        if log.task_name in ["Rozpoczęcie pracy", "Zakończenie pracy"]: continue
        mins = calc_mins(log.start_time, log.end_time) if log.end_time else 0
        if mins > 0:
            act, u = log.task_name, log.username
            chartData[act] = chartData.get(act, 0) + mins
            if act not in headcount: headcount[act] = []
            if u not in headcount[act]: headcount[act].append(u)
            if u not in workerDetails: workerDetails[u] = {}
            workerDetails[u][act] = workerDetails[u].get(act, 0) + mins
    for p in db.query(Productivity).filter(Productivity.date_str >= d1, Productivity.date_str <= d2).all():
        u = p.username
        if u not in packingStats: packingStats[u] = {"paczki": 0, "produkty": 0}
        packingStats[u]["paczki"] += p.paczki
        packingStats[u]["produkty"] += p.produkty
    for act in headcount: headcount[act] = len(headcount[act])
    return {"chartData": chartData, "headcount": headcount, "workerDetails": workerDetails, "packingStats": packingStats}

@app.post("/api/admin/equipment-log")
def get_eq_log_json(req: dict, db: Session = Depends(get_db)):
    d1, d2, s_filt, w_filt = req.get("d1"), req.get("d2"), req.get("skaner", ""), req.get("wozek", "")
    query = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2)
    if s_filt: query = query.filter(WorkLog.skaner == s_filt)
    if w_filt: query = query.filter(WorkLog.wozek == w_filt)
    results = []
    for log in query.order_by(desc(WorkLog.start_time)).all():
        if log.skaner or log.wozek: results.append({"worker": log.username, "task": log.task_name, "data": log.date_str, "start": log.start_time.strftime("%H:%M"), "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa", "skaner": log.skaner, "wozek": log.wozek})
    return results
