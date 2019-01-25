#-*- coding: utf-8 -*-
'''
Run this with `$ python ./mini_django.py runserver` and go
to http://localhost:8000/
'''
import hmac
import hashlib
import os
import sys
import socket
import logging
import django
from django.http import HttpResponse
from django.shortcuts import render
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from . import net_utils as tutil
except:
    sys.path.insert(0, os.path.abspath('.'))
    import net_utils as tutil

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

try:
    import appoptics_apm
    import appoptics_apm.djangoware
    appoptics_apm.config['tracing_mode'] = 'always'
    appoptics_apm.config['sample_rate'] = 1.0
    appoptics_apm.config['enable_sanitize_sql'] = False
    CONFIG_STR = 'localhost:%d' % (tutil.COLLECTOR_LISTEN_PORT)
    # surprisingly appoptics_apm.reporter_instance and
    # appoptics_apm.util.reporter_instance are not the same
    # the real instance used should only be later one
    appoptics_apm.config._config['reporter_port'] = CONFIG_STR
    appoptics_apm.config._config['collector_mode'] = "udp"
    if appoptics_apm.reporter_instance:
        del appoptics_apm.reporter_instance
        appoptics_apm.reporter_instance = None
    if appoptics_apm.util.reporter_instance:
        del appoptics_apm.util.reporter_instance
        appoptics_apm.util.reporter_instance = None

except ImportError as o_error:
    try:
        import oboe
        oboe.config['tracing_mode'] = 'always'
        oboe.config['sample_rate'] = 1.0
    except ImportError:
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger(__name__)
        logger.info('appoptics library is not there, skipped:\n'
                    '{}'.format(o_error))

#import base

# this module
ME = os.path.splitext(os.path.split(__file__)[1])[0]
# helper function to locate this directory
HERE = lambda x: os.path.join(os.path.abspath(os.path.dirname(__file__)), x)

#BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SETTINGS
DEBUG = True
ALLOWED_HOSTS = ['*']
ROOT_URLCONF = ME


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': 'mydatabase',
    },
    'mysql': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'mysql',
        'USER': 'root',
        'PASSWORD': '',
        'HOST': '127.0.0.1',
        'PORT': '3306',
    },
    'postgres': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'postgres',
        'USER': 'postgres',
        'PASSWORD': '',
        'HOST': '127.0.0.1',
        'PORT': '5432',
    }
}

TEMPLATE_DIRS = (
    HERE('.'),  # Templates in current directory
)
TEMPLATE_CONTEXT_PROCESSORS = (
    'django.core.context_processors.debug',
    'django.core.context_processors.i18n',
    'django.core.context_processors.media',
    'django.core.context_processors.static',
    'django.core.context_processors.tz',
    'django.core.context_processors.request',
)
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': TEMPLATE_DIRS,
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': (
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.template.context_processors.request',
            ),
        },
    },
]
SECRET_KEY = 'so so secret'
django_version_str = django.get_version()
django_version_value = [int(x) for x in django_version_str.split(".")]
#test for AO-103096: MIDDLEWARE_CLASSES as tuple fails for version above 1.9
if 'TEST_MIDDLEWARE_CLASSES_TUPLE' in os.environ.keys():
    MIDDLEWARE_CLASSES = (
        'django.contrib.sessions.middleware.SessionMiddleware',
    )
#regular test for agent API
else:
    if django_version_value[0] < 2 and django_version_value[1] < 10:
        MIDDLEWARE_CLASSES = (
            'django.contrib.sessions.middleware.SessionMiddleware',
        )
    else:
        MIDDLEWARE = [
            'django.contrib.sessions.middleware.SessionMiddleware',
        ]

INSTALLED_APPS = (
    'django.contrib.sessions',
)
SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'

STATIC_URL = '/static/'
STATICFILES_DIRS = (
    HERE('static'),
)

SILENCED_SYSTEM_CHECKS = ['1_8.W001']  # Silence warning for using TEMPLATE_*

if not settings.configured:
    settings.configure(**locals())

# Settings must be configured before importing some things
# from django.views.decorators.csrf import csrf_exempt


# VIEW
from django.db import connections

def index(request, name=None):
    return HttpResponse()

def test_simple(request, name = None):
    query = request.GET['query']
    with  connections['default'].cursor() as cursor:
        cursor.execute(query)
    return HttpResponse("test_simple")

def test_args(request):
    query = request.GET['query']
    value = request.GET['value']
    with  connections['default'].cursor() as cursor:
        cursor.execute(query, [value])
    return HttpResponse(test_args)

def do_transaction(db_cursor, rollback):
    query = 'SELECT 1'
    with  connections['default'].cursor() as cursor:
        cursor.execute(query)
        if rollback:
            query = 'ROLLBACK'
            cursor.execute(query)
        else:
            query = 'COMMIT'
            db_cursor.execute(query)

