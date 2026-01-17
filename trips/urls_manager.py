"""
URL configuration for Manager Dashboard
"""
from django.urls import path
from . import views

urlpatterns = [
    # Manager Dashboard
    path('', views.manager_dashboard, name='manager-dashboard'),
]