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
import re
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

# ==========================================
# 2. STRUKTURA BAZY DANYCH (ERP + VMAX)
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
    role = Column(String, default="EMPLOYEE") 
    group_name = Column(String, default="Nieprzypisani") 
    name = Column(String, unique=True, index=True)
    pin = Column(String)
    hire_date = Column(DateTime, default=get_now)
    last_eval_date = Column(DateTime, nullable=True)
    eval_count = Column(Integer, default=0)
    notes = Column(String, default="")

class EvaluationLog(Base):
    __tablename__ = "evaluation_logs"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    eval_date = Column(DateTime, default=get_now)
    rating = Column(Integer, default=3) 
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
        conn.execute(text("UPDATE users SET group_name = 'Nieprzypisani' WHERE group_name IS NULL"))
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
    for u in users_to_fix: u.hire_date = get_now() - timedelta(days=90)
    db.commit()

    if not db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").first():
        db.add(GlobalSetting(setting_type="admin_login", value="ADMIN"))
    if not db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_pass").first():
        db.add(GlobalSetting(setting_type="admin_pass", value="admin"))
    
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
# 3. MODUŁ IMPORTU I SEEDOWANIA Z EXCELA
# ==========================================
@app.post("/api/admin/import-excel")
def import_excel_endpoint(db: Session = Depends(get_db)):
    if not os.path.exists("imiona i nazwiska.xlsx"):
        return {"ok": False, "msg": "Brak pliku 'imiona i nazwiska.xlsx' w głównym folderze serwera!"}
        
    try:
        root_logins = [l.value for l in db.query(GlobalSetting).filter(GlobalSetting.setting_type == "admin_login").all()]
        if not root_logins: root_logins = ["ADMIN"]
        
        # Wypalamy bazę na czysto, oszczędzając super-admina
        db.query(User).filter(User.name.notin_(root_logins)).delete()
        db.query(UserGroup).delete()
        db.commit()
        
        wb = openpyxl.load_workbook("imiona i nazwiska.xlsx")
        sheet = wb.active
        headers = [cell.value for cell in sheet[1]]
        
        for col_idx, h_name in enumerate(headers, start=1):
            if h_name and not str(h_name).startswith("Unnamed"):
                g_name = str(h_name).strip()
                if g_name == "POSTY.1": g_name = "POSTY"
                
                # Tworzymy grupę jeśli nie istnieje
                if not db.query(UserGroup).filter(UserGroup.name == g_name).first():
                    db.add(UserGroup(name=g_name, emp_type="Stały", allowed_activities="[]", is_flexible=0))
                    db.commit()
                    
                # Ładujemy pracowników do tej grupy
                for row_idx in range(2, sheet.max_row + 1):
                    cell_val = sheet.cell(row=row_idx, column=col_idx).value
                    if cell_val:
                        name = str(cell_val).strip()
                        if not db.query(User).filter(User.name == name).first():
                            db.add(User(
                                global_id=generate_global_id(db), 
                                name=name, 
                                pin="1111", 
                                role="EMPLOYEE", 
                                group_name=g_name
                            ))
                db.commit()
                
        # Zabezpieczamy domyślną grupę dla przyszłych
        if not db.query(UserGroup).filter(UserGroup.name == "Nieprzypisani").first():
            db.add(UserGroup(name="Nieprzypisani", emp_type="Stały", allowed_activities="[]", is_flexible=0))
            db.commit()
            
        return {"ok": True, "msg": "Baza została zresetowana i załadowana pracownikami z pliku Excel!"}
    except Exception as e:
        db.rollback()
        return {"ok": False, "msg": f"Błąd importu: {str(e)}"}

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
            
        for a in acts: db.add(Competence(username=u.name, activity=a, rating=3))
            
        curr_d = start_date
        while curr_d <= end_date:
            d_str = curr_d.strftime("%Y-%m-%d")
            if curr_d.weekday() >= 5: db.add(Schedule(username=u.name, date_str=d_str, activity="Wolne 🏠", hours="", is_override=1))
            else:
                rand_val = random.randint(1, 100)
                if rand_val <= 5: db.add(Schedule(username=u.name, date_str=d_str, activity="Chory 🤒", hours="", is_override=1))
                elif rand_val <= 10: db.add(Schedule(username=u.name, date_str=d_str, activity="Urlop 🌴", hours="", is_override=1))
                elif rand_val <= 12: db.add(Schedule(username=u.name, date_str=d_str, activity="No Show ❌", hours="", is_override=1))
                else:
                    if acts: db.add(Schedule(username=u.name, date_str=d_str, activity=random.choice(acts), hours="07:00-15:00", is_override=1))
            curr_d += timedelta(days=1)
            
    db.commit()
    return {"ok": True, "msg": "Wygenerowano sztuczną historię na potrzeby testów."}

# ==========================================
# 4. STANDARDOWE API (BEZ ZMIAN)
# ==========================================
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
        return {"ok": True, "msg": "Hasło zostało zmienione!"}

    user = db.query(User).filter(User.name == name).first()
    if not user: return {"ok": False, "msg": "Nie znaleziono"}
    if not is_admin and user.pin != oldP: return {"ok": False, "msg": "Stary PIN błędny"}
    user.pin = newP
    db.commit()
    return {"ok": True, "msg": "PIN został zmieniony!"}

