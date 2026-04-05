"""
Configuration — all secrets come from environment variables.
Set these in Railway's dashboard under Variables.
"""
import os

# Database (Railway provides DATABASE_URL when you add PostgreSQL plugin)
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///restaurant.db')

# Railway's PostgreSQL URLs start with "postgres://" but SQLAlchemy needs "postgresql://"
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Fortnox OAuth2 credentials — get these from developer.fortnox.se
FORTNOX_CLIENT_ID = os.environ.get('FORTNOX_CLIENT_ID', '')
FORTNOX_CLIENT_SECRET = os.environ.get('FORTNOX_CLIENT_SECRET', '')
FORTNOX_REDIRECT_URI = os.environ.get('FORTNOX_REDIRECT_URI', 'http://localhost:5000/auth/callback')

# Fortnox tokens — stored in DB after first auth, but can also be set as env vars
FORTNOX_ACCESS_TOKEN = os.environ.get('FORTNOX_ACCESS_TOKEN', '')
FORTNOX_REFRESH_TOKEN = os.environ.get('FORTNOX_REFRESH_TOKEN', '')

# Fortnox API base URL
FORTNOX_API_BASE = 'https://api.fortnox.se/3'
FORTNOX_AUTH_URL = 'https://apps.fortnox.se/oauth-v1/auth'
FORTNOX_TOKEN_URL = 'https://apps.fortnox.se/oauth-v1/token'

# Anthropic (Claude AI) — for smart inventory parsing
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

# App settings
PORT = int(os.environ.get('PORT', 5000))
DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'
SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')
