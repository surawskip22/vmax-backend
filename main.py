from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import os
import csv
import uuid
import json
from datetime import datetime, timedelta
import zoneinfo
import openpyxl

# 1. KONFIGURACJA BAZY DANYCH
db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_now():
    try: return datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)
    except Exception: return datetime.utcnow() + timedelta(hours=2)

# ==========================================
# 2. STRUKTURA BAZY DANYCH (MODUŁ 1 & 2)
# ==========================================

class UserGroup(Base):
    __tablename__ = "user_groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    emp_type = Column(String) # STALY / DODATKOWY
    allowed_activities = Column(String)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    global_id = Column(String, unique=True, index=True) # Nowe 5-cyfrowe ID
    role = Column(String, default="EMPLOYEE")
    group_name = Column(String, default="Magazyn osoby stałe")
    name = Column(String, unique=True, index=True)
    pin = Column(String)

class GlobalSetting(Base):
    __tablename__ = "global_settings"
    id = Column(Integer, primary_key=True, index=True)
    setting_type = Column(String, index=True) 
    value = Column(String)

# --- TABELE MODUŁU 1 (PLANNER) ---
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

class Request(Base):
    __tablename__ = "requests"
    id = Column(String, primary_key=True, index=True)
    username = Column(String, index=True)
    date_str = Column(String, index=True)
    req_type = Column(String) 
    hours = Column(String)
    status = Column(String, default="Oczekuje") 
    timestamp = Column(DateTime, default=get_now)

# --- TABELE MODUŁU 2 (V-MAX) ---
class Activity(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)

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

# CHIRURGICZNA MIGRACJA: Dodanie grup, nowych kolumn i nadanie 5-cyfrowych ID
with engine.connect() as conn:
    try: 
        conn.execute(text("ALTER TABLE users ADD COLUMN global_id VARCHAR"))
        conn.commit()
    except Exception: 
        conn.rollback()
        
    try: 
        conn.execute(text("ALTER TABLE users ADD COLUMN group_name VARCHAR"))
        conn.commit()
        conn.execute(text("UPDATE users SET group_name = 'Magazyn osoby stałe' WHERE group_name IS NULL"))
        conn.commit()
    except Exception: 
        conn.rollback()

def generate_global_id(db: Session):
    last_user = db.query(User).filter(User.global_id.op('~')('^[0-9]+$')).order_by(desc(User.global_id)).first()
    if not last_user: return "00001"
    return f"{int(last_user.global_id) + 1:05d}"

with SessionLocal() as db:
    default_groups = [
        {"name": "Magazyn osoby stałe", "type": "STALY"},
        {"name": "Magazyn osoby dodatkowe", "type": "DODATKOWY"},
        {"name": "Nowi Magazyn stali", "type": "STALY"},
        {"name": "Nowi Magazyn dodatkowi", "type": "DODATKOWY"},
        {"name": "Hydry", "type": "DODATKOWY"},
        {"name": "Zwroty", "type": "STALY"},
        {"name": "Obsługa", "type": "STALY"}
    ]
    for g in default_groups:
        if not db.query(UserGroup).filter(UserGroup.name == g["name"]).first():
            db.add(UserGroup(name=g["name"], emp_type=g["type"], allowed_activities="[]"))
    
    bad_users = db.query(User).filter(~User.global_id.op('~')('^[0-9]{5}$')).all()
    for u in bad_users:
        u.global_id = generate_global_id(db)
        db.commit()
    db.commit()

# 3. INICJALIZACJA APLIKACJI
app = FastAPI(title="WMS Enterprise Platform")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def calc_mins(start: datetime, end: datetime):
    if not start or not end: return 0
    return int((end - start).total_seconds() / 60)

def format_dur(mins: int):
    if mins < 0: mins = 0
    return f"{mins//60}h {mins%60}m"

# ==========================================
# 4. ROUTING HTML
# ==========================================
@app.get("/")
def serve_vmax():
    return FileResponse("index.html")

@app.get("/planner")
def serve_planner():
    if os.path.exists("planner.html"): return FileResponse("planner.html")
    return HTMLResponse("<h1>Brak pliku planner.html</h1>", status_code=404)

# ==========================================
# 5. INTEGRACJA: LOGOWANIE
# ==========================================
@app.get("/api/public")
def get_public_data(db: Session = Depends(get_db)):
    employees = [u.name for u in db.query(User).filter(User.role == "EMPLOYEE").all()]
    return {"employees": employees}

