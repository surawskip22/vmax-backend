from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import os
from datetime import datetime
from pydantic import BaseModel

# 1. KONFIGURACJA BAZY DANYCH (PostgreSQL)
# SQLAlchemy wymaga prefiksu 'postgresql://', a Render często podaje 'postgres://', stąd ta podmiana
db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. DEFINICJA TABEL W BAZIE SQL
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    pin = Column(String, unique=True, index=True)
    qr_code = Column(String, unique=True, index=True, nullable=True)

class WorkLog(Base):
    __tablename__ = "work_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    task_name = Column(String)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    skaner_no = Column(String, nullable=True)

# Automatyczne utworzenie tabel, jeśli nie istnieją
Base.metadata.create_all(bind=engine)

# 3. KONFIGURACJA SERWERA FastAPI
app = FastAPI(title="V-MAX 8.0 ERP System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Zależność bazy danych dla ścieżek
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 4. SCHEMATY DANYCH (Pydantic - do walidacji danych ze skanera)
class ActionStart(BaseModel):
    pin: str
    task_name: str
    skaner_no: str

# 5. ŚCIEŻKI (ENDPOINTY) - Tu skanery wysyłają dane
@app.get("/")
def read_root():
    return {"status": "V-MAX 8.0 BAZA SQL JEST PODŁĄCZONA I DZIAŁA! 🚀"}

@app.post("/api/start-task")
def start_task(action: ActionStart, db: Session = Depends(get_db)):
    # Najpierw sprawdzamy, czy PIN istnieje
    user = db.query(User).filter(User.pin == action.pin).first()
    if not user:
        raise HTTPException(status_code=404, detail="Błędny PIN pracownika!")
    
    # Zapis do SQL (0.01 sekundy!)
    new_log = WorkLog(
        user_id=user.id,
        task_name=action.task_name,
        skaner_no=action.skaner_no
    )
    db.add(new_log)
    db.commit()
    db.refresh(new_log)
    
    # Tutaj w przyszłości dodamy wysyłanie do Google Sheets "w tle"
    
    return {"success": True, "message": f"Zapisano start: {action.task_name}", "user": user.name}

# Dodaj ten schemat w sekcji 4 (tam gdzie jest ActionStart)
class UserCreate(BaseModel):
    name: str
    pin: str

# Dodaj ten endpoint w sekcji 5 (na samym końcu pliku)
@app.post("/api/add-user")
def add_user(user: UserCreate, db: Session = Depends(get_db)):
    # Sprawdzamy czy PIN jest już zajęty
    db_user = db.query(User).filter(User.pin == user.pin).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Ten PIN jest już w bazie!")
    
    # Tworzymy nowego pracownika
    new_user = User(name=user.name, pin=user.pin)
    db.add(new_user)
    db.commit()
    return {"success": True, "message": f"Dodano pracownika: {user.name}"}
