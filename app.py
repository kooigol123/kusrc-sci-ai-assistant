from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, session as flask_session
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from datetime import datetime, timedelta as _td
from PyPDF2 import PdfReader
from urllib.parse import urlencode
import threading
import time
import os
import re
import json
import secrets
import hmac
import requests
import traceback
from bs4 import BeautifulSoup

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

try:
    from scraper import get_kusrc_data, scrape_calendar
except Exception as e:
    print("SCRAPER ERROR:", traceback.format_exc())
    get_kusrc_data = None
    scrape_calendar = None

try:
    from prompt import prompt
except Exception as e:
    print("PROMPT ERROR:", traceback.format_exc())
    prompt = None

# ──────────────────────────────────────────
# App & DB
# ──────────────────────────────────────────
app = Flask(__name__)

_DB_URL = os.getenv("DATABASE_URL")
if not _DB_URL:
    _BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    _DB_URL = f"sqlite:///{os.path.join(_BASE_DIR, 'science_assistant.db')}"
if _DB_URL.startswith("postgres://"):
    _DB_URL = _DB_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = _DB_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config["PERMANENT_SESSION_LIFETIME"] = _td(hours=12)
db = SQLAlchemy(app)

# Admin credentials (ตั้งผ่าน env เท่านั้น — ไม่มี default เพื่อกันรหัสหลุดใน repo สาธารณะ)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# Gemini
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
GEMINI_API_KEY2 = os.getenv("GEMINI_API_KEY2")
GEMINI_API_KEY3 = os.getenv("GEMINI_API_KEY3")

# LINE Login
LINE_CLIENT_ID     = os.getenv("LINE_CLIENT_ID") or os.getenv("LINE_CHANNEL_ID")
LINE_CLIENT_SECRET = os.getenv("LINE_CLIENT_SECRET") or os.getenv("LINE_CHANNEL_SECRET")
LINE_REDIRECT_URI  = os.getenv("LINE_REDIRECT_URI")

# ──────────────────────────────────────────
# Decorators (ต้องนิยามก่อน routes ทุกตัว)
# ──────────────────────────────────────────
def login_required(f):
    """บังคับ login ด้วย LINE ก่อนเข้าใช้งาน"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not flask_session.get("line_user_id"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    """บังคับ login admin ก่อนเข้า admin panel"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not flask_session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

# ──────────────────────────────────────────
# DB Models
# ──────────────────────────────────────────
class ScrapedPage(db.Model):
    __tablename__ = "scraped_pages"
    id           = db.Column(db.Integer, primary_key=True)
    url          = db.Column(db.String(500), unique=True, nullable=False)
    category     = db.Column(db.String(100))
    content      = db.Column(db.Text, nullable=False)
    last_scraped = db.Column(db.DateTime, default=datetime.utcnow)

class ScrapeLog(db.Model):
    __tablename__ = "scrape_logs"
    id          = db.Column(db.Integer, primary_key=True)
    started_at  = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)
    pages       = db.Column(db.Integer, default=0)
    status      = db.Column(db.String(50), default="running")
    trigger     = db.Column(db.String(50), default="auto")

class ChatLog(db.Model):
    __tablename__ = "chat_log"
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, nullable=True)
    user_message = db.Column(db.Text, nullable=False)
    bot_answer   = db.Column(db.Text, nullable=False)
    timestamp    = db.Column(db.DateTime, default=datetime.utcnow)
    session_id   = db.Column(db.String(100), nullable=True)
    feedback     = db.Column(db.Integer, nullable=True)

class LineUser(db.Model):
    __tablename__ = "line_users"
    id            = db.Column(db.Integer, primary_key=True)
    line_user_id  = db.Column(db.String(100), unique=True, nullable=False)
    display_name  = db.Column(db.String(100))
    picture_url   = db.Column(db.String(300))
    first_login   = db.Column(db.DateTime, default=datetime.utcnow)
    last_login    = db.Column(db.DateTime, default=datetime.utcnow)
    login_count   = db.Column(db.Integer, default=1)

class FAQ(db.Model):
    __tablename__ = "faq"
    id         = db.Column(db.Integer, primary_key=True)
    question   = db.Column(db.Text, nullable=False)
    answer     = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ──────────────────────────────────────────
# In-memory context
# ──────────────────────────────────────────
CHAT_CONTEXT = []
FAQ_CONTEXT = []

_FAQ_EXCEL_PATH = os.path.join(os.path.dirname(__file__), "FAQ_AI_updated.xlsx")

def migrate_faq_from_excel():
    """โหลด FAQ จาก Excel เข้า DB ครั้งแรก (ถ้า DB ว่าง)"""
    try:
        if FAQ.query.count() > 0:
            return
        import openpyxl
        wb = openpyxl.load_workbook(_FAQ_EXCEL_PATH)
        ws = wb.active
        count = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            q = str(row[1]).strip() if row[1] else ""
            a = str(row[2]).strip() if row[2] else ""
            if q and a:
                db.session.add(FAQ(question=q, answer=a))
                count += 1
        db.session.commit()
        print(f"[FAQ] Migrated {count} entries from Excel to DB")
    except Exception as e:
        print(f"[FAQ] Migration failed: {e}")

def load_faq_from_db():
    global FAQ_CONTEXT
    try:
        entries = [{"q": f.question, "a": f.answer} for f in FAQ.query.all()]
        FAQ_CONTEXT = entries
        print(f"[FAQ] Loaded {len(FAQ_CONTEXT)} entries from DB")
    except Exception as e:
        print(f"[FAQ] Failed to load from DB: {e}")

def load_faq_from_excel():
    load_faq_from_db()

def load_context_from_db():
    global CHAT_CONTEXT
    with app.app_context():
        rows = ScrapedPage.query.all()
        CHAT_CONTEXT = [{"source": r.url, "category": r.category, "content": r.content} for r in rows]
        print(f"[Context] Loaded {len(CHAT_CONTEXT)} pages from DB")
    # โหลดหลักสูตรต่อท้ายเสมอ กันข้อมูลหายหลัง scrape เสร็จ
    load_curriculum_pdfs()

# ──────────────────────────────────────────
# Scraper
# ──────────────────────────────────────────
_scrape_lock = threading.Lock()