@app.post("/api/auth/login")
def login(req: dict, db: Session = Depends(get_db)):
    u, p, r = str(req.get("username", "")).strip(), str(req.get("pin", "")).strip(), req.get("role", "EMPLOYEE")
    
    if u == "ADMIN" and p == "admin": return {"ok": True, "name": "ADMIN", "role": "ADMIN"}
    
    if r == "ADMIN":
        custom_admin_pass = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first()
        actual_pass = custom_admin_pass.value if custom_admin_pass else "Biore123"
        if u.lower() == "admin" and p == actual_pass: return {"ok": True, "name": "Admin", "role": "ADMIN"}
            
    user = db.query(User).filter(User.name == u, User.pin == p).first()
    if user: 
        return_role = "USER" if user.role == "EMPLOYEE" else user.role
        return {"ok": True, "name": user.name, "role": return_role}
        
    raise HTTPException(status_code=401, detail="Błędny PIN lub Hasło")

@app.post("/api/auth/change-pin")
def change_pin(req: dict, db: Session = Depends(get_db)):
    name, oldP, newP, is_admin = req.get("name"), req.get("oldPin"), req.get("newPin"), req.get("isAdmin")
    user = db.query(User).filter(User.name == name).first()
    if not user: return {"ok": False, "msg": "Nie znaleziono użytkownika"}
    if not is_admin and user.pin != oldP: return {"ok": False, "msg": "Stary PIN jest błędny"}
    user.pin = newP
    db.commit()
    return {"ok": True, "msg": "PIN został zmieniony!"}


# ==========================================
# 6. WMS PLANNER (MODUŁ 1) - ENDPOINTY
# ==========================================
@app.post("/api/emp/dashboard")
def get_emp_dash(req: dict, db: Session = Depends(get_db)):
    name, year, month = req.get("name"), int(req.get("year")), int(req.get("month"))
    
    schedules = db.query(Schedule).filter(Schedule.username == name).all()
    requests = db.query(Request).filter(Request.username == name).all()
    shifts = [s.value for s in db.query(GlobalSetting).filter(GlobalSetting.setting_type == "shift").all()]
    if not shifts: shifts = ["07:00-15:00", "08:00-16:00"]
    
    activities = [a.name for a in db.query(Activity).all()]
    
    plan_map = {s.date_str: s for s in schedules}
    req_map = {r.date_str: r for r in requests}
    
    next_month = month + 1 if month < 11 else 0
    next_year = year if month < 11 else year + 1
    days_in_month = (datetime(next_year, next_month + 1, 1) - timedelta(days=1)).day
    
    schedule_list = []
    for d in range(1, days_in_month + 1):
        date_str = f"{year}-{month}-{d}"
        iso_date = datetime(year, month + 1, d).isoformat()
        
        p = plan_map.get(date_str)
        r = req_map.get(date_str)
        req_obj = {"type": r.req_type, "hrs": r.hours, "status": r.status} if r else None
        
        schedule_list.append({
            "date": iso_date,
            "act": p.activity if p and p.activity else "Brak planu",
            "hrs": p.hours if p else "",
            "req": req_obj
        })
        
    return {"schedule": schedule_list, "shifts": shifts, "activities": activities}

@app.post("/api/emp/request")
def submit_request(req: dict, db: Session = Depends(get_db)):
    name, start_str, end_str = req.get("name"), req.get("start"), req.get("end")
    r_type, hrs = req.get("type"), req.get("hrs")
    
    start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    end_dt = datetime.strptime(end_str, "%Y-%m-%d")
    today = get_now().date()
    
    curr = start_dt
    while curr <= end_dt:
        date_query = f"{curr.year}-{curr.month - 1}-{curr.day}"
        
        # REGUŁA 20. DNIA: Obliczamy deadline (20. dzień poprzedniego miesiąca dla malowanego dnia)
        if curr.month == 1:
            dl_year, dl_month = curr.year - 1, 12
        else:
            dl_year, dl_month = curr.year, curr.month - 1
            
        deadline = datetime(dl_year, dl_month, 20).date()
        is_auto = today <= deadline
        
        if is_auto:
            # Zapisuje się bezpośrednio na twardo w głównej tabeli Grafiku
            sched = db.query(Schedule).filter(Schedule.username == name, Schedule.date_str == date_query).first()
            if not sched:
                sched = Schedule(username=name, date_str=date_query)
                db.add(sched)
            sched.activity = r_type if r_type != "Wyczyść" else ""
            sched.hours = hrs if r_type != "Wyczyść" else ""
        else:
            # Tworzy wniosek do Managera
            existing = db.query(Request).filter(Request.username == name, Request.date_str == date_query, Request.status == "Oczekuje").first()
            if existing:
                existing.req_type, existing.hours = r_type, hrs
            else:
                new_req = Request(id=str(uuid.uuid4())[:8], username=name, date_str=date_query, req_type=r_type, hours=hrs, status="Oczekuje")
                db.add(new_req)
                
        curr += timedelta(days=1)
        
    db.commit()
    msg = "Grafik zapisany automatycznie!" if is_auto else "Zgłoszono zmianę do akceptacji Managera."
    return {"ok": True, "msg": msg}

