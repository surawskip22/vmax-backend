from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pydantic import BaseModel
import os
import csv
import uuid
import re   
import random
from datetime import datetime, timedelta
import zoneinfo # Do poprawnej strefy czasowej PL

# 1. KONFIGURACJA BAZY DANYCH
db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- STREFA CZASOWA POLSKA ---
def get_now():
    try:
        # Konwersja na czas polski
        return datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).replace(tzinfo=None)
    except Exception:
        # Fallback (Gdyby serwer marudził)
        return datetime.utcnow() + timedelta(hours=2)

# 2. DEFINICJA TABEL W BAZIE
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    role = Column(String, default="EMPLOYEE")
    name = Column(String, unique=True, index=True)
    pin = Column(String)

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

# Automatyczne utworzenie tabel
Base.metadata.create_all(bind=engine)
os.makedirs("exports", exist_ok=True)

app = FastAPI(title="V-MAX 10.0 Matrix API")
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

def run_checks(db: Session):
    now = get_now()
    stale_logs = db.query(WorkLog).filter(WorkLog.end_time == None).all()
    for log in stale_logs:
        if (now - log.start_time).total_seconds() > 15 * 3600:
            log.end_time = log.start_time + timedelta(hours=15)
            log.is_autoclosed = 1
    db.commit()

# --- API KONFIGURACJI ---
@app.get("/api/config")
def get_config(db: Session = Depends(get_db)):
    run_checks(db)
    admins = {u.name: u.pin for u in db.query(User).filter(User.role == "ADMIN").all()}
    employees = [u.name for u in db.query(User).filter(User.role == "EMPLOYEE").all()]
    activities = [a.name for a in db.query(Activity).all()]
    scanners = [s.name for s in db.query(Scanner).all()]
    trolleys = [t.name for t in db.query(Trolley).all()]
    
    # Znajdź używany sprzęt (żeby zablokować na frontendzie)
    active_logs = db.query(WorkLog).filter(WorkLog.end_time == None).all()
    used_scanners = [log.skaner for log in active_logs if log.skaner]
    used_trolleys = [log.wozek for log in active_logs if log.wozek]

    return {
        "admins": admins, "pracownicy": employees, "aktywnosci": activities,
        "skanery": scanners, "wozki": trolleys,
        "zajete_skanery": used_scanners, "zajete_wozki": used_trolleys
    }

# --- PRACOWNIK ---
@app.post("/api/auth/login")
def login(req: dict, db: Session = Depends(get_db)):
    u, p = str(req.get("username", "")).strip(), str(req.get("pin", "")).strip()
    if u == "ADMIN" and p == "admin": return {"name": "ADMIN", "role": "ADMIN"}
    user = db.query(User).filter(User.name == u, User.pin == p).first()
    if not user: raise HTTPException(status_code=401, detail="Błędny PIN lub Hasło")
    return {"name": user.name, "role": "USER" if user.role == "EMPLOYEE" else user.role}

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
    
    # Bezpieczeństwo: Upewnij się że skaner nie jest kradziony
    if act_type in ["START", "TASK"]:
        if skaner:
            in_use = db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.skaner == skaner, WorkLog.username != user).first()
            if in_use: raise HTTPException(status_code=400, detail="Skaner zajęty przez kogoś innego!")
        if wozek:
            in_use = db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.wozek == wozek, WorkLog.username != user).first()
            if in_use: raise HTTPException(status_code=400, detail="Wózek zajęty przez kogoś innego!")

    active_log = db.query(WorkLog).filter(WorkLog.username == user, WorkLog.end_time == None).first()
    if active_log:
        active_log.end_time = now
        db.commit()
    if act_type in ["START", "TASK"]:
        db.add(WorkLog(username=user, task_name=task, start_time=now, date_str=date_str, skaner=skaner, wozek=wozek))
        db.commit()
    elif act_type == "STOP":
        db.add(WorkLog(username=user, task_name="Zakończenie pracy", start_time=now, end_time=now, date_str=date_str))
        db.commit()
    return get_user_history({"username": user}, db)

