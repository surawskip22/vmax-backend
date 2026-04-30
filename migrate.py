import os
import csv
import re
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

# 1. PODŁĄCZENIE DO BAZY
db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. STRUKTURA TABEL
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

# 3. CZYSZCZENIE BAZY
print("🧹 Usuwanie starych i zepsutych danych...")
Base.metadata.drop_all(bind=engine)
print("🏗️ Tworzenie nowej, czystej struktury SQL...")
Base.metadata.create_all(bind=engine)

db = SessionLocal()

def parse_dt(d_raw, t_raw):
    if not d_raw or not t_raw or t_raw == "-": return None, None
    d_match = re.search(r'(\d+)[-./](\d+)[-./](\d+)', str(d_raw))
    if not d_match: return None, None
    p1, p2, p3 = d_match.groups()
    y, m, d = (p1, p2, p3) if len(p1) == 4 else (p3, p2, p1)
    d_str = f"{y}-{int(m):02d}-{int(d):02d}"
    
    t_match = re.search(r'(\d+):(\d+)', str(t_raw))
    if not t_match: return None, d_str
    h, mnt = t_match.groups()
    return datetime.strptime(f"{d_str} {h}:{mnt}", "%Y-%m-%d %H:%M"), d_str

def read_csv_safe(filename):
    if not os.path.exists(filename): return []
    with open(filename, 'r', encoding='utf-8-sig') as f:
        content = f.read()
        if not content.strip(): return []
        # Wykrywacz separatora (Google sheets bywa kapryśny)
        delimiter = ';' if ';' in content.split('\n')[0] else ','
        f.seek(0)
        return list(csv.reader(f, delimiter=delimiter))

# 4. IMPORTOWANIE DANYCH
print("📦 Import bazy użytkowników, PIN-ów i Adminów...")
for i, row in enumerate(read_csv_safe('Database.csv')):
    if i == 0 or not row: continue
    u_name = row[0].strip() if len(row) > 0 else ""
    pin = row[1].strip() if len(row) > 1 and row[1].strip() else "1234"
    act = row[2].strip() if len(row) > 2 else ""
    admin_name = row[4].strip() if len(row) > 4 else ""
    admin_pin = row[5].strip() if len(row) > 5 and row[5].strip() else "admin"

    if u_name and not db.query(User).filter_by(name=u_name).first():
        db.add(User(name=u_name, pin=pin, role="EMPLOYEE"))
    if act and not db.query(Activity).filter_by(name=act).first():
        db.add(Activity(name=act))
    if admin_name and not db.query(User).filter_by(name=admin_name).first():
        db.add(User(name=admin_name, pin=admin_pin, role="ADMIN"))
db.commit()

print("🕰️ Wciąganie Historii Pracy do SQL...")
for i, row in enumerate(read_csv_safe('Raport.csv')):
    if i == 0 or len(row) < 5: continue
    u, task, d_raw, start_raw, end_raw = [x.strip() for x in row[:5]]
    skaner = row[7].strip() if len(row) > 7 else ""
    wozek = row[8].strip() if len(row) > 8 else ""
    
    # Czyszczenie śmieci z Google Sheets!
    if skaner == u: skaner = ""
    if wozek == task: wozek = ""
    
    if u and not db.query(User).filter_by(name=u).first():
        db.add(User(name=u, pin="1234", role="EMPLOYEE"))
        db.commit()
    if task and task not in ["Rozpoczęcie pracy", "Zakończenie pracy"] and not db.query(Activity).filter_by(name=task).first():
        db.add(Activity(name=task))
        db.commit()
        
    start_dt, d_str = parse_dt(d_raw, start_raw)
    if start_dt:
        end_dt, _ = parse_dt(d_raw, end_raw) if end_raw and end_raw != "-" else (None, None)
        db.add(WorkLog(username=u, task_name=task, start_time=start_dt, end_time=end_dt, date_str=d_str, skaner=skaner, wozek=wozek))
db.commit()

print("🛒 Import Produktywności z konwersją formatów...")
for i, row in enumerate(read_csv_safe('Produktywnosc.csv')):
    if i == 0 or len(row) < 5: continue
    d_raw, u = row[0].strip(), row[1].strip()
    try:
        paczki, produkty, mins = int(float(row[2] or 0)), int(float(row[3] or 0)), int(float(row[4] or 0))
        d_match = re.search(r'(\d+)[-./](\d+)[-./](\d+)', str(d_raw))
        if d_match:
            p1, p2, p3 = d_match.groups()
            y, m, d = (p1, p2, p3) if len(p1) == 4 else (p3, p2, p1)
            d_str = f"{y}-{int(m):02d}-{int(d):02d}"
            db.add(Productivity(date_str=d_str, username=u, paczki=paczki, produkty=produkty, mins=mins))
    except Exception: pass
db.commit()

print("✅ MIGRACJA ZAKOŃCZONA SUKCESEM! ODPALAJ SZAMPANA! 🍾")
