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

    # Entry Review & Verification URLs
    path('review/', views.FinancialEntryReviewView.as_view(), name='financial-entry-review'),
    path('review/<int:pk>/verify/', views.verify_financial_entry, name='verify-financial-entry'),
    path('review/<int:pk>/flag/', views.flag_financial_entry, name='flag-financial-entry'),
    path('review/<int:pk>/restore/', views.restore_financial_entry, name='restore-financial-entry'),
    path('review/bulk-verify/', views.bulk_verify_financial_entries, name='bulk-verify-financial-entries'),

    # Party URLs
    path('parties/', views.PartyListView.as_view(), name='party-list'),
    path('parties/create/', views.PartyCreateView.as_view(), name='party-create'),
    path('parties/<int:pk>/', views.PartyDetailView.as_view(), name='party-detail'),
    path('parties/<int:pk>/update/', views.PartyUpdateView.as_view(), name='party-update'),
    path('parties/<int:pk>/delete/', views.PartyDeleteView.as_view(), name='party-delete'),
    path('parties/<int:pk>/statement/', views.party_statement_pdf, name='party-statement-pdf'),
    path('ajax/get-party-unpaid-trips/', views.get_party_unpaid_trips, name='get-party-unpaid-trips'),
    path('ajax/get-party-unbilled-trips/', views.get_party_unbilled_trips, name='get-party-unbilled-trips'),
    path('ajax/get-party-bills/', views.get_party_bills, name='get-party-bills'),
    path('ajax/get-bill-balance/', views.get_bill_balance, name='get-bill-balance'),
    path('ajax/get-trip-balance/', views.get_trip_balance, name='get-trip-balance'),
    path('ajax/get-next-invoice-number/', views.get_next_invoice_number, name='get-next-invoice-number'),
    path('global-resync/', views.global_resync, name='global-resync'),


    # Account URLs
    path('accounts/', views.CompanyAccountListView.as_view(), name='account-list'),
    path('accounts/create/', views.CompanyAccountCreateView.as_view(), name='account-create'),
    path('accounts/<int:pk>/', views.CompanyAccountDetailView.as_view(), name='account-detail'),
    path('accounts/<int:pk>/update/', views.CompanyAccountUpdateView.as_view(), name='account-update'),
    path('accounts/<int:pk>/delete/', views.CompanyAccountDeleteView.as_view(), name='account-delete'),
    path('accounts/<int:pk>/statement/', views.account_statement_pdf, name='account-statement-pdf'),
    path('unified-ledger/statement/', views.unified_ledger_pdf, name='unified-ledger-pdf'),

    # Bill URLs
    path('bills/', views.BillListView.as_view(), name='bill-list'),
    path('bills/create/', views.BillCreateView.as_view(), name='bill-create'),
    path('bills/<int:pk>/', views.BillDetailView.as_view(), name='bill-detail'),
    path('bills/<int:pk>/update/', views.BillUpdateView.as_view(), name='bill-update'),
    path('bills/<int:pk>/vendor-update/', views.VendorBillUpdateView.as_view(), name='vendor-bill-update'),
    path('bills/<int:pk>/delete/', views.BillDeleteView.as_view(), name='bill-delete'),
    
    # New print views
    path('bills/<int:pk>/print/', views.print_invoice, name='print-invoice'),
    path('bills/<int:pk>/annexure/', views.print_annexure, name='print-annexure'),
    path('bills/<int:pk>/print-combined/', views.print_combined_bill, name='print-combined-bill'),
    path('bills/shared/<str:token>/', views.shared_bill_view, name='shared-bill'),
    path('bills/bulk-print/', views.bulk_print_invoices, name='bulk-print-invoices'),

    # Toll Ledger URLs
    path('toll/', views.TollLedgerView.as_view(), name='toll-ledger'),
    path('toll/accounts/', views.TollAccountListView.as_view(), name='toll-account-list'),
    path('toll/accounts/create/', views.TollAccountCreateView.as_view(), name='toll-account-create'),
    path('toll/accounts/<int:pk>/update/', views.TollAccountUpdateView.as_view(), name='toll-account-update'),
    path('toll/accounts/<int:pk>/delete/', views.TollAccountDeleteView.as_view(), name='toll-account-delete'),
    path('toll/recharge/create/', views.TollRechargeCreateView.as_view(), name='toll-recharge-create'),
    path('toll/recharge/<int:pk>/update/', views.TollRechargeUpdateView.as_view(), name='toll-recharge-update'),
    path('toll/recharge/<int:pk>/delete/', views.TollRechargeDeleteView.as_view(), name='toll-recharge-delete'),
    path('toll/entry/create/', views.TollEntryCreateView.as_view(), name='toll-entry-create'),
    path('toll/entry/bulk/', views.TollEntryBulkCreateView.as_view(), name='toll-entry-bulk-create'),
    path('toll/entry/<int:pk>/update/', views.TollEntryUpdateView.as_view(), name='toll-entry-update'),
    path('toll/entry/<int:pk>/delete/', views.TollEntryDeleteView.as_view(), name='toll-entry-delete'),
    path('toll/statement/', views.toll_statement_print, name='toll-statement-print'),
    path('toll/import/', views.TollStatementImportView.as_view(), name='toll-statement-import'),
    path('toll/reconcile/', views.TollReconciliationView.as_view(), name='toll-reconcile'),
    path('toll/reconcile/<int:pk>/unlink/', views.toll_recharge_unlink_firm, name='toll-recharge-unlink'),
]
