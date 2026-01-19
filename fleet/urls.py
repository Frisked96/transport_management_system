"""
URL configuration for Fleet app
"""
from django.urls import path
from . import views
from . import views_fuel

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

    # Fuel Log URLs
    path('vehicle/<int:vehicle_pk>/fuel/add/', views_fuel.FuelLogCreateView.as_view(), name='fuel-log-create'),
    path('fuel/<int:pk>/update/', views_fuel.FuelLogUpdateView.as_view(), name='fuel-log-update'),
    path('fuel/<int:pk>/delete/', views_fuel.FuelLogDeleteView.as_view(), name='fuel-log-delete'),

    # Tyre URLs
    path('tyres/', views.TyreListView.as_view(), name='tyre-list'),
    path('tyre/<int:pk>/', views.TyreDetailView.as_view(), name='tyre-detail'),
    path('tyre/add/', views.TyreCreateView.as_view(), name='tyre-create'),
    path('tyre/<int:pk>/update/', views.TyreUpdateView.as_view(), name='tyre-update'),
    path('tyre/log/add/', views.TyreLogCreateView.as_view(), name='tyre-log-create'),
]
