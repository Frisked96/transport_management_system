"""
URL configuration for Trips app
"""
from django.urls import path
from . import views

urlpatterns = [
    # Trip URLs
    path('', views.TripListView.as_view(), name='trip-list'),
    path('trip/<int:pk>/', views.TripDetailView.as_view(), name='trip-detail'),
    path('trip/create/', views.TripCreateView.as_view(), name='trip-create'),
    path('trip/<int:pk>/update/', views.TripUpdateView.as_view(), name='trip-update'),
    path('trip/<int:pk>/delete/', views.TripDeleteView.as_view(), name='trip-delete'),
    
    # Status update URL
    path('trip/<int:pk>/status/', views.update_trip_status, name='trip-status-update'),
]