@app.get("/api/admin/data")
def get_admin_data(db: Session = Depends(get_db)):
    db_users = db.query(User).filter(User.role == "EMPLOYEE").order_by(User.global_id).all()
    employees = [{"id": u.global_id, "name": u.name, "group": u.group_name} for u in db_users]
    
    groups = [{"name": g.name, "type": g.emp_type, "activities": json.loads(g.allowed_activities) if g.allowed_activities else []} for g in db.query(UserGroup).all()]
    
    activities = [a.name for a in db.query(Activity).all()]
    planner_activities = list(set(activities + ["Chory 🤒", "Urlop 🌴", "Wolne 🏠"]))
    shifts = [s.value for s in db.query(GlobalSetting).filter(GlobalSetting.setting_type == "shift").all()]
    if not shifts: shifts = ["07:00-15:00", "08:00-16:00"]
    
    comps = db.query(Competence).all()
    ratings_map = {f"{c.username}_{c.activity}": c.rating for c in comps}
    
    schedules = db.query(Schedule).all()
    plan_map = {f"{s.username}_{s.date_str}": f"{s.activity}||{s.hours}" for s in schedules}
    
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

    return {
        "employees": employees, "groups": groups, "activityNames": activities, "plannerActivities": planner_activities,
        "shifts": shifts, "ratingsMap": ratings_map, "planMap": plan_map, 
        "alerts": alerts, "availMap": avail_map
    }

@app.post("/api/admin/alerts/resolve")
def resolve_alert(req: dict, db: Session = Depends(get_db)):
    a_id, stat, name, date_str, a_type, hrs = req.get("id"), req.get("status"), req.get("name"), req.get("date"), req.get("type"), req.get("hrs")
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
        db.commit()
    return {"ok": True, "msg": "Wniosek rozpatrzony!"}

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
    
    start_dt = datetime.strptime(start, "%Y-%m-%d") if "-" in start and len(start.split("-"))==3 else None
    end_dt = datetime.strptime(end, "%Y-%m-%d") if "-" in end and len(end.split("-"))==3 else None
    
    for n in names:
        if not start_dt:
            date_query = start 
            sched = db.query(Schedule).filter(Schedule.username == n, Schedule.date_str == date_query).first()
            if not sched:
                sched = Schedule(username=n, date_str=date_query)
                db.add(sched)
            sched.activity = act
            sched.hours = hrs
        else:
            curr = start_dt
            while curr <= end_dt:
                date_query = f"{curr.year}-{curr.month - 1}-{curr.day}"
                sched = db.query(Schedule).filter(Schedule.username == n, Schedule.date_str == date_query).first()
                if not sched:
                    sched = Schedule(username=n, date_str=date_query)
                    db.add(sched)
                sched.activity = act
                sched.hours = hrs
                curr += timedelta(days=1)
    db.commit()
    return {"ok": True}

@app.post("/api/admin/planner/copy-day")
def copy_daily(req: dict, db: Session = Depends(get_db)):
    src_date, t_start, t_end = req.get("sourceDate"), req.get("targetStart"), req.get("targetEnd")
    d_obj = datetime.strptime(src_date, "%Y-%m-%d")
    src_query = f"{d_obj.year}-{d_obj.month - 1}-{d_obj.day}"
    
    source_schedules = db.query(Schedule).filter(Schedule.date_str == src_query).all()
    if not source_schedules: return {"ok": True}
    
    start_dt = datetime.strptime(t_start, "%Y-%m-%d")
    end_dt = datetime.strptime(t_end, "%Y-%m-%d")
    
    curr = start_dt
    while curr <= end_dt:
        t_query = f"{curr.year}-{curr.month - 1}-{curr.day}"
        for s in source_schedules:
            if s.activity and s.activity != "Wyczyść":
                target_s = db.query(Schedule).filter(Schedule.username == s.username, Schedule.date_str == t_query).first()
                if not target_s:
                    target_s = Schedule(username=s.username, date_str=t_query)
                    db.add(target_s)
                target_s.activity = s.activity
                target_s.hours = s.hours
        curr += timedelta(days=1)
    db.commit()
    return {"ok": True}