def run_scrape(trigger="auto"):
    if not _scrape_lock.acquire(blocking=False):
        print("[Scraper] Already running, skipping.")
        return
    with app.app_context():
        log = ScrapeLog(trigger=trigger, status="running")
        db.session.add(log)
        db.session.commit()
        log_id = log.id
    try:
        print(f"[Scraper] Starting ({trigger})...")
        # ดึงปฏิทินการศึกษาเข้า DB ก่อนทันที ไม่ต้องรอดึงเว็บครบ 60 หน้า
        if scrape_calendar:
            cal_items = scrape_calendar()
            if cal_items:
                with app.app_context():
                    for item in cal_items:
                        existing = ScrapedPage.query.filter_by(url=item["source"]).first()
                        if existing:
                            existing.content = item["content"]
                            existing.category = item["category"]
                            existing.last_scraped = datetime.utcnow()
                        else:
                            db.session.add(ScrapedPage(url=item["source"], category=item["category"], content=item["content"]))
                    db.session.commit()
                load_context_from_db()
        data = get_kusrc_data()
        with app.app_context():
            current_urls = set()
            for item in data:
                current_urls.add(item["source"])
                existing = ScrapedPage.query.filter_by(url=item["source"]).first()
                if existing:
                    existing.content = item["content"]
                    existing.category = item["category"]
                    existing.last_scraped = datetime.utcnow()
                else:
                    db.session.add(ScrapedPage(url=item["source"], category=item["category"], content=item["content"]))
            db.session.commit()
            # ลบหน้าเก่า/ตาย ที่ไม่เจอในรอบล่าสุด — มี guard กันเคส scrape ล่มแล้วลบ DB เกลี้ยง
            removed = 0
            if len(data) >= 30:
                stale = ScrapedPage.query.filter(~ScrapedPage.url.in_(current_urls)).all()
                removed = len(stale)
                for sp in stale:
                    db.session.delete(sp)
                db.session.commit()
                print(f"[Scraper] Pruned {removed} stale pages.")
            else:
                print(f"[Scraper] Skip prune ({len(data)} pages < 30 — อาจ scrape ไม่ครบ)")
            log = db.session.get(ScrapeLog, log_id)
            log.pages = len(data)
            log.finished_at = datetime.utcnow()
            log.status = "done"
            db.session.commit()
        load_context_from_db()
        print(f"[Scraper] Done — {len(data)} pages saved, {removed} pruned.")
    except Exception as e:
        with app.app_context():
            log = ScrapeLog.query.get(log_id)
            log.status = "error"
            log.finished_at = datetime.utcnow()
            db.session.commit()
        print(f"[Scraper] Error: {e}")
    finally:
        _scrape_lock.release()

def scrape_in_background(trigger="auto"):
    threading.Thread(target=run_scrape, args=(trigger,), daemon=True).start()

# ──────────────────────────────────────────
# Auto-scrape เที่ยงคืนทุกวัน
# ──────────────────────────────────────────
def start_scheduler():
    from datetime import timedelta
    THAI_OFFSET = 7

    def _loop():
        while True:
            now_utc = datetime.utcnow()
            now_thai = now_utc + timedelta(hours=THAI_OFFSET)
            next_midnight_thai = now_thai.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            next_midnight_utc  = next_midnight_thai - timedelta(hours=THAI_OFFSET)
            wait_secs = (next_midnight_utc - datetime.utcnow()).total_seconds()
            thai_time_str = next_midnight_thai.strftime("%d/%m/%Y 00:00")
            print(f"[Scheduler] Next auto-scrape at {thai_time_str} Thai time ({wait_secs/3600:.1f} hours from now)")
            time.sleep(wait_secs)
            scrape_in_background("auto")
            try:
                with app.app_context():
                    from datetime import timedelta as _td
                    cutoff = datetime.utcnow() - _td(days=30)
                    deleted = ChatLog.query.filter(ChatLog.timestamp < cutoff).delete()
                    db.session.commit()
                    if deleted:
                        print(f"[Cleanup] Deleted {deleted} old chat logs")
            except Exception as ce:
                print(f"[Cleanup] Error: {ce}")

    threading.Thread(target=_loop, daemon=True).start()
    print("[Scheduler] Auto-scrape scheduled at 00:00 Thai time (UTC+7) every day")

# ──────────────────────────────────────────
# PDF helpers
# ──────────────────────────────────────────
PDF_DIR = os.path.join(os.path.dirname(__file__), "static", "pdfs")

def get_pdf_category(fn):
    cats = {
        "ทั่วไป":     ["คำร้องทั่วไป","ขอลาพักการศึกษา","ขอรักษาสถานภาพ","ขอคืนสภาพนิสิต","ใบลา","คำสั่งแต่งตั้ง"],
        "ลงทะเบียน":  ["ขอลงทะเบียน","ขอกลับเข้าศึกษา","KU","ลงทะเบียน","Admission"],
        "การเงิน":    ["ขอผ่อนผัน","ขอลาออก","ขอคืน","Refund","Insurance","tuition"],
        "สอบ":        ["ขอสอบชดใช้","แนวปฏิบัติ","make-up","exam"],
        "เอกสาร":     ["รับรอง","Certificate","รายวิชา"],
        "แบบฟอร์ม":   ["format","Form"],
    }
    for cat, kws in cats.items():
        for kw in kws:
            if kw.lower() in fn.lower():
                return cat
    return "เอกสารอื่นๆ"

ALL_PDFS     = sorted([f for f in os.listdir(PDF_DIR) if f.endswith(".pdf")]) if os.path.exists(PDF_DIR) else []
PDF_LIST_STR = "\n".join(f"- {f}" for f in ALL_PDFS)

# ──────────────────────────────────────────
# Gemini helper
# ──────────────────────────────────────────
def clean_response(text):
    if not text: return text
    if "<" in text and ">" in text: return text.strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*",     r"\1", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    emoji_re = re.compile("[" + u"\U0001F600-\U0001F64F" + u"\U0001F300-\U0001F5FF"
        + u"\U0001F680-\U0001F6FF" + u"\U0001F1E0-\U0001F1FF"
        + u"\u2640-\u2642" + u"\u2600-\u2B55" + u"\u200d\u23cf\ufe0f" + "]+", flags=re.UNICODE)
    text = emoji_re.sub("", text)
    return re.sub(r"\n\s*\n\s*\n", "\n\n", text).strip()

