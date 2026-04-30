from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pydantic import BaseModel
import os
import csv
import uuid
from datetime import datetime, timedelta
from typing import Optional, List
import os
import csv
import uuid
import re   # <--- TO DODAJEMY (do czytania dat)
from datetime import datetime, timedelta

# 1. KONFIGURACJA BAZY DANYCH
db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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

class WorkLog(Base):
    __tablename__ = "work_logs"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, index=True)
    task_name = Column(String)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    date_str = Column(String, index=True)
    skaner = Column(String, nullable=True, default="")
    wozek = Column(String, nullable=True, default="")

class Productivity(Base):
    __tablename__ = "productivity"
    id = Column(Integer, primary_key=True, index=True)
    date_str = Column(String, index=True)
    username = Column(String, index=True)
    paczki = Column(Integer, default=0)
    produkty = Column(Integer, default=0)
    mins = Column(Integer, default=0)

# Automatyczne utworzenie tabel (jeśli nie istnieją)
Base.metadata.create_all(bind=engine)

# Tworzymy folder na pliki eksportu
os.makedirs("exports", exist_ok=True)

# 3. KONFIGURACJA SERWERA
app = FastAPI(title="V-MAX 8.0 API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 4. POMOCNICZE FUNKCJE
def calc_mins(start: datetime, end: datetime):
    if not start or not end: return 0
    return int((end - start).total_seconds() / 60)

def format_dur(mins: int):
    if mins < 0: mins = 0
    return f"{mins//60}h {mins%60}m"

# 5. GŁÓWNE ŚCIEŻKI (ENDPOINTY API)
@app.get("/api/config")
def get_config(db: Session = Depends(get_db)):
    admins = {u.name: u.pin for u in db.query(User).filter(User.role == "ADMIN").all()}
    employees = [u.name for u in db.query(User).filter(User.role == "EMPLOYEE").all()]
    activities = [a.name for a in db.query(Activity).all()]
    if not activities:
        default_acts = ["Rozpoczęcie pracy", "Zakończenie pracy", "Pakowanie Paczek", "Kompletacja"]
        for act in default_acts: db.add(Activity(name=act))
        db.commit()
        activities = default_acts
    return {"admins": admins, "pracownicy": employees, "aktywnosci": activities}

@app.post("/api/auth/login")
def login(req: dict, db: Session = Depends(get_db)):
    u = str(req.get("username", "")).strip()
    p = str(req.get("pin", "")).strip()
    
    # Domyślny admin awaryjny
    if u == "ADMIN" and p == "admin": 
        return {"name": "ADMIN", "role": "ADMIN"}
        
    user = db.query(User).filter(User.name == u, User.pin == p).first()
    if not user: 
        raise HTTPException(status_code=401, detail="Błędny PIN lub Hasło")
    
    # TŁUMACZ DLA FRONTENDU: 
    # Baza używa nazwy "EMPLOYEE", ale nasz kod HTML oczekuje nazwy "USER"
    front_role = "USER" if user.role == "EMPLOYEE" else user.role
    
    return {"name": user.name, "role": front_role}

@app.post("/api/user/history")
def get_user_history(req: dict, db: Session = Depends(get_db)):
    user = req.get("username")
    month = req.get("month", "") # format "YYYY-MM"
    logs = db.query(WorkLog).filter(WorkLog.username == user).order_by(desc(WorkLog.id)).limit(100).all()
    hist_data, current_task = [], None
    
    for log in logs:
        if not month or log.date_str.startswith(month):
            start_str = log.start_time.strftime("%H:%M") if log.start_time else ""
            end_str = log.end_time.strftime("%H:%M") if log.end_time else "Trwa..."
            czas_str = "-"
            if log.end_time and log.start_time:
                czas_str = format_dur(calc_mins(log.start_time, log.end_time))
            hist_data.append({"data": log.date_str, "zadanie": log.task_name, "start": start_str, "koniec": end_str, "czas": czas_str, "skaner": log.skaner, "wozek": log.wozek})
        if not log.end_time and not current_task and log.task_name != "Zakończenie pracy":
            current_task = {"name": log.task_name, "skaner": log.skaner, "wozek": log.wozek}
    return {"hist": hist_data, "currentTask": current_task}

@app.post("/api/user/action")
def user_action(req: dict, db: Session = Depends(get_db)):
    user, act_type, task = req.get("username"), req.get("type"), req.get("task")
    skaner, wozek = req.get("skaner", ""), req.get("wozek", "")
    now = datetime.utcnow()
    date_str = now.strftime("%Y-%m-%d")
    
    active_log = db.query(WorkLog).filter(WorkLog.username == user, WorkLog.end_time == None).first()
    
    if active_log:
        active_log.end_time = now
        db.commit()
        
    if act_type in ["START", "TASK"]:
        new_log = WorkLog(username=user, task_name=task, start_time=now, date_str=date_str, skaner=skaner, wozek=wozek)
        db.add(new_log)
        db.commit()
    elif act_type == "STOP":
        new_log = WorkLog(username=user, task_name="Zakończenie pracy", start_time=now, end_time=now, date_str=date_str)
        db.add(new_log)
        db.commit()

    return get_user_history({"username": user}, db)

@app.post("/api/user/equipment")
def update_equipment(req: dict, db: Session = Depends(get_db)):
    active_log = db.query(WorkLog).filter(WorkLog.username == req.get("username"), WorkLog.end_time == None).first()
    if not active_log: return False
    active_log.skaner = req.get("skaner", "")
    active_log.wozek = req.get("wozek", "")
    db.commit()
    return get_user_history({"username": req.get("username")}, db)

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

# --- SEKCJA ADMINA ---
@app.post("/api/admin/reports")
def get_admin_reports(req: dict, db: Session = Depends(get_db)):
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
        entry.paczki = int(u.get("paczki", 0))
        entry.produkty = int(u.get("produkty", 0))
        entry.mins = int(float(u.get("mins", 0)))
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
    date_str = req.get("date")
    updates = req.get("updates", [])
    for u in updates:
        log = db.query(WorkLog).filter(WorkLog.id == int(u.get("id"))).first()
        if log:
            log.task_name = u.get("task")
            if u.get("start"):
                log.start_time = datetime.strptime(f"{date_str} {u.get('start')}", "%Y-%m-%d %H:%M")
            if u.get("end"):
                log.end_time = datetime.strptime(f"{date_str} {u.get('end')}", "%Y-%m-%d %H:%M")
    db.commit()
    return True

@app.post("/api/admin/db")
def manage_db(req: dict, db: Session = Depends(get_db)):
    action, db_type, name, val = req.get("action"), req.get("type"), req.get("name"), req.get("val")
    if action == "ADD":
        if db_type == "EMPLOYEE": db.add(User(name=name, pin=val, role="EMPLOYEE"))
        elif db_type == "ADMIN": db.add(User(name=name, pin=val, role="ADMIN"))
        elif db_type == "ACTIVITY": db.add(Activity(name=name))
    elif action == "DELETE":
        if db_type in ["EMPLOYEE", "ADMIN"]: db.query(User).filter(User.name == name).delete()
        elif db_type == "ACTIVITY": db.query(Activity).filter(Activity.name == name).delete()
    elif action == "EDIT_PIN":
        user = db.query(User).filter(User.name == name, User.role == db_type).first()
        if user: user.pin = val
    db.commit()
    return {"status": "success"}

# --- SYSTEM EKSPORTU PLIKÓW ---
def create_csv(filename: str, rows: list):
    filepath = os.path.join("exports", filename)
    # Zapis z BOM, aby Excel na Windowsie z miejsca rozpoznawał UTF-8 i polskie znaki
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';') # Średnik to domyślny podział kolumn w polskim Excelu
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
    for u, v in stats.items():
        rows.append([u, format_dur(v["weekday"]), format_dur(v["weekend"]), format_dur(v["weekday"] + v["weekend"])])
        
    url = create_csv(f"Raport_HR_{d1}_{uuid.uuid4().hex[:4]}.csv", rows)
    return {"url": url}

