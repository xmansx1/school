from pathlib import Path
import os
from dotenv import load_dotenv
import dj_database_url

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ----------------- البيئة -----------------
SECRET_KEY = os.getenv("SECRET_KEY", "unsafe-secret")
ENV = os.getenv("ENV", "development").lower()

# كشف تلقائي لـ Render
if os.getenv("RENDER", "") or os.getenv("RENDER_EXTERNAL_URL", ""):
    ENV = "production"

DEBUG = ENV == "development"

def _split_env_list(val: str) -> list[str]:
    return [x.strip() for x in (val or "").split(",") if x.strip()]

ALLOWED_HOSTS = _split_env_list(
    os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,school-7lgm.onrender.com,.onrender.com")
)

CSRF_TRUSTED_ORIGINS = _split_env_list(
    os.getenv("CSRF_TRUSTED_ORIGINS", "https://*.onrender.com,https://*.render.com,https://school-7lgm.onrender.com")
)

# ----------------- التطبيقات -----------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # طرف ثالث
    "cloudinary",
    "cloudinary_storage",

    # تطبيقاتنا
    "reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # لملفات static
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "reports.context_processors.nav_context",

                # ⬇️ استبدال المعالج القديم بالجديد المتوافق مع الأيقونة/الهيدر
                "reports.context_processors.nav_badges",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ----------------- قاعدة البيانات -----------------
if ENV == "production":
    DATABASES = {
        "default": dj_database_url.config(
            default=os.getenv("DATABASE_URL"),
            conn_max_age=600,
            ssl_require=True,
        )
    }
else:
    DB_ENGINE = os.getenv("DB_ENGINE", "django.db.backends.sqlite3")
    if "sqlite" in DB_ENGINE:
        DATABASES = {
            "default": {
                "ENGINE": DB_ENGINE,
                "NAME": os.getenv("DB_NAME", BASE_DIR / "db.sqlite3"),
            }
        }
    else:
        DATABASES = {
            "default": {
                "ENGINE": DB_ENGINE,
                "NAME": os.getenv("DB_NAME", ""),
                "USER": os.getenv("DB_USER", ""),
                "PASSWORD": os.getenv("DB_PASSWORD", ""),
                "HOST": os.getenv("DB_HOST", ""),
                "PORT": os.getenv("DB_PORT", ""),
            }
        }

# خلف Proxy (مثل Render) حافظ على HTTPS
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ----------------- كلمات المرور -----------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ----------------- اللغة والتوقيت -----------------
LANGUAGE_CODE = "ar"
TIME_ZONE = "Asia/Riyadh"
USE_I18N = True
USE_TZ = True

# ----------------- الملفات الثابتة -----------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]  # هنا يوجد img/logo.png

# WhiteNoise في الإنتاج
if ENV == "production":
    STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
    WHITENOISE_MAX_AGE = 60 * 60 * 24 * 365  # 1 سنة

# ----------------- ملفات الوسائط -----------------
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ----------------- Cloudinary (شرطي) -----------------
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
    CLOUDINARY_STORAGE = {
        "CLOUD_NAME": CLOUDINARY_CLOUD_NAME,
        "API_KEY": CLOUDINARY_API_KEY,
        "API_SECRET": CLOUDINARY_API_SECRET,
    }

# ----------------- الأمان في الإنتاج -----------------
if ENV == "production":
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))  # سنة
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"
else:
    SECURE_SSL_REDIRECT = False

# ----------------- تسجيل الأحداث (Logging) -----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# ----------------- المستخدم المخصص -----------------
AUTH_USER_MODEL = "reports.Teacher"

# توجيه افتراضي
LOGIN_URL = "reports:login"
LOGIN_REDIRECT_URL = "reports:home"
LOGOUT_REDIRECT_URL = "reports:login"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
