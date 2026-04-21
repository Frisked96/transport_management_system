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
    
    # Route URLs
    path('routes/', views.RouteListView.as_view(), name='route-list'),
    path('routes/create/', views.RouteCreateView.as_view(), name='route-create'),
    path('routes/<int:pk>/update/', views.RouteUpdateView.as_view(), name='route-update'),
    path('routes/<int:pk>/delete/', views.RouteDeleteView.as_view(), name='route-delete'),
    
    # Autocomplete
    path('autocomplete/', views.get_autocomplete_suggestions, name='autocomplete-suggestions'),
]
