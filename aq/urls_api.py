from django.urls import path
from . import views_api

urlpatterns = [
    path("cities", views_api.cities),
    path("aq/current", views_api.aq_current),
    path("aq/outlook", views_api.aq_outlook),
    path("stations/near", views_api.stations_near),
    path("aq/series24h", views_api.aq_series24h),
    path("recommendation", views_api.recommendation),
    path("school/decision", views_api.school_decision),
    path("geocode", views_api.geocode),
]
