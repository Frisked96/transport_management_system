"""
Ledger signals package.
Imports all signal handlers to ensure registration on startup.
"""
from .bills import (
    track_bill_changes,
    update_original_bill_on_adjustment,
    update_original_bill_on_adjustment_delete,
    update_bill_on_trip_change,
    cleanup_financial_record_on_bill_delete,
    sync_trip_ledger_on_billtrip_save,
    sync_trip_ledger_on_billtrip_delete,
)
from .records import (
    delete_old_financial_document_on_change,
    delete_financial_document_on_delete,
    sync_party_balance_on_opening_change,
    sync_account_balance_on_opening_change,
    track_financial_record_changes,
    update_balances_on_save,
    update_balances_on_delete,
    update_trip_bill_caches_on_save,
    update_trip_bill_caches_on_delete,
)
from .allocations import (
    track_bill_allocation_changes,
    update_bill_caches_on_alloc_save,
    track_trip_allocation_changes,
    update_trip_bill_caches_on_alloc_save,
    update_trip_bill_caches_on_alloc_delete,
    update_bill_caches_on_alloc_delete,
)
from .toll import (
    sync_toll_account_on_opening_change,
    track_toll_recharge_changes,
    update_toll_recharge_on_save,
    cleanup_toll_recharge_on_delete,
    track_toll_entry_changes,
    update_toll_entry_on_save,
    update_toll_entry_on_delete,
)

__all__ = [
    'track_bill_changes',
    'update_original_bill_on_adjustment',
    'update_original_bill_on_adjustment_delete',
    'update_bill_on_trip_change',
    'cleanup_financial_record_on_bill_delete',
    'sync_trip_ledger_on_billtrip_save',
    'sync_trip_ledger_on_billtrip_delete',
    'delete_old_financial_document_on_change',
    'delete_financial_document_on_delete',
    'sync_party_balance_on_opening_change',
    'sync_account_balance_on_opening_change',
    'track_financial_record_changes',
    'update_balances_on_save',
    'update_balances_on_delete',
    'update_trip_bill_caches_on_save',
    'update_trip_bill_caches_on_delete',
    'track_bill_allocation_changes',
    'update_bill_caches_on_alloc_save',
    'track_trip_allocation_changes',
    'update_trip_bill_caches_on_alloc_save',
    'update_trip_bill_caches_on_alloc_delete',
    'update_bill_caches_on_alloc_delete',
    'sync_toll_account_on_opening_change',
    'track_toll_recharge_changes',
    'update_toll_recharge_on_save',
    'cleanup_toll_recharge_on_delete',
    'track_toll_entry_changes',
    'update_toll_entry_on_save',
    'update_toll_entry_on_delete',
]