@app.post("/api/user/equipment")
def update_equipment(req: dict, db: Session = Depends(get_db)):
    user, skaner, wozek = str(req.get("username", "")).strip(), str(req.get("skaner", "")).strip(), str(req.get("wozek", "")).strip()
    
    if skaner:
        in_use = db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.skaner == skaner, WorkLog.username != user).first()
        if in_use: raise HTTPException(status_code=400, detail="Skaner w użyciu!")
    if wozek:
        in_use = db.query(WorkLog).filter(WorkLog.end_time == None, WorkLog.wozek == wozek, WorkLog.username != user).first()
        if in_use: raise HTTPException(status_code=400, detail="Wózek w użyciu!")

    active_log = db.query(WorkLog).filter(WorkLog.username == user, WorkLog.end_time == None).first()
    if active_log:
        active_log.skaner, active_log.wozek = skaner, wozek
        db.commit()
    return get_user_history({"username": user}, db)

@app.post("/api/user/change-pin")
def change_pin(req: dict, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.name == req.get("username")).first()
    if user:
        user.pin = req.get("newPin")
        db.commit()
        return True
    return False

@app.post("/api/user/correct-task")
def correct_task(req: dict, db: Session = Depends(get_db)):
    last_log = db.query(WorkLog).filter(WorkLog.username == req.get("username"), WorkLog.task_name != "Zakończenie pracy").order_by(desc(WorkLog.id)).first()
    if last_log:
        last_log.task_name = req.get("task")
        db.commit()
        return get_user_history({"username": req.get("username")}, db)
    return False

# --- SEKCJA ADMINA: LIVE SESSIONS ---
@app.get("/api/admin/active-sessions")
def get_active_sessions(db: Session = Depends(get_db)):
    run_checks(db)
    active_logs = db.query(WorkLog).filter(WorkLog.end_time == None).all()
    sessions = {}
    for log in active_logs:
        task = log.task_name
        if task not in sessions: sessions[task] = []
        sessions[task].append({
            "user": log.username, "skaner": log.skaner, "wozek": log.wozek, "start": log.start_time.strftime("%H:%M")
        })
    return sessions

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
    actives = db.query(WorkLog).filter(WorkLog.end_time == None).all()
    for active in actives:
        active.end_time = now
        db.add(WorkLog(username=active.username, task_name="Zakończenie pracy", start_time=now, end_time=now, date_str=now.strftime("%Y-%m-%d")))
    db.commit()
    return True

@app.post("/api/admin/live-session/edit")
def edit_live_session(req: dict, db: Session = Depends(get_db)):
    user, new_task, new_sk, new_wz, new_start = req.get("username"), req.get("task"), req.get("skaner"), req.get("wozek"), req.get("start_time")
    active = db.query(WorkLog).filter(WorkLog.username == user, WorkLog.end_time == None).first()
    if active:
        active.task_name = new_task
        active.skaner = new_sk if new_sk else ""
        active.wozek = new_wz if new_wz else ""
        if new_start:
            active.start_time = datetime.strptime(f"{active.date_str} {new_start}", "%Y-%m-%d %H:%M")
        db.commit()
    return True

# --- BAZA DANYCH ADMINA ---
@app.post("/api/admin/db")
def manage_db(req: dict, db: Session = Depends(get_db)):
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

# --- SYSTEM EKSPORTU ---
def create_csv(filename: str, rows: list):
    filepath = os.path.join("exports", filename)
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerows(rows)
    return f"/api/download/{filename}"

