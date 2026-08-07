import os
import sys
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

INSECURE_DEFAULT_SECRET_KEY = "django-insecure-gerayse-dev-key-change-me"
SECRET_KEY = env("DJANGO_SECRET_KEY", default=INSECURE_DEFAULT_SECRET_KEY)
# Default seguro: correr con DEBUG activo en produccion expone tracebacks y desactiva
# cookies seguras. El entorno productivo debe setear DEBUG explicitamente.
DEBUG = env.bool("DEBUG", default=False)
RUNNING_DEV_SERVER = "runserver" in sys.argv
RUNNING_TESTS = "test" in sys.argv

# En produccion no arrancar con la SECRET_KEY insegura por default: es preferible
# fallar el deploy a servir con una key publica. Se eximen los tests y el server de
# desarrollo porque no sirven trafico productivo (asi CI y dev local siguen andando
# sin .env). La guarda solo muerde al servir por WSGI/gunicorn con DEBUG=False.
if (
    not DEBUG
    and not RUNNING_TESTS
    and not RUNNING_DEV_SERVER
    and SECRET_KEY == INSECURE_DEFAULT_SECRET_KEY
):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY debe definirse con un valor propio cuando DEBUG=False."
    )

# Habilita el reinicio destructivo de datos operativos (borra TODO, sin scope de
# empresa). Es una herramienta de testing: NUNCA debe quedar activa en produccion.
# Por defecto sigue a DEBUG, asi el entorno decide y no depende de borrar codigo a mano.
ENABLE_DANGER_RESET = env.bool("ENABLE_DANGER_RESET", default=DEBUG)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "gerayse10-production.up.railway.app"])

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core',
    'users',
    'cashops',
    'treasury',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'users.middleware.ForcePasswordChangeMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.app_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


DATABASES = {
    'default': env.db(
        'DATABASE_URL',
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}
DATABASES['default']['CONN_MAX_AGE'] = 60

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

# Hasher barato SOLO cuando corre la suite. Django 5.2 usa PBKDF2 con 1.000.000 de
# iteraciones: 384 ms por hash medidos en esta maquina. Como cada setUp crea varios
# usuarios y corre una vez por test, hashear passwords se comia casi todo el tiempo
# de la suite (medido: 570 s -> 26 s, los mismos 430 tests en verde).
# PRODUCCION NO SE TOCA: RUNNING_TESTS es "test" in sys.argv (mismo flag que ya
# exime la guarda de SECRET_KEY), y gunicorn nunca corre con ese argumento.
# Se deja PBKDF2 de segundo para poder VERIFICAR hashes fuertes si algun test
# levanta datos con password ya hasheado; el primero es el que se usa al crear.
if RUNNING_TESTS:
    PASSWORD_HASHERS = [
        'django.contrib.auth.hashers.MD5PasswordHasher',
        'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    ]

LANGUAGE_CODE = 'es-ar'
TIME_ZONE = 'America/Argentina/Buenos_Aires'

USE_I18N = True
USE_TZ = True
# Separador de miles en TODO numero renderizado por templates ($ 1.234.567,89).
# OJO: tambien localiza ids/anios interpolados a mano en href/value; esos casos
# llevan |unlocalize en el template (hay test de regresion que los cubre).
# No afecta widgets de formulario, {% url %}, ni el CSV (csv.writer de Python).
USE_THOUSAND_SEPARATOR = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# Antes esto era `STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifest...'`.
# Ese setting fue REMOVIDO en Django 5.1 y el proyecto corre 5.2: Django lo ignoraba
# en silencio y caia al storage por defecto, o sea que WhiteNoise no comprimia ni
# versionaba nada aunque la linea dijera que si. La forma que Django 5 lee es STORAGES.
#
# El manifest solo se usa al servir de verdad. Con manifest, `{% static %}` exige que
# el archivo este en staticfiles.json, asi que sin `collectstatic` previo TODA pagina
# revienta en runtime. En dev y en tests eso seria una trampa (nadie corre collectstatic
# para lanzar el runserver o el suite), asi que ahi se usa el storage plano. Es el mismo
# criterio que ya venian usando WHITENOISE_USE_FINDERS/AUTOREFRESH abajo.
_SIRVE_ESTATICOS_VERSIONADOS = not (DEBUG or RUNNING_DEV_SERVER or RUNNING_TESTS)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if _SIRVE_ESTATICOS_VERSIONADOS
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

WHITENOISE_USE_FINDERS = DEBUG or RUNNING_DEV_SERVER
WHITENOISE_AUTOREFRESH = DEBUG or RUNNING_DEV_SERVER

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'users.User'

LOGIN_URL = 'users:login'
LOGIN_REDIRECT_URL = 'cashops:dashboard'
LOGOUT_REDIRECT_URL = 'users:login'

MESSAGE_TAGS = {
    10: 'debug',
    20: 'info',
    25: 'success',
    30: 'warning',
    40: 'error',
}

CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=["https://gerayse10-production.up.railway.app"])

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