def ask_gemini(user_message, history=None):
    # ดักคำทักทายและคำถามเล่นทั่วไปก่อนยิง API เพื่อประหยัดโควต้า
    msg_clean = re.sub(r'[\s\?\!\.\,~…]+$', '', user_message.strip().lower())
    msg_norm = re.sub(r'(.)\1{2,}', r'\1', msg_clean)
    msg_nospace = msg_clean.replace(" ", "")

    _greetings = {
        "สวัสดี", "สวัสดีครับ", "สวัสดีครับผม", "สวัสดีคับ", "สวัสดีค้าบ",
        "สวัสดีค่ะ", "สวัสดีคะ", "สวัสดีค่า", "สวัสดีจ้า", "สวัสดีฮะ",
        "หวัดดี", "หวัดดีครับ", "หวัดดีคับ", "หวัดดีค้าบ", "หวัดดีค่ะ",
        "ดีครับ", "ดีคับ", "ดีค้าบ", "ดีค่ะ", "ดีจ้า", "ทัก", "ทักครับ", "ทักทาย",
        "ฮัลโหล", "ฮโหล", "hello", "hi", "hey", "good morning", "good afternoon",
    }
    if msg_clean in _greetings or msg_norm in _greetings:
        return (
            "สวัสดีครับ มีข้อมูลด้านใดของคณะวิทยาศาสตร์ ศรีราชา ให้ช่วยค้นหาไหมครับ "
            "สามารถสอบถามเรื่องหลักสูตร การลงทะเบียน แบบฟอร์มคำร้อง หรือทุนการศึกษาได้เลยครับ",
            [], [],
        )

    _thanks = {
        "ขอบคุณ", "ขอบคุณครับ", "ขอบคุณคับ", "ขอบคุณค้าบ", "ขอบคุณค่ะ", "ขอบคุณคะ", "ขอบคุณค่า",
        "ขอบคุณมาก", "ขอบคุณมากครับ", "ขอบคุณมากค่ะ", "ขอบใจ", "ขอบใจจ้า",
        "โอเค", "โอเคครับ", "โอเคค่ะ", "รับทราบ", "เข้าใจแล้ว",
        "ok", "okay", "thanks", "thank you", "thx", "ty",
    }
    if msg_clean in _thanks or msg_norm in _thanks:
        return (
            "ยินดีครับ หากมีข้อสงสัยเพิ่มเติมเกี่ยวกับคณะวิทยาศาสตร์ ศรีราชา สอบถามได้ตลอดเลยครับ",
            [], [],
        )

    _model_exact = {
        "ชื่ออะไร", "ชื่อไร", "คุณชื่ออะไร", "เธอชื่ออะไร", "บอทชื่ออะไร",
        "ใครสร้าง", "ใครพัฒนา", "โมเดล", "model",
    }
    _model_phrases = (
        "ใช้โมเดล", "โมเดลอะไร", "โมเดลไร", "โมเดลไหน",
        "ใช้modelอะไร", "ใช้modelไร", "ใช้modelไหน", "ใช้modelตัวไหน",
        "modelอะไร", "modelไร", "modelไหน",
        "ใช้aiอะไร", "ใช้aiไร", "ใช้aiตัวไหน", "ใช้aiไหน", "เป็นaiอะไร", "aiรุ่นไหน",
        "ใช้เอไออะไร", "ใช้เอไอไร", "เป็นเอไออะไร", "llmอะไร",
        "ใช้gpt", "ใช่gpt", "chatgpt", "ใช้gemini", "geminiอะไร", "geminiรุ่น", "ใช้claude",
        "คุณคือใคร", "คุณเป็นใคร", "เธอคือใคร", "เธอเป็นใคร", "นายคือใคร", "แกคือใคร",
        "เป็นบอทอะไร", "ใครสร้างคุณ", "ใครสร้างบอท", "ใครพัฒนาระบบ",
    )
    if msg_clean in _model_exact or (len(msg_clean) <= 60 and any(p in msg_nospace for p in _model_phrases)):
        return (
            "ผมคือ Science Assistant ระบบผู้ช่วยอัจฉริยะของคณะวิทยาศาสตร์ ศรีราชา มหาวิทยาลัยเกษตรศาสตร์ "
            "พัฒนาขึ้นเพื่อช่วยตอบคำถามเกี่ยวกับหลักสูตร การลงทะเบียน เอกสารคำร้อง และข้อมูลภายในคณะครับ "
            "พิมพ์เรื่องที่ต้องการสอบถามได้เลยครับ",
            [], [],
        )

    _playful = {
        "เทส", "เทสๆ", "ทดสอบ", "ทดสอบระบบ", "test", "testing", "ping",
        "อยู่ไหม", "อยู่ป่าว", "มีใครอยู่ไหม", "บอท", "แอดมิน",
        "หิวข้าว", "กินข้าวยัง", "ทำไรอยู่", "ทำอะไรอยู่", "เหงา", "เบื่อ",
        "รักนะ", "น่ารัก", "เก่งจัง", "ฉลาดไหม",
    }
    if (
        len(msg_clean) <= 1
        or msg_clean in _playful
        or msg_norm in _playful
        or re.fullmatch(r'5{2,}\+*|ฮ่าๆ+|haha+|lol|อิอิ', msg_clean)
    ):
        return (
            "ระบบพร้อมใช้งานครับ สามารถพิมพ์คำถามเกี่ยวกับหลักสูตร การลงทะเบียน "
            "เอกสารคำร้อง หรือข้อมูลของคณะวิทยาศาสตร์ ศรีราชา ได้เลยครับ",
            [], [],
        )

    msg_lower = user_message.lower()
    # ตัดคำฟุ่มเฟือยออกก่อนทำ 3-gram กันไม่ให้ทุกหน้าในเว็บติดผลค้นหาหมด
    _stop_words = (
        "คณะวิทยาศาสตร์", "มหาวิทยาลัยเกษตรศาสตร์", "วิทยาเขตศรีราชา",
        "ม.เกษตร", "ศรีราชา", "เกษตร", "ต้องทำยังไง", "ทำยังไง", "ยังไง",
        "อย่างไร", "อะไรบ้าง", "เมื่อไหร่", "เท่าไหร่", "ที่ไหน", "ได้ไหม",
        "คือใคร", "ครับ", "ค่ะ", "คะ", "นะ",
    )
    cleaned_msg = msg_lower
    for sw in _stop_words:
        cleaned_msg = cleaned_msg.replace(sw, " ")
    cleaned_msg = cleaned_msg.strip()
    search_src = cleaned_msg if len(cleaned_msg) >= 2 else msg_lower

    kw = [w for w in search_src.split() if len(w) > 1]
    compact = search_src.replace(" ", "")
    kw += [compact[i:i+3] for i in range(len(compact) - 2)]
    kw = list(dict.fromkeys(kw))

    def _score(text):
        if not kw or not text:
            return 0
        t = text.lower()
        return sum(1 for w in kw if w in t)

    # กรองและเรียงลำดับเฉพาะหน้าที่เกี่ยวข้องมากสุด
    context_str = ""
    if CHAT_CONTEXT:
        scored_docs = []
        for idx, d in enumerate(CHAT_CONTEXT):
            sc = _score(d["content"]) + _score(d.get("category", "")) * 3
            if sc > 0:
                scored_docs.append((sc, idx, d))
        scored_docs.sort(key=lambda x: (-x[0], x[1]))
        filtered = [d for _, _, d in scored_docs]

        # ค่าเทอม / หลักสูตรปกติ-พิเศษ: ดันข้อมูลหลักสูตรขึ้นหน้าสุด
        if any(k in msg_lower for k in ("ค่าเทอม", "ค่าธรรมเนียม", "ค่าเล่าเรียน", "หลักสูตรพิเศษ", "ภาคพิเศษ")):
            prog_docs = [d for d in filtered if "program" in d.get("category", "")]
            if len(prog_docs) < 5:
                for d in CHAT_CONTEXT:
                    if "program" in d.get("category", "") and d not in prog_docs:
                        prog_docs.append(d)
            filtered = prog_docs + [d for d in filtered if d not in prog_docs]

        # ปฏิทินการศึกษา: ถ้าคำถามเกี่ยวกับวัน/สอบ/ลงทะเบียน ให้ดันปฏิทินขึ้นหน้าสุดเสมอ
        _cal_kw = ("สอบ", "ปฏิทิน", "เปิดเทอม", "ปิดเทอม", "เปิดภาค", "ปิดภาค",
                   "ลงทะเบียน", "ถอน", "เพิ่มวิชา", "กำหนดการ", "เทอม", "ภาคเรียน", "วันสุดท้าย")
        if any(k in msg_lower for k in _cal_kw) and "ค่าเทอม" not in msg_lower:
            cal_docs = [d for d in CHAT_CONTEXT if "ปฏิทิน" in d.get("category", "")]
            filtered = cal_docs + [d for d in filtered if d not in cal_docs]
        is_overview = any(p in msg_nospace for p in (
            "มีข้อมูลอะไรบ้าง", "มีข้อมูลด้านไหน", "มีข้อมูลเรื่องอะไร", "มีข้อมูลอะไร",
            "ช่วยอะไรได้บ้าง", "ช่วยเรื่องอะไรได้บ้าง", "ทำอะไรได้บ้าง", "ถามอะไรได้บ้าง",
            "มีบริการอะไรบ้าง", "ค้นหาอะไรได้บ้าง", "รู้เรื่องอะไรบ้าง", "มีอะไรให้ถามบ้าง",
            "ในระบบมีข้อมูลอะไร", "มีข้อมูลเกี่ยวกับอะไร"
        ))
        docs_to_use = [] if is_overview else (filtered[:5] if filtered else CHAT_CONTEXT[:2])
        context_str = "\n\nRelevant Information:\n"
        for doc in docs_to_use:
            limit = 8000 if "ปฏิทิน" in doc.get("category", "") else 1500
            context_str += f"Source: {doc['source']} ({doc['category']})\nContent: {doc['content'][:limit]}\n\n"

    # เพิ่ม FAQ จาก Excel ที่เกี่ยวข้อง
    faq_str = ""
    if FAQ_CONTEXT:
        scored_faq = []
        for idx, e in enumerate(FAQ_CONTEXT):
            sc = _score(e["q"]) * 2 + _score(e["a"])
            if sc > 0:
                scored_faq.append((sc, idx, e))
        scored_faq.sort(key=lambda x: (-x[0], x[1]))
        if scored_faq:
            faq_str = "\n\nข้อมูล FAQ ที่เกี่ยวข้อง:\n"
            for _, _, e in scored_faq[:5]:
                faq_str += f"Q: {e['q']}\nA: {e['a']}\n\n"

    system = f"""{prompt}
{context_str}{faq_str}
---
รายชื่อไฟล์ PDF ที่มีในระบบ (ใช้ชื่อเหล่านี้เท่านั้น):
{PDF_LIST_STR}

ตอบเป็น JSON เท่านั้น:
{{
  "response": "ข้อความตอบกลับ (ห้ามใส่ URL/ลิงค์ในนี้)",
  "files": ["ชื่อไฟล์.pdf"],
  "links": [
    {{"title": "ชื่อที่แสดง", "url": "https://...", "desc": "คำอธิบายสั้น"}}
  ]
}}
- files: ทุกครั้งที่ตอบเรื่องที่เกี่ยวกับเอกสาร/แบบฟอร์ม ต้องใส่ชื่อไฟล์ที่เกี่ยวข้องเสมอ แม้เคยบอกไปแล้วในประวัติการสนทนา
- files: ใช้ชื่อจากรายชื่อข้างต้นเท่านั้น ห้ามแต่งชื่อเอง ถ้าไม่มีที่เกี่ยวข้องให้ใส่ []
- links: ถ้า response มีการอ้างถึง URL ใดๆ ให้แยกมาใส่ใน links แทน ห้ามใส่ URL ใน response
- links ที่ใส่ได้ เช่น MyTCAS, เว็บคณะ, ระบบลงทะเบียน, admission.ku.ac.th ฯลฯ
- ถ้าไม่มีลิงค์ที่เกี่ยวข้องให้ใส่ []
"""
    messages = []
    if history:
        for h in history[-6:]:
            messages.append({"role": h["role"], "parts": [{"text": h["parts"]}]})
    messages.append({"role": "user", "parts": [{"text": user_message}]})

    def _call_gemini(api_key):
        genai.configure(api_key=api_key)
        m = genai.GenerativeModel(model_name="gemini-3.5-flash-lite", system_instruction=system)
        return m.generate_content(messages)

    # ลอง key แรกก่อน ถ้าพังสลับ key 2 และ key 3 อัตโนมัติ
    try:
        raw = _call_gemini(GEMINI_API_KEY)
    except Exception as e1:
        print(f"[Gemini] Key1 failed: {e1}")
        if GEMINI_API_KEY2:
            try:
                print("[Gemini] Switching to Key2...")
                raw = _call_gemini(GEMINI_API_KEY2)
            except Exception as e2:
                print(f"[Gemini] Key2 failed: {e2}")
                if GEMINI_API_KEY3:
                    print("[Gemini] Switching to Key3...")
                    raw = _call_gemini(GEMINI_API_KEY3)
                else:
                    raise e2
        else:
            raise e1
    raw_text = (raw.text or "").strip()
    if not raw_text:
        return "ขออภัยค่ะ ระบบไม่สามารถตอบได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง", [], []
    raw_text = re.sub(r"^```json\s*\n?", "", raw_text, flags=re.MULTILINE)
    raw_text = re.sub(r"\n?```\s*$", "", raw_text, flags=re.MULTILINE).strip()
    parsed = None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        m = re.search(r'\{[\s\S]*\}', raw_text)
        if m:
            try:
                parsed = json.loads(m.group())
            except json.JSONDecodeError:
                pass
    if parsed is None:
        return clean_response(raw_text), [], []
    bot_text   = clean_response(parsed.get("response", ""))
    def _match_pdf(name):
        if name in ALL_PDFS:
            return name
        nl = name.lower()
        for f in ALL_PDFS:
            if nl in f.lower() or f.lower() in nl:
                return f
        return None
    good_files = [m for m in (_match_pdf(f) for f in parsed.get("files", [])) if m]
    links      = parsed.get("links", [])
    good_links = [l for l in links if isinstance(l, dict) and l.get("title") and l.get("url")]

    # เติมลิงก์ระบบสำคัญให้อัตโนมัติ ป้องกันกรณีโมเดลลืมส่ง links ในบางคำตอบ
    existing_urls = {l.get("url") for l in good_links}
    msg_check = (user_message + " " + bot_text).lower()

    if any(k in msg_check for k in ("ตารางเรียน", "ตารางสอน")) and "https://regis.src.ku.ac.th/res/select_std.php" not in existing_urls:
        good_links.append({
            "title": "ตรวจสอบตารางเรียนนิสิต",
            "url": "https://regis.src.ku.ac.th/res/select_std.php",
            "desc": "ตรวจสอบตารางเรียนตามรหัสนิสิตหรือรายวิชา"
        })
        if "https://regis.src.ku.ac.th/res/cs_sectx.php" not in existing_urls:
            good_links.append({
                "title": "ตรวจสอบรายวิชาที่เปิดสอน",
                "url": "https://regis.src.ku.ac.th/res/cs_sectx.php",
                "desc": "ค้นหารายวิชาและหมู่เรียนที่เปิดสอน"
            })

    if any(k in msg_check for k in ("ตารางสอบ", "ห้องสอบ", "วันสอบ", "table_test")) and "https://regis.src.ku.ac.th/res/table_test/?c_level=Bachelor" not in existing_urls:
        good_links.append({
            "title": "ตรวจสอบตารางสอบ",
            "url": "https://regis.src.ku.ac.th/res/table_test/?c_level=Bachelor",
            "desc": "ตรวจสอบตารางสอบและห้องสอบประจำภาคการศึกษา"
        })

    return bot_text, good_files, good_links