@app.post("/api/admin/export-hr")
def export_hr(req: dict, db: Session = Depends(get_db)):
    d1, d2 = req.get("d1"), req.get("d2")
    logs = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2).all()
    stats = {}
    for log in logs:
        if not log.end_time: continue
        u = log.username
        if u not in stats: stats[u] = {"weekday": 0, "weekend": 0}
        mins = calc_mins(log.start_time, log.end_time)
        if log.start_time.weekday() >= 5: stats[u]["weekend"] += mins
        else: stats[u]["weekday"] += mins
    rows = [["Pracownik", "Godziny Pn-Pt", "Godziny Weekend", "Suma Godzin"]]
    for u, v in stats.items(): rows.append([u, format_dur(v["weekday"]), format_dur(v["weekend"]), format_dur(v["weekday"] + v["weekend"])])
    return {"url": create_csv(f"Raport_HR_{d1}_{uuid.uuid4().hex[:4]}.csv", rows)}

@app.post("/api/admin/export-sheets")
def export_sheets(req: dict, db: Session = Depends(get_db)):
    d1, d2, u_filt = req.get("d1"), req.get("d2"), req.get("user")
    query = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2)
    if u_filt and u_filt != "Wszyscy": query = query.filter(WorkLog.username == u_filt)
    logs = query.order_by(WorkLog.start_time).all()
    rows = [["Pracownik", "Zadanie", "Data", "Start", "Koniec", "Czas trwania", "Skaner", "Wozek"]]
    for log in logs:
        mins = calc_mins(log.start_time, log.end_time) if log.end_time else 0
        rows.append([log.username, log.task_name, log.date_str, log.start_time.strftime("%H:%M"), log.end_time.strftime("%H:%M") if log.end_time else "Trwa", format_dur(mins) if log.end_time else "-", log.skaner, log.wozek])
    return {"url": create_csv(f"Raport_Pelny_{d1}_{uuid.uuid4().hex[:4]}.csv", rows)}

@app.post("/api/admin/export-productivity")
def export_prod(req: dict, db: Session = Depends(get_db)):
    d1, d2 = req.get("d1"), req.get("d2")
    logs = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2, WorkLog.task_name == "Pakowanie Paczek").all()
    prod_entries = db.query(Productivity).filter(Productivity.date_str >= d1, Productivity.date_str <= d2).all()
    stats = {}
    for log in logs:
        u = log.username
        if u not in stats: stats[u] = {"mins": 0, "paczki": 0, "produkty": 0}
        stats[u]["mins"] += calc_mins(log.start_time, log.end_time) if log.end_time else 0
    for p in prod_entries:
        u = p.username
        if u not in stats: stats[u] = {"mins": 0, "paczki": 0, "produkty": 0}
        stats[u]["paczki"] += p.paczki
        stats[u]["produkty"] += p.produkty
    rows = [["Pracownik", "Suma Godzin", "Spakowane Paczki", "Spakowane Produkty", "Paczki/h", "Produkty/h"]]
    for u, v in stats.items():
        if v["mins"] == 0 and v["paczki"] == 0: continue
        h = v["mins"] / 60
        pph = round(v["paczki"] / h, 2) if h > 0 else 0
        prh = round(v["produkty"] / h, 2) if h > 0 else 0
        rows.append([u, format_dur(v["mins"]), v["paczki"], v["produkty"], pph, prh])
    return {"url": create_csv(f"Wydajnosc_{d1}_{uuid.uuid4().hex[:4]}.csv", rows)}

@app.post("/api/admin/export-equipment")
def export_eq(req: dict, db: Session = Depends(get_db)):
    d1, d2, s_filt, w_filt = req.get("d1"), req.get("d2"), req.get("skaner", ""), req.get("wozek", "")
    query = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2)
    if s_filt: query = query.filter(WorkLog.skaner == s_filt)
    if w_filt: query = query.filter(WorkLog.wozek == w_filt)
    logs = query.order_by(WorkLog.start_time).all()
    rows = [["Pracownik", "Zadanie", "Data", "Start", "Koniec", "Skaner", "Wozek"]]
    for log in logs:
        if not log.skaner and not log.wozek: continue
        rows.append([log.username, log.task_name, log.date_str, log.start_time.strftime("%H:%M"), log.end_time.strftime("%H:%M") if log.end_time else "Trwa", log.skaner, log.wozek])
    return {"url": create_csv(f"Sprzet_{d1}_{uuid.uuid4().hex[:4]}.csv", rows)}

