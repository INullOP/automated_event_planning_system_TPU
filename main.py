import re
import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Request, Form, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from passlib.context import CryptContext
from ics import Calendar, Event
import pdfplumber
from bs4 import BeautifulSoup
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="tpu_super_secret_key")
templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# --- БД ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./tpu_v3.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    hashed_password = Column(String)
    history = relationship("Operation", back_populates="owner")

class Operation(Base):
    __tablename__ = "history"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    custom_name = Column(String)  # Название от пользователя
    event_list = Column(JSON)      # Список конкретных мероприятий
    user_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", back_populates="history")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

# --- ПАРСЕРЫ ---

def parse_content(file_bytes, filename, date_str):
    text = ""
    if filename.endswith('.pdf'):
        with pdfplumber.open(file_bytes) as pdf:
            text = "\n".join([p.extract_text() for p in pdf.pages if p.extract_text()])
    elif filename.endswith('.html'):
        soup = BeautifulSoup(file_bytes.read(), 'html.parser')
        text = soup.get_text(separator='\n')
    
    events = []
    pattern = r'(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})\s*(.*)'
    for line in text.split('\n'):
        m = re.search(pattern, line)
        if m:
            s, e, t = m.groups()
            events.append({
                "title": t.strip(),
                "time": f"{s}-{e}",
                "s_iso": f"{date_str}T{s.replace('.', ':')}:00",
                "e_iso": f"{date_str}T{e.replace('.', ':')}:00"
            })
    return events

# --- РОУТЫ ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db), error: str = None):
    user_id = request.session.get("user_id")
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    # Текущая дата для формы
    today = datetime.now().strftime("%Y-%m-%d")
    return templates.TemplateResponse("index.html", {
        "request": request, "user": user, "today": today, "error": error
    })

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not pwd_context.verify(password, user.hashed_password):
        return RedirectResponse("/?error=Неверный логин или пароль", status_code=303)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)

@app.post("/register")
async def register(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        return RedirectResponse("/?error=Пользователь уже существует", status_code=303)
    new_user = User(username=username, hashed_password=pwd_context.hash(password))
    db.add(new_user)
    db.commit()
    return RedirectResponse("/", status_code=303)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

@app.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), event_date: str = Form(...), db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    try:
        events = parse_content(file.file, file.filename, event_date)
        if not events:
            return RedirectResponse("/?error=События не найдены в файле", status_code=303)
        return templates.TemplateResponse("index.html", {
            "request": request, "user": user, "events": events, "today": event_date
        })
    except Exception as e:
        return RedirectResponse(f"/?error=Ошибка парсинга: {str(e)}", status_code=303)

@app.post("/export")
async def export(request: Request, 
                 titles: List[str] = Form(...), 
                 starts: List[str] = Form(...), 
                 ends: List[str] = Form(...), 
                 selected: List[int] = Form(...),
                 schedule_name: str = Form("Мой график"),
                 db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id: return RedirectResponse("/?error=Войдите в систему", status_code=303)
    
    cal = Calendar()
    saved_events = []
    for i in selected:
        start_dt = datetime.fromisoformat(starts[i])
        end_dt = datetime.fromisoformat(ends[i])
        e = Event()
        e.name = titles[i]
        e.begin = start_dt
        e.end = end_dt
        cal.events.add(e)
        saved_events.append({"title": titles[i], "time": starts[i].split('T')[1][:5]})
    
    # Сохраняем в историю
    new_op = Operation(custom_name=schedule_name, event_list=saved_events, user_id=user_id)
    db.add(new_op)
    db.commit()
    
    fname = f"export_{user_id}.ics"
    ics_data = cal.serialize()
    ics_fixed = ics_data.replace("Z", "")
    with open(fname, "w", encoding="utf-8") as f:
        f.writelines(ics_fixed)
    
    return FileResponse(fname, filename=f"{schedule_name}.ics")
