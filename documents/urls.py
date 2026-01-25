"""
URL configuration for Documents app
"""
from django.urls import path
from . import views

urlpatterns = [
    # Document Create (linked to parents)
    path('vehicle/<int:vehicle_pk>/add/', views.DocumentCreateView.as_view(), name='document-create-vehicle'),
    path('driver/<int:driver_pk>/add/', views.DocumentCreateView.as_view(), name='document-create-driver'),

    # Document Update/Delete
    path('<int:pk>/update/', views.DocumentUpdateView.as_view(), name='document-update'),
    path('<int:pk>/delete/', views.DocumentDeleteView.as_view(), name='document-delete'),
]
