"""
Transport Management System - Django Settings
Production-ready configuration for closed network deployment
"""

import os
import sys
from pathlib import Path
from decouple import config

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
# Change this to a strong random key in production
SECRET_KEY = config('SECRET_KEY', default='django-insecure-transport-mgmt-system-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
# Set to False for production deployment
DEBUG = config('DEBUG', default=True, cast=bool)

# For closed network testing - restrict in production
ALLOWED_HOSTS = ['*']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Django contrib apps
    'django.contrib.humanize',
    
    # Local apps
    'accounts',
    'trips',
    'fleet',
    'ledger',
    'drivers',
    'documents',
    'gdstorage',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.ActiveUserMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'transport_mgmt.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'documents.context_processors.document_alerts',
            ],
        },
    },
]

WSGI_APPLICATION = 'transport_mgmt.wsgi.application'

# Database Configuration - SQLite3
# Using SQLite for simplicity in closed network deployment
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Storage Backend Selection: 'r2' (Cloudflare R2), 'gdrive' (Legacy Google Drive), or 'local' (FileSystem)
STORAGE_BACKEND = config('STORAGE_BACKEND', default=None)
if STORAGE_BACKEND:
    STORAGE_BACKEND = STORAGE_BACKEND.lower()

# Cloudflare R2 Configuration (S3-Compatible Object Storage)
CLOUDFLARE_R2_ACCOUNT_ID = config('CLOUDFLARE_R2_ACCOUNT_ID', default=None)
CLOUDFLARE_R2_ACCESS_KEY_ID = config('CLOUDFLARE_R2_ACCESS_KEY_ID', default=None)
CLOUDFLARE_R2_SECRET_ACCESS_KEY = config('CLOUDFLARE_R2_SECRET_ACCESS_KEY', default=None)
CLOUDFLARE_R2_BUCKET_NAME = config('CLOUDFLARE_R2_BUCKET_NAME', default=None)
CLOUDFLARE_R2_CUSTOM_DOMAIN = config('CLOUDFLARE_R2_CUSTOM_DOMAIN', default=None)
CLOUDFLARE_R2_EXPIRATION_SECS = config('CLOUDFLARE_R2_EXPIRATION_SECS', default=3600, cast=int)

# Legacy Google Drive Storage Configuration
GOOGLE_DRIVE_STORAGE_JSON_KEY_FILE = None
GOOGLE_DRIVE_STORAGE_CLIENT_ID = config('GOOGLE_DRIVE_STORAGE_CLIENT_ID', default=None)
GOOGLE_DRIVE_STORAGE_CLIENT_SECRET = config('GOOGLE_DRIVE_STORAGE_CLIENT_SECRET', default=None)
GOOGLE_DRIVE_STORAGE_REFRESH_TOKEN = config('GOOGLE_DRIVE_STORAGE_REFRESH_TOKEN', default=None)
GOOGLE_DRIVE_STORAGE_MEDIA_ROOT = config('GOOGLE_DRIVE_STORAGE_MEDIA_ROOT', default='')

# Configure active storage backend
is_r2 = (STORAGE_BACKEND == 'r2') or (STORAGE_BACKEND is None and CLOUDFLARE_R2_ACCOUNT_ID and CLOUDFLARE_R2_ACCESS_KEY_ID)
is_gdrive = (STORAGE_BACKEND == 'gdrive') or (STORAGE_BACKEND is None and not is_r2 and GOOGLE_DRIVE_STORAGE_REFRESH_TOKEN)

# During automated test runs, always use local FileSystemStorage so tests run fast, locally,
# and never consume external cloud API calls (R2 / Google Drive) or quotas.
if 'test' in sys.argv:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
elif is_r2 and CLOUDFLARE_R2_ACCOUNT_ID and CLOUDFLARE_R2_ACCESS_KEY_ID:
    STORAGES = {
        "default": {
            "BACKEND": "transport_mgmt.storage_bridge.CloudflareR2Storage",
            "OPTIONS": {
                "access_key": CLOUDFLARE_R2_ACCESS_KEY_ID,
                "secret_key": CLOUDFLARE_R2_SECRET_ACCESS_KEY,
                "bucket_name": CLOUDFLARE_R2_BUCKET_NAME,
                "endpoint_url": f"https://{CLOUDFLARE_R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
                "custom_domain": CLOUDFLARE_R2_CUSTOM_DOMAIN or None,
                "querystring_auth": False if CLOUDFLARE_R2_CUSTOM_DOMAIN else True,
                "querystring_expire": CLOUDFLARE_R2_EXPIRATION_SECS,
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
elif is_gdrive and GOOGLE_DRIVE_STORAGE_REFRESH_TOKEN:
    STORAGES = {
        "default": {
            "BACKEND": "transport_mgmt.storage_bridge.GoogleDriveOAuth2Storage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

# Media files (File uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Security settings for production (closed network)
if not DEBUG:
    # HTTPS security headers
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    
    # Session security
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    
    # Additional security
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Login URLs
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# Google Maps API Key
GOOGLE_MAPS_API_KEY = config('GOOGLE_MAPS_API_KEY', default='')

# File upload settings
FILE_UPLOAD_MAX_MEMORY_SIZE = 10485760  # 10MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 10485760  # 10MB