import base64
import hashlib
import importlib.util
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_csv(name: str, default: str = '') -> list[str]:
    return [value.strip() for value in os.getenv(name, default).split(',') if value.strip()]

SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-me')
DEBUG = _env_bool('DEBUG', True)
APP_BASE_URL = os.getenv('APP_BASE_URL', '').strip()
ALLOWED_HOSTS = _env_csv('ALLOWED_HOSTS', '127.0.0.1,localhost')
_app_base_url = urlparse(APP_BASE_URL) if APP_BASE_URL else None
if _app_base_url and _app_base_url.hostname and _app_base_url.hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_app_base_url.hostname)

CSRF_TRUSTED_ORIGINS = _env_csv('CSRF_TRUSTED_ORIGINS')
if _app_base_url and _app_base_url.scheme and _app_base_url.netloc:
    _app_origin = f'{_app_base_url.scheme}://{_app_base_url.netloc}'
    if _app_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_app_origin)

USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True
if _app_base_url and _app_base_url.scheme == 'https':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

if _app_base_url and _app_base_url.scheme in {'http', 'https'}:
    ACCOUNT_DEFAULT_HTTP_PROTOCOL = _app_base_url.scheme
else:
    ACCOUNT_DEFAULT_HTTP_PROTOCOL = 'http'

_SOCIAL_PROVIDER_APPS = [
    'allauth.socialaccount.providers.microsoft',
    'allauth.socialaccount.providers.github',
]
if importlib.util.find_spec('allauth.socialaccount.providers.asana') is not None:
    _SOCIAL_PROVIDER_APPS.append('allauth.socialaccount.providers.asana')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'dashboard.apps.DashboardConfig',
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    *_SOCIAL_PROVIDER_APPS,
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'alshival.middleware.LoginRequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'alshival.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'dashboard.context_processors.sidebar_workspace_widget',
            ],
        },
    },
]

WSGI_APPLICATION = 'alshival.wsgi.application'
ASGI_APPLICATION = 'alshival.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.getenv('SQLITE_PATH', str(BASE_DIR / 'var' / 'db.sqlite3')),
    }
}
Path(DATABASES['default']['NAME']).parent.mkdir(parents=True, exist_ok=True)

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'dashboard' / 'static']
STATIC_ROOT = Path(os.getenv('STATIC_ROOT', str(BASE_DIR / 'var' / 'staticfiles')))
STATIC_ROOT.mkdir(parents=True, exist_ok=True)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

USER_DATA_ROOT = Path(os.getenv('USER_DATA_ROOT', str(BASE_DIR / 'var' / 'user_data')))
USER_DATA_ROOT.mkdir(parents=True, exist_ok=True)
TEAM_DATA_ROOT = Path(os.getenv('TEAM_DATA_ROOT', str(BASE_DIR / 'var' / 'team_data')))
TEAM_DATA_ROOT.mkdir(parents=True, exist_ok=True)
GLOBAL_DATA_ROOT = Path(os.getenv('GLOBAL_DATA_ROOT', str(BASE_DIR / 'var' / 'global_data')))
GLOBAL_DATA_ROOT.mkdir(parents=True, exist_ok=True)

VITE_DEV_SERVER = 'http://localhost:5173'
VITE_DEV_MODE = DEBUG

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

LOGIN_REDIRECT_URL = '/'
LOGIN_URL = '/accounts/login/'
SETUP_URL = '/setup/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/accounts/login/'
ACCOUNT_EMAIL_VERIFICATION = 'none'
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS = ['email', 'username*', 'password1*', 'password2*']
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_STORE_TOKENS = True
SOCIALACCOUNT_ADAPTER = "dashboard.allauth_adapter.SocialAccountAdapter"
ALSHIVAL_INGEST_API_KEY = os.getenv('ALSHIVAL_INGEST_API_KEY', '').strip()

_ssh_keys_env = os.getenv('ALSHIVAL_SSH_KEY_MASTER_KEYS', '').strip()
if _ssh_keys_env:
    SSH_KEY_MASTER_KEYS = [key.strip() for key in _ssh_keys_env.split(',') if key.strip()]
else:
    digest = hashlib.sha256(SECRET_KEY.encode('utf-8')).digest()
    SSH_KEY_MASTER_KEYS = [base64.urlsafe_b64encode(digest).decode('utf-8')]
