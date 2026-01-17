"""
URL configuration for Fleet app
"""
from django.urls import path
from . import views

urlpatterns = [
    # Vehicle URLs
    path('', views.VehicleListView.as_view(), name='vehicle-list'),
    path('vehicle/<int:pk>/', views.VehicleDetailView.as_view(), name='vehicle-detail'),
    path('vehicle/create/', views.VehicleCreateView.as_view(), name='vehicle-create'),
    path('vehicle/<int:pk>/update/', views.VehicleUpdateView.as_view(), name='vehicle-update'),
    path('vehicle/<int:pk>/delete/', views.VehicleDeleteView.as_view(), name='vehicle-delete'),
    
    # Maintenance Log URLs
    path('maintenance/', views.MaintenanceLogListView.as_view(), name='maintenance-log-list'),
    path('maintenance/<int:pk>/', views.MaintenanceLogDetailView.as_view(), name='maintenance-log-detail'),
    path('maintenance/create/', views.MaintenanceLogCreateView.as_view(), name='maintenance-log-create'),
    path('maintenance/<int:pk>/update/', views.MaintenanceLogUpdateView.as_view(), name='maintenance-log-update'),
]