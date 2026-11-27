"""
Thread-local signal deferral context for batching ledger cache and balance refreshes.
"""
import threading
from contextlib import contextmanager


_ledger_signal_state = threading.local()


def _get_state():
    state = getattr(_ledger_signal_state, 'data', None)
    if state is None:
        state = {
            'depth': 0,
            'party_ids': set(),
            'account_ids': set(),
            'trip_ids': set(),
            'bill_ids': set(),
        }
        _ledger_signal_state.data = state
    return state


def is_ledger_refresh_deferred():
    """Return True when inside an active deferred_ledger_refresh block."""
    state = getattr(_ledger_signal_state, 'data', None)
    return bool(state and state['depth'] > 0)


def queue_party_refresh(party_id):
    if party_id:
        _get_state()['party_ids'].add(party_id)


def queue_account_refresh(account_id):
    if account_id:
        _get_state()['account_ids'].add(account_id)


def queue_trip_refresh(trip_id):
    if trip_id:
        _get_state()['trip_ids'].add(trip_id)


def queue_bill_refresh(bill_id):
    if bill_id:
        _get_state()['bill_ids'].add(bill_id)


def _flush_deferred_refreshes(bill_ids, trip_ids, party_ids, account_ids):
    """
    Refresh each unique touched Bill, Trip, Party, and CompanyAccount once.
    """
    from django.db.models import F, Q
    from ledger.models import Bill, BillTrip, CompanyAccount, Party
    from ledger.services import BalanceService, BillingService, TripFinancialService
    from trips.models import Trip

    # 1. Bills & Trips
    direct_bill_ids = set(bill_ids)
    indirect_bill_ids = set()
    if trip_ids:
        indirect_bill_ids = set(
            BillTrip.objects.filter(
                trip_id__in=trip_ids,
                bill__customer_bill__isnull=True,
            )
            .exclude(bill__category__name='Lorry Hire')
            .filter(Q(bill__party=F('trip__party')) | ~Q(bill__party__party_type='Creditor'))
            .values_list('bill_id', flat=True)
        ) - direct_bill_ids

    all_bill_ids = direct_bill_ids | indirect_bill_ids
    bills_by_id = {}
    trips_to_update = set(trip_ids)

    if all_bill_ids:
        bills = list(
            Bill.objects.filter(pk__in=all_bill_ids)
            .select_related('category', 'party')
            .prefetch_related('items', 'bill_trips__trip', 'adjustment_bills__category')
        )
        for bill in bills:
            bills_by_id[bill.pk] = bill
            old_status = bill.payment_status_cached
            BillingService.update_bill_financial_caches(bill, update_trips=False)
            new_status = bill.payment_status_cached
            paid_toggled = (old_status == Bill.PAYMENT_STATUS_PAID) != (
                new_status == Bill.PAYMENT_STATUS_PAID
            )
            if bill.bill_type == Bill.TYPE_TRIP and (bill.pk in direct_bill_ids or paid_toggled):
                trips_to_update.update(bt.trip_id for bt in bill.bill_trips.all())

    if trips_to_update:
        trips = list(
            Trip.objects.filter(pk__in=trips_to_update).select_related(
                'vehicle', 'party', 'route'
            )
        )
        # Pre-populate customer bill mapping in one query to avoid per-trip is_billed/associated_bill queries
        trip_bill_map = {}
        for bt in (
            BillTrip.objects.filter(
                trip_id__in=trips_to_update,
                bill__customer_bill__isnull=True,
            )
            .exclude(bill__category__name='Lorry Hire')
            .filter(Q(bill__party=F('trip__party')) | ~Q(bill__party__party_type='Creditor'))
            .select_related('bill__category', 'bill__party')
        ):
            if bt.trip_id not in trip_bill_map:
                trip_bill_map[bt.trip_id] = bills_by_id.get(bt.bill_id, bt.bill)

        bill_pool_cache = {}
        for trip in trips:
            bill = trip_bill_map.get(trip.pk)
            trip._is_billed_cache = bill is not None
            trip._associated_bill_cache = bill
            TripFinancialService.update_trip_financial_caches(
                trip, bill_pool_cache=bill_pool_cache
            )

    # 2. Parties
    if party_ids:
        for party in Party.objects.filter(pk__in=party_ids):
            BalanceService.refresh_party_balance(party)

    # 3. CompanyAccounts
    if account_ids:
        for account in CompanyAccount.objects.filter(pk__in=account_ids):
            BalanceService.refresh_account_balance(account)


@contextmanager
def deferred_ledger_refresh():
    """
    Context manager that defers per-save Party/CompanyAccount/Trip/Bill signal
    refreshes until exit, flushing each affected entity at most once.
    Supports safe nesting via a thread-local depth counter.
    """
    state = _get_state()
    state['depth'] += 1
    try:
        yield
    except BaseException:
        state['depth'] -= 1
        if state['depth'] <= 0:
            state['depth'] = 0
            state['party_ids'].clear()
            state['account_ids'].clear()
            state['trip_ids'].clear()
            state['bill_ids'].clear()
        raise
    else:
        state['depth'] -= 1
        if state['depth'] <= 0:
            state['depth'] = 0
            bill_ids = set(state['bill_ids'])
            trip_ids = set(state['trip_ids'])
            party_ids = set(state['party_ids'])
            account_ids = set(state['account_ids'])
            state['bill_ids'].clear()
            state['trip_ids'].clear()
            state['party_ids'].clear()
            state['account_ids'].clear()
            if bill_ids or trip_ids or party_ids or account_ids:
                _flush_deferred_refreshes(bill_ids, trip_ids, party_ids, account_ids)
