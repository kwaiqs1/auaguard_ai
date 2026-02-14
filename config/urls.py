from django.contrib import admin
from django.urls import path, include
from core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),

    path("", core_views.dashboard, name="dashboard"),
    path("map/", core_views.map_view, name="map"),
    path("outlook/", core_views.outlook_view, name="outlook"),
    path("school/", core_views.school_view, name="school"),
    path("about/", core_views.about_view, name="about"),

    path("auth/", include("accounts.urls")),
    path("api/v1/", include("aq.urls_api")),
]