# ──────────────────────────────────────────
# Routes — Public (ไม่ต้อง login)
# ──────────────────────────────────────────
@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/login")
def login_page():
    if flask_session.get("line_user_id"):
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/auth/line")
def line_login():
    state = secrets.token_hex(16)
    flask_session["oauth_state"] = state
    params = {
        "response_type": "code",
        "client_id":     LINE_CLIENT_ID,
        "redirect_uri":  LINE_REDIRECT_URI,
        "scope":         "profile openid",
        "state":         state,
    }
    return redirect("https://access.line.me/oauth2/v2.1/authorize?" + urlencode(params))

@app.route("/auth/callback")
@app.route("/auth/line/callback")
def line_callback():
    code  = request.args.get("code")
    state = request.args.get("state")
    saved_state = flask_session.pop("oauth_state", None)
    # เทียบ state เฉพาะตอนมี session เดิม กันพังเวลาสลับ localhost/127.0.0.1 ตอน dev
    if not code or (saved_state and state != saved_state):
        return redirect(url_for("login_page"))

    try:
        token_res = requests.post("https://api.line.me/oauth2/v2.1/token", data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  LINE_REDIRECT_URI,
            "client_id":     LINE_CLIENT_ID,
            "client_secret": LINE_CLIENT_SECRET,
        }, timeout=10)
        access_token = token_res.json().get("access_token") if token_res.ok else None
        if not access_token:
            return redirect(url_for("login_page"))

        prof_res = requests.get(
            "https://api.line.me/v2/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        profile = prof_res.json() if prof_res.ok else {}
    except Exception as e:
        print(f"[LINE Auth] Callback error: {e}")
        return redirect(url_for("login_page"))

    line_uid = profile.get("userId")
    if not line_uid:
        return redirect(url_for("login_page"))

    user = LineUser.query.filter_by(line_user_id=line_uid).first()
    if not user:
        user = LineUser(
            line_user_id = line_uid,
            display_name = profile.get("displayName"),
            picture_url  = profile.get("pictureUrl"),
            login_count  = 1,
        )
        db.session.add(user)
    else:
        user.last_login   = datetime.utcnow()
        user.display_name = profile.get("displayName")
        user.picture_url  = profile.get("pictureUrl")
        user.login_count  = (user.login_count or 1) + 1
    db.session.commit()

    flask_session.permanent            = True
    flask_session["db_user_id"]        = user.id
    flask_session["line_user_id"]      = line_uid
    flask_session["line_display_name"] = profile.get("displayName")
    flask_session["line_picture"]      = profile.get("pictureUrl")
    return redirect(url_for("index"))

@app.route("/auth/line/logout")
def line_logout():
    flask_session.clear()
    return redirect("/login")

@app.route("/api/top_questions")
def api_top_questions():
    """Top 5 คำถามยอดนิยม — public endpoint"""
    default_chips = [
        "มีสาขาอะไรบ้าง",
        "ค่าเทอมเท่าไหร่",
        "ขอเอกสารทำยังไง",
        "การลงทะเบียนเรียน",
        "มีทุนการศึกษาไหม",
    ]
    try:
        from collections import Counter
        import re as _re2
        def normalize_chip(text):
            text = _re2.sub(r'(ครับ|ค่ะ|คะ|นะ|หน่อย|ได้ไหม|ไหม|บ้าง|เลย|นะครับ|นะคะ)\s*$', '', text.strip().lower()).strip()
            if _re2.search(r'(สาขา|หลักสูตร|มีสาขา)', text): return 'มีสาขาอะไรบ้าง'
            if _re2.search(r'(รับสมัคร|สมัครเรียน|tcas)', text): return 'รับสมัครนิสิตใหม่เมื่อไหร่'
            if _re2.search(r'(ทุน|scholarship)', text): return 'มีทุนการศึกษาไหม'
            if _re2.search(r'(ค่าเทอม|ค่าเรียน|tuition)', text): return 'ค่าเทอมเท่าไหร่'
            if _re2.search(r'(ลา|ป่วย|ขาด|leave)', text): return 'ลาป่วยหรือขาดเรียนทำยังไง'
            if _re2.search(r'(เอกสาร|แบบฟอร์ม|คำร้อง)', text): return 'ขอเอกสารทำยังไง'
            if _re2.search(r'(อาจารย์|ดร\.|ผศ\.|คณบดี)', text): return 'ข้อมูลอาจารย์และผู้บริหาร'
            if _re2.search(r'(ลงทะเบียน|เพิ่มวิชา|ถอนวิชา)', text): return 'การลงทะเบียนเรียน'
            if _re2.search(r'(สอบ|ชดเชย)', text): return 'การสอบและขอสอบชดเชย'
            # คืนค่า None ถ้าไม่เข้าหมวดหมู่ กันข้อความดิบของผู้ใช้หลุดออก public API
            return None
        rows = (ChatLog.query.with_entities(ChatLog.user_message)
                .order_by(ChatLog.id.desc()).limit(500).all())
        mapped = [c for r in rows if (c := normalize_chip(r.user_message))]
        top5 = [{"question": q, "count": cnt} for q, cnt in Counter(mapped).most_common(5)]
        seen = {item["question"] for item in top5}
        for q in default_chips:
            if len(top5) >= 5:
                break
            if q not in seen:
                top5.append({"question": q, "count": 0})
        return jsonify({"questions": top5})
    except Exception:
        return jsonify({"questions": [{"question": q, "count": 0} for q in default_chips]})

# ──────────────────────────────────────────
# Routes — ต้อง login LINE
# ──────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("chat.html")

@app.route("/chat", methods=["GET", "POST"])
@login_required
def chat():
    if request.method == "GET":
        return render_template("chat.html")
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return jsonify({"response": "ขออภัยครับ ข้อมูลไม่ถูกต้อง", "suggested_files": []}), 400
    if not GEMINI_API_KEY:
        return jsonify({"response": "ขออภัยครับ ระบบยังไม่พร้อมใช้งาน กรุณาติดต่อผู้ดูแลระบบ", "suggested_files": []})

    session_id = data.get("session_id") or request.headers.get("X-Session-Id", "anonymous")
    db_user_id = flask_session.get("db_user_id")
    if not db_user_id and flask_session.get("line_user_id"):
        u = LineUser.query.filter_by(line_user_id=flask_session["line_user_id"]).first()
        if u:
            db_user_id = u.id
            flask_session["db_user_id"] = u.id

    # จำกัด 10 ข้อความ/นาที ต่อบัญชีผู้ใช้ กันโควต้า Gemini 15 RPM เต็ม
    now = datetime.utcnow()
    one_min_ago = now - _td(minutes=1)
    rate_q = ChatLog.query.filter(ChatLog.timestamp >= one_min_ago)
    if db_user_id:
        rate_q = rate_q.filter(ChatLog.user_id == db_user_id)
    else:
        rate_q = rate_q.filter(ChatLog.session_id == session_id)
    if rate_q.count() >= 10:
        return jsonify({"response": "ขออภัยครับ คุณส่งข้อความถี่เกินไป กรุณารอสักครู่แล้วลองใหม่ครับ", "suggested_files": []})

    # ── ตรวจสอบความยาวข้อความ ──
    user_msg = data["message"].strip()
    if not user_msg:
        return jsonify({"response": "กรุณาพิมพ์คำถามครับ", "suggested_files": []})
    if len(user_msg) > 1000:
        return jsonify({"response": "ขออภัยครับ ข้อความยาวเกินไป กรุณาพิมพ์คำถามให้สั้นลงครับ", "suggested_files": []})

    history = data.get("history", [])
    try:
        bot_text, files, links = ask_gemini(user_msg, history=history)
        print(f"[DEBUG] suggested_files={files} | ALL_PDFS_count={len(ALL_PDFS)}")
        log = None
        try:
            log = ChatLog(
                user_message = user_msg,
                bot_answer   = bot_text,
                session_id   = session_id,
                user_id      = db_user_id,
            )
            db.session.add(log)
            db.session.commit()
        except Exception as db_err:
            print(f"[ChatLog] Save error: {db_err}")
            db.session.rollback()
        return jsonify({"response": bot_text, "suggested_files": files, "suggested_links": links, "log_id": log.id if log else None})
    except Exception as e:
        print(f"[Chat Error] {e}")
        return jsonify({"response": "ขออภัยครับ ระบบขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้งครับ", "suggested_files": []})

@app.route("/downloads")
@login_required
def downloads():
    docs = []
    if os.path.exists(PDF_DIR):
        for i, fn in enumerate(sorted(os.listdir(PDF_DIR)), 1):
            if fn.endswith(".pdf"):
                docs.append({"id": i, "filename": fn, "category": get_pdf_category(fn)})
    cats = {}
    for d in docs:
        cats.setdefault(d["category"], []).append(d)
    return render_template("downloads.html", categories=cats)

@app.route("/download/<int:doc_id>")
@login_required
def download_file(doc_id):
    pdfs = sorted([f for f in os.listdir(PDF_DIR) if f.endswith(".pdf")]) if os.path.exists(PDF_DIR) else []
    if doc_id < 1 or doc_id > len(pdfs):
        return "File not found", 404
    return send_from_directory(PDF_DIR, pdfs[doc_id - 1], as_attachment=True)

@app.route("/api/documents")
@login_required
def api_documents():
    docs = []
    if os.path.exists(PDF_DIR):
        for fn in sorted(os.listdir(PDF_DIR)):
            if fn.endswith(".pdf"):
                docs.append({"id": len(docs)+1, "name": fn, "category": get_pdf_category(fn)})
    return jsonify(docs)

@app.route("/api/feedback", methods=["POST"])
@login_required
def save_feedback():
    data   = request.get_json(silent=True) or {}
    log_id = data.get("log_id")
    score  = data.get("score")
    if not log_id or score not in (1, -1):
        return jsonify({"error": "invalid"}), 400
    log = ChatLog.query.get(log_id)
    if not log:
        return jsonify({"error": "not found"}), 404
    log.feedback = score
    db.session.commit()
    return jsonify({"ok": True})

# ──────────────────────────────────────────
# Admin Routes
# ──────────────────────────────────────────
@app.route("/admin")
@app.route("/admin/login", methods=["GET"])
def admin_login():
    if flask_session.get("admin_logged_in"):
        return redirect(url_for("dashboard"))
    return render_template("admin_login.html")

# ── Rate-limit หน้า login (in-memory; Render ใช้ worker เดียว จึงเพียงพอ) ──
_login_attempts  = {}     # ip -> [timestamps ของครั้งที่ล้มเหลว]
_LOGIN_MAX_TRIES = 5
_LOGIN_WINDOW    = 300    # 5 นาที

def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "unknown")

