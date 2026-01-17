"""
URL configuration for Ledger app
"""
from django.urls import path
from . import views

urlpatterns = [
    # Financial Record URLs
    path('', views.FinancialRecordListView.as_view(), name='financialrecord-list'),
    path('record/<int:pk>/', views.FinancialRecordDetailView.as_view(), name='financialrecord-detail'),
    path('record/create/', views.FinancialRecordCreateView.as_view(), name='financialrecord-create'),
    path('record/<int:pk>/update/', views.FinancialRecordUpdateView.as_view(), name='financialrecord-update'),
    path('record/<int:pk>/delete/', views.FinancialRecordDeleteView.as_view(), name='financialrecord-delete'),
    
    # Financial Summary URL
    path('summary/', views.financial_summary, name='financial-summary'),
]