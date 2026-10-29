import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

# Security
SECRET_KEY = os.getenv('SECRET_KEY', os.urandom(24).hex())

# Database
DATABASE_URI = os.getenv('DATABASE_URI', 'sqlite:///science_assistant.db')

# File Upload
UPLOAD_FOLDER = 'static/pdfs'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'ppt', 'pptx'}
MAX_FILE_SIZE = 16 * 1024 * 1024  # 16MB

# Application Settings
APP_NAME = "Science Sriracha Assistant"
APP_VERSION = "2.0.0"
ADMIN_EMAIL = "admin@science.ku.th"

# Pagination
ITEMS_PER_PAGE = 20

# Session
SESSION_COOKIE_SECURE = True  # Set to True in production with HTTPS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
PERMANENT_SESSION_LIFETIME = 3600  # 1 hour

# AI Settings
AI_MODEL = "gemini-3.5-flash"
AI_MAX_TOKENS = 1000
AI_TEMPERATURE = 0.7
