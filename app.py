from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, session as flask_session, Response
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from datetime import datetime, timedelta as _td
from PyPDF2 import PdfReader
from urllib.parse import urlencode, urlparse, unquote, urldefrag
import threading
import time
import os
import re
import json
import secrets
import hmac
import requests
import traceback
import math
from collections import Counter
from bs4 import BeautifulSoup

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

try:
    from scraper import get_kusrc_data, scrape_calendar, is_spider_trap
except Exception as e:
    print("SCRAPER ERROR:", traceback.format_exc())
    get_kusrc_data = None
    scrape_calendar = None
    is_spider_trap = None

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
    _DEFAULT_DB = os.path.join(_BASE_DIR, 'instance', 'science_assistant.db')
    if not os.path.exists(_DEFAULT_DB):
        _DEFAULT_DB = os.path.join(_BASE_DIR, 'science_assistant.db')
    _DB_URL = f"sqlite:///{_DEFAULT_DB}"
if _DB_URL.startswith("postgres://"):
    _DB_URL = _DB_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = _DB_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "pool_size": 5,
    "max_overflow": 5,
}
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config["PERMANENT_SESSION_LIFETIME"] = _td(minutes=30)  # Session หมดอายุหลังจากไม่ได้ใช้งาน 30 นาที
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Session Cookie Security (ป้องกัน XSS ขโมยคุกกี้, บังคับ HTTPS บน Render, ป้องกัน CSRF)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = bool(os.getenv("RENDER") or os.getenv("DYNO"))
db = SQLAlchemy(app)