@app.get("/api/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join("exports", filename)
    if os.path.exists(file_path): return FileResponse(file_path, filename=filename, media_type="text/csv")
    raise HTTPException(status_code=404, detail="Plik nie istnieje.")

@app.get("/api/planning")
def get_planning():
    today = get_now()
    monday = today - timedelta(days=today.weekday())
    weekly_data = []
    for i in range(7):
        weekly_data.append({"date": (monday + timedelta(days=i)).strftime("%d.%m.%Y"), "found": True, "url": "https://docs.google.com/spreadsheets/d/1kP-AIiDTPPwWJurt8AtQNnpuj_HhTciLzNHbToqgYjs/edit"})
    return weekly_data

# --- POZOSTAŁE FUNKCJE ---
@app.post("/api/admin/reports")
def get_admin_reports_list(req: dict, db: Session = Depends(get_db)):
    d1, d2, u_filter = req.get("d1"), req.get("d2"), req.get("user")
    query = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2)
    if u_filter and u_filter != "Wszyscy": query = query.filter(WorkLog.username == u_filter)
    logs = query.order_by(desc(WorkLog.start_time)).all()
    raport = {}
    for log in logs:
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

@app.get("/api/admin/alerts")
def get_alerts(db: Session = Depends(get_db)):
    today = get_now().strftime("%Y-%m-%d")
    logs = db.query(WorkLog).filter(WorkLog.date_str == today).all()
    alerts = []
    auto = set([l.username for l in logs if l.is_autoclosed == 1])
    for u in auto: alerts.append(f"🔴 {u}: System zamknął sesję z powodu braku aktywności (>15h).")
    users = set([l.username for l in logs])
    for u in users:
        u_logs = [l for l in logs if l.username == u]
        break_mins = sum([calc_mins(l.start_time, l.end_time) for l in u_logs if "Przerwa" in l.task_name and l.end_time])
        has_finished = any([l.end_time and l.task_name == "Zakończenie pracy" for l in u_logs])
        if break_mins == 0 and has_finished: alerts.append(f"⚠️ {u}: Zakończył pracę bez użycia przerwy.")
        elif break_mins > 40: alerts.append(f"⏱️ {u}: Przekroczono limit przerwy (trwała {break_mins} min).")
    return alerts

@app.post("/api/admin/productivity")
def get_productivity(req: dict, db: Session = Depends(get_db)):
    d1, d2 = req.get("d1"), req.get("d2")
    logs = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2).all()
    prod_entries = db.query(Productivity).filter(Productivity.date_str >= d1, Productivity.date_str <= d2).all()
    chartData, headcount, workerDetails, packingStats = {}, {}, {}, {}
    for log in logs:
        if log.task_name in ["Rozpoczęcie pracy", "Zakończenie pracy"]: continue
        mins = calc_mins(log.start_time, log.end_time) if log.end_time else 0
        if mins > 0:
            act, u = log.task_name, log.username
            chartData[act] = chartData.get(act, 0) + mins
            if act not in headcount: headcount[act] = []
            if u not in headcount[act]: headcount[act].append(u)
            if u not in workerDetails: workerDetails[u] = {}
            workerDetails[u][act] = workerDetails[u].get(act, 0) + mins
    for p in prod_entries:
        u = p.username
        if u not in packingStats: packingStats[u] = {"paczki": 0, "produkty": 0}
        packingStats[u]["paczki"] += p.paczki
        packingStats[u]["produkty"] += p.produkty
    for act in headcount: headcount[act] = len(headcount[act])
    return {"chartData": chartData, "headcount": headcount, "workerDetails": workerDetails, "packingStats": packingStats}