def test_commit(request):
    return HttpResponse(status=500, content="sqlite doesn't support autocommit cursor")

def test_rollback(request):
    return HttpResponse(status=500, content="sqlite doesn't suport autocommit rollback")

def test_remotehost_and_flavor(request):
    query = request.GET['query']
    with  connections['default'].cursor() as cursor:
        cursor.execute(query)
    return HttpResponse('test_remotehost_and_flavor')

# postgres section
def test_commit_postgres(request):
    try:
        with  connections['postgres'].cursor() as cursor:
            do_transaction(cursor, False)
    except:
        pass
    return HttpResponse("test_commit_postgres")

def test_rollback_postgres(request):
    try:
        with  connections['postgres'].cursor() as cursor:
            do_transaction(cursor, True)
    except:
        pass
    return HttpResponse('test_rollback_postgres')

def test_simple_postgres(request):
    query = request.GET['query']
    with  connections['postgres'].cursor() as cursor:
        cursor.execute(query)
    return HttpResponse('test_simple_postgres')

def test_remotehost_and_flavor_postgres(request):
    query = request.GET['query']
    with  connections['postgres'].cursor() as cursor:
        cursor.execute(query)
    return HttpResponse('test_remotehost_and_flavor_postgres')

def test_args_postgres(request):
    query = request.GET['query']
    value = request.GET['value']
    with  connections['postgres'].cursor() as cursor:
        cursor.execute(query, [value])
    return HttpResponse('test_args_postgres')


# mysql section
def test_commit_mysql(request):
    try:
        with  connections['mysql'].cursor() as cursor:
            do_transaction(cursor, False)
    except:
        pass
    return HttpResponse(django_version_str)

def test_rollback_mysql(request):
    try:
        with  connections['mysql'].cursor() as cursor:
            do_transaction(cursor, True)
    except:
        pass
    return HttpResponse(django_version_str)

def test_simple_mysql(request):
    query = request.GET['query']
    with  connections['mysql'].cursor() as cursor:
        cursor.execute(query)
    return HttpResponse('test_simple_mysql')

def test_args_mysql(request):
    query = request.GET['query']
    value = int(request.GET['value'])
    with  connections['mysql'].cursor() as cursor:
        cursor.execute(query,(value,))
    return HttpResponse('test_args_mysql')

def test_remotehost_and_flavor_mysql(request):
    query = request.GET['query']
    with  connections['mysql'].cursor() as cursor:
        cursor.execute(query)
    return HttpResponse('test_remotehost_and_flavor_mysql')

# transaction name test
def test_djangoware_transaction_name(request):
    query = request.GET['query']
    appoptics_apm.set_transaction_name(query)

    appoptics_apm.set_request_info(host="localhost", status_code=200, method='GET', full_path="/")

    return HttpResponse('test_djangoware_transaction_name')

def test_unicode_transaction_name(request):
    trans_name = request.GET['query']
    appoptics_apm.set_transaction_name(trans_name)
    appoptics_apm.set_request_info(host="localhost", status_code=200, method='GET', full_path="/")
    return HttpResponse('test_unicode_transaction_name')

# URLS
from django.conf.urls import url
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

urlpatterns = [
    url(r'^$', index),
    url(r'^test_simple$', test_simple),
    url(r'^test_args$', test_args),
    url(r'^test_commit$', test_commit),
    url(r'^test_remotehost_and_flavor$', test_remotehost_and_flavor),
    url(r'^test_commit_postgres$', test_commit_postgres),
    url(r'^test_rollback_postgres$', test_rollback_postgres),
    url(r'^test_simple_postgres$', test_simple_postgres),
    url(r'^test_remotehost_and_flavor_postgres$', test_remotehost_and_flavor_postgres),
    url(r'^test_args_postgres$', test_args_postgres),
    url(r'^test_commit_mysql$', test_commit_mysql),
    url(r'^test_rollback_mysql$', test_rollback_mysql),
    url(r'^test_simple_mysql$', test_simple_mysql),
    url(r'^test_remotehost_and_flavor_mysql$', test_remotehost_and_flavor_mysql),
    url(r'^test_args_mysql$', test_args_mysql),
    url(r'^test_djangoware_transaction_name$', test_djangoware_transaction_name),
    url(r'^test_unicode_transaction_name$', test_unicode_transaction_name),
]

urlpatterns += staticfiles_urlpatterns()

if __name__ == '__main__':
    # set the ENV
    sys.path += (HERE('.'),)
    # run the development server
    from django.core import management
    management.execute_from_command_line()


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "test.unit.mini_djapp")

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()

# to run from gunicorn
# gunicorn mini_djapp
