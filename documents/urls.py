"""
URL configuration for Documents app
"""
from django.urls import path
from . import views

urlpatterns = [
    # Document List (Main Menu)
    path('', views.DocumentListView.as_view(), name='document-list'),

    # Document Create (linked to parents)
    path('vehicle/<int:vehicle_pk>/add/', views.DocumentCreateView.as_view(), name='document-create-vehicle'),
    path('driver/<int:driver_pk>/add/', views.DocumentCreateView.as_view(), name='document-create-driver'),

    # Document Update/Delete
    path('<int:pk>/update/', views.DocumentUpdateView.as_view(), name='document-update'),
    path('<int:pk>/delete/', views.DocumentDeleteView.as_view(), name='document-delete'),
    
    # Document Renewal & History
    path('<int:pk>/renew/', views.DocumentRenewView.as_view(), name='document-renew'),
    path('<int:pk>/history/', views.DocumentHistoryView.as_view(), name='document-history'),
    path('renewals/<int:pk>/update/', views.DocumentRenewalUpdateView.as_view(), name='document-renewal-update'),
    path('renewals/<int:pk>/delete/', views.DocumentRenewalDeleteView.as_view(), name='document-renewal-delete'),

    # Proxy for viewing/downloading documents to avoid slow page loads
    path('<int:pk>/view/', views.document_download_proxy, name='document-view'),
    path('renewals/<int:pk>/view/', views.renewal_download_proxy, name='renewal-file-view'),
    
    # API for background upload tracking
    path('api/upload-status/', views.get_upload_status, name='upload-status-api'),
]