@app.post("/api/admin/save-productivity")
def save_prod(req: dict, db: Session = Depends(get_db)):
    date_str, updates = req.get("date"), req.get("updates", [])
    for u in updates:
        worker = u.get("worker")
        entry = db.query(Productivity).filter(Productivity.date_str == date_str, Productivity.username == worker).first()
        if not entry:
            entry = Productivity(date_str=date_str, username=worker)
            db.add(entry)
        entry.paczki, entry.produkty, entry.mins = int(u.get("paczki", 0)), int(u.get("produkty", 0)), int(float(u.get("mins", 0)))
    db.commit()
    return True

@app.post("/api/admin/equipment-log")
def eq_log(req: dict, db: Session = Depends(get_db)):
    d1, d2, s_filt, w_filt = req.get("d1"), req.get("d2"), req.get("skaner", ""), req.get("wozek", "")
    query = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2)
    if s_filt: query = query.filter(WorkLog.skaner == s_filt)
    if w_filt: query = query.filter(WorkLog.wozek == w_filt)
    logs = query.order_by(desc(WorkLog.start_time)).all()
    results = []
    for log in logs:
        if not log.skaner and not log.wozek: continue
        results.append({
            "worker": log.username, "task": log.task_name, "data": log.date_str,
            "start": log.start_time.strftime("%H:%M"), "koniec": log.end_time.strftime("%H:%M") if log.end_time else "Trwa",
            "skaner": log.skaner, "wozek": log.wozek
        })
    return results

@app.post("/api/admin/edit-logs")
def get_edit_logs(req: dict, db: Session = Depends(get_db)):
    logs = db.query(WorkLog).filter(WorkLog.username == req.get("username"), WorkLog.date_str == req.get("date")).order_by(desc(WorkLog.id)).all()
    results = []
    for log in logs:
        results.append({
            "id": log.id, "zadanie": log.task_name,
            "start": log.start_time.strftime("%H:%M") if log.start_time else "",
            "koniec": log.end_time.strftime("%H:%M") if log.end_time else ""
        })
    return results

@app.post("/api/admin/update-batch")
def update_batch(req: dict, db: Session = Depends(get_db)):
    date_str, updates = req.get("date"), req.get("updates", [])
    for u in updates:
        log = db.query(WorkLog).filter(WorkLog.id == int(u.get("id"))).first()
        if log:
            log.task_name = u.get("task")
            if u.get("start"): log.start_time = datetime.strptime(f"{date_str} {u.get('start')}", "%Y-%m-%d %H:%M")
            if u.get("end"): log.end_time = datetime.strptime(f"{date_str} {u.get('end')}", "%Y-%m-%d %H:%M")
    db.commit()
    return True