if os.getenv("RENDER") or os.getenv("DYNO"):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

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
    """บังคับ login ด้วย LINE หรือเข้าด้วยโหมด Guest ก่อนเข้าใช้งาน"""
    @wraps(f)
    def decorated(*args, **kwargs):
        is_api = request.is_json or request.path.startswith("/api/") or request.path == "/chat"
        if flask_session.get("is_guest"):
            if get_setting("guest_mode", "true") == "true":
                if not flask_session.get("line_picture"):
                    flask_session["line_picture"] = "/static/img/guest_avatar.png"
                return f(*args, **kwargs)
            else:
                flask_session.clear()
                if is_api:
                    return jsonify({"error": "Unauthorized", "redirect": url_for("login_page")}), 401
                return redirect(url_for("login_page"))
        if not flask_session.get("line_user_id"):
            if is_api:
                return jsonify({"error": "Unauthorized", "redirect": url_for("login_page")}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    """บังคับ login admin ก่อนเข้า admin panel"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not flask_session.get("admin_logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
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
    timestamp    = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    session_id   = db.Column(db.String(100), nullable=True)
    feedback     = db.Column(db.Integer, nullable=True)
    is_guest     = db.Column(db.Boolean, default=False, index=True)
    debug_info   = db.Column(db.Text, nullable=True)

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

class DiscoveredLink(db.Model):
    """ลิงก์ภายนอกเครือข่าย มก. ที่ตรวจพบจากการ Scrape หน้าเว็บและ FAQ"""
    __tablename__ = "discovered_links"
    id               = db.Column(db.Integer, primary_key=True)
    url              = db.Column(db.String(500), unique=True, nullable=False, index=True)
    title            = db.Column(db.String(255), nullable=True)
    source_page      = db.Column(db.String(500), nullable=True)
    category         = db.Column(db.String(100), default="ku-network")
    link_type        = db.Column(db.String(50), default="website")
    is_system_mapped = db.Column(db.Boolean, default=False)
    is_ignored       = db.Column(db.Boolean, default=False)
    mapped_at        = db.Column(db.DateTime, nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen        = db.Column(db.DateTime, default=datetime.utcnow)

class SystemSetting(db.Model):
    """การตั้งค่าระบบ เช่น เปิด/ปิดโหมดทดลองใช้งาน"""
    __tablename__ = "system_settings"
    key   = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(255), nullable=False)

# ── In-Memory Caches ──────────────────────
_SETTINGS_CACHE = {}
_IGNORED_URLS_CACHE = None
_STATS_CACHE = {"data": None, "ts": 0}
_STATS_CACHE_TTL = 15  # seconds
_TOP_QUESTIONS_CACHE = {"data": None, "ts": 0}
_DOCUMENTS_CACHE = None

def invalidate_stats_cache():
    global _STATS_CACHE
    _STATS_CACHE = {"data": None, "ts": 0}

def invalidate_links_cache():
    global _IGNORED_URLS_CACHE
    _IGNORED_URLS_CACHE = None
    invalidate_stats_cache()

def get_ignored_urls():
    global _IGNORED_URLS_CACHE
    if _IGNORED_URLS_CACHE is not None:
        return _IGNORED_URLS_CACHE
    try:
        _IGNORED_URLS_CACHE = {l.url.rstrip("/").lower() for l in DiscoveredLink.query.filter_by(is_ignored=True).all()}
    except Exception:
        _IGNORED_URLS_CACHE = set()
    return _IGNORED_URLS_CACHE

def get_setting(key, default="false"):
    global _SETTINGS_CACHE
    if key in _SETTINGS_CACHE:
        return _SETTINGS_CACHE[key]
    try:
        s = SystemSetting.query.filter_by(key=key).first()
        val = s.value if s else default
        _SETTINGS_CACHE[key] = val
        return val
    except Exception:
        return default

def set_setting(key, value):
    global _SETTINGS_CACHE
    try:
        s = SystemSetting.query.filter_by(key=key).first()
        if not s:
            s = SystemSetting(key=key, value=str(value))
            db.session.add(s)
        else:
            s.value = str(value)
        db.session.commit()
        _SETTINGS_CACHE[key] = str(value)
        invalidate_stats_cache()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"[Setting Error] {e}")
        return False

# ──────────────────────────────────────────
# In-memory context
# ──────────────────────────────────────────
CHAT_CONTEXT = []
FAQ_CONTEXT = []
_LAST_FAQ_CHECK_TS = 0.0     # เวลาที่ตรวจสอบฐานข้อมูลล่าสุด
_FAQ_CHECK_INTERVAL = 60.0   # ตรวจสอบทุก 60 วินาทีเพื่อรองรับการแก้ DB โดยตรงบนเครื่องมหาลัย

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

FAQ_IDF = {}
FAQ_TOKEN_DATA = []

def get_faq_ngrams(text, n_range=(3, 4)):
    """สกัดคำและ Character N-Grams (3-4 ตัวอักษร) สำหรับการจับคู่ข้อความภาษาไทย"""
    if not text:
        return []
    cleaned = text.lower().strip()
    words = [w for w in cleaned.split() if len(w) > 1]
    compact = cleaned.replace(" ", "")
    ngrams = []
    L = len(compact)
    for n in range(n_range[0], n_range[1] + 1):
        if L >= n:
            ngrams.extend([compact[i:i+n] for i in range(L - n + 1)])
    return list(dict.fromkeys(words + ngrams))

def load_faq_from_db():
    global FAQ_CONTEXT, FAQ_IDF, FAQ_TOKEN_DATA
    try:
        entries = [{"q": f.question, "a": f.answer} for f in FAQ.query.all()]
        FAQ_CONTEXT = entries
        N = len(FAQ_CONTEXT)
        if N > 0:
            df = Counter()
            token_data = []
            for f in FAQ_CONTEXT:
                q_set = set(get_faq_ngrams(f["q"]))
                a_set = set(get_faq_ngrams(f["a"]))
                all_set = q_set | a_set
                token_data.append((f, q_set, a_set, all_set))
                for g in all_set:
                    df[g] += 1
            FAQ_TOKEN_DATA = token_data
            FAQ_IDF = {
                g: max(0.01, math.log((N - count + 0.5) / (count + 0.5) + 1.0))
                for g, count in df.items()
            }
            print(f"[FAQ] Loaded {N} entries from DB (Dynamic IDF indexed {len(FAQ_IDF)} n-grams)")
        else:
            FAQ_TOKEN_DATA = []
            FAQ_IDF = {}
            print(f"[FAQ] Loaded 0 entries from DB")
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
# System Links Registry (Data-Driven Link System)
# ──────────────────────────────────────────
SYSTEM_LINKS_REGISTRY = [
    {
        "id": "myku",
        "title": "ระบบ MyKU",
        "url": "https://my.ku.th",
        "desc": "ตรวจสอบตารางเรียนและตารางสอบรายบุคคล",
        "keywords": ("ตารางเรียน", "ตารางสอน", "ตารางสอบ", "ห้องสอบ", "myku", "my.ku", "เลขประจำตัวนิสิต", "เลขนิตสิต", "เลขนิสิต", "รหัสนิสิต"),
        "check_target": "user",
    },
    {
        "id": "timetable_std",
        "title": "ตรวจสอบตารางเรียนนิสิต",
        "url": "https://regis.src.ku.ac.th/res/select_std.php",
        "desc": "ตรวจสอบตารางเรียนตามรหัสนิสิตหรือรายวิชา",
        "keywords": ("ตารางเรียน", "ตารางสอน"),
        "check_target": "user",
    },
    {
        "id": "open_courses",
        "title": "ตรวจสอบรายวิชาที่เปิดสอน",
        "url": "https://regis.src.ku.ac.th/res/cs_sectx.php",
        "desc": "ค้นหารายวิชาและหมู่เรียนที่เปิดสอน",
        "keywords": ("วิชาที่เปิดสอน", "รายวิชาที่เปิดสอน", "ค้นหาหมู่เรียน", "เปิดสอน", "หมู่เรียน", "เปิดเซค"),
        "check_target": "user",
    },
    {
        "id": "exam_schedule",
        "title": "ตรวจสอบตารางสอบ",
        "url": "https://regis.src.ku.ac.th/res/table_test/?c_level=Bachelor",
        "desc": "ตรวจสอบตารางสอบและห้องสอบประจำภาคการศึกษา",
        "keywords": ("ตารางสอบ", "ห้องสอบ", "ตารางสอบไล่", "table_test"),
        "check_target": "user",
    },
    {
        "id": "admission_src",
        "title": "ระบบรับสมัครเข้าศึกษา มก.ศรีราชา",
        "url": "https://admissions.src.ku.ac.th/",
        "desc": "ตรวจสอบประกาศระเบียบการและสมัครเข้าศึกษาต่อ",
        "keywords": ("tcas", "รับสมัคร", "portfolio", "รับเข้าศึกษา", "admission", "สมัครเรียน", "พอร์ต", "เกณฑ์รับ", "รอบ 1", "รอบ 2", "รอบ 3", "โควตา", "quota", "เปิดรับ", "อยากเข้า"),
        "check_target": "user",
        "is_admission": True,
    },
    {
        "id": "dorm_src",
        "title": "ระบบจองหอพักนิสิต มก.ศรีราชา",
        "url": "https://dorms.src.ku.ac.th/",
        "desc": "ระบบจองหอพักและตรวจสอบสถานะหอพักนิสิต",
        "keywords": ("หอพัก", "หอใน", "หอนอก", "จองหอ", "ค่าหอ", "ห้องพัก", "รูมเมท"),
        "check_target": "user",
    },
    {
        "id": "academic_calendar",
        "title": "ปฏิทินการศึกษา มก.ศรีราชา",
        "url": "https://regis.src.ku.ac.th/res/calender.php",
        "desc": "กำหนดการเปิด-ปิดภาคเรียน และวันลงทะเบียน",
        "keywords": ("ปฏิทิน", "เปิดเทอม", "ปิดเทอม", "ตารางกิจกรรม", "กำหนดการเรียน", "กำหนดการสอบ"),
        "check_target": "user",
    },
    {
        "id": "verify_docs",
        "title": "ระบบขอเอกสารการศึกษาออนไลน์ (Verify KU@SRC)",
        "url": "https://verify.src.ku.ac.th/",
        "desc": "ยื่นคำร้องขอ Transcript และใบรับรองออนไลน์",
        "keywords": ("transcript", "ทรานสคริป", "หนังสือรับรอง", "verify", "ขอใบรับรอง", "ใบรับรองการศึกษา", "ใบรับรองนิสิต", "ใบรับรองสถานภาพ"),
        "check_target": "user",  # กรองเฉพาะผู้ใช้ถาม ป้องกันบอทพูด 'ใบรับรองแพทย์' แล้วดึงระบบทรานสคริปต์มามั่ว
    },
    {
        "id": "tuition_fees",
        "title": "อัตราค่าธรรมเนียมการศึกษา (ค่าเทอม)",
        "url": "https://regis.src.ku.ac.th/res/fee.php",
        "desc": "ตรวจสอบอัตราค่าธรรมเนียมการศึกษาและค่าธรรมเนียมพิเศษ",
        "keywords": ("ค่าเทอม", "ค่าเล่าเรียน", "ค่าธรรมเนียมการศึกษา", "ค่าใช้จ่ายการเรียน", "tuition"),
        "check_target": "user",
    },
    {
        "id": "scholarship",
        "title": "ทุนการศึกษา คณะวิทยาศาสตร์ ศรีราชา",
        "url": "https://sci.src.ku.ac.th/%e0%b8%97%e0%b8%b8%e0%b8%99%e0%b8%81%e0%b8%b2%e0%b8%a3%e0%b8%a8%e0%b8%b6%e0%b8%81%e0%b8%a9%e0%b8%b2/",
        "desc": "ประกาศรับสมัครทุนการศึกษาและระเบียบการกู้ยืม",
        "keywords": ("ทุน", "ทุนการศึกษา", "scholarship", "กยศ", "กรอ", "กู้ยืม", "ขาดแคลนทุนทรัพย์", "ยากจน"),
        "check_target": "user",
    },
    {
        "id": "kulabs",
        "title": "ระบบจองห้องปฏิบัติการและอุปกรณ์ (KULabs)",
        "url": "https://app.sci.src.ku.ac.th/kulabs/index.php",
        "desc": "ระบบจองห้องแล็บและเครื่องมือวิทยาศาสตร์ คณะวิทยาศาสตร์ ศรีราชา",
        "keywords": ("kulabs", "จองห้องแล็บ", "จองแล็บ", "จองห้องปฏิบัติการ", "เครื่องมือวิทยาศาสตร์"),
        "check_target": "user",
    },
    {
        "id": "document_center",
        "title": "ศูนย์รวมแบบฟอร์มคำร้องทั้งหมด (Document Center)",
        "url": "/downloads",
        "desc": "ดาวน์โหลดแบบฟอร์มคำร้องและเอกสารราชการกว่า 20 รายการ",
        "keywords": ("แบบฟอร์ม", "ใบคำร้อง", "ดาวน์โหลด", "ดาวโหลด", "โหลดไฟล์", "ขอเอกสาร"),
        "check_target": "user",
    },
    # หลักสูตรรายสาขา (จะเปิดให้แสดงเฉพาะเมื่อผู้ใช้ถามเจาะจงหลักสูตร ไม่แสดงตอนถามรับสมัครรวม)
    {
        "id": "curriculum_all",
        "title": "รวมหลักสูตรทั้งหมด คณะวิทยาศาสตร์ ศรีราชา",
        "url": "https://sci.src.ku.ac.th/en/program/",
        "desc": "รายละเอียดหลักสูตรระดับปริญญาตรีและบัณฑิตศึกษาทุกสาขาวิชา",
        "keywords": ("รวมหลักสูตร", "ทุกหลักสูตร", "ทุกสาขา", "มีสาขาอะไรบ้าง", "มีหลักสูตรอะไรบ้าง", "มีกี่สาขา", "เปิดสอนสาขาอะไร", "สาขาอะไรบ้าง", "เปิดสอนอะไรบ้าง", "มีสาขาอะไร", "มีกี่หลักสูตร"),
        "check_target": "user",
        "is_curriculum": True,
    },
    {
        "id": "curriculum_cs",
        "title": "เล่มหลักสูตรวิทยาการคอมพิวเตอร์ (CS)",
        "url": "https://sci.src.ku.ac.th/program/computer-science/",
        "desc": "แผนการเรียน โครงสร้างหลักสูตร และรายวิชาสาขา CS",
        "keywords": ("คอม", "cs", "com sci", "วิทยาการคอมพิวเตอร์", "วิทย์คอม"),
        "check_target": "user",
        "is_curriculum": True,
    },
    {
        "id": "curriculum_dst",
        "title": "เล่มหลักสูตรวิทยาการและเทคโนโลยีดิจิทัล (DST)",
        "url": "https://sci.src.ku.ac.th/program/digital-science-and-technology/",
        "desc": "แผนการเรียนและรายวิชาสาขา DST",
        "keywords": ("dst", "ดิจิทัล", "เทคโนโลยีดิจิทัล"),
        "check_target": "user",
        "is_curriculum": True,
    },
    {
        "id": "curriculum_it",
        "title": "เล่มหลักสูตรเทคโนโลยีสารสนเทศ (IT)",
        "url": "https://sci.src.ku.ac.th/program/information-technology/",
        "desc": "แผนการเรียนและรายวิชาสาขา IT",
        "keywords": (" it ", "ไอที", "เทคโนโลยีสารสนเทศ", "it "),
        "check_target": "user",
        "is_curriculum": True,
    },
    {
        "id": "curriculum_daas",
        "title": "หลักสูตรวิทยาการวิเคราะห์ข้อมูลและคณิตศาสตร์ประกันภัย (DAAS)",
        "url": "https://sci.src.ku.ac.th/department/data-analytics-and-actuarial-science/",
        "desc": "แผนการเรียนและรายวิชาสาขา DAAS",
        "keywords": ("daas", "วิเคราะห์ข้อมูล", "คณิตศาสตร์ประกันภัย", "ประกันภัย", "actuarial"),
        "check_target": "user",
        "is_curriculum": True,
    },
    {
        "id": "curriculum_env",
        "title": "หลักสูตรวิทยาศาสตร์สิ่งแวดล้อม",
        "url": "https://sci.src.ku.ac.th/program/environmental-science/",
        "desc": "แผนการเรียนและรายวิชาสาขาวิทยาศาสตร์สิ่งแวดล้อม",
        "keywords": ("สิ่งแวดล้อม", "สิ่งแวดล้อมทางทะเล", "environment"),
        "check_target": "user",
        "is_curriculum": True,
    },
    {
        "id": "curriculum_chem",
        "title": "หลักสูตรเคมีประยุกต์",
        "url": "https://sci.src.ku.ac.th/department/applied-chemical-science-and-technology/",
        "desc": "แผนการเรียนและรายวิชาสาขาเคมีประยุกต์",
        "keywords": ("เคมี", "chemical", "เคมีประยุกต์"),
        "check_target": "user",
        "is_curriculum": True,
    },
    {
        "id": "curriculum_phys",
        "title": "หลักสูตรฟิสิกส์",
        "url": "https://sci.src.ku.ac.th/department/physics/",
        "desc": "แผนการเรียนและรายวิชาสาขาฟิสิกส์",
        "keywords": ("ฟิสิกส์", "physics"),
        "check_target": "user",
        "is_curriculum": True,
    },
    {
        "id": "curriculum_gened",
        "title": "หมวดวิชาศึกษาทั่วไป พ.ศ. 2567",
        "url": "https://regis.src.ku.ac.th/filet/GenEd67.pdf",
        "desc": "เล่มหลักสูตรหมวดวิชาศึกษาทั่วไป มหาวิทยาลัยเกษตรศาสตร์ (ฉบับปรับปรุง พ.ศ. 2567)",
        "keywords": ("ศึกษาทั่วไป", "gened", "gen-ed", "วิชาศึกษาทั่วไป", "หมวดศึกษาทั่วไป"),
        "check_target": "user",
        "is_curriculum": True,
    },
]

def get_matching_system_links(user_message, bot_text="", history=None, initial_links=None, is_curriculum_query=False, has_pdf=False):
    """
    Data-Driven Link Registry Matcher:
    - ป้องกันการดึงลิงก์เมื่อบอทตอบปฏิเสธ Out-of-Scope (เช่น ร้านอาหาร, คุยเล่น, เขียนโค้ด)
    - ป้องกัน Context Leaking จากประวัติการคุยเก่า โดยดึง history เฉพาะกรณีถามสั้นต่อเนื่อง (Follow-up)
    - ป้องกันคำตอบของบอทดึงลิงก์ผิดหมวด (เช่น 'ใบรับรองแพทย์' ในบอทจะไม่ดึงระบบขอ Transcript)
    - กรอง Criteria.php ออก ให้เหลือเฉพาะระบบรับสมัครหลัก admissions.src.ku.ac.th
    - แก้ไข Substring Collision โดยใช้ Word Boundary สำหรับคีย์เวิร์ดสั้น เช่น cs ไม่ให้ตรงกับ physics
    - จำกัดปุ่มลิงก์ไม่เกิน 1-2 ปุ่ม เพื่อความสบายตา ไม่รกหน้าจอ
    """
    # 0. Out-of-Scope / Refusal Guard: ถ้าบอทตอบปฏิเสธว่าอยู่นอกขอบเขต หรือผู้ใช้ถามเรื่องนอกขอบเขต ให้งดดึงลิงก์ทุกกรณี
    refusal_patterns = (
        "อยู่นอกขอบเขต",
        "นอกเหนือขอบเขต",
        "ไม่สามารถช่วย",
        "ไม่สามารถเขียนโค้ด",
        "ไม่สามารถแนะนำร้าน",
        "ไม่มีข้อมูลเกี่ยวกับ",
    )
    if any(p in bot_text for p in refusal_patterns):
        return []

    user_q = user_message.lower()
    out_of_scope_user_keywords = (
        "หมูกระทะ", "คาเฟ่", "ร้านอาหาร", "ร้านเด็ด", "เขียนโค้ดภาษา c", "ทำการบ้าน", "เขียนโค้ด"
    )
    if any(k in user_q for k in out_of_scope_user_keywords):
        return []

    DEAD_OR_OBSOLETE_PATTERNS = (
        "web.src.ku.ac.th",
        "borrow.asp",
        "survey_vaccine",
        "nisit.ku.ac.th",
        "criteria.php",        # ผู้ใช้สั่งชัดเจน: ตัด Criteria.php ออก ให้เหลือแค่หน้าหลัก admissions.src.ku.ac.th
        "login_admin.php",
        "login.asp",
    )

    ignored_db_urls = get_ignored_urls()

    hist_user_context = ""
    # ใช้ประวัติการสนทนาเฉพาะเมื่อผู้ใช้ถามประโยคสั้นต่อเนื่อง (Follow-up) เท่านั้น ป้องกัน Context Leak ข้ามหัวข้อ
    is_short_followup = len(user_q) < 35 and any(k in user_q for k in ("แล้ว", "ละ", "ล่ะ", "เท่าไหร่", "ยังไง", "ไหม", "หรือเปล่า", "มีไหม", "กี่บาท", "รอบไหน", "เมื่อไหร่", "ที่ไหน"))
    if history and is_short_followup:
        for h in history[-2:]:
            if isinstance(h, dict) and h.get("role") == "user" and isinstance(h.get("parts"), str):
                hist_user_context += " " + h["parts"].lower()

    user_focus = (user_q + " " + hist_user_context).strip()

    # ตรวจสอบเจตนาทุนการศึกษา / ความช่วยเหลือทางการเงิน
    is_scholarship_q = any(k in user_q for k in ("ทุน", "ทุนการศึกษา", "กยศ", "กรอ", "กู้ยืม", "ขาดแคลนทุนทรัพย์", "ยากจน"))

    # ตรวจสอบเจตนาเครื่องแต่งกาย (Dress Code Intent)
    is_dress_code_q = any(k in user_q for k in ("แต่งกาย", "ชุดนิสิต", "เครื่องแต่งกาย", "แต่งตัว", "กางเกง", "กระโปรง", "เนคไท", "เน็คไท", "เสื้อนิสิต"))

    # ตรวจสอบเจตนาการรับสมัคร (Admission Intent)
    admission_keywords = (
        "tcas", "รับสมัคร", "เปิดรับ", "รับเข้า", "เข้าศึกษา", "admission",
        "สมัครเรียน", "พอร์ต", "portfolio", "เกณฑ์รับ", "โควตา", "quota",
        "รอบ 1", "รอบ 2", "รอบ 3", "รอบ 4", "อยากเข้า", "เข้าได้ไหม",
        "ศิลป์-คำนวณ", "ศิลป์คำนวณ", "วิทย์-คณิต", "วิทย์คณิต", "สายศิลป์"
    )
    # ถ้าถามเรื่องทุนหรือเรื่องการแต่งกายอย่างชัดเจน จะไม่ปนลิงก์รับสมัคร เพื่อไม่ให้ปุ่มรกหน้าจอ
    is_admission_q = not is_scholarship_q and not is_dress_code_q and any(k in user_q for k in admission_keywords)
    if not is_admission_q and not is_scholarship_q and not is_dress_code_q and is_short_followup:
        curriculum_intent = any(k in user_q for k in ("เรียนอะไร", "หลักสูตร", "แผนการเรียน", "วิชาที่ต้องเรียน", "ต่างจากบางเขน", "เน้นอะไร"))
        if not curriculum_intent and any(k in hist_user_context for k in ("tcas", "รับสมัคร", "portfolio", "admission", "พอร์ต")):
            is_admission_q = True

    good_links = []
    existing_urls = set()

    # 1. กลั่นกรอง initial_links จากโมเดล Gemini
    if initial_links:
        for l in initial_links:
            if isinstance(l, dict) and l.get("url"):
                url = l["url"].strip()

                # ตัดลิงก์ตาย/เก่า/ไม่ต้องการ หรือถูกซ่อนโดยแอดมินออกทันที
                if any(bad in url.lower() for bad in DEAD_OR_OBSOLETE_PATTERNS) or url.rstrip("/").lower() in ignored_db_urls:
                    continue

                title = l.get("title") or l.get("name") or ""
                # ปรับแก้ลิงก์รับสมัครส่วนกลางให้ชี้มาที่วิทยาเขตศรีราชาโดยตรง
                if "admissions.ku.ac.th" in url or "admission.ku.ac.th" in url:
                    url = "https://admissions.src.ku.ac.th/"
                    if "มหาวิทยาลัยเกษตรศาสตร์" in title and "ศรีราชา" not in title:
                        title = "ระบบรับสมัครเข้าศึกษา มก.ศรีราชา"

                # ถ้าเป็นคำถามรับสมัคร ห้ามนำเล่มหลักสูตรเฉพาะทาง (เช่น CS book) ที่โมเดลส่งมา
                if is_admission_q and any(p in url.lower() for p in ("/course/", "/program/", "/department/")):
                    continue

                # ถ้าเป็นคำถามหลักสูตร/มีสาขาอะไรบ้าง แต่ไม่ได้ถามรับสมัคร ห้ามดึงลิงก์รับสมัครที่โมเดลส่งมา
                if not is_admission_q and any(k in user_q for k in ("มีสาขาอะไร", "สาขาอะไรบ้าง", "มีกี่สาขา", "เปิดสอนสาขา", "มีหลักสูตรอะไร", "หลักสูตรอะไรบ้าง")) and "admissions.src.ku.ac.th" in url:
                    continue

                # ถ้าถามเรื่องทุน ไม่เอาลิงก์รับสมัครที่โมเดลส่งมา
                if is_scholarship_q and "admissions.src.ku.ac.th" in url:
                    continue

                # ถ้าถามเรื่องเครื่องแต่งกาย ไม่เอาลิงก์รับสมัคร และไม่เอาข้อบังคับการศึกษาปริญญาตรีที่โมเดลส่งมา
                if is_dress_code_q and ("admissions.src.ku.ac.th" in url or "ข้อบังคับมหาวิทยาลัยเกษตรศาสตร์-2566.pdf" in url):
                    continue

                if title and url not in existing_urls:
                    good_links.append({"title": title, "url": url, "desc": l.get("desc", "")})
                    existing_urls.add(url)

    # 2. จับคู่กับ Registry ตาม Intent และเป้าหมายการตรวจ (Target)
    for item in SYSTEM_LINKS_REGISTRY:
        url = item["url"]
        if url in existing_urls:
            continue

        # ตัดลิงก์ตาย/เก่า หรือถูกซ่อนโดยแอดมิน
        if any(bad in url.lower() for bad in DEAD_OR_OBSOLETE_PATTERNS) or url.rstrip("/").lower() in ignored_db_urls:
            continue

        # กฎ UX: คำถามรับสมัครทั่วไป ห้ามดึงเล่มหลักสูตรเฉพาะทางมาปน
        if is_admission_q and item.get("is_curriculum"):
            continue

        # ถ้าถามเรื่องทุน หรือเรื่องการแต่งกาย ห้ามดึงลิงก์รับสมัคร
        if (is_scholarship_q or is_dress_code_q) and item.get("is_admission"):
            continue

        # ตรวจสอบว่าต้องมี keyword หลักสูตรก่อนหรือไม่สำหรับเล่มหลักสูตรรายสาขา
        if item.get("is_curriculum"):
            has_curriculum_intent = (
                is_curriculum_query or
                any(k in user_q for k in ("หลักสูตร", "แผนการเรียน", "วิชาที่ต้องเรียน", "เรียนอะไรบ้าง", "program", "curriculum", "เน้นอะไร", "ต่างจาก", "รวมหลักสูตร", "ทุกหลักสูตร", "ทุกสาขา", "มีสาขาอะไรบ้าง", "มีกี่สาขา", "เปิดสอนสาขา", "สาขาอะไรบ้าง", "มีสาขาอะไร", "มีกี่หลักสูตร"))
            )
            if not has_curriculum_intent:
                continue

        target_text = user_focus if item.get("check_target") == "user" else (user_focus + " " + bot_text.lower())
        matched = False
        for k in item["keywords"]:
            if k.strip() in ("cs", "it"):
                # Word boundary check ป้องกัน 'cs' ตรงในคำว่า 'physics'
                if re.search(rf'(?<![a-zA-Z0-9]){re.escape(k.strip())}(?![a-zA-Z0-9])', target_text, flags=re.I):
                    matched = True
                    break
            elif k in target_text:
                matched = True
                break

        if matched:
            good_links.append({
                "title": item["title"],
                "url": url,
                "desc": item.get("desc", "")
            })
            existing_urls.add(url)

    # กฎ UX: จำกัดจำนวนปุ่มเพื่อความสบายตา ไม่รกหน้าจอ
    # ถ้ามีแบบฟอร์ม PDF แนบอยู่แล้ว ให้แสดงลิงก์ที่เกี่ยวข้องมากที่สุดเพียง 1 ปุ่ม (รวมเป็น 2 ปุ่มบนจอ)
    # ถ้าไม่มี PDF ให้แสดงไม่เกิน 2 ปุ่ม เพื่อไม่ให้เยอะเกินไปตามคำสั่งผู้ใช้
    limit = 1 if has_pdf else 2
    return good_links[:limit]

def canonicalize_discovered_url(url: str) -> str:
    """ทำความสะอาดและจัดรูปแบบ URL ให้อยู่ในมาตรฐานเดียวกัน (ตัด fragment #..., ตัด trailing slash, จัด lower domain)"""
    if not url:
        return ""
    url_no_frag = urldefrag(url)[0].strip()
    cleaned = re.sub(r'/[^/]+\.php/(pdf/[^/]+)$', r'/\1', url_no_frag, flags=re.I)
    cleaned = cleaned.rstrip('.,;:)()[]\'"')
    parsed = urlparse(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return cleaned

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path
    if path == "/" or not path:
        path = ""
    elif path.endswith("/"):
        path = path.rstrip("/")

    query = f"?{parsed.query}" if parsed.query else ""
    return f"{scheme}://{netloc}{path}{query}"

def sync_discovered_links_from_db():
    """สแกนและบันทึกรวมลิงก์ภายนอกเครือข่าย มก. (*.ku.ac.th, *.ku.th) จาก ScrapedPage และ FAQ ลง discovered_links"""
    try:
        registered_urls = {item["url"].rstrip("/").lower() for item in SYSTEM_LINKS_REGISTRY}
        known_service_titles = {
            "dorms.src.ku.ac.th": "ระบบจองหอพักนิสิต มก.ศรีราชา",
            "isea.ku.ac.th": "ระบบสารสนเทศนิสิต (ISEA / ขึ้นทะเบียนนิสิตใหม่)",
            "accounts.ku.ac.th": "ระบบจัดการบัญชีผู้ใช้งานเครือข่ายนนทรี (Nonthree Account)",
            "admissions.src.ku.ac.th": "ระบบรับสมัครเข้าศึกษา มก.ศรีราชา",
            "regis.src.ku.ac.th": "สำนักทะเบียนและประมวลผล มก.ศรีราชา",
            "verify.src.ku.ac.th": "ระบบขอเอกสารการศึกษาออนไลน์ (Verify KU@SRC)",
            "my.ku.th": "ระบบสารสนเทศนิสิต MyKU",
            "lib.src.ku.ac.th": "สำนักหอสมุด มก.ศรีราชา",
            "sa.src.ku.ac.th": "กองกิจการนิสิต มก.ศรีราชา",
        }

        labeled_pattern = re.compile(r'-\s*([^:\n]+):\s*(https?://[a-zA-Z0-9.-]*\.ku\.(?:ac\.)?th(?:/[^\s<>\"]*)?)', re.I)
        general_pattern = re.compile(r'https?://[a-zA-Z0-9.-]*\.ku\.(?:ac\.)?th(?:/[^\s<>\"]*)?', re.I)

        discovered = {}

        # 1. จาก ScrapedPage
        pages = ScrapedPage.query.all()
        for p in pages:
            if not p.content:
                continue
            for label, link in labeled_pattern.findall(p.content):
                clean_url = canonicalize_discovered_url(link)
                if is_spider_trap and is_spider_trap(clean_url):
                    continue
                lbl = label.strip()
                if clean_url not in discovered:
                    discovered[clean_url] = {"source": p.url, "label": lbl}
                elif not discovered[clean_url]["label"] and lbl:
                    discovered[clean_url]["label"] = lbl

            for link in general_pattern.findall(p.content):
                clean_url = canonicalize_discovered_url(link)
                if is_spider_trap and is_spider_trap(clean_url):
                    continue
                if clean_url not in discovered:
                    discovered[clean_url] = {"source": p.url, "label": ""}

        # 2. จาก FAQ
        faqs = FAQ.query.all()
        for f in faqs:
            if not f.answer:
                continue
            for link in general_pattern.findall(f.answer):
                clean_url = canonicalize_discovered_url(link)
                if is_spider_trap and is_spider_trap(clean_url):
                    continue
                if clean_url not in discovered:
                    discovered[clean_url] = {"source": f"FAQ: {f.question[:50]}", "label": ""}

        now = datetime.utcnow()
        new_count = 0
        existing_list = DiscoveredLink.query.all()
        existing_map = {}
        for l in existing_list:
            existing_map[l.url] = l
            existing_map[l.url.rstrip("/") + "/"] = l
            existing_map[l.url.rstrip("/")] = l

        for url, data in discovered.items():
            existing = existing_map.get(url)
            if not existing:
                alt_url = url + "/" if not url.endswith("/") else url.rstrip("/")
                existing = existing_map.get(alt_url)
                if existing:
                    existing.url = url
            norm_url = url.rstrip("/").lower()
            is_blocked_from_mapping = any(bad in norm_url for bad in ("login_admin", "criteria", "announce.php", "login_c.php"))
            is_mapped = (not is_blocked_from_mapping) and any(norm_url == r or norm_url.startswith(r) for r in registered_urls)

            title = data.get("label") or ""
            parsed_host = urlparse(url).netloc.lower()
            if not title:
                for host_key, host_title in known_service_titles.items():
                    if host_key in parsed_host:
                        title = host_title
                        break
            if not title:
                title = parsed_host

            is_doc = any(norm_url.endswith(ext) for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx"))
            auto_link_type = "document" if is_doc else "website"

            if existing:
                existing.last_seen = now
                # คงสถานะที่แอดมินเคยผูกหรือซ่อนไว้ ไม่ให้การ sync อัตโนมัติเขียนทับ
                if not getattr(existing, "is_system_mapped", False) and is_mapped and not getattr(existing, "is_ignored", False):
                    existing.is_system_mapped = True
                    if not getattr(existing, "mapped_at", None):
                        existing.mapped_at = now
                if (not existing.title or existing.title == parsed_host) and title:
                    existing.title = title
                if not getattr(existing, "link_type", None):
                    existing.link_type = auto_link_type
            else:
                db.session.add(DiscoveredLink(
                    url=url,
                    title=title,
                    source_page=data["source"],
                    category="ku-network",
                    link_type=auto_link_type,
                    is_system_mapped=is_mapped,
                    mapped_at=now if is_mapped else None,
                    is_ignored=False,
                    created_at=now,
                    last_seen=now
                ))
                new_count += 1

        db.session.commit()
        invalidate_links_cache()
        total_links = len(existing_list) + new_count
        print(f"[DiscoveredLinks] Synced {total_links} links ({new_count} new).")
        return total_links, new_count
    except Exception as e:
        print(f"[DiscoveredLinks] Sync error: {e}")
        return 0, 0

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
        with app.app_context():
            sync_discovered_links_from_db()
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
PDF_DIR = os.path.join(os.path.dirname(__file__), "static", "docs", "forms")

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
    if isinstance(text, str) and text.strip().startswith(("{", "[")):
        resp_m = re.search(r'"(?:response|message)"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if resp_m:
            try:
                text = json.loads(f'"{resp_m.group(1)}"')
            except Exception:
                text = resp_m.group(1)
    if "<" in text and ">" in text: return text.strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*",     r"\1", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    emoji_re = re.compile("[" + u"\U0001F600-\U0001F64F" + u"\U0001F300-\U0001F5FF"
        + u"\U0001F680-\U0001F6FF" + u"\U0001F1E0-\U0001F1FF"
        + u"\u2640-\u2642" + u"\u2600-\u2B55" + u"\u200d\u23cf\ufe0f" + "]+", flags=re.UNICODE)
    text = emoji_re.sub("", text)
    return re.sub(r"\n\s*\n\s*\n", "\n\n", text).strip()



def sanitize_bot_output(bot_text, has_links=False, query_context=""):
    """
    ทำความสะอาดข้อความตอบกลับของ AI (Sanitization Layer):
    - ตัดข้อมูลติดต่อออฟไลน์ที่ไม่จำเป็นสำหรับบริการออนไลน์ (ป้องกันผู้ใช้สับสน)
    - ตัดข้อความชี้นำ UI ที่เวิ่นเว้อหรือหลอน (เช่น 'คลิกลิงก์ด้านล่าง', 'ดูแถบด้านขวา')
    - ปรับสรรพนามการขึ้นต้นประโยคให้เป็นธรรมชาติและไร้สรรพนาม (Subjectless Tone)
    """
    if not bot_text:
        return ""

    # บริการออนไลน์/ตารางเรียน: ตัดข้อความติดต่อตึก 25 ออก เฉพาะกรณีคำถามตรวจสอบตารางเรียน/ตารางสอบออนไลน์ (เพื่อไม่ให้สับสนว่าต้องเดินทางไปคณะ)
    online_only_keywords = ("ตารางเรียน", "ตารางสอน", "ตารางสอบ", "ห้องสอบ", "เลขประจำตัวนิสิต", "เลขนิตสิต", "เลขนิสิต", "รหัสนิสิต")
    if any(k in query_context for k in online_only_keywords):
        bot_text = re.sub(r'\n*\s*ติดต่อ:\s*(?:ฝ่ายการศึกษา|สำนักงานคณะวิทยาศาสตร์).*?(?:โทร|038|663013|อาคาร).*?$', '', bot_text, flags=re.DOTALL).strip()
        bot_text = re.sub(r'\n*\s*ติดต่อฝ่ายการศึกษาวิทยาเขต.*?(?:โทร|038|อาคาร).*?$', '', bot_text, flags=re.DOTALL).strip()

    # ตัดข้อความชี้นำ UI ซ้ำซ้อน (ปุ่ม Action Chips ด้านล่างแสดงอยู่แล้ว)
    ui_patterns = [
        r'[\s,]*(?:ตาม|คลิก|เปิด|เข้า|ดู)?\s*(?:ที่)?\s*(?:ลิงก์|ลิงค์|ปุ่ม)\s*(?:ด้านล่าง|ข้างล่าง|ที่แนบไว้)?(?:นี้)?.*?(?:ได้เลยครับ|ครับ|ค่ะ)?$',
        r'[\s,]*โดย\s*(?:คลิก|กด|เปิด|เข้า|ตาม).*?(?:ลิงก์|ลิงค์|ปุ่ม)\s*(?:ด้านล่าง|ข้างล่าง|ที่แนบไว้).*?(?:ได้เลยครับ|ครับ|ค่ะ)?',
        r'((?:ครับ|ค่ะ|[\.\!\?\n])\s*)(?:(?!ครับ|ค่ะ|[\.\!\?\n])[^\n])*?(?:ตาม|คลิก|กด|ดู|เปิด)?(?:ลิงก์|ลิงค์|ปุ่ม)\s*(?:ด้านล่าง|ข้างล่าง|ที่แนบไว้)[^\n]*?$',
        r'((?:ครับ|ค่ะ|[\.\!\?\n])\s*)(?:(?!ครับ|ค่ะ|[\.\!\?\n])[^\n])*?(?:แถบ(?:ผลการค้นหา|ด้านขวา))[^\n]*?$',
        r'[\s\n]*(?:ได้)?ที่\s*(?:ลิงก์|ลิงค์|ปุ่ม)\s*(?:ด้านล่าง|ข้างล่าง|ที่แนบไว้).*?$',
    ]
    for p in ui_patterns:
        bot_text = re.sub(p, '', bot_text, flags=re.IGNORECASE).strip()

    # ถ้าไม่มีลิงก์ส่งกลับไปเลย ให้ลบคำว่า ลิงก์ด้านล่าง หรือ ตามลิงก์ ออกจากข้อความทั้งหมด
    if not has_links:
        bot_text = re.sub(r'[\s,]*(?:ตาม|คลิก|ดู|ที่)?\s*(?:ลิงก์|ลิงค์|ปุ่ม)\s*(?:ด้านล่าง|ข้างล่าง|นี้).*?(?:ได้เลยครับ|ครับ|ค่ะ)?', '', bot_text, flags=re.IGNORECASE).strip()

    # ปรับสไตล์การขึ้นต้นประโยคให้เป็นธรรมชาติและไร้สรรพนาม (Subjectless Tone)
    bot_text = re.sub(r'^(?:สวัสดีครับ|สวัสดีค่ะ)?[\s,]*(?:นิสิต|น้องๆ(?:\s*หรือผู้ปกครอง)?)\s*สามารถ', 'สามารถ', bot_text)
    bot_text = re.sub(r'(?:[\s\n]|^)(?:นิสิต|น้องๆ(?:\s*หรือผู้ปกครอง)?)\s*สามารถ', ' สามารถ', bot_text)
    bot_text = re.sub(r'การตรวจสอบตาราง(\w+)ของ(?:นิสิต|น้องๆ(?:\s*หรือผู้ปกครอง)?)', r'สำหรับการตรวจสอบตาราง\1', bot_text)
    bot_text = re.sub(r'(?:แต่นิสิตสามารถ|แต่น้องๆ\s*หรือผู้ปกครองสามารถ)', 'แต่สามารถ', bot_text)
    bot_text = re.sub(r'(?:ของนิสิต|ของน้องๆ\s*หรือผู้ปกครอง)', '', bot_text)

    # ล้างคำลงท้าย ครับ หรือ ค่ะ ที่ซ้ำซ้อน
    bot_text = re.sub(r'(ครับ\s*){2,}', 'ครับ ', bot_text)
    bot_text = re.sub(r'(ค่ะ\s*){2,}', 'ค่ะ ', bot_text).strip()
    if bot_text and not bot_text.endswith(('ครับ', 'ค่ะ', 'ครับ.', 'ค่ะ.')):
        bot_text += ' ครับ'

    return bot_text

def evaluate_faithfulness(bot_text, context_text):
    """ตรวจสอบความซื่อตรงของข้อมูล (Faithfulness) ตามมาตรฐาน Skill rag-system-auditor
    สกัดตัวเลข, วันที่, ค่าธรรมเนียม, หน่วยกิต และคำสำคัญเฉพาะทางในคำตอบ แล้วตรวจว่ามีใน Context หรือไม่"""
    if not bot_text:
        return {"status": "unverified", "score": 1.0, "grounded_facts": [], "unsupported_facts": []}

    combined_ctx = (context_text or "").lower()

    # สกัดตัวเลขสำคัญและข้อเท็จจริงทางการเงิน/การศึกษา
    number_patterns = re.findall(
        r'\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\s*(?:บาท|หน่วยกิต|วัน|เทอม|เดือน|ปี|รอบ|gpax)\b|\b(?:25\d{2}|6\d{1})\b',
        bot_text,
        flags=re.I
    )

    facts_to_check = list(dict.fromkeys([p.strip() for p in number_patterns if len(p.strip()) > 1]))
    grounded_facts = []
    unsupported_facts = []

    for f in facts_to_check:
        clean_f = f.lower()
        num_only = re.sub(r'[^\d\.,]', '', clean_f).strip()
        if clean_f in combined_ctx or (num_only and num_only in combined_ctx):
            grounded_facts.append(f)
        else:
            unsupported_facts.append(f)

    # ตรวจจับชื่อวันทำการ/ช่วงเวลา/สถานที่/สิ่งอำนวยความสะดวกในคำตอบ ว่ามีหลักฐานใน Context หรือไม่
    claim_patterns = re.findall(
        r'(?:วันจันทร์|วันอังคาร|วันพุธ|วันพฤหัสบดี|วันศุกร์|วันเสาร์|วันอาทิตย์|เสาร์-อาทิตย์|จันทร์-ศุกร์|เวลาราชการ|โรงอาหาร(?:กลาง|ชาย|หญิง)?|โรงชาย|โรงหญิง|ตลาดนัด|รถราง|สระว่ายน้ำ|สนามกีฬา|ฟิตเนส|โรงพยาบาล|คลินิก|ห้องพยาบาล|สหกรณ์|ร้านค้า|เซเว่น|7-eleven|ธนาคาร)',
        bot_text,
        flags=re.I
    )
    for c in dict.fromkeys([p.strip() for p in claim_patterns if p.strip()]):
        if c.lower() in combined_ctx:
            grounded_facts.append(c)
        else:
            unsupported_facts.append(c)

    total_facts = len(grounded_facts) + len(unsupported_facts)
    if total_facts == 0:
        score = 1.0
        status = "grounded"
    else:
        score = round(len(grounded_facts) / total_facts, 2)
        if score >= 0.85:
            status = "grounded"
        elif score >= 0.50:
            status = "warning"
        else:
            status = "hallucination_risk"

    return {
        "status": status,
        "score": score,
        "grounded_facts": grounded_facts,
        "unsupported_facts": unsupported_facts
    }

def _perform_retrieval(user_message, history=None):
    """ดำเนินการสืบค้นข้อมูลในคลังเอกสารและ FAQ (Core RAG Retrieval)"""
    msg_lower = user_message.lower()
    prev_user_text = ""
    if history and isinstance(history, list):
        prev_user_msgs = []
        for h in history[-4:]:
            if isinstance(h, dict) and h.get("role") == "user":
                p = h.get("parts")
                if isinstance(p, str):
                    txt = p.strip()
                elif isinstance(p, list) and p and isinstance(p[0], dict):
                    txt = p[0].get("text", "").strip()
                else:
                    txt = str(p or "").strip()
                if txt:
                    prev_user_msgs.append(txt)
        if prev_user_msgs:
            prev_user_text = " ".join(prev_user_msgs[-2:]).lower()
    _stop_words = (
        "คณะวิทยาศาสตร์", "มหาวิทยาลัยเกษตรศาสตร์", "วิทยาเขตศรีราชา",
        "ม.เกษตร", "ศรีราชา", "เกษตร", "ต้องทำยังไง", "ทำยังไง", "ยังไง",
        "อย่างไร", "อะไรบ้าง", "เมื่อไหร่", "เท่าไหร่", "ที่ไหน", "ได้ไหม",
        "ได้มั้ย", "คือใคร", "สอบถามได้ไหม", "สอบถามได้มั้ย", "สอบถาม", "ขอสอบถาม",
        "รบกวนสอบถาม", "รบกวน", "อยากทราบ", "ขอถาม", "อยากรู้", "พอดีจะ", "พอดี",
        "ครับ", "ค่ะ", "คะ", "นะ",
    )
    cleaned_msg = msg_lower
    for sw in _stop_words:
        cleaned_msg = cleaned_msg.replace(sw, " ")
    cleaned_msg = cleaned_msg.strip()
    search_src = cleaned_msg if len(cleaned_msg) >= 2 else msg_lower

    kw = [w for w in search_src.split() if len(w) > 1]
    compact = search_src.replace(" ", "")
    kw += [compact[i:i+3] for i in range(len(compact) - 2)]
    if prev_user_text:
        cleaned_prev = prev_user_text
        for sw in _stop_words:
            cleaned_prev = cleaned_prev.replace(sw, " ")
        cleaned_prev = cleaned_prev.strip()
        prev_kw = [w for w in cleaned_prev.split() if len(w) > 1]
        compact_prev = cleaned_prev.replace(" ", "")
        prev_kw += [compact_prev[i:i+3] for i in range(len(compact_prev) - 2)]

        _topic_kws = ("วิชา", "หลักสูตร", "ค่าเทอม", "ทุน", "ปฏิทิน", "สอบ", "ลงทะเบียน", "คำร้อง", "เอกสาร", "หอพัก", "เกรด", "ปี", "เทอม")
        is_follow_up = len(cleaned_msg) < 30 or not any(k in cleaned_msg for k in _topic_kws) or any(k in msg_lower for k in ("บอกผม", "บอกได้", "สาขา", "ภาค", "แล้ว", "ต่อ", "เพิ่มเติม"))
        if is_follow_up and prev_kw:
            kw = list(dict.fromkeys(kw + prev_kw))
        else:
            kw = list(dict.fromkeys(kw))
    else:
        kw = list(dict.fromkeys(kw))

    def _score(text):
        if not kw or not text:
            return 0
        t = text.lower()
        return sum(1 for w in kw if w in t)

    _MAJOR_MAP = {
        "cs": {
            "keywords": ("cs", "วิทยาการคอมพิวเตอร์", "วิทย์คอม", "คอมพิวเตอร์", "com sci", "computer science"),
            "files": ("computerscience", "computer-science"),
        },
        "dst": {
            "keywords": ("dst", "ดิจิทัล", "ดิจิทัลไซน์", "digital", "เทคโนโลยีดิจิทัล"),
            "files": ("digital", "digitaltechnology"),
        },
        "it": {
            "keywords": ("it", "เทคโนโลยีสารสนเทศ", "สารสนเทศ", "information technology"),
            "files": ("information technology", "information-technology"),
        },
        "data": {
            "keywords": ("data", "วิเคราะห์ข้อมูล", "คณิตศาสตร์ประกันภัย", "ประกันภัย", "actuarial"),
            "files": ("data-analytics", "dataanalytics", "actuarial"),
        },
        "env": {
            "keywords": ("สิ่งแวดล้อม", "environmental"),
            "files": ("environmental", "environmentalscience"),
        },
        "physics": {
            "keywords": ("ฟิสิกส์", "physics"),
            "files": ("physics",),
        },
        "chem": {
            "keywords": ("เคมี", "chemical"),
            "files": ("chemical", "chemistry", "appliedchemistry"),
        },
        "gened": {
            "keywords": ("ศึกษาทั่วไป", "หมวดศึกษาทั่วไป", "วิชาศึกษาทั่วไป", "gened", "gen-ed", "general education"),
            "files": ("generaleducation", "gened"),
        },
    }
    detected_majors = set()
    for m_key, m_info in _MAJOR_MAP.items():
        if any(k in msg_lower for k in m_info["keywords"]):
            detected_majors.add(m_key)
    if not detected_majors and prev_user_text:
        for m_key, m_info in _MAJOR_MAP.items():
            if any(k in prev_user_text for k in m_info["keywords"]):
                detected_majors.add(m_key)

    context_str = ""
    docs_to_use = []
    doc_score_map = {}
    scored_docs = []
    is_fallback = False
    raw_compact = msg_lower.replace(" ", "")
    is_overview = any(p in raw_compact for p in (
        "มีข้อมูลอะไรบ้าง", "มีข้อมูลด้านไหน", "มีข้อมูลเรื่องอะไร", "มีข้อมูลอะไร",
        "ช่วยอะไรได้บ้าง", "ช่วยเรื่องอะไรได้บ้าง", "ทำอะไรได้บ้าง", "ถามอะไรได้บ้าง",
        "มีบริการอะไรบ้าง", "ค้นหาอะไรได้บ้าง", "รู้เรื่องอะไรบ้าง", "มีอะไรให้ถามบ้าง",
        "ในระบบมีข้อมูลอะไร", "มีข้อมูลเกี่ยวกับอะไร"
    ))
    is_all_programs_q = any(p in raw_compact for p in (
        "มีสาขาอะไร", "สาขาอะไรบ้าง", "มีกี่สาขา", "เปิดสอนสาขา", "สาขาวิชาอะไรบ้าง",
        "มีหลักสูตรอะไร", "หลักสูตรอะไรบ้าง", "มีกี่หลักสูตร", "รวมหลักสูตร", "ทุกสาขา", "ทุกหลักสูตร",
        "สาขาทั้งหมด", "หลักสูตรทั้งหมด", "เรียนสาขาไหนดี", "แนะนำสาขา", "มีสาขา"
    ))

    if CHAT_CONTEXT:
        for idx, d in enumerate(CHAT_CONTEXT):
            src_lower = d.get("source", "").lower()
            content_lower = d.get("content", "").lower()
            cat_lower = d.get("category", "").lower()

            sc = _score(content_lower) + _score(cat_lower) * 3 + _score(src_lower) * 3
            if detected_majors:
                for m_key in detected_majors:
                    target_files = _MAJOR_MAP[m_key]["files"]
                    if any(f in src_lower for f in target_files):
                        sc += 25
                if "หลักสูตร" in cat_lower or ".txt" in src_lower or ".pdf" in src_lower:
                    is_target_major = any(any(f in src_lower for f in _MAJOR_MAP[m_key]["files"]) for m_key in detected_majors)
                    if not is_target_major:
                        sc -= 15

            if is_all_programs_q:
                if "program" in src_lower or "curriculum" in cat_lower or "regis_curriculum" in cat_lower:
                    sc += 35
                elif ".txt" in src_lower:
                    sc = min(sc, 5)

            doc_score_map[id(d)] = sc
            if sc > 0:
                scored_docs.append((sc, idx, d))

        scored_docs.sort(key=lambda x: (-x[0], x[1]))
        filtered = [d for _, _, d in scored_docs]

        if any(k in msg_lower for k in ("ค่าเทอม", "ค่าธรรมเนียม", "ค่าเล่าเรียน", "หลักสูตรพิเศษ", "ภาคพิเศษ")):
            fee_docs = [d for d in CHAT_CONTEXT if any(k in d.get("category", "").lower() or k in d.get("source", "").lower() for k in ("ค่าธรรมเนียม", "ระเบียบการ", "tuition", "fee", "tcas"))]
            prog_docs = [d for d in filtered if "program" in d.get("category", "")]
            top_tuition_docs = fee_docs + [d for d in prog_docs if d not in fee_docs]
            filtered = top_tuition_docs + [d for d in filtered if d not in top_tuition_docs]

        # ป้องกันไม่ให้คำว่า "สอบถาม", "ทดสอบ", "ตรวจสอบ" ไปทริกเกอร์หมวดปฏิทินสอบ
        cal_clean_text = re.sub(r'สอบถาม|ทดสอบ|ตรวจสอบ', ' ', msg_lower)
        _cal_kw = ("วันสอบ", "ตารางสอบ", "ห้องสอบ", "สอบกลางภาค", "สอบปลายภาค", "สอบไล่", "ขอสอบ",
                   "ปฏิทิน", "เปิดเทอม", "ปิดเทอม", "เปิดภาค", "ปิดภาค",
                   "ลงทะเบียน", "ถอน", "เพิ่มวิชา", "กำหนดการ", "เทอม", "ภาคเรียน", "วันสุดท้าย")
        has_cal_match = any(k in cal_clean_text for k in _cal_kw) or bool(re.search(r'(?<![ก-๙])สอบ(?![ก-๙])', cal_clean_text))
        if has_cal_match and "ค่าเทอม" not in msg_lower:
            cal_docs = [d for d in CHAT_CONTEXT if "ปฏิทิน" in d.get("category", "")]
            filtered = cal_docs + [d for d in filtered if d not in cal_docs]

        if is_overview:
            docs_to_use = []
        elif filtered:
            docs_to_use = filtered[:8]
        else:
            docs_to_use = CHAT_CONTEXT[:2]
            is_fallback = True

        context_str = "\n\nRelevant Information:\n"
        for doc in docs_to_use:
            limit = 40000 if ("ปฏิทิน" in doc.get("category", "") or "หลักสูตร" in doc.get("category", "") or "ระเบียบการ" in doc.get("category", "") or ".pdf" in doc.get("source", "").lower()) else 6000
            context_str += f"Source: {doc['source']} ({doc['category']})\nContent: {doc['content'][:limit]}\n\n"

    faq_str = ""
    scored_faq = []
    is_faq_enabled = (get_setting("faq_enabled", "true") == "true")
    if is_faq_enabled and FAQ_CONTEXT and FAQ_TOKEN_DATA:
        N = len(FAQ_CONTEXT)
        q_ngrams = get_faq_ngrams(search_src)
        if q_ngrams:
            query_weights = {g: FAQ_IDF.get(g, math.log(N + 1)) for g in q_ngrams}
            weights_sorted = sorted(query_weights.values())
            median_weight = weights_sorted[len(weights_sorted) // 2]
            significant_grams = [g for g, w in query_weights.items() if w >= median_weight]

            for idx, (f, q_set, a_set, all_set) in enumerate(FAQ_TOKEN_DATA):
                matched_sig = sum(1 for g in significant_grams if g in all_set)
                coverage = matched_sig / len(significant_grams) if significant_grams else 0
                if coverage < 0.30:
                    continue

                q_sc = sum(query_weights[g] * 3.0 for g in q_ngrams if g in q_set)
                a_sc = sum(query_weights[g] * 1.0 for g in q_ngrams if g in a_set)
                if search_src in f["q"].lower():
                    q_sc += 20.0

                total_sc = round((q_sc + a_sc) * coverage, 1)
                if total_sc >= 10.0:
                    scored_faq.append((total_sc, idx, f))

            scored_faq.sort(key=lambda x: (-x[0], x[1]))
            if scored_faq:
                top_faq_sc = scored_faq[0][0]
                filtered_faq = [e for sc, _, e in scored_faq if sc >= max(10.0, top_faq_sc * 0.6)][:3]
                if filtered_faq:
                    faq_str = "\n\nข้อมูล FAQ ที่เกี่ยวข้อง:\n"
                    for e in filtered_faq:
                        faq_str += f"Q: {e['q']}\nA: {e['a']}\n\n"

    return {
        "kw": kw,
        "detected_majors": detected_majors,
        "docs_to_use": docs_to_use,
        "doc_score_map": doc_score_map,
        "scored_faq": scored_faq,
        "is_fallback": is_fallback,
        "is_overview": is_overview,
        "is_all_programs_q": is_all_programs_q,
        "context_str": context_str,
        "faq_str": faq_str
    }

def compute_rag_debug_info(user_message, bot_text, good_files, good_links,
                           docs_to_use, scored_docs_map, scored_faq,
                           detected_majors, kw, is_fallback, is_overview,
                           context_str="", faq_str="", is_all_programs_q=False):
    """คำนวณระดับความมั่นใจ และรวบรวมที่มาของข้อมูล (The RAG Triad Analysis)"""
    top_doc_score = max(scored_docs_map.values()) if scored_docs_map else 0
    top_faq_score = scored_faq[0][0] if scored_faq else 0

    reasons = []
    confidence_score = 50

    # ตรวจสอบว่าคำตอบของบอทมีการอ้างอิงข้อมูลจาก FAQ จริงหรือไม่ (Grounding Fact-Check)
    faq_actually_cited = False
    if scored_faq:
        top_faq_entry = scored_faq[0][2]
        ans_sig = [g for g in get_faq_ngrams(top_faq_entry.get("a", "")) if FAQ_IDF.get(g, 0) >= 1.5]
        if ans_sig:
            overlap = sum(1 for tok in ans_sig if tok in bot_text.lower())
            if (overlap / len(ans_sig)) >= 0.20:
                faq_actually_cited = True
        elif len(top_faq_entry.get("a", "")) > 0:
            words_in_ans = [w for w in top_faq_entry["a"].split() if len(w) >= 3]
            if any(w in bot_text.lower() for w in words_in_ans):
                faq_actually_cited = True

    if faq_actually_cited and top_faq_score >= 18:
        confidence_score += 28
        reasons.append(f"พบคู่คำถาม-คำตอบตรงใน FAQ (คะแนนความตรง {int(top_faq_score)})")
    elif faq_actually_cited and top_faq_score >= 10:
        confidence_score += 15
        reasons.append(f"พบ FAQ ที่มีคำสำคัญสอดคล้อง (คะแนน {int(top_faq_score)})")

    if top_doc_score >= 25:
        confidence_score += 22
        reasons.append(f"พบคลังข้อมูลหน้าเว็บ/ระเบียบการตรงจุดสูงมาก (คะแนน {top_doc_score})")
    elif top_doc_score >= 10:
        confidence_score += 14
        reasons.append(f"พบเอกสารที่เกี่ยวข้องในระบบ (คะแนน {top_doc_score})")
    elif top_doc_score > 0:
        confidence_score += 8
        reasons.append(f"พบเอกสารที่มีคำค้นบางส่วน (คะแนน {top_doc_score})")

    if detected_majors:
        confidence_score += 10
        m_list = ", ".join(sorted(m.upper() for m in detected_majors))
        reasons.append(f"ตรวจพบสาขาวิชา {m_list} และคัดกรองเอกสารหลักสูตรเฉพาะทาง")

    if is_all_programs_q:
        confidence_score += 10
        reasons.append("ตรวจพบคำถามภาพรวมหลักสูตร/สาขาวิชา และดึงข้อมูลหลักสูตรทั้งหมดของคณะโดยตรง")

    if is_fallback:
        confidence_score -= 30
        reasons.append("ไม่พบเอกสารตรงเป้า ระบบจำเป็นต้องใช้เอกสารภาพรวมสำรอง")
    elif is_overview:
        reasons.append("คำถามภาพรวมทั่วไป")

    combined_ctx = (context_str or "") + " " + (faq_str or "")
    faith_res = evaluate_faithfulness(bot_text, combined_ctx)
    if faith_res["unsupported_facts"]:
        confidence_score -= 20
        reasons.append(f"ตรวจพบข้อมูลหรือสถานที่ {len(faith_res['unsupported_facts'])} จุดที่ไม่ปรากฏในเอกสารอ้างอิง ({', '.join(faith_res['unsupported_facts'])})")
    elif faith_res["grounded_facts"]:
        confidence_score += 10
        reasons.append("ข้อมูลและเงื่อนไขสำคัญในคำตอบ มีหลักฐานยืนยันในเอกสารอ้างอิงครบถ้วน")

    confidence_score = max(20, min(98, confidence_score))

    if confidence_score >= 75:
        confidence_level = "HIGH"
    elif confidence_score >= 45:
        confidence_level = "MEDIUM"
    else:
        confidence_level = "LOW"

    if faq_actually_cited and top_faq_score >= 10 and top_faq_score >= top_doc_score:
        route = "faq_grounded"
    elif is_fallback:
        route = "no_context_fallback"
    else:
        route = "rag_retrieval"

    sources_data = []
    for d in (docs_to_use or []):
        sc = scored_docs_map.get(id(d), 0)
        content_raw = d.get("content", "")
        sources_data.append({
            "source": d.get("source", "-"),
            "category": d.get("category", "ทั่วไป"),
            "score": sc,
            "snippet": (content_raw[:280] + "...") if len(content_raw) > 280 else content_raw,
            "char_count": len(content_raw),
        })

    faq_data = []
    is_faq_enabled = (get_setting("faq_enabled", "true") == "true")
    if is_faq_enabled and scored_faq:
        top_faq_sc = scored_faq[0][0]
        # แสดงเฉพาะ FAQ ที่ผ่านเกณฑ์ 60% และส่งให้ AI อ่านจริงเท่านั้น ป้องกันไม่ให้แชทขยะติดมาในหน้าจอ
        actual_faqs = [(sc, e) for sc, _, e in scored_faq if sc >= max(12, top_faq_sc * 0.6)][:3]
        for sc, e in actual_faqs:
            ans_raw = e.get("a", "")
            faq_data.append({
                "question": e.get("q", "-"),
                "answer_snippet": (ans_raw[:200] + "...") if len(ans_raw) > 200 else ans_raw,
                "score": sc,
            })

    return {
        "route": route,
        "confidence_score": confidence_score,
        "confidence_level": confidence_level,
        "confidence_reasons": reasons,
        "faithfulness": faith_res,
        "retrieved_sources": sources_data,
        "matched_faq": faq_data,
        "detected_majors": sorted(list(detected_majors)),
        "extracted_keywords": kw[:10] if kw else [],
        "suggested_files": good_files or [],
        "suggested_links": good_links or [],
        "model": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        "timestamp": datetime.utcnow().isoformat(),
        "is_reconstructed": False,
    }

def generate_chat_debug_analysis(user_message, bot_answer=None):
    """จำลองการวิเคราะห์ที่มาและความมั่นใจย้อนหลัง (Reconstructed on-the-fly)"""
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
        return {
            "route": "direct_rule",
            "confidence_score": 100,
            "confidence_level": "HIGH",
            "confidence_reasons": ["ตรงกับคำทักทายมาตรฐาน ตอบกลับทันที"],
            "faithfulness": {"status": "grounded", "score": 1.0, "grounded_facts": [], "unsupported_facts": []},
            "retrieved_sources": [],
            "matched_faq": [],
            "detected_majors": [],
            "extracted_keywords": [],
            "suggested_files": [],
            "suggested_links": [],
            "model": "system_guard",
            "timestamp": datetime.utcnow().isoformat(),
            "is_reconstructed": True,
        }

    _thanks = {
        "ขอบคุณ", "ขอบคุณครับ", "ขอบคุณคับ", "ขอบคุณค้าบ", "ขอบคุณค่ะ", "ขอบคุณคะ", "ขอบคุณค่า",
        "ขอบคุณมาก", "ขอบคุณมากครับ", "ขอบคุณมากค่ะ", "ขอบใจ", "ขอบใจจ้า",
        "โอเค", "โอเคครับ", "โอเคค่ะ", "รับทราบ", "เข้าใจแล้ว",
        "ok", "okay", "thanks", "thank you", "thx", "ty",
    }
    if msg_clean in _thanks or msg_norm in _thanks:
        return {
            "route": "direct_rule",
            "confidence_score": 100,
            "confidence_level": "HIGH",
            "confidence_reasons": ["ตรงกับคำขอบคุณมาตรฐาน ตอบกลับทันที"],
            "faithfulness": {"status": "grounded", "score": 1.0, "grounded_facts": [], "unsupported_facts": []},
            "retrieved_sources": [],
            "matched_faq": [],
            "detected_majors": [],
            "extracted_keywords": [],
            "suggested_files": [],
            "suggested_links": [],
            "model": "system_guard",
            "timestamp": datetime.utcnow().isoformat(),
            "is_reconstructed": True,
        }

    ret = _perform_retrieval(user_message)
    debug_data = compute_rag_debug_info(
        user_message=user_message,
        bot_text=bot_answer or "",
        good_files=[],
        good_links=[],
        docs_to_use=ret["docs_to_use"],
        scored_docs_map=ret["doc_score_map"],
        scored_faq=ret["scored_faq"],
        detected_majors=ret["detected_majors"],
        kw=ret["kw"],
        is_fallback=ret["is_fallback"],
        is_overview=ret["is_overview"],
        context_str=ret["context_str"],
        faq_str=ret["faq_str"],
        is_all_programs_q=ret.get("is_all_programs_q", False)
    )
    debug_data["is_reconstructed"] = True
    return debug_data

def ask_gemini(user_message, history=None, return_debug=False):
    msg_lower = user_message.lower()
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
        resp = (
            "สวัสดีครับ มีข้อมูลด้านใดของคณะวิทยาศาสตร์ ศรีราชา ให้ช่วยค้นหาไหมครับ "
            "สามารถสอบถามเรื่องหลักสูตร การลงทะเบียน แบบฟอร์มคำร้อง หรือทุนการศึกษาได้เลยครับ"
        )
        if return_debug:
            dbg = generate_chat_debug_analysis(user_message, resp)
            dbg["is_reconstructed"] = False
            return resp, [], [], dbg
        return resp, [], []

    _thanks = {
        "ขอบคุณ", "ขอบคุณครับ", "ขอบคุณคับ", "ขอบคุณค้าบ", "ขอบคุณค่ะ", "ขอบคุณคะ", "ขอบคุณค่า",
        "ขอบคุณมาก", "ขอบคุณมากครับ", "ขอบคุณมากค่ะ", "ขอบใจ", "ขอบใจจ้า",
        "โอเค", "โอเคครับ", "โอเคค่ะ", "รับทราบ", "เข้าใจแล้ว",
        "ok", "okay", "thanks", "thank you", "thx", "ty",
    }
    if msg_clean in _thanks or msg_norm in _thanks:
        resp = "ยินดีครับ หากมีข้อสงสัยเพิ่มเติมเกี่ยวกับคณะวิทยาศาสตร์ ศรีราชา สอบถามได้ตลอดเลยครับ"
        if return_debug:
            dbg = generate_chat_debug_analysis(user_message, resp)
            dbg["is_reconstructed"] = False
            return resp, [], [], dbg
        return resp, [], []

    # ตรวจสอบและรีเฟรช FAQ จากฐานข้อมูล (เช็คใน RAM ทันที และซิงค์กับ DB ทุก 60 วินาทีเพื่อรองรับเครื่องเซิร์ฟเวอร์มหาลัย)
    global _LAST_FAQ_CHECK_TS
    now_ts = time.time()
    if not FAQ_CONTEXT or (now_ts - _LAST_FAQ_CHECK_TS > _FAQ_CHECK_INTERVAL):
        _LAST_FAQ_CHECK_TS = now_ts
        try:
            faq_count = FAQ.query.count()
            if len(FAQ_CONTEXT) != faq_count:
                load_faq_from_db()
        except Exception:
            pass

    _curriculum_guard = ("เรียน", "สอน", "วิชา", "สาขา", "หลักสูตร", "แผนการเรียน", "ศึกษา")
    is_curriculum_query = any(w in msg_lower for w in _curriculum_guard)

    _model_exact = {
        "ชื่ออะไร", "ชื่อไร", "คุณชื่ออะไร", "เธอชื่ออะไร", "บอทชื่ออะไร",
        "ใครสร้าง", "ใครพัฒนา",
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
    if not is_curriculum_query and (msg_clean in _model_exact or (len(msg_clean) <= 60 and any(p in msg_nospace for p in _model_phrases))):
        resp = (
            "ผมคือ Science Assistant ระบบผู้ช่วยอัจฉริยะของคณะวิทยาศาสตร์ ศรีราชา มหาวิทยาลัยเกษตรศาสตร์ "
            "พัฒนาขึ้นเพื่อช่วยตอบคำถามเกี่ยวกับหลักสูตร การลงทะเบียน เอกสารคำร้อง และข้อมูลภายในคณะครับ "
            "พิมพ์เรื่องที่ต้องการสอบถามได้เลยครับ"
        )
        if return_debug:
            dbg = {
                "route": "direct_rule",
                "confidence_score": 100,
                "confidence_level": "HIGH",
                "confidence_reasons": ["ตรงกับคำถามระบุข้อมูลระบบ"],
                "faithfulness": {"status": "grounded", "score": 1.0, "grounded_facts": [], "unsupported_facts": []},
                "retrieved_sources": [],
                "matched_faq": [],
                "detected_majors": [],
                "extracted_keywords": [],
                "suggested_files": [],
                "suggested_links": [],
                "model": "system_guard",
                "timestamp": datetime.utcnow().isoformat(),
                "is_reconstructed": False,
            }
            return resp, [], [], dbg
        return resp, [], []

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
        resp = (
            "ระบบพร้อมใช้งานครับ สามารถพิมพ์คำถามเกี่ยวกับหลักสูตร การลงทะเบียน "
            "เอกสารคำร้อง หรือข้อมูลของคณะวิทยาศาสตร์ ศรีราชา ได้เลยครับ"
        )
        if return_debug:
            dbg = {
                "route": "direct_rule",
                "confidence_score": 100,
                "confidence_level": "HIGH",
                "confidence_reasons": ["ตรงกับข้อความทดสอบทั่วไป"],
                "faithfulness": {"status": "grounded", "score": 1.0, "grounded_facts": [], "unsupported_facts": []},
                "retrieved_sources": [],
                "matched_faq": [],
                "detected_majors": [],
                "extracted_keywords": [],
                "suggested_files": [],
                "suggested_links": [],
                "model": "system_guard",
                "timestamp": datetime.utcnow().isoformat(),
                "is_reconstructed": False,
            }
            return resp, [], [], dbg
        return resp, [], []

    ret = _perform_retrieval(user_message, history=history)
    kw = ret["kw"]
    detected_majors = ret["detected_majors"]
    docs_to_use = ret["docs_to_use"]
    doc_score_map = ret["doc_score_map"]
    scored_faq = ret["scored_faq"]
    is_fallback = ret["is_fallback"]
    is_overview = ret["is_overview"]
    context_str = ret["context_str"]
    faq_str = ret["faq_str"]
    is_all_programs_q = ret.get("is_all_programs_q", False)

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
- links ที่ใส่ได้ เช่น MyTCAS, เว็บคณะ, ระบบลงทะเบียน, admissions.src.ku.ac.th ฯลฯ
- ถ้าไม่มีลิงค์ที่เกี่ยวข้องให้ใส่ []
"""
    messages = []
    if history:
        for h in history[-6:]:
            messages.append({"role": h["role"], "parts": [{"text": h["parts"]}]})
    messages.append({"role": "user", "parts": [{"text": user_message}]})

    def _call_gemini(api_key):
        genai.configure(api_key=api_key)
        m = genai.GenerativeModel(
            model_name=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            system_instruction=system,
            generation_config={"response_mime_type": "application/json"}
        )
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

    # หาก Gemini ตอบกลับมาเป็น JSON Array ครอบ Object เช่น [{"response": ...}]
    if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
        parsed = parsed[0]
    def _stem_pdf(s):
        s = re.sub(r'\.pdf$', '', s.strip(), flags=re.I)
        s = re.sub(r'[-_]?(?:revised|revision)', '', s, flags=re.I)
        s = re.sub(r'\s+', ' ', s).strip().lower()
        return s

    def _match_pdf(name):
        if not name or not isinstance(name, str):
            return None
        name_clean = name.strip()
        if name_clean in ALL_PDFS:
            return name_clean

        target_stem = _stem_pdf(name_clean)
        if not target_stem:
            return None

        # 1. Exact stem match (ตัด .pdf และ -Revised ออกก่อนเทียบ)
        for f in ALL_PDFS:
            if target_stem == _stem_pdf(f):
                return f

        # 2. Number prefix match (เช่น '3 ' ตรงกับ '3 ขอผ่อนผัน...')
        m_num = re.match(r'^(\d+)\s*', target_stem)
        if m_num:
            num = m_num.group(1)
            for f in ALL_PDFS:
                if re.match(rf'^{num}\s+', f):
                    words = [w for w in re.split(r'[\s\(\)]+', target_stem) if len(w) > 2]
                    if any(w in f.lower() for w in words):
                        return f

        # 3. Substring match บน stem
        for f in ALL_PDFS:
            f_stem = _stem_pdf(f)
            if target_stem in f_stem or f_stem in target_stem:
                return f
        return None

    if parsed is None or not isinstance(parsed, dict):
        m = re.search(r'\{[\s\S]*\}', raw_text)
        if m:
            try:
                temp_parsed = json.loads(m.group())
                if isinstance(temp_parsed, dict):
                    parsed = temp_parsed
            except Exception:
                pass

    if parsed is None or not isinstance(parsed, dict):
        resp_match = re.search(r'"response"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_text)
        if resp_match:
            try:
                bot_text = clean_response(json.loads(f'"{resp_match.group(1)}"'))
            except Exception:
                bot_text = clean_response(resp_match.group(1))
        else:
            bot_text = clean_response(raw_text)
        good_files = []
        raw_links  = []
    else:
        bot_text   = clean_response(parsed.get("response") or parsed.get("message") or "")
        good_files = [m for m in (_match_pdf(f) for f in parsed.get("files", [])) if m]
        raw_links  = parsed.get("links", [])

    # Fallback: หาก AI ลืมใส่ใน parsed['files'] แต่ในเนื้อหา bot_text พูดถึงแบบฟอร์ม ให้สแกนดึงมาให้ผู้ใช้อัตโนมัติ
    if not good_files and bot_text:
        # สแกนหาคำลงท้าย .pdf ในข้อความ
        for m in re.finditer(r'([^\s\'\"และ\(\)]+?\.pdf)', bot_text, flags=re.I):
            matched = _match_pdf(m.group(1))
            if matched and matched not in good_files:
                good_files.append(matched)

        # สแกนหาชื่อแบบฟอร์มที่มีหมายเลขนำหน้า เช่น 'แบบฟอร์ม 3 ขอผ่อนผัน' หรือ 'คำร้อง 14 ขอสอบชดใช้'
        if not good_files:
            for f in ALL_PDFS:
                m_num = re.match(r'^(\d+)\s+([^\(\-_]+)', f)
                if m_num:
                    num, key_phrase = m_num.group(1), m_num.group(2).strip()
                    if (num in bot_text and key_phrase in bot_text) or f"{num} {key_phrase}" in bot_text:
                        if f not in good_files:
                            good_files.append(f)

    # ดึงลิงก์จาก Data-Driven Registry พร้อมกรองไม่ให้คำในบอทดึงมั่ว
    good_links = get_matching_system_links(
        user_message=user_message,
        bot_text=bot_text,
        history=history,
        initial_links=raw_links,
        is_curriculum_query=is_curriculum_query,
        has_pdf=bool(good_files)
    )

    query_context = (user_message + " " + bot_text).lower()
    bot_text = sanitize_bot_output(bot_text, has_links=bool(good_links), query_context=query_context)

    if return_debug:
        debug_info = compute_rag_debug_info(
            user_message=user_message,
            bot_text=bot_text,
            good_files=good_files,
            good_links=good_links,
            docs_to_use=docs_to_use,
            scored_docs_map=doc_score_map,
            scored_faq=scored_faq,
            detected_majors=detected_majors,
            kw=kw,
            is_fallback=is_fallback,
            is_overview=is_overview,
            context_str=context_str,
            faq_str=faq_str,
            is_all_programs_q=is_all_programs_q
        )
        return bot_text, good_files, good_links, debug_info

    return bot_text, good_files, good_links

# ──────────────────────────────────────────
# Routes — Public (ไม่ต้อง login)
# ──────────────────────────────────────────
@app.route("/ping")
def ping():
    return "pong", 200

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

_OAUTH_STATE_MAX_AGE = 600  # อายุ OAuth state สูงสุด 10 นาที
_USED_OAUTH_STATES = {}     # ป้องกัน Replay Attack เมื่อสลับเบราว์เซอร์บนมือถือ

def _generate_oauth_state():
    """สร้าง OAuth state ที่ลงลายเซ็น HMAC เพื่อให้ตรวจสอบได้แม้ iOS/Android สลับเบราว์เซอร์ตอน Callback"""
    s = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="line-oauth-state")
    state = s.dumps({"n": secrets.token_hex(8)})
    flask_session["oauth_state"] = state
    return state

def _verify_oauth_state(state, saved_state):
    """ตรวจสอบ OAuth state ทั้งจาก Session Cookie และลายเซ็น HMAC พร้อมป้องกันการใช้ซ้ำ"""
    if not state:
        return False
    now = time.time()
    expired_keys = [k for k, ts in _USED_OAUTH_STATES.items() if now - ts > _OAUTH_STATE_MAX_AGE]
    for k in expired_keys:
        _USED_OAUTH_STATES.pop(k, None)
    if state in _USED_OAUTH_STATES:
        return False
    if saved_state and hmac.compare_digest(state, saved_state):
        _USED_OAUTH_STATES[state] = now
        return True
    try:
        s = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="line-oauth-state")
        s.loads(state, max_age=_OAUTH_STATE_MAX_AGE)
        _USED_OAUTH_STATES[state] = now
        return True
    except (BadSignature, SignatureExpired):
        return False

@app.route("/login")
def login_page():
    # ถ้าล็อกอินด้วย LINE ตัวจริงแล้ว ให้ redirect ไปหน้าแชท
    if flask_session.get("line_user_id") and not flask_session.get("is_guest"):
        return redirect(url_for("index"))
    guest_mode = (get_setting("guest_mode", "true") == "true")

    # สร้าง Direct Universal Link สำหรับเปิดแอป LINE ในเครื่องอัตโนมัติบนมือถือ
    line_auth_url = None
    if LINE_CLIENT_ID:
        state = _generate_oauth_state()
        params = {
            "response_type": "code",
            "client_id":     LINE_CLIENT_ID,
            "redirect_uri":  LINE_REDIRECT_URI,
            "scope":         "profile openid",
            "state":         state,
            "bot_prompt":    "normal"
        }
        line_auth_url = "https://access.line.me/oauth2/v2.1/authorize?" + urlencode(params)

    return render_template("login.html", guest_mode_enabled=guest_mode, line_auth_url=line_auth_url)

@app.route("/auth/guest")
def guest_login():
    if get_setting("guest_mode", "true") != "true":
        return redirect(url_for("login_page"))
    guest_id = f"guest_{secrets.token_hex(6)}"
    flask_session.permanent = True
    flask_session["is_guest"] = True
    flask_session["guest_question_count"] = 0
    flask_session["line_user_id"] = guest_id
    flask_session["line_display_name"] = "ทดลองใช้งาน (Guest)"
    flask_session["line_picture"] = "/static/img/guest_avatar.png"
    flask_session["db_user_id"] = None
    return redirect(url_for("index"))

@app.route("/auth/line")
def line_login():
    if not LINE_CLIENT_ID:
        return redirect(url_for("login_page"))
    state = _generate_oauth_state()
    params = {
        "response_type": "code",
        "client_id":     LINE_CLIENT_ID,
        "redirect_uri":  LINE_REDIRECT_URI,
        "scope":         "profile openid",
        "state":         state,
        "bot_prompt":    "normal",
    }
    return redirect("https://access.line.me/oauth2/v2.1/authorize?" + urlencode(params))

@app.route("/auth/callback")
@app.route("/auth/line/callback")
def line_callback():
    code  = request.args.get("code")
    state = request.args.get("state")
    saved_state = flask_session.pop("oauth_state", None)
    # บังคับตรวจ OAuth state เสมอ ป้องกัน Login CSRF / Session Fixation
    if not code or not _verify_oauth_state(state, saved_state):
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
    flask_session["is_guest"]          = False
    flask_session.pop("guest_question_count", None)
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
    """Top 5 คำถามยอดนิยม — public endpoint (แคชในหน่วยความจำ 5 นาที)"""
    global _TOP_QUESTIONS_CACHE
    now_ts = time.time()
    if _TOP_QUESTIONS_CACHE["data"] and (now_ts - _TOP_QUESTIONS_CACHE["ts"] < 300):
        return jsonify(_TOP_QUESTIONS_CACHE["data"])

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
                .filter(ChatLog.is_guest == False)
                .order_by(ChatLog.id.desc()).limit(500).all())
        mapped = [c for r in rows if (c := normalize_chip(r.user_message))]
        top5 = [{"question": q, "count": cnt} for q, cnt in Counter(mapped).most_common(5)]
        seen = {item["question"] for item in top5}
        for q in default_chips:
            if len(top5) >= 5:
                break
            if q not in seen:
                top5.append({"question": q, "count": 0})
        result = {"questions": top5}
        _TOP_QUESTIONS_CACHE = {"data": result, "ts": now_ts}
        return jsonify(result)
    except Exception:
        return jsonify({"questions": [{"question": q, "count": 0} for q in default_chips]})

# ── In-Memory Rate Limiter ต่อบัญชีผู้ใช้/เซสชัน (ไม่ใช้ IP เพื่อรองรับ Campus Wi-Fi NAT) ──
_chat_rate_buckets = {}   # session_key -> [timestamps ภายใน 60 วินาทีล่าสุด]
_CHAT_RATE_LIMIT = 10     # สูงสุด 10 ข้อความต่อนาทีต่อคน (ป้องกันชนเพดาน Gemini 15 RPM)
_CHAT_RATE_WINDOW = 60

def _check_chat_rate_limit(session_key: str) -> bool:
    """ตรวจสอบความถี่การส่งแชทใน RAM (True = ผ่าน, False = ส่งถี่เกินไป)"""
    now = time.time()
    timestamps = [t for t in _chat_rate_buckets.get(session_key, []) if now - t < _CHAT_RATE_WINDOW]
    if len(timestamps) >= _CHAT_RATE_LIMIT:
        _chat_rate_buckets[session_key] = timestamps
        return False
    timestamps.append(now)
    _chat_rate_buckets[session_key] = timestamps
    return True

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

    is_guest = bool(flask_session.get("is_guest"))
    default_sid = "guest" if is_guest else "anonymous"
    session_id = data.get("session_id") or request.headers.get("X-Session-Id", default_sid)
    db_user_id = flask_session.get("db_user_id")
    if not db_user_id and flask_session.get("line_user_id"):
        u = LineUser.query.filter_by(line_user_id=flask_session["line_user_id"]).first()
        if u:
            db_user_id = u.id
            flask_session["db_user_id"] = u.id

    # จำกัด 10 ข้อความ/นาที ต่อบัญชีผู้ใช้/เซสชัน ในหน่วยความจำ (ไม่ใช้ IP เพื่อรองรับ Wi-Fi มหาลัย และลด SQL Query ข้าม Supabase)
    rate_key = f"user_{db_user_id}" if db_user_id else f"guest_{flask_session.get('line_user_id', session_id)}"
    if not _check_chat_rate_limit(rate_key):
        return jsonify({"response": "ขออภัยครับ คุณส่งข้อความถี่เกินไป กรุณารอสักครู่แล้วลองใหม่ครับ", "suggested_files": []})

    # ── ตรวจสอบโควตาสำหรับโหมดทดลองใช้งาน (Guest Mode: จำกัด 5 ข้อ) ──
    is_guest = bool(flask_session.get("is_guest"))
    GUEST_MAX_QUESTIONS = 5
    if is_guest:
        guest_count = flask_session.get("guest_question_count", 0)
        if guest_count >= GUEST_MAX_QUESTIONS:
            return jsonify({
                "response": "คุณใช้สิทธิ์ทดลองใช้งานครบ 5 คำถามแล้วครับ หากต้องการพูดคุยต่อ กรุณาเข้าสู่ระบบผ่าน LINE ได้เลยครับ",
                "suggested_files": [],
                "suggested_links": [],
                "is_guest": True,
                "guest_count": guest_count,
                "remaining_quota": 0,
                "quota_exceeded": True,
                "login_url": url_for("line_login") if LINE_CLIENT_ID else url_for("login_page")
            }), 403

    # ── ตรวจสอบความยาวข้อความ ──
    user_msg = data["message"].strip()
    if not user_msg:
        return jsonify({"response": "กรุณาพิมพ์คำถามครับ", "suggested_files": []})
    if len(user_msg) > 1000:
        return jsonify({"response": "ขออภัยครับ ข้อความยาวเกินไป กรุณาพิมพ์คำถามให้สั้นลงครับ", "suggested_files": []})

    history = data.get("history", [])
    try:
        bot_text, files, links, debug_data = ask_gemini(user_msg, history=history, return_debug=True)
        print(f"[DEBUG] suggested_files={files} | ALL_PDFS_count={len(ALL_PDFS)}")

        if is_guest:
            guest_count = flask_session.get("guest_question_count", 0) + 1
            flask_session["guest_question_count"] = guest_count
            remaining_quota = max(0, GUEST_MAX_QUESTIONS - guest_count)
            quota_exhausted = (guest_count >= GUEST_MAX_QUESTIONS)
        else:
            guest_count = 0
            remaining_quota = None
            quota_exhausted = False

        log = None
        try:
            log = ChatLog(
                user_message = user_msg,
                bot_answer   = bot_text,
                session_id   = session_id,
                user_id      = db_user_id,
                is_guest     = is_guest,
                debug_info   = json.dumps(debug_data, ensure_ascii=False) if debug_data else None,
            )
            db.session.add(log)
            db.session.commit()
            invalidate_stats_cache()
        except Exception as db_err:
            print(f"[ChatLog] Save error: {db_err}")
            db.session.rollback()

        return jsonify({
            "response": bot_text,
            "suggested_files": files,
            "suggested_links": links,
            "log_id": log.id if log else None,
            "is_guest": is_guest,
            "guest_count": guest_count,
            "remaining_quota": remaining_quota,
            "quota_exceeded": quota_exhausted,
            "login_url": (url_for("line_login") if LINE_CLIENT_ID else url_for("login_page")) if quota_exhausted else None
        })
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
                fp = os.path.join(PDF_DIR, fn)
                size_kb = round(os.path.getsize(fp) / 1024, 1) if os.path.exists(fp) else 0
                size_str = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb / 1024, 2)} MB"
                clean_title = re.sub(r'\.pdf$', '', fn, flags=re.I)
                clean_title = re.sub(r'[-_]?(?:revised|revision)', '', clean_title, flags=re.I).strip()
                docs.append({
                    "id": i,
                    "filename": fn,
                    "title": clean_title,
                    "category": get_pdf_category(fn),
                    "size_str": size_str,
                })
    cats = {}
    for d in docs:
        cats.setdefault(d["category"], []).append(d)
    return render_template("downloads.html", categories=cats, all_docs=docs, total_docs=len(docs))

@app.route("/download/<int:doc_id>")
@login_required
def download_file(doc_id):
    pdfs = sorted([f for f in os.listdir(PDF_DIR) if f.endswith(".pdf")]) if os.path.exists(PDF_DIR) else []
    if doc_id < 1 or doc_id > len(pdfs):
        return "File not found", 404
    as_attachment = (request.args.get("view") != "1")
    return send_from_directory(PDF_DIR, pdfs[doc_id - 1], as_attachment=as_attachment)

@app.route("/api/documents/search", methods=["GET", "POST"])
@login_required
def api_documents_search():
    keyword = ""
    if request.is_json:
        keyword = (request.get_json(silent=True) or {}).get("keyword", "").strip().lower()
    else:
        keyword = request.args.get("q", "").strip().lower()

    docs = []
    if os.path.exists(PDF_DIR):
        for i, fn in enumerate(sorted(os.listdir(PDF_DIR)), 1):
            if fn.endswith(".pdf"):
                cat = get_pdf_category(fn)
                clean_title = re.sub(r'\.pdf$', '', fn, flags=re.I)
                clean_title = re.sub(r'[-_]?(?:revised|revision)', '', clean_title, flags=re.I).strip()
                if not keyword or keyword in fn.lower() or keyword in cat.lower() or keyword in clean_title.lower():
                    fp = os.path.join(PDF_DIR, fn)
                    size_kb = round(os.path.getsize(fp) / 1024, 1) if os.path.exists(fp) else 0
                    size_str = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb / 1024, 2)} MB"
                    docs.append({
                        "id": i,
                        "filename": fn,
                        "title": clean_title,
                        "category": cat,
                        "size": size_str,
                    })
    return jsonify(docs)

@app.route("/api/documents")
@login_required
def api_documents():
    global _DOCUMENTS_CACHE
    if _DOCUMENTS_CACHE is not None:
        return jsonify(_DOCUMENTS_CACHE)
    docs = []
    if os.path.exists(PDF_DIR):
        for fn in sorted(os.listdir(PDF_DIR)):
            if fn.endswith(".pdf"):
                docs.append({"id": len(docs)+1, "name": fn, "category": get_pdf_category(fn)})
    _DOCUMENTS_CACHE = docs
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
    invalidate_stats_cache()
    return jsonify({"ok": True})

# ──────────────────────────────────────────
# Admin Routes
# ──────────────────────────────────────────
@app.route("/api/admin/chatlogs/<int:log_id>/debug")
@admin_required
def api_chatlog_debug(log_id):
    log = ChatLog.query.get_or_404(log_id)
    if log.debug_info:
        try:
            data = json.loads(log.debug_info)
            data["log_id"] = log.id
            data["user_message"] = log.user_message
            data["bot_answer"] = log.bot_answer
            data["timestamp"] = to_thai_time(log.timestamp)
            data["is_guest"] = bool(log.is_guest)
            data["session_id"] = log.session_id or "-"
            return jsonify(data)
        except Exception as e:
            print(f"[Debug Parse Error] {e}")

    # Log เก่าที่ยังไม่มี debug_info: วิเคราะห์ย้อนหลังสด
    reconstructed = generate_chat_debug_analysis(log.user_message, log.bot_answer)
    reconstructed["log_id"] = log.id
    reconstructed["user_message"] = log.user_message
    reconstructed["bot_answer"] = log.bot_answer
    reconstructed["timestamp"] = to_thai_time(log.timestamp)
    reconstructed["is_guest"] = bool(log.is_guest)
    reconstructed["session_id"] = log.session_id or "-"
    reconstructed["is_reconstructed"] = True
    return jsonify(reconstructed)

@app.route("/api/admin/debug/inspect", methods=["POST"])
@admin_required
def api_debug_inspect():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "กรุณาระบุคำถามที่ต้องการตรวจสอบ"}), 400

    run_llm = bool(data.get("run_llm", False))
    if run_llm:
        bot_text, files, links, debug_data = ask_gemini(question, return_debug=True)
        debug_data["user_message"] = question
        debug_data["bot_answer"] = bot_text
        debug_data["is_live_test"] = True
        return jsonify(debug_data)
    else:
        debug_data = generate_chat_debug_analysis(question, bot_answer=None)
        debug_data["user_message"] = question
        debug_data["bot_answer"] = "(โหมดพรีวิวการค้นหา: ไม่ได้เรียก Gemini API เพื่อประหยัดโควตา)"
        debug_data["is_live_test"] = True
        return jsonify(debug_data)

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
    if os.getenv("RENDER") or os.getenv("DYNO"):
        return request.remote_addr or "unknown"
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[-1].strip() if fwd else (request.remote_addr or "unknown")

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
    global _STATS_CACHE
    now_ts = time.time()
    if _STATS_CACHE["data"] and (now_ts - _STATS_CACHE["ts"] < _STATS_CACHE_TTL):
        return jsonify(_STATS_CACHE["data"])

    try:
        from sqlalchemy import func, case
        from datetime import timedelta
        from collections import Counter
        thai_now = datetime.utcnow() + timedelta(hours=7)
        thai_today = thai_now.date()
        today_start_utc = datetime(thai_today.year, thai_today.month, thai_today.day) - timedelta(hours=7)
        seven_days_start_utc = today_start_utc - timedelta(days=6)

        # 1. ยุบรวมการนับจำนวนแชท สถิติวันนี้ feedback และ session ทั้งหมดใน 1 คิวรี
        stats_agg = db.session.query(
            func.count(ChatLog.id).label("total_chats"),
            func.count(case((ChatLog.is_guest == False, ChatLog.id))).label("student_chats"),
            func.count(case((ChatLog.is_guest == True, ChatLog.id))).label("guest_chats"),
            func.count(case((ChatLog.timestamp >= today_start_utc, ChatLog.id))).label("today_chats"),
            func.count(case(((ChatLog.timestamp >= today_start_utc) & (ChatLog.is_guest == False), ChatLog.id))).label("today_student_chats"),
            func.count(case(((ChatLog.timestamp >= today_start_utc) & (ChatLog.is_guest == True), ChatLog.id))).label("today_guest_chats"),
            func.count(case(((ChatLog.is_guest == False) & (ChatLog.feedback.isnot(None)), ChatLog.id))).label("total_fb"),
            func.count(case(((ChatLog.is_guest == False) & (ChatLog.feedback == 1), ChatLog.id))).label("positive_fb"),
            func.count(func.distinct(ChatLog.session_id)).label("total_sessions")
        ).first()

        total_chats         = stats_agg.total_chats if stats_agg else 0
        student_chats       = stats_agg.student_chats if stats_agg else 0
        guest_chats         = stats_agg.guest_chats if stats_agg else 0
        today_chats         = stats_agg.today_chats if stats_agg else 0
        today_student_chats = stats_agg.today_student_chats if stats_agg else 0
        today_guest_chats   = stats_agg.today_guest_chats if stats_agg else 0
        total_fb            = stats_agg.total_fb if stats_agg else 0
        positive            = stats_agg.positive_fb if stats_agg else 0
        total_sessions      = stats_agg.total_sessions if stats_agg else 0
        satisfaction        = round(positive / total_fb * 100, 1) if total_fb > 0 else None

        # 2. ยุบรวมกราฟย้อนหลัง 7 วัน ดึง timestamp ครั้งเดียวแล้วนับใน Python
        recent_ts = db.session.query(ChatLog.timestamp).filter(ChatLog.timestamp >= seven_days_start_utc).all()
        daily_counts = Counter((t[0] + timedelta(hours=7)).strftime("%d/%m") for t in recent_ts if t[0])
        daily = []
        for i in range(6, -1, -1):
            d = thai_today - timedelta(days=i)
            d_str = d.strftime("%d/%m")
            daily.append({"date": d_str, "count": daily_counts.get(d_str, 0)})

        # 3. คำถามยอดนิยม (จำกัด 500 แถวล่าสุด)
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
            return text[:90]

        student_rows = ChatLog.query.with_entities(ChatLog.user_message)\
            .filter_by(is_guest=False)\
            .order_by(ChatLog.id.desc()).limit(500).all()
        if not student_rows:
            student_rows = ChatLog.query.with_entities(ChatLog.user_message)\
                .order_by(ChatLog.id.desc()).limit(500).all()
        top_questions = Counter([normalize_q(r.user_message) for r in student_rows]).most_common(15)

        # 4. ประวัติ Scrape (ดึง 5 รายการแล้วใช้ index 0 เป็นตัวล่าสุด)
        scrape_logs  = ScrapeLog.query.order_by(ScrapeLog.started_at.desc()).limit(5).all()
        last_scrape  = scrape_logs[0] if scrape_logs else None

        # 5. จำนวนหน้าที่ดึงแล้ว และจำนวนลิงก์ที่ยังไม่ได้เปิดใช้งาน
        scraped_pages_count = ScrapedPage.query.count()
        unmapped_links_count = DiscoveredLink.query.filter_by(is_system_mapped=False, is_ignored=False).count()

        result = {
            "total_chats":       total_chats,
            "student_chats":     student_chats,
            "guest_chats":       guest_chats,
            "today_chats":       today_chats,
            "today_student_chats": today_student_chats,
            "today_guest_chats":   today_guest_chats,
            "feedback_positive": positive,
            "scraped_pages":     scraped_pages_count,
            "unmapped_links_count": unmapped_links_count,
            "total_sessions":    total_sessions,
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
            "guest_mode": (get_setting("guest_mode", "true") == "true"),
            "faq_enabled": (get_setting("faq_enabled", "true") == "true"),
        }
        _STATS_CACHE = {"data": result, "ts": now_ts}
        return jsonify(result)
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
    logs_data = []
    for l in logs.items:
        conf = None
        if l.debug_info:
            try:
                m = re.search(r'"confidence_score":\s*(\d+)', l.debug_info)
                if m:
                    conf = int(m.group(1))
            except Exception:
                pass
        logs_data.append({
            "id":           l.id,
            "user_message": l.user_message,
            "bot_answer":   l.bot_answer[:200] + "..." if len(l.bot_answer) > 200 else l.bot_answer,
            "raw_answer":   l.bot_answer,
            "timestamp":    to_thai_time(l.timestamp),
            "session_id":   l.session_id or "-",
            "is_guest":     bool(getattr(l, "is_guest", False)),
            "has_debug":    bool(getattr(l, "debug_info", None)),
            "feedback":     l.feedback,
            "confidence":   conf,
        })
    return jsonify({
        "logs": logs_data,
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
    writer.writerow(["ID", "Timestamp", "Session ID", "User Type", "User Message", "Bot Answer"])
    for l in logs:
        user_type = "Guest" if getattr(l, "is_guest", False) else "LINE User"
        writer.writerow([l.id, to_thai_time(l.timestamp),
                         l.session_id or "", user_type, l.user_message, l.bot_answer])
    output.seek(0)
    from flask import Response
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=chat_logs.csv"}
    )
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

@app.route("/api/admin/settings/guest_mode", methods=["POST"])
@admin_required
def api_toggle_guest_mode():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", False))
    set_setting("guest_mode", "true" if enabled else "false")
    return jsonify({
        "success": True,
        "enabled": enabled,
        "message": "เปิดโหมดทดลองใช้งานแล้ว" if enabled else "ปิดโหมดทดลองใช้งานแล้ว"
    })

@app.route("/api/admin/settings/faq_mode", methods=["POST"])
@admin_required
def api_toggle_faq_mode():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    set_setting("faq_enabled", "true" if enabled else "false")
    return jsonify({
        "success": True,
        "enabled": enabled,
        "message": "เปิดใช้งานคลังข้อมูล FAQ ในระบบ AI แล้ว" if enabled else "ปิดการใช้งานคลังข้อมูล FAQ ชั่วคราวแล้ว"
    })

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

@app.route("/api/admin/knowledge/export", methods=["GET"])
@admin_required
def export_knowledge():
    pages = ScrapedPage.query.order_by(ScrapedPage.id.asc()).all()
    payload = {
        "exported_at": datetime.utcnow().isoformat(),
        "count": len(pages),
        "pages": [{
            "url": p.url,
            "category": p.category or "auto",
            "content": p.content or "",
            "last_scraped": p.last_scraped.isoformat() if p.last_scraped else None
        } for p in pages]
    }
    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"kusrc_knowledge_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        json_bytes,
        mimetype="application/json",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": "application/json; charset=utf-8"
        }
    )

@app.route("/api/admin/knowledge/import", methods=["POST"])
@admin_required
def import_knowledge():
    pages_data = []
    if "file" in request.files:
        uploaded_file = request.files["file"]
        if not uploaded_file or uploaded_file.filename == "":
            return jsonify({"success": False, "error": "กรุณาเลือกไฟล์ JSON สำหรับนำเข้า"}), 400
        try:
            raw_data = json.load(uploaded_file)
            if isinstance(raw_data, dict) and "pages" in raw_data:
                pages_data = raw_data["pages"]
            elif isinstance(raw_data, list):
                pages_data = raw_data
            else:
                return jsonify({"success": False, "error": "รูปแบบไฟล์ JSON ไม่ถูกต้อง"}), 400
        except Exception as e:
            return jsonify({"success": False, "error": f"อ่านไฟล์ JSON ไม่สำเร็จ: {str(e)}"}), 400
    else:
        req_json = request.get_json(silent=True) or {}
        if req_json.get("use_default_seed"):
            seed_path = os.path.join(os.path.dirname(__file__), "static", "seed_scraped_pages.json")
            if not os.path.exists(seed_path):
                return jsonify({"success": False, "error": "ไม่พบไฟล์ seed_scraped_pages.json ในระบบ"}), 404
            with open(seed_path, "r", encoding="utf-8") as f:
                pages_data = json.load(f)
        elif "pages" in req_json:
            pages_data = req_json["pages"]
        elif isinstance(req_json, list):
            pages_data = req_json
        else:
            return jsonify({"success": False, "error": "ไม่พบข้อมูลสำหรับนำเข้า"}), 400

    if not isinstance(pages_data, list) or len(pages_data) == 0:
        return jsonify({"success": False, "error": "ไม่พบรายการหน้าเว็บในข้อมูลที่ส่งมา"}), 400

    imported = 0
    updated = 0
    for item in pages_data:
        url = item.get("url") or item.get("source")
        if not url:
            continue
        content = item.get("content", "")
        category = item.get("category", "auto")
        existing = ScrapedPage.query.filter_by(url=url).first()
        if existing:
            existing.content = content
            existing.category = category
            existing.last_scraped = datetime.utcnow()
            updated += 1
        else:
            db.session.add(ScrapedPage(
                url=url,
                category=category,
                content=content,
                last_scraped=datetime.utcnow()
            ))
            imported += 1

    try:
        db.session.commit()
        total_affected = imported + updated
        log = ScrapeLog(
            trigger="import_backup",
            pages=total_affected,
            status="done",
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow()
        )
        db.session.add(log)
        db.session.commit()

        # ซิงก์เข้า memory context และลิงก์ทันที
        load_context_from_db()
        sync_discovered_links_from_db()

        return jsonify({
            "success": True,
            "message": f"นำเข้าข้อมูลสำเร็จ: เพิ่มใหม่ {imported} หน้า, อัปเดต {updated} หน้า (รวม {total_affected} หน้า)",
            "imported": imported,
            "updated": updated,
            "total": total_affected
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": f"เกิดข้อผิดพลาดในการบันทึก: {str(e)}"}), 500

@app.route("/api/admin/discovered-links")
@admin_required
def api_admin_discovered_links():
    summary_only = bool(request.args.get("summary_only", 0, type=int))
    if summary_only:
        from sqlalchemy import case, func
        row = db.session.query(
            func.count(case((DiscoveredLink.is_ignored == False, DiscoveredLink.id))).label("total"),
            func.count(case(((DiscoveredLink.is_system_mapped == False) & (DiscoveredLink.is_ignored == False), DiscoveredLink.id))).label("unmapped_count"),
            func.count(case(((DiscoveredLink.is_system_mapped == True) & (DiscoveredLink.is_ignored == False), DiscoveredLink.id))).label("mapped_count"),
            func.count(case((DiscoveredLink.is_ignored == True, DiscoveredLink.id))).label("ignored_count")
        ).first()
        return jsonify({
            "total": row.total if row else 0,
            "unmapped_count": row.unmapped_count if row else 0,
            "mapped_count": row.mapped_count if row else 0,
            "ignored_count": row.ignored_count if row else 0,
            "links": []
        })

    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "all")

    query = DiscoveredLink.query
    if search:
        query = query.filter(
            (DiscoveredLink.url.ilike(f"%{search}%")) |
            (DiscoveredLink.title.ilike(f"%{search}%")) |
            (DiscoveredLink.source_page.ilike(f"%{search}%"))
        )

    if status_filter == "unmapped":
        query = query.filter(DiscoveredLink.is_system_mapped == False, DiscoveredLink.is_ignored == False)
    elif status_filter == "mapped":
        query = query.filter(DiscoveredLink.is_system_mapped == True, DiscoveredLink.is_ignored == False)
    elif status_filter == "ignored":
        query = query.filter(DiscoveredLink.is_ignored == True)
    elif status_filter == "active":
        query = query.filter(DiscoveredLink.is_ignored == False)
    # "all": คืนค่าทุกรายการเพื่อให้ฝั่ง Frontend แคชและสลับแท็บได้ทันทีโดยไม่ต้องโหลดใหม่

    links = query.order_by(
        DiscoveredLink.is_ignored.asc(),
        DiscoveredLink.is_system_mapped.asc(),
        DiscoveredLink.mapped_at.desc(),
        DiscoveredLink.last_seen.desc()
    ).all()

    if status_filter == "all" and not search:
        total = sum(1 for l in links if not l.is_ignored)
        unmapped_count = sum(1 for l in links if not l.is_system_mapped and not l.is_ignored)
        mapped_count = sum(1 for l in links if l.is_system_mapped and not l.is_ignored)
        ignored_count = sum(1 for l in links if l.is_ignored)
    else:
        from sqlalchemy import case, func
        row = db.session.query(
            func.count(case((DiscoveredLink.is_ignored == False, DiscoveredLink.id))).label("total"),
            func.count(case(((DiscoveredLink.is_system_mapped == False) & (DiscoveredLink.is_ignored == False), DiscoveredLink.id))).label("unmapped_count"),
            func.count(case(((DiscoveredLink.is_system_mapped == True) & (DiscoveredLink.is_ignored == False), DiscoveredLink.id))).label("mapped_count"),
            func.count(case((DiscoveredLink.is_ignored == True, DiscoveredLink.id))).label("ignored_count")
        ).first()
        total = row.total if row else 0
        unmapped_count = row.unmapped_count if row else 0
        mapped_count = row.mapped_count if row else 0
        ignored_count = row.ignored_count if row else 0

    return jsonify({
        "total": total,
        "unmapped_count": unmapped_count,
        "mapped_count": mapped_count,
        "ignored_count": ignored_count,
        "links": [
            {
                "id": l.id,
                "url": l.url,
                "url_decoded": unquote(l.url or ""),
                "title": l.title,
                "source_page": l.source_page,
                "source_page_decoded": unquote(l.source_page or "") if l.source_page else "",
                "category": l.category,
                "link_type": getattr(l, "link_type", "website") or ("document" if any(l.url.lower().endswith(ext) for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx")) else "website"),
                "is_system_mapped": bool(l.is_system_mapped),
                "is_ignored": bool(getattr(l, "is_ignored", False)),
                "mapped_at": to_thai_time(l.mapped_at) if getattr(l, "mapped_at", None) else "-",
                "last_seen": to_thai_time(l.last_seen).split(" ")[0] if l.last_seen else "-"
            }
            for l in links
        ]
    })

@app.route("/api/admin/discovered-links/<int:link_id>", methods=["PUT"])
@admin_required
def api_admin_update_discovered_link(link_id):
    link = db.session.get(DiscoveredLink, link_id)
    if not link:
        return jsonify({"error": "ไม่พบข้อมูลลิงก์ที่ต้องการแก้ไข"}), 404
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    url = data.get("url", "").strip()
    link_type = data.get("link_type", "website")
    is_mapped = data.get("is_system_mapped", True)
    is_ignored = data.get("is_ignored", False)

    if title:
        link.title = title
    if url:
        link.url = url
    if link_type in ("website", "document"):
        link.link_type = link_type

    new_mapped = bool(is_mapped)
    if new_mapped:
        if not link.is_system_mapped or not getattr(link, "mapped_at", None):
            link.mapped_at = datetime.utcnow()
    else:
        link.mapped_at = None
    link.is_system_mapped = new_mapped
    link.is_ignored = bool(is_ignored)

    db.session.commit()
    invalidate_links_cache()
    return jsonify({
        "success": True,
        "message": "บันทึกข้อมูลเรียบร้อยแล้ว",
        "link": {
            "id": link.id,
            "title": link.title,
            "url": link.url,
            "url_decoded": unquote(link.url or ""),
            "link_type": link.link_type,
            "is_system_mapped": link.is_system_mapped,
            "is_ignored": link.is_ignored,
            "mapped_at": to_thai_time(link.mapped_at) if link.mapped_at else "-"
        }
    })

@app.route("/api/admin/discovered-links/<int:link_id>/ignore", methods=["POST"])
@admin_required
def api_admin_ignore_discovered_link(link_id):
    link = db.session.get(DiscoveredLink, link_id)
    if not link:
        return jsonify({"error": "ไม่พบข้อมูลลิงก์"}), 404
    data = request.get_json(silent=True) or {}
    if "ignored" in data:
        link.is_ignored = bool(data["ignored"])
    else:
        link.is_ignored = not bool(link.is_ignored)

    db.session.commit()
    invalidate_links_cache()
    return jsonify({
        "success": True,
        "is_ignored": link.is_ignored,
        "message": "ซ่อนลิงก์เรียบร้อยแล้ว" if link.is_ignored else "ยกเลิกการซ่อนเรียบร้อยแล้ว"
    })

@app.route("/api/admin/discovered-links/bulk", methods=["POST"])
@admin_required
def api_admin_bulk_discovered_links():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    action = data.get("action", "")

    if not ids or not isinstance(ids, list):
        return jsonify({"error": "กรุณาเลือกอย่างน้อย 1 รายการ"}), 400

    links = DiscoveredLink.query.filter(DiscoveredLink.id.in_(ids)).all()
    count = len(links)

    if action == "map":
        for l in links:
            l.is_system_mapped = True
            l.is_ignored = False
            if not getattr(l, 'mapped_at', None):
                l.mapped_at = datetime.utcnow()
        msg = f"เปิดใช้งาน {count} รายการสำเร็จ"
    elif action == "ignore":
        for l in links:
            l.is_ignored = True
        msg = f"ซ่อน {count} รายการสำเร็จ"
    elif action == "unignore":
        for l in links:
            l.is_ignored = False
        msg = f"ยกเลิกการซ่อน {count} รายการสำเร็จ"
    else:
        return jsonify({"error": "Action ไม่ถูกต้อง"}), 400

    db.session.commit()
    invalidate_links_cache()
    return jsonify({"success": True, "count": count, "message": msg})

@app.route("/api/admin/discovered-links/resync", methods=["POST"])
@admin_required
def api_admin_discovered_links_resync():
    total, new_count = sync_discovered_links_from_db()
    return jsonify({
        "success": True,
        "total": total,
        "new": new_count,
        "message": f"สแกนสำเร็จ พบลิงก์ทั้งหมด {total} รายการ (ลิงก์ใหม่ {new_count} รายการ)"
    })

@app.route("/api/admin/discovered-links/auto-verify", methods=["POST"])
@admin_required
def api_admin_discovered_links_auto_verify():
    """
    Smart Auto-Verification สำหรับ Discovered Links:
    1. ตรวจสอบสถานะการเชื่อมต่อ (Alive/HTTP Status) แบบ Multi-threaded
    2. คัดกรองและเปิดใช้งาน (Auto-Activate) เฉพาะบริการและเอกสาร มก. ที่จำเป็นและใช้งานได้จริง
    3. ซ่อนลิงก์เสียถาวร (HTTP 404, 410 หรือ URL สะกดผิด/ไฟล์วิดีโอ) โดยอัตโนมัติ
    """
    try:
        from concurrent.futures import ThreadPoolExecutor

        unmapped_links = DiscoveredLink.query.filter_by(is_system_mapped=False, is_ignored=False).all()
        if not unmapped_links:
            return jsonify({
                "success": True,
                "total_checked": 0,
                "activated_count": 0,
                "dead_count": 0,
                "skipped_count": 0,
                "message": "ไม่มีลิงก์ที่รอการตรวจสอบ (ทุกลิงก์ได้รับการจัดสถานะเรียบร้อยแล้ว)"
            })

        def is_essential_link(title, url):
            title = (title or "").lower()
            url = (url or "").lower()
            # ข้ามลิงก์ที่ไม่พึงประสงค์
            if any(bad in url for bad in ("login_admin", "login_c.php", "wp-admin", "wp-login", ".mp4", ".avi", ".mov")):
                return False
            if "admissionssrc.ku.ac.th" in url:
                return False
            # รับสมัคร / TCAS
            if any(k in url or k in title for k in ("tcas", "criteria", "announce", "เกณฑ์การรับสมัคร", "โครงการขยายโอกาส", "รับตรง")):
                return True
            # ทะเบียน & คำร้อง & เอกสารสำคัญ
            if any(k in url or k in title for k in ("curriculum", "download.php", "rules", "help_pay", "help_reg", "ข้อบังคับ", "คำร้อง", "ผ่อนผัน", "กยศ", "ปฏิทิน", "table_test", "ตารางคุมสอบ", "กู้ยืม")):
                return True
            # หลักสูตรสาขาวิชา มก.
            if "listcur.aspx" in url and any(k in title for k in ("วิทยาการคอมพิวเตอร์", "เทคโนโลยีสารสนเทศ", "ดิจิทัล", "เคมี", "ฟิสิกส์", "คณิตศาสตร์", "สิ่งแวดล้อม", "วิศวกรรม")):
                return True
            # บริการกลาง
            if any(h in url for h in ("dorms.src.ku.ac.th", "accounts.ku.ac.th", "isea.ku.ac.th", "verify.src.ku.ac.th", "my.ku.th", "nisit.ku.ac.th")):
                return True
            # เอกสาร PDF ระเบียบ/คู่มือ
            if url.endswith(".pdf") and any(k in url or k in title for k in ("ระเบียบ", "ข้อบังคับ", "tcas", "help", "คู่มือ", "คำร้อง", "ประกาศ")):
                return True
            return False

        def check_health(link_obj):
            url = link_obj.url
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            try:
                r = requests.head(url, timeout=3.5, headers=headers, allow_redirects=True)
                if r.status_code == 405:
                    r = requests.get(url, timeout=3.5, headers=headers, stream=True)
                is_alive = (r.status_code < 400)
                return link_obj.id, is_alive, r.status_code
            except Exception:
                return link_obj.id, False, 0

        with ThreadPoolExecutor(max_workers=15) as executor:
            check_results = list(executor.map(check_health, unmapped_links))

        status_map = {cid: (alive, code) for cid, alive, code in check_results}

        activated_count = 0
        dead_count = 0
        skipped_count = 0
        now = datetime.utcnow()

        for l in unmapped_links:
            alive, code = status_map.get(l.id, (False, 0))
            essential = is_essential_link(l.title, l.url)

            # 1. จำเป็น + เข้าถึงได้ -> Auto-Activate
            if essential and alive:
                l.is_system_mapped = True
                l.is_ignored = False
                l.mapped_at = now
                activated_count += 1
            # 2. ลิงก์เสียถาวร (HTTP 404, 410 หรือโดเมนพิมพ์ผิด/ไฟล์วิดีโอ) -> Auto-Hide
            elif code in (404, 410) or "admissionssrc.ku.ac.th" in l.url or any(l.url.lower().endswith(ext) for ext in (".mp4", ".avi", ".mov")):
                l.is_ignored = True
                dead_count += 1
            else:
                skipped_count += 1

        db.session.commit()
        invalidate_links_cache()

        return jsonify({
            "success": True,
            "total_checked": len(unmapped_links),
            "activated_count": activated_count,
            "dead_count": dead_count,
            "skipped_count": skipped_count,
            "message": f"ตรวจสอบสำเร็จ: เปิดใช้งานบริการจำเป็น {activated_count} รายการ, ซ่อนลิงก์เสีย {dead_count} รายการ, คงไว้รอพิจารณา {skipped_count} รายการ"
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"เกิดข้อผิดพลาดในการตรวจสอบ: {str(e)}"}), 500

# ──────────────────────────────────────────
# Seed Data Initialization
# ──────────────────────────────────────────
def seed_initial_scraped_pages():
    """โหลดข้อมูลหน้าเว็บเริ่มต้นจาก static/seed_scraped_pages.json เข้า DB หากฐานข้อมูลยังไม่มีหรือว่างเปล่า"""
    seed_path = os.path.join(os.path.dirname(__file__), "static", "seed_scraped_pages.json")
    if not os.path.exists(seed_path):
        return 0
    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            pages = json.load(f)
        added = 0
        existing_urls = {p.url for p in ScrapedPage.query.with_entities(ScrapedPage.url).all()}
        for item in pages:
            url = item.get("source") or item.get("url")
            if not url or url in existing_urls:
                continue
            db.session.add(ScrapedPage(
                url=url,
                category=item.get("category", "auto"),
                content=item.get("content", ""),
                last_scraped=datetime.utcnow()
            ))
            existing_urls.add(url)
            added += 1
        if added > 0:
            db.session.commit()
            print(f"[Seed] โหลดหน้าเว็บเริ่มต้น {added} หน้าเข้า DB เรียบร้อย")
            if ScrapeLog.query.count() == 0:
                log = ScrapeLog(
                    trigger="seed",
                    pages=added,
                    status="done",
                    started_at=datetime.utcnow(),
                    finished_at=datetime.utcnow()
                )
                db.session.add(log)
                db.session.commit()
        return added
    except Exception as e:
        print(f"[Seed Error] ไม่สามารถโหลด seed scraped pages ได้: {e}")
        db.session.rollback()
        return 0


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
            if "debug_info" not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE chat_log ADD COLUMN debug_info TEXT"))
                    conn.commit()
                print("[Migration] Added column: debug_info")
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

        try:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_chat_log_timestamp ON chat_log(timestamp)"))
                conn.commit()
            print("[Migration] Index on chat_log(timestamp) verified")
        except Exception as e:
            print(f"[Migration] idx_chat_log_timestamp Error: {e}")

        try:
            from sqlalchemy import text, inspect
            inspector = inspect(db.engine)
            if 'chat_log' in inspector.get_table_names():
                cl_columns = [col['name'] for col in inspector.get_columns('chat_log')]
                with db.engine.connect() as conn:
                    if "is_guest" not in cl_columns:
                        conn.execute(text("ALTER TABLE chat_log ADD COLUMN is_guest BOOLEAN DEFAULT FALSE"))
                        conn.execute(text("UPDATE chat_log SET is_guest = TRUE WHERE user_id IS NULL AND (session_id LIKE 'guest_%' OR session_id = 'anonymous')"))
                        conn.commit()
                        print("[Migration] Added column: is_guest to chat_log and backfilled")
        except Exception as e:
            print(f"[Migration] chat_log is_guest Error: {e}")

        try:
            from sqlalchemy import text, inspect
            inspector = inspect(db.engine)
            if 'discovered_links' in inspector.get_table_names():
                dl_columns = [col['name'] for col in inspector.get_columns('discovered_links')]
                with db.engine.connect() as conn:
                    if "is_ignored" not in dl_columns:
                        conn.execute(text("ALTER TABLE discovered_links ADD COLUMN is_ignored BOOLEAN DEFAULT FALSE"))
                        print("[Migration] Added column: is_ignored to discovered_links")
                    if "link_type" not in dl_columns:
                        conn.execute(text("ALTER TABLE discovered_links ADD COLUMN link_type VARCHAR(50) DEFAULT 'website'"))
                        print("[Migration] Added column: link_type to discovered_links")
                    if "mapped_at" not in dl_columns:
                        conn.execute(text("ALTER TABLE discovered_links ADD COLUMN mapped_at TIMESTAMP"))
                        print("[Migration] Added column: mapped_at to discovered_links")
                    conn.commit()
        except Exception as e:
            print(f"[Migration] discovered_links columns Error: {e}")

        print(f"[PDF] Found {len(ALL_PDFS)} files")
        count = ScrapedPage.query.count()
        if count == 0:
            print("[Startup] DB ว่าง → โหลด Seed Data หน้าเว็บเริ่มต้น...")
            seed_count = seed_initial_scraped_pages()
            load_context_from_db()
            print(f"[Startup] โหลด Seed Data สำเร็จ ({seed_count} หน้า) → พร้อมตอบคำถามทันที")
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
            # โหลด seed เพิ่มเติมเฉพาะหน้าที่ยังไม่มีใน DB
            seed_initial_scraped_pages()
            load_context_from_db()

        # migrate FAQ จาก Excel → DB (ครั้งแรกเท่านั้น) แล้วโหลดจาก DB
        migrate_faq_from_excel()
        load_faq_from_excel()
        sync_discovered_links_from_db()
    start_scheduler()

def load_curriculum_pdfs():
    """โหลด curriculum จาก .txt (OCR) ใน static/docs/curriculum/txt/ และ PDF ที่อ่านได้ใน static/docs/curriculum/pdf/"""
    global CHAT_CONTEXT
    chunk_size = 8000
    count = 0

    # โหลดจาก txt ที่ OCR ไว้แล้วก่อน
    txt_dir = os.path.join(os.path.dirname(__file__), "static", "docs", "curriculum", "txt")
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
    pdf_dir = os.path.join(os.path.dirname(__file__), "static", "docs", "curriculum", "pdf")
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
