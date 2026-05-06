from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import os
import csv
import uuid
import json
import re
from datetime import datetime, timedelta
import zoneinfo
import openpyxl

# ==========================================
# 1. KONFIGURACJA BAZY DANYCH (WSPÓŁBIEŻNOŚĆ)
# ==========================================
db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

# connect_args zabezpiecza nas przed blokowaniem, gdy działa kilku Liderów naraz
if "sqlite" in db_url:
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(db_url)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_now():
    try: return datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)
    except Exception: return datetime.utcnow() + timedelta(hours=2)

# ==========================================
# 2. STRUKTURA BAZY DANYCH
# ==========================================
class UserGroup(Base):
    __tablename__ = "user_groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    emp_type = Column(String) 
    allowed_activities = Column(String, default="[]") 
    is_flexible = Column(Integer, default=0)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    global_id = Column(String, unique=True, index=True) 
    # Role w systemie: EMPLOYEE, TEAM_LEADER, MANAGER, ADMIN, SUPER_ADMIN
    role = Column(String, default="EMPLOYEE") 
    group_name = Column(String, default="Magazyn osoby stałe")
    name = Column(String, unique=True, index=True)
    pin = Column(String)
    # --- POLA DO HR ---
    hire_date = Column(DateTime, default=get_now)
    last_eval_date = Column(DateTime, nullable=True)
    eval_count = Column(Integer, default=0)
    notes = Column(String, default="")

class EvaluationLog(Base):
    __tablename__ = "evaluation_logs"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    eval_date = Column(DateTime, default=get_now)
    notes_snapshot = Column(String)

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

# MIGRACJE
with engine.connect() as conn:
    try: conn.execute(text("ALTER TABLE users ADD COLUMN hire_date TIMESTAMP")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE users ADD COLUMN last_eval_date TIMESTAMP")); conn.commit()
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
        conn.execute(text("UPDATE users SET group_name = 'Magazyn osoby stałe' WHERE group_name IS NULL"))
        conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE schedules ADD COLUMN is_override INTEGER DEFAULT 0")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE user_groups ADD COLUMN is_flexible INTEGER DEFAULT 0")); conn.commit()
    except: conn.rollback()
    try: conn.execute(text("ALTER TABLE activities ADD COLUMN color VARCHAR DEFAULT '#0A84FF'")); conn.commit()
    except: conn.rollback()

with SessionLocal() as db:
    users_to_fix = db.query(User).filter(User.hire_date == None).all()
    for u in users_to_fix:
        # Wypełniamy bezpieczną datę dla starych kont
        u.hire_date = get_now() - timedelta(days=90)
    db.commit()

    if not db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").first():
        db.add(GlobalSetting(setting_type="admin_login", value="ADMIN"))
    if not db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first():
        db.add(GlobalSetting(setting_type="admin_pass", value="admin"))

    default_groups = [
        {"name": "Magazyn osoby stałe", "type": "Stały", "flex": 0},
        {"name": "Magazyn osoby dodatkowe", "type": "Dodatkowy", "flex": 1},
        {"name": "Hydry", "type": "Dodatkowy", "flex": 1}
    ]
    for g in default_groups:
        if not db.query(UserGroup).filter(UserGroup.name == g["name"]).first():
            db.add(UserGroup(name=g["name"], emp_type=g["type"], allowed_activities="[]", is_flexible=g["flex"]))
    
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