@app.post("/api/admin/db")
def admin_db_action(req: dict, db: Session = Depends(get_db)):
    action, t, name, val = req.get("action"), req.get("type"), req.get("name"), req.get("val")
    try:
        if action == "ADD":
            if t == "ADMIN":
                if db.query(User).filter(User.name == name).first(): return False
                db.add(User(global_id=generate_global_id(db), name=name, pin=val, role="MANAGER", group_name="Nieprzypisani"))
            elif t == "EMPLOYEE":
                if db.query(User).filter(User.name == name).first(): return False
                db.add(User(global_id=generate_global_id(db), name=name, pin=val, role="EMPLOYEE", group_name="Nieprzypisani"))
            elif t == "ACTIVITY":
                if db.query(Activity).filter(Activity.name == name).first(): return False
                db.add(Activity(name=name))
            elif t == "SCANNER": db.add(Scanner(name=name))
            elif t == "TROLLEY": db.add(Trolley(name=name))
            
        elif action == "DELETE":
            if t == "ADMIN" or t == "EMPLOYEE": db.query(User).filter(User.name == name).delete()
            elif t == "ACTIVITY": db.query(Activity).filter(Activity.name == name).delete()
            elif t == "SCANNER": db.query(Scanner).filter(Scanner.name == name).delete()
            elif t == "TROLLEY": db.query(Trolley).filter(Trolley.name == name).delete()
            
        elif action == "EDIT_PIN":
            if t == "ADMIN" or t == "EMPLOYEE":
                u = db.query(User).filter(User.name == name).first()
                if u: u.pin = val
        db.commit()
        return True
    except:
        db.rollback()
        return False

# ==========================================
# 5. V-MAX (CROSS-CHECK ALERTY I CZAS PRACY)
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

@app.get("/api/admin/alerts")
def get_vmax_alerts(db: Session = Depends(get_db)):
    today = get_now().strftime("%Y-%m-%d")
    now = get_now()
    
    logs = db.query(WorkLog).filter(WorkLog.date_str == today).all()
    schedules = db.query(Schedule).filter(Schedule.date_str == today).all()
    alerts = []
    
    replies = db.query(Message).filter(Message.is_read == 1, Message.reply != None, Message.is_archived == 0).all()
    for r in replies: alerts.append({"type": "msg", "id": r.id, "date": r.timestamp.strftime("%Y-%m-%d"), "text": f"✉️ <b>{r.receiver}</b> odpisał: <i>{r.reply}</i>"})
    
    probs = db.query(Problem).filter(Problem.is_resolved == 0).all()
    for p in probs: alerts.append({"type": "prob", "id": p.id, "date": p.timestamp.strftime("%Y-%m-%d"), "text": f"⚠️ PROBLEM ({p.username}): {p.description}"})
    
    dismissed = [d.alert_key for d in db.query(AlertDismiss).all()]
    
    # 1. Wbudowane V-MAX systemowe
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

    # 2. CROSS-CHECK ERP vs V-MAX (Zaplanowani a rzeczywistość)
    for s in schedules:
        if not s.hours or s.activity in ["Wyczyść", "Brak planu", "Urlop 🌴", "Chory 🤒", "Wolne 🏠", "No Show ❌"]:
            continue
            
        try:
            start_str, end_str = s.hours.split("-")
            start_dt = datetime.strptime(f"{today} {start_str.strip()}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{today} {end_str.strip()}", "%Y-%m-%d %H:%M")
        except:
            continue
            
        u_logs = [l for l in logs if l.username == s.username]
        
        # Błąd startu: Zbliża się 5 min po starcie, a pracownik nie ma logu
        if now > start_dt + timedelta(minutes=5) and not u_logs:
            key = f"cross_nostart_{s.username}_{today}"
            if key not in dismissed:
                alerts.append({"type": "sys", "id": key, "date": today, "text": f"🔴 BRAK ODBICIA: <b>{s.username}</b> miał zacząć o {start_str}, a nie zalogował się do V-MAX!"})
                
        # Błąd końca: Minęło 5 min od końca zmiany, a sesja nadal jest aktywna
        active_session = any([l.end_time is None for l in u_logs])
        if now > end_dt + timedelta(minutes=5) and active_session:
            key = f"cross_noend_{s.username}_{today}"
            if key not in dismissed:
                alerts.append({"type": "sys", "id": key, "date": today, "text": f"⚠️ NADGODZINY / BRAK WYLOGOWANIA: <b>{s.username}</b> kończy zmianę o {end_str}, a sesja V-MAX wciąż trwa!"})

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
    if skaner and db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.skaner == skaner, WorkLog.username != user).first(): raise HTTPException(status_code=400, detail="Skaner w użyciu!")
    if wozek and db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.wozek == wozek, WorkLog.username != user).first(): raise HTTPException(status_code=400, detail="Wózek w użyciu!")
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