@app.post("/api/admin/settings")
def cms_settings(req: dict, db: Session = Depends(get_db)):
    action, t, val = req.get("action"), req.get("type"), req.get("value")
    group = req.get("group", "Magazyn osoby stałe")
    
    if action == "ADD":
        if t == "employee": 
            new_id = generate_global_id(db)
            db.add(User(global_id=new_id, name=val, pin="1111", role="EMPLOYEE", group_name=group))
        elif t == "shift": db.add(GlobalSetting(setting_type="shift", value=val))
        elif t == "activity": db.add(Activity(name=val))
    elif action == "DELETE":
        if t == "employee": db.query(User).filter(User.name == val).delete()
        elif t == "shift": db.query(GlobalSetting).filter(GlobalSetting.setting_type == "shift", GlobalSetting.value == val).delete()
        elif t == "activity": db.query(Activity).filter(Activity.name == val).delete()
    elif action == "EDIT_ADMIN_PASS":
        p = db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first()
        if not p: db.add(GlobalSetting(setting_type="admin_pass", value=val))
        else: p.value = val
    db.commit()
    return {"ok": True}


# ==========================================
# 7. V-MAX (MODUŁ 2) - CZYSTY, NIEBLOKOWANY V-MAX
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

    admins = {u.name: u.pin for u in db.query(User).filter(User.role == "ADMIN").all()}
    employees = [u.name for u in db.query(User).filter(User.role == "EMPLOYEE").all()]
    activities = [a.name for a in db.query(Activity).all()]
    scanners = [s.name for s in db.query(Scanner).all()]
    trolleys = [t.name for t in db.query(Trolley).all()]
    active_logs = db.query(WorkLog).filter(WorkLog.end_time == None).all()
    
    return {
        "admins": admins, "pracownicy": employees, "aktywnosci": activities,
        "skanery": scanners, "wozki": trolleys,
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
            hist_data.append({
                "data": log.date_str, "zadanie": log.task_name, 
                "start": log.start_time.strftime("%H:%M"), "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa...", 
                "czas": czas_str, "skaner": log.skaner, "wozek": log.wozek
            })
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
        if skaner:
            if db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.skaner == skaner, WorkLog.username != user).first():
                raise HTTPException(status_code=400, detail="Skaner zajęty przez kogoś innego!")
        if wozek:
            if db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.wozek == wozek, WorkLog.username != user).first():
                raise HTTPException(status_code=400, detail="Wózek zajęty przez kogoś innego!")

    active_log = db.query(WorkLog).filter(WorkLog.username == user, WorkLog.end_time == None).first()
    if active_log and act_type == "TASK":
        if not skaner: skaner = active_log.skaner
        if not wozek: wozek = active_log.wozek

    if active_log:
        active_log.end_time = now
        db.commit()
        
    if act_type in ["START", "TASK"]:
        db.add(WorkLog(username=user, task_name=task, start_time=now, date_str=date_str, skaner=skaner, wozek=wozek))
    elif act_type == "STOP":
        db.add(WorkLog(username=user, task_name="Zakończenie pracy", start_time=now, end_time=now, date_str=date_str))
    db.commit()
    
    return get_user_history({"username": user}, db)

@app.post("/api/user/equipment")
def update_equipment(req: dict, db: Session = Depends(get_db)):
    user, skaner, wozek = str(req.get("username", "")).strip(), str(req.get("skaner", "")).strip(), str(req.get("wozek", "")).strip()
    if skaner and db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.skaner == skaner, WorkLog.username != user).first():
        raise HTTPException(status_code=400, detail="Skaner jest już w użyciu!")
    if wozek and db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.wozek == wozek, WorkLog.username != user).first():
        raise HTTPException(status_code=400, detail="Wózek jest już w użyciu!")

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
        sessions[task].append({
            "user": log.username, "skaner": log.skaner, "wozek": log.wozek, "start": log.start_time.strftime("%H:%M")
        })
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
        raport[u]["logi"].append({
            "zadanie": log.task_name, "data": log.date_str,
            "start": log.start_time.strftime("%H:%M"), "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa",
            "czas": format_dur(mins) if log.end_time else "-", "skaner": log.skaner, "wozek": log.wozek
        })
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
        if log.skaner or log.wozek:
            results.append({
                "worker": log.username, "task": log.task_name, "data": log.date_str,
                "start": log.start_time.strftime("%H:%M"), "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa",
                "skaner": log.skaner, "wozek": log.wozek
            })
    return results