def _login_blocked(ip):
    now   = time.time()
    tries = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = tries
    return len(tries) >= _LOGIN_MAX_TRIES

@app.route("/admin/login", methods=["POST"])
def admin_auth():
    ip = _client_ip()
    if _login_blocked(ip):
        return render_template("admin_login.html",
            error="พยายามเข้าสู่ระบบบ่อยเกินไป กรุณารอสักครู่แล้วลองใหม่"), 429
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        return render_template("admin_login.html",
            error="ระบบยังไม่ได้ตั้งค่าผู้ดูแล (ตั้ง ADMIN_USERNAME/ADMIN_PASSWORD ใน environment)"), 500
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    ok = (hmac.compare_digest(username.encode("utf-8"), ADMIN_USERNAME.encode("utf-8"))
          and hmac.compare_digest(password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")))
    if ok:
        _login_attempts.pop(ip, None)   # ล้างประวัติเมื่อล็อกอินสำเร็จ
        flask_session["admin_logged_in"] = True
        flask_session.permanent = False
        return redirect(url_for("dashboard"))
    _login_attempts.setdefault(ip, []).append(time.time())
    return render_template("admin_login.html", error="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

@app.route("/admin/logout")
def admin_logout():
    flask_session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))

@app.route("/Dashboard")
@admin_required
def dashboard():
    return render_template("Dashboard.html")

def to_thai_time(dt):
    """แปลง UTC datetime เป็นเวลาไทย UTC+7"""
    if dt is None:
        return "-"
    from datetime import timedelta
    thai = dt + timedelta(hours=7)
    return thai.strftime("%d/%m/%Y %H:%M")

@app.route("/api/admin/stats")
@admin_required
def api_admin_stats():
    try:
        from sqlalchemy import func
        from datetime import timedelta
        from collections import Counter
        thai_now = datetime.utcnow() + timedelta(hours=7)
        thai_today = thai_now.date()
        total_chats  = ChatLog.query.count()
        today_start_utc = datetime(thai_today.year, thai_today.month, thai_today.day) - timedelta(hours=7)
        today_chats  = ChatLog.query.filter(ChatLog.timestamp >= today_start_utc).count()
        daily = []
        for i in range(6, -1, -1):
            d = thai_today - timedelta(days=i)
            start_utc = datetime(d.year, d.month, d.day) - timedelta(hours=7)
            end_utc = start_utc + timedelta(days=1)
            cnt = ChatLog.query.filter(ChatLog.timestamp >= start_utc, ChatLog.timestamp < end_utc).count()
            daily.append({"date": d.strftime("%d/%m"), "count": cnt})

        raw_msgs = [r.user_message.strip().lower() for r in ChatLog.query.with_entities(ChatLog.user_message).all()]
        import re as _re
        def normalize_q(text):
            text = text.strip().lower()
            text = _re.sub(r'(ครับ|ค่ะ|คะ|นะ|หน่อย|ได้ไหม|ไหม|บ้าง|เลย|จ้า|นะครับ|นะคะ)\s*$', '', text).strip()
            if _re.match(r'^(สวัสดี|หวัดดี|ดีครับ|hello|hi|hey)', text): return 'สวัสดี'
            if _re.search(r'(สาขา|หลักสูตร|มีสาขา)', text): return 'มีสาขาอะไรบ้าง'
            if _re.search(r'(รับสมัคร|สมัครเรียน|tcas)', text): return 'รับสมัครนิสิตใหม่เมื่อไหร่'
            if _re.search(r'(ทุน|scholarship)', text): return 'มีทุนการศึกษาไหม'
            if _re.search(r'(ค่าเทอม|ค่าเรียน|tuition)', text): return 'ค่าเทอมเท่าไหร่'
            if _re.search(r'(ลา|ป่วย|ขาด|leave)', text): return 'ลาป่วยหรือขาดเรียนทำยังไง'
            if _re.search(r'(เอกสาร|แบบฟอร์ม|คำร้อง)', text): return 'ขอเอกสารทำยังไง'
            if _re.search(r'(อาจารย์|ดร\.|ผศ\.)', text): return 'ข้อมูลอาจารย์'
            if _re.search(r'(ลงทะเบียน|เพิ่มวิชา|ถอนวิชา)', text): return 'การลงทะเบียนเรียน'
            if _re.search(r'(สอบ|ชดเชย|make-up)', text): return 'การสอบและขอสอบชดเชย'
            return text[:40]

        all_msgs = [r.user_message for r in ChatLog.query.with_entities(ChatLog.user_message).all()]
        top_questions = Counter([normalize_q(m) for m in all_msgs]).most_common(10)
        last_scrape  = ScrapeLog.query.order_by(ScrapeLog.started_at.desc()).first()
        scrape_logs  = ScrapeLog.query.order_by(ScrapeLog.started_at.desc()).limit(5).all()
        total_fb     = ChatLog.query.filter(ChatLog.feedback != None).count()
        positive     = ChatLog.query.filter(ChatLog.feedback == 1).count()
        satisfaction = round(positive / total_fb * 100, 1) if total_fb > 0 else None
        return jsonify({
            "total_chats":       total_chats,
            "today_chats":       today_chats,
            "feedback_positive": positive,
            "scraped_pages":     ScrapedPage.query.count(),
            "total_sessions":    db.session.query(func.count(func.distinct(ChatLog.session_id))).scalar() or 0,
            "feedback_total":    total_fb,
            "satisfaction":      satisfaction,
            "daily_chart":       daily,
            "top_questions":     [{"question": q, "count": c} for q, c in top_questions],
            "last_scrape": {
                "status":      last_scrape.status if last_scrape else "ยังไม่เคย scrape",
                "finished_at": to_thai_time(last_scrape.finished_at) if last_scrape else "-",
                "pages":       last_scrape.pages if last_scrape else 0,
            },
            "scrape_logs": [{
                "id": l.id, "trigger": l.trigger, "status": l.status, "pages": l.pages,
                "started_at":  to_thai_time(l.started_at),
                "finished_at": to_thai_time(l.finished_at),
            } for l in scrape_logs],
        })
    except Exception as e:
        print(f"[Stats Error] {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/chatlogs")
@admin_required
def api_chatlogs():
    page     = request.args.get("page", 1, type=int)
    per_page = 20
    logs = ChatLog.query.order_by(ChatLog.timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify({
        "logs": [{
            "id":           l.id,
            "user_message": l.user_message,
            "bot_answer":   l.bot_answer[:200] + "..." if len(l.bot_answer) > 200 else l.bot_answer,
            "timestamp":    to_thai_time(l.timestamp),
            "session_id":   l.session_id or "-",
        } for l in logs.items],
        "total": logs.total,
        "pages": logs.pages,
        "current_page": page,
    })

@app.route("/api/admin/chatlogs/export")
@admin_required
def export_chatlogs():
    import csv, io
    logs = ChatLog.query.order_by(ChatLog.timestamp.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Timestamp", "Session ID", "User Message", "Bot Answer"])
    for l in logs:
        writer.writerow([l.id, to_thai_time(l.timestamp),
                         l.session_id or "", l.user_message, l.bot_answer])
    output.seek(0)
    from flask import Response
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=chat_logs.csv"}
    )
@app.route("/api/admin/faq", methods=["GET"])
@admin_required
def api_faq_list():
    page     = request.args.get("page", 1, type=int)
    per_page = 20
    search   = request.args.get("q", "").strip()
    query    = FAQ.query
    if search:
        query = query.filter(FAQ.question.ilike(f"%{search}%") | FAQ.answer.ilike(f"%{search}%"))
    paginated = query.order_by(FAQ.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "items": [{"id": f.id, "question": f.question, "answer": f.answer} for f in paginated.items],
        "total": paginated.total,
        "pages": paginated.pages,
        "current": page,
    })

@app.route("/api/admin/faq", methods=["POST"])
@admin_required
def api_faq_create():
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()
    a = (data.get("answer") or "").strip()
    if not q or not a:
        return jsonify({"error": "กรุณาใส่คำถามและคำตอบ"}), 400
    faq = FAQ(question=q, answer=a)
    db.session.add(faq)
    db.session.commit()
    load_faq_from_db()
    return jsonify({"id": faq.id, "question": faq.question, "answer": faq.answer}), 201

@app.route("/api/admin/faq/<int:faq_id>", methods=["PUT"])
@admin_required
def api_faq_update(faq_id):
    faq = FAQ.query.get_or_404(faq_id)
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()
    a = (data.get("answer") or "").strip()
    if not q or not a:
        return jsonify({"error": "กรุณาใส่คำถามและคำตอบ"}), 400
    faq.question = q
    faq.answer   = a
    faq.updated_at = datetime.utcnow()
    db.session.commit()
    load_faq_from_db()
    return jsonify({"id": faq.id, "question": faq.question, "answer": faq.answer})

@app.route("/api/admin/faq/<int:faq_id>", methods=["DELETE"])
@admin_required
def api_faq_delete(faq_id):
    faq = FAQ.query.get_or_404(faq_id)
    db.session.delete(faq)
    db.session.commit()
    load_faq_from_db()
    return jsonify({"ok": True})

@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    """รายชื่อผู้ใช้ที่ login ด้วย LINE"""
    page     = request.args.get("page", 1, type=int)
    per_page = 20
    users = LineUser.query.order_by(LineUser.last_login.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    result = []
    for u in users.items:
        result.append({
            "id":           u.id,
            "line_user_id": u.line_user_id,
            "display_name": u.display_name,
            "picture_url":  u.picture_url,
            "first_login":  to_thai_time(u.first_login),
            "last_login":   to_thai_time(u.last_login),
            "login_count":  u.login_count or 1,
        })
    return jsonify({
        "users":   result,
        "total":   users.total,
        "pages":   users.pages,
        "current": page,
    })

@app.route("/admin/scrape", methods=["POST"])
@admin_required
def admin_scrape():
    scrape_in_background(trigger="manual")
    return jsonify({"message": "เริ่ม scrape ในพื้นหลังแล้วครับ"})

@app.route("/admin/scrape/status")
@admin_required
def scrape_status():
    logs = ScrapeLog.query.order_by(ScrapeLog.started_at.desc()).limit(5).all()
    return jsonify([{
        "id": l.id, "trigger": l.trigger, "status": l.status, "pages": l.pages,
        "started_at":  to_thai_time(l.started_at),
        "finished_at": to_thai_time(l.finished_at),
    } for l in logs])

# ──────────────────────────────────────────
# Startup
# ──────────────────────────────────────────
def initialize_app():
    with app.app_context():
        db.create_all()

        if db.engine.name == 'sqlite':
            try:
                from sqlalchemy import text
                with db.engine.connect() as conn:
                    conn.execute(text("PRAGMA journal_mode=WAL"))
                    conn.execute(text("PRAGMA synchronous=NORMAL"))
                    conn.commit()
                print("[DB] WAL mode enabled for SQLite")
            except Exception as e:
                print(f"[DB] WAL setup error: {e}")

        try:
            from sqlalchemy import text, inspect
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('chat_log')]
            if "feedback" not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE chat_log ADD COLUMN feedback INTEGER"))
                    conn.commit()
                print("[Migration] Added column: feedback")
            else:
                print("[Migration] Schema OK")
        except Exception as e:
            print(f"[Migration] Error: {e}")

        try:
            from sqlalchemy import text, inspect
            inspector = inspect(db.engine)
            lu_columns = [col['name'] for col in inspector.get_columns('line_users')]
            if "login_count" not in lu_columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE line_users ADD COLUMN login_count INTEGER DEFAULT 1"))
                    conn.commit()
                print("[Migration] Added column: login_count")
        except Exception as e:
            print(f"[Migration] login_count Error: {e}")

        print(f"[PDF] Found {len(ALL_PDFS)} files")
        count = ScrapedPage.query.count()
        if count == 0:
            print("[Startup] DB ว่าง → scrape ทันที")
            # โหลด PDF หลักสูตรมารอไว้ก่อนระหว่างรอดึงหน้าเว็บในพื้นหลัง
            load_curriculum_pdfs()
            is_production = os.environ.get("FLASK_ENV") == "production"
            if is_production:
                def delayed_scrape():
                    time.sleep(30)
                    run_scrape("startup")
                threading.Thread(target=delayed_scrape, daemon=True).start()
            else:
                scrape_in_background(trigger="startup")
        else:
            print(f"[Startup] DB มี {count} หน้า → โหลดจาก DB ข้าม scrape")
            load_context_from_db()

        # migrate FAQ จาก Excel → DB (ครั้งแรกเท่านั้น) แล้วโหลดจาก DB
        migrate_faq_from_excel()
        load_faq_from_excel()
    start_scheduler()

def load_curriculum_pdfs():
    """โหลด curriculum จาก .txt (OCR) ใน static/curriculum_text/ และ PDF ที่อ่านได้ใน static/curriculum/"""
    global CHAT_CONTEXT
    chunk_size = 8000
    count = 0

    # โหลดจาก txt ที่ OCR ไว้แล้วก่อน
    txt_dir = os.path.join(os.path.dirname(__file__), "static", "curriculum_text")
    txt_names = set()
    if os.path.exists(txt_dir):
        for fn in sorted(os.listdir(txt_dir)):
            if not fn.endswith(".txt"):
                continue
            path = os.path.join(txt_dir, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                if len(text) < 100:
                    print(f"[Curriculum] SKIP (too short): {fn}")
                    continue
                for i in range(0, len(text), chunk_size):
                    CHAT_CONTEXT.append({
                        "source":   fn,
                        "category": "หลักสูตร",
                        "content":  text[i:i + chunk_size]
                    })
                txt_names.add(fn.replace(".txt", ".pdf"))
                count += 1
                print(f"[Curriculum] Loaded TXT: {fn} ({len(text)} chars)")
            except Exception as e:
                print(f"[Curriculum] Error reading {fn}: {e}")

    # โหลด PDF ที่อ่านได้ปกติ (เฉพาะที่ไม่มี txt แล้ว)
    pdf_dir = os.path.join(os.path.dirname(__file__), "static", "curriculum")
    if os.path.exists(pdf_dir):
        for fn in sorted(os.listdir(pdf_dir)):
            if not fn.endswith(".pdf") or fn in txt_names:
                continue
            path = os.path.join(pdf_dir, fn)
            try:
                reader = PdfReader(path)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() or ""
                if len(text) < 100:
                    print(f"[Curriculum] SKIP (no text): {fn}")
                    continue
                for i in range(0, len(text), chunk_size):
                    CHAT_CONTEXT.append({
                        "source":   fn,
                        "category": "หลักสูตร",
                        "content":  text[i:i + chunk_size]
                    })
                count += 1
                print(f"[Curriculum] Loaded PDF: {fn} ({len(text)} chars)")
            except Exception as e:
                print(f"[Curriculum] Error reading {fn}: {e}")

    print(f"[Curriculum] รวม {count} ไฟล์ถูกโหลดเข้า context")

import os as _os
if _os.environ.get("WERKZEUG_RUN_MAIN") != "true":
    initialize_app()

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 3000))
    debug = _os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)