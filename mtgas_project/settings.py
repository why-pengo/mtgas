"""
Django settings for mtgas_project.

Environment variables (all optional — sensible defaults for local development):
  DJANGO_SECRET_KEY    — secret key (insecure default used when not set; MUST be set in production)
  DJANGO_DEBUG         — "True" / "False" (default: "True" for local dev; set "False" in production)
  DJANGO_ALLOWED_HOSTS — comma-separated extra hosts (e.g. "example.com,api.example.com")
  POSTGRES_DB          — PostgreSQL database name; when set, enables PostgreSQL instead of SQLite
  POSTGRES_USER        — PostgreSQL user (default: "mtgas")
  POSTGRES_PASSWORD    — PostgreSQL password
  POSTGRES_HOST        — PostgreSQL host (default: "localhost")
  POSTGRES_PORT        — PostgreSQL port (default: "5432")
  TIME_ZONE            — Django timezone (default: "America/New_York")
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "django-insecure-mtgas-dev-key-change-in-production"
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() not in ("false", "0", "no")

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
_extra_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "")
if _extra_hosts:
    ALLOWED_HOSTS += [h.strip() for h in _extra_hosts.split(",") if h.strip()]

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "stats",  # Our main app
    "cards",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "mtgas_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "stats" / "templates", BASE_DIR / "docs"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "mtgas_project.wsgi.application"

# Database
# When POSTGRES_DB is set, use PostgreSQL. Otherwise, fall back to SQLite (default for local dev).
_postgres_db = os.environ.get("POSTGRES_DB")
if _postgres_db:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _postgres_db,
            "USER": os.environ.get("POSTGRES_USER", "mtgas"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "data" / "mtga_stats.db",
        }
    }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "America/New_York")
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / "stats" / "static",
    BASE_DIR / "data" / "cache",  # Serve cached card images
]

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Security settings ---
# Always set (safe defaults even in development).
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

# Production-only hardening — activated automatically when DEBUG=False.
# When running behind HTTPS (e.g. with a reverse proxy), also set:
#   SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# MTG Arena specific settings
MTGA_LOG_PATH = None  # Set via environment or command line
SCRYFALL_CACHE_DIR = BASE_DIR / "data" / "cache"

# --- Media files (local filesystem) ---
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")