@app.post("/api/admin/edit-logs")
def get_edit_logs_json(req: dict, db: Session = Depends(get_db)):
    logs = db.query(WorkLog).filter(WorkLog.username == req.get("username"), WorkLog.date_str == req.get("date")).order_by(desc(WorkLog.id)).all()
    return [{"id": l.id, "zadanie": l.task_name, "start": l.start_time.strftime("%H:%M") if l.start_time else "", "koniec": l.end_time.strftime("%H:%M") if l.end_time else ""} for l in logs]

@app.post("/api/admin/update-batch")
def update_batch_vmax(req: dict, db: Session = Depends(get_db)):
    date_str = req.get("date")
    for u in req.get("updates", []):
        log = db.query(WorkLog).filter(WorkLog.id == int(u.get("id"))).first()
        if log:
            log.task_name = u.get("task")
            if u.get("start"): log.start_time = datetime.strptime(f"{date_str} {u.get('start')}", "%Y-%m-%d %H:%M")
            if u.get("end"): log.end_time = datetime.strptime(f"{date_str} {u.get('end')}", "%Y-%m-%d %H:%M")
    db.commit()
    return True

def create_export_file(filename_base: str, rows: list, fmt: str):
    os.makedirs("exports", exist_ok=True)
    if fmt == "xls":
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in rows: ws.append(row)
        filepath = os.path.join("exports", f"{filename_base}.xlsx")
        wb.save(filepath)
        return f"/api/download/{filename_base}.xlsx"
    else:
        filepath = os.path.join("exports", f"{filename_base}.csv")
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerows(rows)
        return f"/api/download/{filename_base}.csv"