# ==========================================
# 3. ENDPOINTY API
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
    u = str(req.get("username", "")).strip()
    p = str(req.get("pin", "")).strip()
    r = req.get("role", "EMPLOYEE")
    
    root_login, root_pass = get_root_admin(db)
    
    if r == "ADMIN":
        if u == root_login and p == root_pass: return {"ok": True, "name": root_login, "role": "SUPER_ADMIN"}
        user = db.query(User).filter(User.name == u, User.pin == p).first()
        # Dostęp menedżerski dla specjalnych statusów
        if user and user.role in ["ADMIN", "MANAGER", "TEAM_LEADER"]: 
            return {"ok": True, "name": user.name, "role": user.role}
        raise HTTPException(status_code=401, detail="Błędny Login/PIN lub brak uprawnień administracyjnych!")
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
    activityColors["Chory 🤒"] = "#FF3B30"
    activityColors["Urlop 🌴"] = "#FFCC00"
    activityColors["Wolne 🏠"] = "#8E8E93"
    activityColors["No Show ❌"] = "#8B0000"
    
    user_db = db.query(User).filter(User.name == name).first()
    allowed_acts = []
    if user_db:
        group_db = db.query(UserGroup).filter(UserGroup.name == user_db.group_name).first()
        if group_db and group_db.allowed_activities:
            allowed_acts = json.loads(group_db.allowed_activities)
            
    if allowed_acts: activities = [a.name for a in db_acts if a.name in allowed_acts]
    else: activities = [a.name for a in db_acts]

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
        
        schedule_list.append({
            "date": iso_date, "date_key": date_str, "act": p.activity if p and p.activity else "Brak planu",
            "hrs": p.hours if p else "", "req": req_obj, "override": p.is_override if p else 0 
        })
        
    return {"schedule": schedule_list, "shifts": shifts, "activities": activities, "activityColors": activityColors}

@app.post("/api/emp/request-batch")
def submit_request_batch(req: dict, db: Session = Depends(get_db)):
    name = req.get("name")
    updates = req.get("updates", [])
    today = get_now().date()
    
    root_login, _ = get_root_admin(db)
    if name == root_login: is_flexible_employee = True
    else:
        user_db = db.query(User).filter(User.name == name).first()
        if not user_db: return {"ok": False, "msg": "Błąd użytkownika"}
        group = db.query(UserGroup).filter(UserGroup.name == user_db.group_name).first()
        is_flexible_employee = group and group.is_flexible == 1
    
    for upd in updates:
        d_str, r_type, hrs = upd["date"], upd["act"], upd["hrs"]
        curr = datetime.strptime(d_str, "%Y-%m-%d").date()
        if curr.month == 1: dl_year, dl_month = curr.year - 1, 12
        else: dl_year, dl_month = curr.year, curr.month - 1
            
        deadline = datetime(dl_year, dl_month, 20).date()
        is_auto = is_flexible_employee or (today <= deadline)
        
        if is_auto:
            sched = db.query(Schedule).filter(Schedule.username == name, Schedule.date_str == d_str).first()
            if not sched:
                sched = Schedule(username=name, date_str=d_str)
                db.add(sched)
            sched.activity = r_type if r_type != "Wyczyść" else ""
            sched.hours = hrs if r_type != "Wyczyść" else ""
            sched.is_override = 0 
            db.query(Request).filter(Request.username == name, Request.date_str == d_str, Request.status == "Oczekuje").delete()
        else:
            existing = db.query(Request).filter(Request.username == name, Request.date_str == d_str, Request.status == "Oczekuje").first()
            if existing: existing.req_type, existing.hours = r_type, hrs
            else: db.add(Request(id=str(uuid.uuid4())[:8], username=name, date_str=d_str, req_type=r_type, hours=hrs, status="Oczekuje"))
    db.commit()
    msg_suffix = " (Omijanie 20-go - Grupa Elastyczna/Admin)" if is_flexible_employee else ""
    return {"ok": True, "msg": "Grafik zaktualizowany!" + msg_suffix}