# --- GENERATOR SYMULACJI (MATRIX) ---
@app.get("/api/generate-mock-data", response_class=HTMLResponse)
def generate_mock_data(db: Session = Depends(get_db)):
    try:
        # Usunięcie starych logów totalnie
        db.query(WorkLog).delete()
        db.query(Productivity).delete()
        db.commit()

        employees = [u.name for u in db.query(User).filter(User.role == "EMPLOYEE").all()]
        if not employees: return "<h1>Brak pracowników. Dodaj kogoś w panelu Admina.</h1>"

        all_acts = [a.name for a in db.query(Activity).all()]
        if "Przerwa" not in all_acts:
            db.add(Activity(name="Przerwa"))
            db.commit()
            all_acts.append("Przerwa")
        task_acts = [a for a in all_acts if a not in ["Rozpoczęcie pracy", "Zakończenie pracy", "Przerwa"]]

        # Zapewnij istnienie skanerów i wózków w bazie
        scanners_in_db = [s.name for s in db.query(Scanner).all()]
        trolleys_in_db = [t.name for t in db.query(Trolley).all()]
        
        if len(scanners_in_db) < 50:
            for x in range(1, 100):
                if f"SK-{x:02d}" not in scanners_in_db: db.add(Scanner(name=f"SK-{x:02d}"))
        if len(trolleys_in_db) < 50:
            for x in range(1, 100):
                if f"WZ-{x:02d}" not in trolleys_in_db: db.add(Trolley(name=f"WZ-{x:02d}"))
        db.commit()

        pool_s = [s.name for s in db.query(Scanner).all()]
        pool_t = [t.name for t in db.query(Trolley).all()]

        # Generowanie od 1 stycznia do 30 kwietnia 2026
        start_date = datetime(2026, 1, 1)
        end_date = datetime(2026, 4, 30)
        days = (end_date - start_date).days

        for i in range(days + 1):
            curr_date = start_date + timedelta(days=i)
            if curr_date.weekday() == 6: continue # Niedziela wolna

            # Reset puli sprzętu na ten dzień (unikalne rozdanie)
            random.shuffle(pool_s)
            random.shuffle(pool_t)
            day_s = pool_s.copy()
            day_t = pool_t.copy()

            for emp in employees:
                if random.random() > 0.9: continue # 10% szansy na nieobecność

                # Zmiana 7, 8, 9, 10
                h = random.choice([7, 8, 9, 10])
                m = random.choice([0, 15, 30])
                dur = random.randint(5, 12)

                start_dt = curr_date.replace(hour=h, minute=m)
                end_dt = start_dt + timedelta(hours=dur)
                db.add(WorkLog(username=emp, task_name="Rozpoczęcie pracy", start_time=start_dt, end_time=start_dt, date_str=curr_date.strftime("%Y-%m-%d")))

                num_tasks = random.randint(1, 3)
                break_mins = random.randint(30, 40)
                total_work = int((end_dt - start_dt).total_seconds() / 60) - break_mins

                durs = []
                for _ in range(num_tasks - 1):
                    d = random.randint(30, max(31, total_work // 2))
                    durs.append(d)
                    total_work -= d
                durs.append(total_work)

                curr_dt = start_dt
                br_idx = random.randint(0, num_tasks - 1)
                br_done = False

                for idx, d in enumerate(durs):
                    # Pobieramy UNIKALNY sprzęt z puli na TO konkretne zadanie
                    sk_use = day_s.pop() if day_s else ""
                    wz_use = day_t.pop() if day_t else ""
                    
                    t_end = curr_dt + timedelta(minutes=d)
                    db.add(WorkLog(username=emp, task_name=random.choice(task_acts), start_time=curr_dt, end_time=t_end, date_str=curr_date.strftime("%Y-%m-%d"), skaner=sk_use, wozek=wz_use))
                    curr_dt = t_end

                    if idx == br_idx and not br_done:
                        b_end = curr_dt + timedelta(minutes=break_mins)
                        db.add(WorkLog(username=emp, task_name="Przerwa", start_time=curr_dt, end_time=b_end, date_str=curr_date.strftime("%Y-%m-%d"), skaner=sk_use, wozek=wz_use))
                        curr_dt = b_end
                        br_done = True
                        
                db.add(WorkLog(username=emp, task_name="Zakończenie pracy", start_time=curr_dt, end_time=curr_dt, date_str=curr_date.strftime("%Y-%m-%d")))
        db.commit()
        return "<h1>✅ MATRIX ZAINICJOWANY! Wygenerowano idealne, realistyczne dane od 1 Stycznia do 30 Kwietnia 2026.</h1><p>Możesz wrócić do aplikacji.</p>"
    except Exception as e:
        return f"<h1>❌ Błąd Generatora: {str(e)}</h1>"

@app.get("/")
def serve_frontend():
    return FileResponse("index.html")
