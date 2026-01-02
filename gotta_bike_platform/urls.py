"""URL configuration for gotta_bike_platform project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/

Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))

"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.dbot_api.api import api as dbot_api
from apps.dbot_api.cron_api import cron_api
from gotta_bike_platform.views import about, home

urlpatterns = [
    path("", home, name="home"),
    path("about/", about, name="about"),
    path("admin/", admin.site.urls),
    path("api/dbot/", dbot_api.urls),
    path("api/cron/", cron_api.urls),
    path("accounts/", include("allauth.urls")),
    path("user/", include("apps.accounts.urls")),
    path("team/", include("apps.team.urls")),
    path("data-connections/", include("apps.data_connection.urls")),
    path("m/", include("apps.magic_links.urls")),
]

if settings.DEBUG:
    urlpatterns += [
        path("__debug__/", include("debug_toolbar.urls")),
        path("__reload__/", include("django_browser_reload.urls")),
    ]
