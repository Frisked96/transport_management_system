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

    # Party URLs
    path('parties/', views.PartyListView.as_view(), name='party-list'),
    path('parties/create/', views.PartyCreateView.as_view(), name='party-create'),
    path('parties/<int:pk>/', views.PartyDetailView.as_view(), name='party-detail'),
    path('parties/<int:pk>/update/', views.PartyUpdateView.as_view(), name='party-update'),
    path('parties/<int:pk>/delete/', views.PartyDeleteView.as_view(), name='party-delete'),
    path('api/party-unpaid-trips/', views.get_party_unpaid_trips, name='get-party-unpaid-trips'),

    # Account URLs
    path('accounts/', views.AccountListView.as_view(), name='account-list'),
    path('accounts/create/', views.AccountCreateView.as_view(), name='account-create'),
    path('accounts/<int:pk>/', views.AccountDetailView.as_view(), name='account-detail'),
    path('accounts/<int:pk>/update/', views.AccountUpdateView.as_view(), name='account-update'),
    path('accounts/<int:pk>/delete/', views.AccountDeleteView.as_view(), name='account-delete'),

    # Bill URLs
    path('bills/', views.BillListView.as_view(), name='bill-list'),
    path('bills/create/', views.BillCreateView.as_view(), name='bill-create'),
    path('bills/<int:pk>/', views.BillDetailView.as_view(), name='bill-detail'),
    path('bills/<int:pk>/update/', views.BillUpdateView.as_view(), name='bill-update'),
    path('bills/<int:pk>/delete/', views.BillDeleteView.as_view(), name='bill-delete'),
    
    # New print views
    path('bills/<int:pk>/print/', views.print_invoice, name='print-invoice'),
    path('bills/<int:pk>/annexure/', views.print_annexure, name='print-annexure'),
    path('bills/<int:pk>/print-combined/', views.print_combined_bill, name='print-combined-bill'),

    # Company Profile
    path('settings/company/', views.CompanyProfileUpdateView.as_view(), name='company-profile-update'),
]