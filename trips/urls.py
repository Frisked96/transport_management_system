"""
URL configuration for Trips app
"""
from django.urls import path
from . import views

urlpatterns = [
    # Trip URLs
    path('', views.TripListView.as_view(), name='trip-list'),
    path('map/', views.TripMapView.as_view(), name='trip-map'),
    path('trip/<int:pk>/', views.TripDetailView.as_view(), name='trip-detail'),
    path('trip/create/', views.TripCreateView.as_view(), name='trip-create'),
    path('trip/<int:pk>/update/', views.TripUpdateView.as_view(), name='trip-update'),
    path('trip/<int:pk>/delete/', views.TripDeleteView.as_view(), name='trip-delete'),
    
    # Status update URL
    path('trip/<int:pk>/status/', views.update_trip_status, name='trip-status-update'),

    # Trip Expense URLs
    path('trip/<int:pk>/expenses/manage/', views.TripExpenseManageView.as_view(), name='trip-expenses-manage'),
    path('trip/<int:pk>/expenses/update/', views.TripExpenseUpdateView.as_view(), name='trip-expense-update'),
    path('trip/<int:trip_pk>/expense/create/', views.TripCustomExpenseCreateView.as_view(), name='trip-custom-expense-create'),
    path('trip/expense/<int:pk>/delete/', views.TripCustomExpenseDeleteView.as_view(), name='trip-custom-expense-delete'),
    
    # Autocomplete
    path('autocomplete/', views.get_autocomplete_suggestions, name='autocomplete-suggestions'),
]
