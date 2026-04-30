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
    
    # Maintenance Record URLs
    path('maintenance/', views.MaintenanceListView.as_view(), name='maintenance-list'),
    path('maintenance/<int:pk>/', views.MaintenanceRecordDetailView.as_view(), name='maintenance-detail'),
    path('maintenance/create/', views.MaintenanceRecordCreateView.as_view(), name='maintenance-create'),
    path('maintenance/<int:pk>/update/', views.MaintenanceRecordUpdateView.as_view(), name='maintenance-update'),
    path('maintenance/<int:pk>/delete/', views.MaintenanceRecordDeleteView.as_view(), name='maintenance-delete'),
    path('maintenance/<int:pk>/complete/', views.maintenance_record_complete, name='maintenance-complete'),

    # Tyre URLs
    path('tyres/', views.TyreListView.as_view(), name='tyre-list'),
    path('tyre/<int:pk>/', views.TyreDetailView.as_view(), name='tyre-detail'),
    path('tyre/add/', views.TyreCreateView.as_view(), name='tyre-create'),
    path('tyre/<int:pk>/update/', views.TyreUpdateView.as_view(), name='tyre-update'),
    path('tyre/log/add/', views.TyreLogCreateView.as_view(), name='tyre-log-create'),
    path('tyre/<int:pk>/action/<str:action>/', views.tyre_quick_action, name='tyre-action'),
    path('tyre/<int:pk>/photo/', views.tyre_photo_serve, name='tyre-photo-serve'),
]
