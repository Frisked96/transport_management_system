"""
Modularized views for Ledger application.
Exports all views and helpers for backward compatibility.
"""
from ledger.views.base import BaseLedgerPermissionMixin
from ledger.utils import format_indian_comma, format_balance

from ledger.views.records import (
    FinancialRecordListView,
    FinancialRecordDetailView,
    FinancialRecordCreateView,
    FinancialRecordUpdateView,
    FinancialRecordDeleteView,
    financial_summary,
)

from ledger.views.parties import (
    PartyListView,
    PartyDetailView,
    PartyCreateView,
    PartyUpdateView,
    PartyDeleteView,
    get_party_unpaid_trips,
    get_party_unbilled_trips,
    get_party_bills,
    get_bill_balance,
    get_trip_balance,
)

from ledger.views.accounts import (
    CompanyAccountListView,
    CompanyAccountDetailView,
    CompanyAccountCreateView,
    CompanyAccountUpdateView,
    CompanyAccountDeleteView,
    global_resync,
)

from ledger.views.bills import (
    BillListView,
    BillDetailView,
    BillCreateView,
    BillUpdateView,
    BillDeleteView,
    group_trips_for_bill,
    print_invoice,
    print_annexure,
    print_combined_bill,
    get_next_invoice_number,
    parse_number_range,
    get_bulk_invoices_context,
    bulk_print_invoices,
)

from ledger.views.statements import (
    party_statement_pdf,
    account_statement_pdf,
    unified_ledger_pdf,
)

__all__ = [
    'BaseLedgerPermissionMixin',
    'format_indian_comma',
    'format_balance',
    'FinancialRecordListView',
    'FinancialRecordDetailView',
    'FinancialRecordCreateView',
    'FinancialRecordUpdateView',
    'FinancialRecordDeleteView',
    'financial_summary',
    'PartyListView',
    'PartyDetailView',
    'PartyCreateView',
    'PartyUpdateView',
    'PartyDeleteView',
    'get_party_unpaid_trips',
    'get_party_unbilled_trips',
    'get_party_bills',
    'get_bill_balance',
    'get_trip_balance',
    'CompanyAccountListView',
    'CompanyAccountDetailView',
    'CompanyAccountCreateView',
    'CompanyAccountUpdateView',
    'CompanyAccountDeleteView',
    'global_resync',
    'BillListView',
    'BillDetailView',
    'BillCreateView',
    'BillUpdateView',
    'BillDeleteView',
    'group_trips_for_bill',
    'print_invoice',
    'print_annexure',
    'print_combined_bill',
    'get_next_invoice_number',
    'parse_number_range',
    'get_bulk_invoices_context',
    'bulk_print_invoices',
    'party_statement_pdf',
    'account_statement_pdf',
    'unified_ledger_pdf',
]