@app.post("/api/admin/export")
def export_general(req: dict, db: Session = Depends(get_db)):
    export_type, fmt, d1, d2, u_filt = req.get("export_type"), req.get("format", "csv"), req.get("d1"), req.get("d2"), req.get("user")
    uid = uuid.uuid4().hex[:4]
    
    if export_type == "HR":
        stats = {}
        for log in db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2).all():
            if not log.end_time: continue
            if log.username not in stats: stats[log.username] = {"wd": 0, "we": 0}
            mins = calc_mins(log.start_time, log.end_time)
            if log.start_time.weekday() >= 5: stats[log.username]["we"] += mins
            else: stats[log.username]["wd"] += mins
        rows = [["Pracownik", "Godziny Pn-Pt", "Godziny Weekend", "Suma Godzin"]]
        for u, v in stats.items(): rows.append([u, format_dur(v["wd"]), format_dur(v["we"]), format_dur(v["wd"] + v["we"])])
        return {"url": create_export_file(f"Raport_HR_{d1}_{uid}", rows, fmt)}
        
    elif export_type == "FULL":
        query = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2)
        if u_filt and u_filt != "Wszyscy": query = query.filter(WorkLog.username == u_filt)
        rows = [["Pracownik", "Zadanie", "Data", "Start", "Koniec", "Czas trwania", "Skaner", "Wozek"]]
        for log in query.order_by(WorkLog.start_time).all():
            mins = calc_mins(log.start_time, log.end_time) if log.end_time else 0
            rows.append([log.username, log.task_name, log.date_str, log.start_time.strftime("%H:%M"), log.end_time.strftime("%H:%M") if log.end_time else "Trwa", format_dur(mins) if log.end_time else "-", log.skaner, log.wozek])
        return {"url": create_export_file(f"Raport_Pelny_{d1}_{uid}", rows, fmt)}
        
    elif export_type == "PROD":
        stats = {}
        for log in db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2, WorkLog.task_name == "Pakowanie Paczek").all():
            if log.username not in stats: stats[log.username] = {"mins": 0, "paczki": 0, "produkty": 0}
            stats[log.username]["mins"] += calc_mins(log.start_time, log.end_time) if log.end_time else 0
        for p in db.query(Productivity).filter(Productivity.date_str >= d1, Productivity.date_str <= d2).all():
            if p.username not in stats: stats[p.username] = {"mins": 0, "paczki": 0, "produkty": 0}
            stats[p.username]["paczki"] += p.paczki
            stats[p.username]["produkty"] += p.produkty
        rows = [["Pracownik", "Suma Godzin", "Spakowane Paczki", "Spakowane Produkty", "Paczki/h", "Produkty/h"]]
        for u, v in stats.items():
            if v["mins"] == 0 and v["paczki"] == 0: continue
            h = v["mins"] / 60
            rows.append([u, format_dur(v["mins"]), v["paczki"], v["produkty"], round(v["paczki"]/h, 2) if h > 0 else 0, round(v["produkty"]/h, 2) if h > 0 else 0])
        return {"url": create_export_file(f"Wydajnosc_{d1}_{uid}", rows, fmt)}
        
    elif export_type == "EQ":
        s_filt, w_filt = req.get("skaner"), req.get("wozek")
        query = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2)
        if s_filt: query = query.filter(WorkLog.skaner == s_filt)
        if w_filt: query = query.filter(WorkLog.wozek == w_filt)
        rows = [["Pracownik", "Zadanie", "Data", "Start", "Koniec", "Skaner", "Wozek"]]
        for log in query.order_by(WorkLog.start_time).all():
            if log.skaner or log.wozek: rows.append([log.username, log.task_name, log.date_str, log.start_time.strftime("%H:%M"), log.end_time.strftime("%H:%M") if log.end_time else "Trwa", log.skaner, log.wozek])
        return {"url": create_export_file(f"Sprzet_{d1}_{uid}", rows, fmt)}

    elif export_type == "BACKUP":
        logs = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2).order_by(WorkLog.id).all()
        if fmt == "json":
            data = [{"id": l.id, "worker": l.username, "task": l.task_name, "date": l.date_str, "start": l.start_time.isoformat() if l.start_time else None, "end": l.end_time.isoformat() if l.end_time else None, "scanner": l.skaner, "trolley": l.wozek} for l in logs]
            filepath = os.path.join("exports", f"DB_SNAPSHOT_{d1}_{uid}.json")
            with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)
            return {"url": f"/api/download/DB_SNAPSHOT_{d1}_{uid}.json"}
        else:
            rows = [["ID", "Pracownik", "Zadanie", "Data", "Start", "Koniec", "Skaner", "Wozek", "Auto_Zamknieto"]]
            for l in logs: rows.append([l.id, l.username, l.task_name, l.date_str, l.start_time.strftime("%Y-%m-%d %H:%M:%S") if l.start_time else "", l.end_time.strftime("%Y-%m-%d %H:%M:%S") if l.end_time else "", l.skaner, l.wozek, l.is_autoclosed])
            return {"url": create_export_file(f"DB_BACKUP_{d1}_{uid}", rows, fmt)}

@app.post("/api/admin/db")
def manage_db_vmax(req: dict, db: Session = Depends(get_db)):
    action, db_type, name, val = req.get("action"), req.get("type"), str(req.get("name", "")).strip(), str(req.get("val", "")).strip()
    if action == "ADD":
        if db_type == "EMPLOYEE": db.add(User(name=name, pin=val, role="EMPLOYEE"))
        elif db_type == "ADMIN": db.add(User(name=name, pin=val, role="ADMIN"))
        elif db_type == "ACTIVITY": db.add(Activity(name=name))
        elif db_type == "SCANNER": db.add(Scanner(name=name))
        elif db_type == "TROLLEY": db.add(Trolley(name=name))
    elif action == "DELETE":
        if db_type in ["EMPLOYEE", "ADMIN"]: db.query(User).filter(User.name == name).delete()
        elif db_type == "ACTIVITY": db.query(Activity).filter(Activity.name == name).delete()
        elif db_type == "SCANNER": db.query(Scanner).filter(Scanner.name == name).delete()
        elif db_type == "TROLLEY": db.query(Trolley).filter(Trolley.name == name).delete()
    elif action == "EDIT_PIN":
        user = db.query(User).filter(User.name == name, User.role == db_type).first()
        if user: user.pin = val
    db.commit()
    return {"status": "success"}

@app.get("/api/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join("exports", filename)
    if os.path.exists(file_path): 
        media = "application/json" if filename.endswith('.json') else ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if filename.endswith('.xlsx') else "text/csv")
        return FileResponse(file_path, filename=filename, media_type=media)
    raise HTTPException(status_code=404, detail="Plik nie istnieje.")