@app.post("/api/admin/export-sheets")
def export_sheets(req: dict, db: Session = Depends(get_db)):
    d1, d2, u_filt = req.get("d1"), req.get("d2"), req.get("user")
    query = db.query(WorkLog).filter(WorkLog.date_str >= d1, WorkLog.date_str <= d2)
    if u_filt and u_filt != "Wszyscy": query = query.filter(WorkLog.username == u_filt)
    logs = query.order_by(WorkLog.start_time).all()
    
    rows = [["Pracownik", "Zadanie", "Data", "Start", "Koniec", "Czas trwania", "Skaner", "Wozek"]]
    for log in logs:
        mins = calc_mins(log.start_time, log.end_time) if log.end_time else 0
        rows.append([
            log.username, log.task_name, log.date_str, 
            log.start_time.strftime("%H:%M"), log.end_time.strftime("%H:%M") if log.end_time else "Trwa",
            format_dur(mins) if log.end_time else "-", log.skaner, log.wozek
        ])
    
    url = create_csv(f"Raport_Pelny_{d1}_{uuid.uuid4().hex[:4]}.csv", rows)
    return {"url": url}

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
        
    url = create_csv(f"Wydajnosc_{d1}_{uuid.uuid4().hex[:4]}.csv", rows)
    return {"url": url}

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
        rows.append([
            log.username, log.task_name, log.date_str,
            log.start_time.strftime("%H:%M"), log.end_time.strftime("%H:%M") if log.end_time else "Trwa",
            log.skaner, log.wozek
        ])
        
    url = create_csv(f"Sprzet_{d1}_{uuid.uuid4().hex[:4]}.csv", rows)
    return {"url": url}

