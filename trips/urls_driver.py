"""
URL configuration for Driver Dashboard
"""
from django.urls import path
from . import views

urlpatterns = [
    # Driver Dashboard
    path('', views.driver_dashboard, name='driver-dashboard'),
]