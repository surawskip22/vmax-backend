from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, desc
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pydantic import BaseModel
import os
from datetime import datetime
from typing import Optional, List

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
    role = Column(String, default="EMPLOYEE") # ADMIN lub EMPLOYEE
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
    date_str = Column(String, index=True) # np. "2026-04-30"
    skaner = Column(String, nullable=True)
    wozek = Column(String, nullable=True)

# Automatyczne utworzenie tabel
Base.metadata.create_all(bind=engine)

# 3. KONFIGURACJA SERWERA
app = FastAPI(title="V-MAX 8.0 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 4. SCHEMATY PYDANTIC (Odbieranie danych z HTML)
class LoginRequest(BaseModel):
    username: str
    pin: str

class ActionRequest(BaseModel):
    username: str
    type: str
    task: Optional[str] = None
    skaner: Optional[str] = ""
    wozek: Optional[str] = ""
    month: Optional[str] = ""

# 5. GŁÓWNE ŚCIEŻKI (ENDPOINTY API)
@app.get("/api/config")
def get_config(db: Session = Depends(get_db)):
    # Pobieranie konfiguracji dla ekranu logowania
    admins = {u.name: u.pin for u in db.query(User).filter(User.role == "ADMIN").all()}
    employees = [u.name for u in db.query(User).filter(User.role == "EMPLOYEE").all()]
    activities = [a.name for a in db.query(Activity).all()]
    
    # Dodajemy domyślne aktywności, jeśli baza jest nowa
    if not activities:
        default_acts = ["Rozpoczęcie pracy", "Zakończenie pracy", "Pakowanie Paczek", "Kompletacja"]
        for act in default_acts:
            db.add(Activity(name=act))
        db.commit()
        activities = default_acts

    return {
        "admins": admins,
        "pracownicy": employees,
        "aktywnosci": activities
    }

@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    # Specjalne awaryjne logowanie dla pierwszego uruchomienia
    if req.username == "ADMIN" and req.pin == "admin":
        return {"name": "ADMIN", "role": "ADMIN"}
        
    user = db.query(User).filter(User.name == req.username, User.pin == req.pin).first()
    if not user:
        raise HTTPException(status_code=401, detail="Błędny PIN lub Hasło")
    
    return {"name": user.name, "role": user.role}

@app.post("/api/user/history")
def get_user_history(req: dict, db: Session = Depends(get_db)):
    # Pobiera historię pracy pracownika
    logs = db.query(WorkLog).filter(WorkLog.username == req.get("username")).order_by(desc(WorkLog.id)).limit(50).all()
    
    hist_data = []
    current_task = None
    
    for log in logs:
        start_str = log.start_time.strftime("%H:%M") if log.start_time else ""
        end_str = log.end_time.strftime("%H:%M") if log.end_time else "Trwa..."
        
        # Obliczanie czasu
        czas_str = "-"
        if log.end_time and log.start_time:
            diff = int((log.end_time - log.start_time).total_seconds() / 60)
            czas_str = f"{diff//60}h {diff%60}m"
            
        hist_data.append({
            "data": log.date_str,
            "zadanie": log.task_name,
            "start": start_str,
            "koniec": end_str,
            "czas": czas_str,
            "skaner": log.skaner,
            "wozek": log.wozek
        })
        
        if not log.end_time and not current_task:
            current_task = {"name": log.task_name, "skaner": log.skaner, "wozek": log.wozek}

    return {"hist": hist_data, "currentTask": current_task}

@app.post("/api/user/action")
def user_action(req: ActionRequest, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    date_str = now.strftime("%Y-%m-%d")
    
    # Znajdź niezakończone zadanie
    active_log = db.query(WorkLog).filter(WorkLog.username == req.username, WorkLog.end_time == None).first()
    
    if req.type == "STOP":
        if active_log:
            active_log.end_time = now
            db.commit()
    else: # START lub ZMIANA ZADANIA
        if active_log:
            active_log.end_time = now
            
        new_log = WorkLog(
            username=req.username,
            task_name=req.task,
            start_time=now,
            date_str=date_str,
            skaner=req.skaner,
            wozek=req.wozek
        )
        db.add(new_log)
        db.commit()

    # Zwraca odświeżoną historię
    return get_user_history({"username": req.username}, db)

# Prosty menedżer bazy danych z panelu Admina (Ustawienia)
@app.post("/api/admin/db")
def manage_db(req: dict, db: Session = Depends(get_db)):
    action = req.get("action")
    db_type = req.get("type")
    name = req.get("name")
    val = req.get("val")

    if action == "ADD":
        if db_type == "EMPLOYEE":
            db.add(User(name=name, pin=val, role="EMPLOYEE"))
        elif db_type == "ADMIN":
            db.add(User(name=name, pin=val, role="ADMIN"))
        elif db_type == "ACTIVITY":
            db.add(Activity(name=name))
    elif action == "DELETE":
        if db_type in ["EMPLOYEE", "ADMIN"]:
            db.query(User).filter(User.name == name).delete()
        elif db_type == "ACTIVITY":
            db.query(Activity).filter(Activity.name == name).delete()
            
    db.commit()
    return {"status": "success"}

# 6. SERWOWANIE FRONTENDU (HTML)
@app.get("/")
def serve_frontend():
    return FileResponse("index.html")