@app.get("/api/admin/data")
def get_admin_data(db: Session = Depends(get_db)):
    db_users = db.query(User).order_by(User.global_id).all()
    employees = [{"id": u.global_id, "name": u.name, "group": u.group_name, "role": u.role} for u in db_users]
    groups = [{"name": g.name, "type": g.emp_type, "is_flexible": g.is_flexible, "activities": json.loads(g.allowed_activities) if g.allowed_activities else []} for g in db.query(UserGroup).all()]
    
    db_acts = db.query(Activity).all()
    activityColors = {a.name: a.color for a in db_acts}
    activityColors["Chory 🤒"] = "#FF3B30"
    activityColors["Urlop 🌴"] = "#FFCC00"
    activityColors["Wolne 🏠"] = "#8E8E93"
    activityColors["No Show ❌"] = "#8B0000"

    activities = [a.name for a in db_acts]
    planner_activities = list(set(activities + ["Chory 🤒", "Urlop 🌴", "Wolne 🏠", "No Show ❌"]))
    
    shifts = [s.value for s in db.query(GlobalSetting).filter(GlobalSetting.setting_type == "shift").all()]
    if not shifts: shifts = ["07:00-15:00", "08:00-16:00"]
    
    comps = db.query(Competence).all()
    ratings_map = {f"{c.username}_{c.activity}": c.rating for c in comps}
    
    schedules = db.query(Schedule).all()
    plan_map = {f"{s.username}_{s.date_str}": f"{s.activity}||{s.hours}||{s.is_override}" for s in schedules}
    
    capacities = db.query(DailyCapacity).all()
    capacity_map = {f"{c.date_str}_{c.activity}": c.required_count for c in capacities}
    
    reqs = db.query(Request).all()
    alerts, avail_map = [], {}
    
    for r in reversed(reqs):
        if r.status == "Oczekuje":
            alerts.append({
                "id": r.id, "name": r.username, "date": r.date_str, 
                "type": r.req_type, "hrs": r.hours, "ts": r.timestamp.strftime("%Y-%m-%d %H:%M")
            })
        elif r.status == "Zatwierdzono" and r.req_type == "Dostępny":
            avail_map[f"{r.username}_{r.date_str}"] = r.hours

    # Generowanie Alertów HR
    now = get_now()
    hr_alerts = []
    for u in db_users:
        if not u.hire_date: continue
        days_since_hire = (now - u.hire_date).days
        if u.role == "SUPER_ADMIN": continue
        
        if u.eval_count == 0 and days_since_hire >= 14:
            hr_alerts.append({"id": f"hr_{u.name}_0", "name": u.name, "date": now.strftime("%Y-%m-%d"), "type": "HR: Pierwsza ocena (14 dni)", "hrs": "", "ts": now.strftime("%Y-%m-%d %H:%M")})
        elif u.eval_count == 1 and days_since_hire >= 45:
            hr_alerts.append({"id": f"hr_{u.name}_1", "name": u.name, "date": now.strftime("%Y-%m-%d"), "type": "HR: Druga ocena (1.5 mc)", "hrs": "", "ts": now.strftime("%Y-%m-%d %H:%M")})
        elif u.eval_count >= 2 and u.last_eval_date:
            days_since_eval = (now - u.last_eval_date).days
            if days_since_eval >= 90:
                hr_alerts.append({"id": f"hr_{u.name}_{u.eval_count}", "name": u.name, "date": now.strftime("%Y-%m-%d"), "type": "HR: Ocena Okresowa (3 mc)", "hrs": "", "ts": now.strftime("%Y-%m-%d %H:%M")})

    root_login, _ = get_root_admin(db)

    return {
        "employees": employees, "groups": groups, "activityNames": activities, "plannerActivities": planner_activities,
        "activityColors": activityColors, "shifts": shifts, "ratingsMap": ratings_map, "planMap": plan_map, 
        "alerts": alerts, "hrAlerts": hr_alerts, "availMap": avail_map, "capacityMap": capacity_map, "rootAdmin": root_login
    }