@app.get("/api/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join("exports", filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=filename, media_type="text/csv")
    raise HTTPException(status_code=404, detail="Plik nie istnieje.")

@app.get("/api/planning")
def get_planning():
    # Pobiera z Google tylko daty i generuje struktury pod ten specyficzny URL
    PLANNING_SHEET_ID = "1kP-AIiDTPPwWJurt8AtQNnpuj_HhTciLzNHbToqgYjs"
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    weekly_data = []
    
    for i in range(7):
        day = monday + timedelta(days=i)
        date_str = day.strftime("%d.%m.%Y")
        # Ponieważ z zewnątrz bez autoryzacji Google API nie możemy sprawdzić istnienia zakładki, 
        # odsyłamy domyślny link kierujący na plik ogólny (Pracownik sam wybierze zakładkę).
        sheet_url = f"https://docs.google.com/spreadsheets/d/{PLANNING_SHEET_ID}/edit"
        weekly_data.append({"date": date_str, "found": True, "url": sheet_url})
        
    return weekly_data
# --- TAJNY MECHANIZM MIGRACJI DANYCH ---
from fastapi.responses import HTMLResponse

@app.get("/api/secret-migration-123", response_class=HTMLResponse)
def run_migration(db: Session = Depends(get_db)):
    try:
        def parse_dt(d_raw, t_raw):
            if not d_raw or not t_raw or t_raw == "-": return None
            d_match = re.search(r'(\d+)[-./](\d+)[-./](\d+)', str(d_raw))
            if not d_match: return None
            p1, p2, p3 = d_match.groups()
            y, m, d = (p1, p2, p3) if len(p1) == 4 else (p3, p2, p1)
            d_str = f"{y}-{int(m):02d}-{int(d):02d}"
            t_match = re.search(r'(\d+):(\d+)', str(t_raw))
            if not t_match: return None
            h, mnt = t_match.groups()
            return datetime.strptime(f"{d_str} {h}:{mnt}", "%Y-%m-%d %H:%M"), d_str

        def read_csv(filename):
            if not os.path.exists(filename): return []
            with open(filename, 'r', encoding='utf-8-sig') as f:
                content = f.read()
                if not content.strip(): return []
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(content[:1024], delimiters=',;')
                    return list(csv.reader(f, dialect))
                except Exception:
                    f.seek(0)
                    if ';' in content[:1024]:
                        return list(csv.reader(f, delimiter=';'))
                    else:
                        return list(csv.reader(f, delimiter=','))

        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

        db_rows = read_csv('Database.csv')
        for i, row in enumerate(db_rows):
            if i == 0 or not row: continue
            name = row[0].strip() if len(row) > 0 else ""
            pin = row[1].strip() if len(row) > 1 else "1234"
            if name and not db.query(User).filter_by(name=name).first():
                db.add(User(name=name, pin=pin, role="EMPLOYEE"))
            if len(row) > 2 and row[2].strip():
                act_name = row[2].strip()
                if not db.query(Activity).filter_by(name=act_name).first():
                    db.add(Activity(name=act_name))
            if len(row) > 4 and row[4].strip():
                ad_name = row[4].strip()
                ad_pin = row[5].strip() if len(row) > 5 else "admin"
                if not db.query(User).filter_by(name=ad_name).first():
                    db.add(User(name=ad_name, pin=ad_pin, role="ADMIN"))
        db.commit()

        rap_rows = read_csv('Raport.csv')
        for i, row in enumerate(rap_rows):
            if i == 0 or len(row) < 5: continue
            u, task, d_raw, start_raw, end_raw = [x.strip() for x in row[:5]]
            skaner = row[7].strip() if len(row) > 7 else ""
            wozek = row[8].strip() if len(row) > 8 else ""
            parsed = parse_dt(d_raw, start_raw)
            if parsed:
                start_dt, d_str = parsed
                end_dt = None
                if end_raw and end_raw != "-":
                    res_e = parse_dt(d_raw, end_raw)
                    if res_e: end_dt = res_e[0]
                db.add(WorkLog(username=u, task_name=task, start_time=start_dt, end_time=end_dt, date_str=d_str, skaner=skaner, wozek=wozek))
        db.commit()

        prod_rows = read_csv('Produktywnosc.csv')
        for i, row in enumerate(prod_rows):
            if i == 0 or len(row) < 5: continue
            d_raw, u = row[0].strip(), row[1].strip()
            try:
                paczki, produkty = int(row[2] or 0), int(row[3] or 0)
                mins = int(float(row[4] or 0))
                d_match = re.search(r'(\d+)[-./](\d+)[-./](\d+)', str(d_raw))
                if d_match:
                    p1, p2, p3 = d_match.groups()
                    y, m, d = (p1, p2, p3) if len(p1) == 4 else (p3, p2, p1)
                    d_str = f"{y}-{int(m):02d}-{int(d):02d}"
                    db.add(Productivity(date_str=d_str, username=u, paczki=paczki, produkty=produkty, mins=mins))
            except: pass
        db.commit()
        return "<h1>✅ MIGRACJA ZAKONCZONA SUKCESEM!</h1>"
    except Exception as e:
        return f"<h1>❌ BLAD:</h1><p>{str(e)}</p>"

# 6. SERWOWANIE FRONTENDU
@app.get("/")
def serve_frontend():
    return FileResponse("index.html")
