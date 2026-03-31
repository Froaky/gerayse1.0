import sys

from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.conf import settings
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('users.urls')),
    path('', include('cashops.urls')),
    path('tesoreria/', include('treasury.urls')),
]

if settings.DEBUG or "runserver" in sys.argv:
    urlpatterns += staticfiles_urlpatterns()