@app.post("/api/admin/employee/hr_details")
def get_hr_details(req: dict, db: Session = Depends(get_db)):
    username = req.get("username")
    if not username: return {"ok": False}
    user_db = db.query(User).filter(User.name == username).first()
    notes = user_db.notes if user_db and user_db.notes else ""
    
    now = get_now()
    curr_y, curr_m = now.year, now.month
    curr_q = (curr_m - 1) // 3 + 1
    tracked_acts = ["Chory 🤒", "Urlop 🌴", "Wolne 🏠", "No Show ❌"]
    
    schedules = db.query(Schedule).filter(Schedule.username == username, Schedule.activity.in_(tracked_acts)).all()
    stats = {"miesiac": {}, "kwartal": {}, "rok": {}}
    for a in tracked_acts:
        stats["miesiac"][a] = 0; stats["kwartal"][a] = 0; stats["rok"][a] = 0
        
    for s in schedules:
        try:
            d = datetime.strptime(s.date_str, "%Y-%m-%d")
            if d.year == curr_y:
                stats["rok"][s.activity] += 1
                if ((d.month - 1) // 3 + 1) == curr_q: stats["kwartal"][s.activity] += 1
                if d.month == curr_m: stats["miesiac"][s.activity] += 1
        except: pass
        
    eval_logs = db.query(EvaluationLog).filter(EvaluationLog.username == username).order_by(desc(EvaluationLog.eval_date)).all()
    history = [{"date": l.eval_date.strftime("%Y-%m-%d"), "notes": l.notes_snapshot} for l in eval_logs]

    return {"ok": True, "stats": stats, "notes": notes, "history": history}

@app.post("/api/admin/employee/hr_save")
def save_hr_details(req: dict, db: Session = Depends(get_db)):
    username = req.get("username")
    notes = req.get("notes", "")
    is_eval = req.get("is_evaluation", False)
    
    user_db = db.query(User).filter(User.name == username).first()
    if user_db:
        user_db.notes = notes
        if is_eval:
            user_db.eval_count += 1
            user_db.last_eval_date = get_now()
            db.add(EvaluationLog(username=username, notes_snapshot=notes))
        db.commit()
    return {"ok": True, "msg": "Zapisano ewaluację!" if is_eval else "Zapisano notatki."}

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
    user = db.query(User).filter(User.name == req.get("name")).first()
    if user:
        user.group_name = req.get("group")
        db.commit()
    return {"ok": True}

@app.post("/api/admin/alerts/resolve")
def resolve_alert(req: dict, db: Session = Depends(get_db)):
    a_id, stat, name, date_str, a_type, hrs = req.get("id"), req.get("status"), req.get("name"), req.get("date"), req.get("type"), req.get("hrs")
    
    if "hr_" in str(a_id):
        return {"ok": False, "msg": "Alerty HR zamykają się automatycznie po zapisaniu Oceny w Karcie HR pracownika!"}
        
    db_req = db.query(Request).filter(Request.id == a_id).first()
    if db_req:
        db_req.status = stat
        if stat == "Zatwierdzono":
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
    stat = req.get("status", "Zatwierdzono")
    ids = req.get("ids", [])
    filtered_ids = [i for i in ids if "hr_" not in str(i)] 
    
    reqs = db.query(Request).filter(Request.id.in_(filtered_ids)).all()
    for db_req in reqs:
        db_req.status = stat
        if stat == "Zatwierdzono":
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
    comp = db.query(Competence).filter(Competence.username == name, Competence.activity == act).first()
    if not comp:
        comp = Competence(username=name, activity=act)
        db.add(comp)
    comp.rating = rating
    db.commit()
    return {"ok": True}

@app.post("/api/admin/planner/save")
def save_planner(req: dict, db: Session = Depends(get_db)):
    names, start, end = req.get("names", []), req.get("start"), req.get("end")
    act, hrs = req.get("activity"), req.get("hours", "")
    start_dt = datetime.strptime(start, "%Y-%m-%d") if start else None
    end_dt = datetime.strptime(end, "%Y-%m-%d") if end else None
    
    for n in names:
        if not start_dt:
            date_query = start 
            sched = db.query(Schedule).filter(Schedule.username == n, Schedule.date_str == date_query).first()
            if not sched:
                sched = Schedule(username=n, date_str=date_query)
                db.add(sched)
            sched.activity = act
            sched.hours = hrs
            sched.is_override = 1 
        else:
            curr = start_dt
            while curr <= end_dt:
                date_query = curr.strftime("%Y-%m-%d")
                sched = db.query(Schedule).filter(Schedule.username == n, Schedule.date_str == date_query).first()
                if not sched:
                    sched = Schedule(username=n, date_str=date_query)
                    db.add(sched)
                sched.activity = act
                sched.hours = hrs
                sched.is_override = 1 
                curr += timedelta(days=1)
    db.commit()
    return {"ok": True}

@app.post("/api/admin/planner/copy-day")
def copy_daily(req: dict, db: Session = Depends(get_db)):
    src_date, t_start, t_end = req.get("sourceDate"), req.get("targetStart"), req.get("targetEnd")
    d_obj = datetime.strptime(src_date, "%Y-%m-%d")
    src_query = d_obj.strftime("%Y-%m-%d")
    
    source_schedules = db.query(Schedule).filter(Schedule.date_str == src_query).all()
    if not source_schedules: return {"ok": True}
    
    start_dt = datetime.strptime(t_start, "%Y-%m-%d")
    end_dt = datetime.strptime(t_end, "%Y-%m-%d")
    
    curr = start_dt
    while curr <= end_dt:
        t_query = curr.strftime("%Y-%m-%d")
        for s in source_schedules:
            if s.activity and s.activity != "Wyczyść":
                target_s = db.query(Schedule).filter(Schedule.username == s.username, Schedule.date_str == t_query).first()
                if not target_s:
                    target_s = Schedule(username=s.username, date_str=t_query)
                    db.add(target_s)
                target_s.activity = s.activity
                target_s.hours = s.hours
                target_s.is_override = 1
        curr += timedelta(days=1)
    db.commit()
    return {"ok": True}

# ==========================================
# ENDPOINT POBIERANIA BAZY (BACKUP)
# ==========================================
@app.get("/api/admin/backup/db")
def download_db(db: Session = Depends(get_db)):
    db_path = "test.db"
    if os.path.exists(db_path):
        return FileResponse(db_path, filename=f"WMS_Backup_{get_now().strftime('%Y%m%d_%H%M')}.db")
    raise HTTPException(status_code=404, detail="Baza nie istnieje")

@app.post("/api/admin/settings")
def cms_settings(req: dict, db: Session = Depends(get_db)):
    action, t, val = req.get("action"), req.get("type"), req.get("value")
    try:
        if action == "ADD":
            if t == "employee": 
                if db.query(User).filter(User.name == val).first(): return {"ok": False, "msg": "Użytkownik już istnieje!"}
                new_id = generate_global_id(db)
                db.add(User(global_id=new_id, name=val, pin="1111", role=req.get("role", "EMPLOYEE"), group_name=req.get("group", "Magazyn osoby stałe")))
            elif t == "shift": db.add(GlobalSetting(setting_type="shift", value=val))
            elif t == "activity": 
                act = db.query(Activity).filter(Activity.name == val).first()
                if act: act.color = req.get("color", "#0A84FF")
                else: db.add(Activity(name=val, color=req.get("color", "#0A84FF")))
            elif t == "group": 
                if not db.query(UserGroup).filter(UserGroup.name == val).first():
                    db.add(UserGroup(name=val, emp_type=req.get("emp_type", "Stały"), is_flexible=req.get("is_flexible", 0), allowed_activities="[]"))
        
        elif action == "EDIT_USER_ROLE":
            user = db.query(User).filter(User.name == val).first()
            if user: user.role = req.get("role", "EMPLOYEE")

        elif action == "EDIT_ACTIVITY_COLOR":
            act = db.query(Activity).filter(Activity.name == val).first()
            if act: act.color = req.get("color", "#0A84FF")
            
        elif action == "EDIT_GROUP_FLEX":
            grp = db.query(UserGroup).filter(UserGroup.name == val).first()
            if grp: grp.is_flexible = req.get("is_flexible", 0)

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
                for g in db.query(UserGroup).all():
                    acts = json.loads(g.allowed_activities) if g.allowed_activities else []
                    if val in acts:
                        acts.remove(val)
                        g.allowed_activities = json.dumps(acts)
            elif t == "group":
                db.query(UserGroup).filter(UserGroup.name == val).delete()
                db.execute(text(f"UPDATE users SET group_name = 'Magazyn osoby stałe' WHERE group_name = '{val}'"))
        
        elif action == "EDIT_ROOT_ADMIN":
            l = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").first()
            p = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first()
            new_login, new_pass = req.get("login"), req.get("pass")
            if l and new_login: l.value = new_login
            if p and new_pass: p.value = new_pass
            
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "msg": str(e)}

# ==========================================
# 7. V-MAX (MODUŁ 2) - POMINIĘTE DLA ZWIĘZŁOŚCI (BEZ ZMIAN)
# ==========================================
