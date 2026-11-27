"""
FinancialRecord, Party, and CompanyAccount balance sync signals.
"""
from decimal import Decimal
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from ledger.models import FinancialRecord, Party, CompanyAccount, Bill
from trips.models import Trip
from ledger.services import BalanceService, BillingService, TripFinancialService
from .context import (
    is_ledger_refresh_deferred,
    queue_account_refresh,
    queue_bill_refresh,
    queue_party_refresh,
    queue_trip_refresh,
)


@receiver(post_delete, sender=FinancialRecord)
def delete_financial_document_on_delete(sender, instance, **kwargs):
    """
    Deletes the supporting document from storage when a FinancialRecord is deleted.
    """
    if instance.document_ref:
        instance.document_ref.delete(save=False)


@receiver(pre_save, sender=Party)
def sync_party_balance_on_opening_change(sender, instance, **kwargs):
    """
    If opening_balance is changed, trigger a refresh of the cached balance.
    """
    if getattr(instance, '_is_being_deleted', False):
        return

    if instance.pk:
        try:
            old_instance = Party.objects.get(pk=instance.pk)
            if old_instance.opening_balance != instance.opening_balance:
                # We calculate but don't save yet, it will be saved by the upcoming save()
                instance.total_debit_amount = instance._calculate_total_debit()
                instance.total_credit_amount = instance._calculate_total_credit()
                instance.current_balance_cached = instance.total_debit_amount - instance.total_credit_amount
        except Party.DoesNotExist:
            pass
    else:
        instance.total_debit_amount = instance.opening_balance if instance.opening_balance > 0 else Decimal('0')
        instance.total_credit_amount = abs(instance.opening_balance) if instance.opening_balance < 0 else Decimal('0')
        instance.current_balance_cached = instance.total_debit_amount - instance.total_credit_amount


@receiver(pre_save, sender=CompanyAccount)
def sync_account_balance_on_opening_change(sender, instance, **kwargs):
    """
    If opening_balance is changed for a company account, trigger a refresh.
    """
    if getattr(instance, '_is_being_deleted', False):
        return

    if instance.pk:
        try:
            old_instance = CompanyAccount.objects.get(pk=instance.pk)
            if old_instance.opening_balance != instance.opening_balance:
                instance.current_balance_cached = BalanceService.calculate_account_balance(instance)
        except CompanyAccount.DoesNotExist:
            pass
    else:
        instance.current_balance_cached = instance.opening_balance


@receiver(pre_save, sender=FinancialRecord)
def track_financial_record_changes(sender, instance, **kwargs):
    """
    Captures old state to handle party/account changes and handles document replacement.
    """
    if instance.pk:
        update_fields = kwargs.get('update_fields')
        tracked_fields = {'party', 'account', 'associated_trip', 'associated_bill', 'document_ref', 'record_type'}
        if update_fields is not None and not tracked_fields.intersection(update_fields):
            instance._old_instance = None
            return
        try:
            old_instance = FinancialRecord.objects.get(pk=instance.pk)
            instance._old_instance = old_instance
            if old_instance.document_ref and old_instance.document_ref != instance.document_ref:
                old_instance.document_ref.delete(save=False)
        except FinancialRecord.DoesNotExist:
            instance._old_instance = None
    else:
        instance._old_instance = None


@receiver(post_save, sender=FinancialRecord)
def update_balances_on_save(sender, instance, created, **kwargs):
    """
    Update Party and CompanyAccount balances when a FinancialRecord is saved.
    """
    old_instance = getattr(instance, '_old_instance', None)
    if is_ledger_refresh_deferred():
        if created:
            if instance.party_id:
                queue_party_refresh(instance.party_id)
        else:
            if old_instance and old_instance.party_id and old_instance.party_id != instance.party_id:
                queue_party_refresh(old_instance.party_id)
            if instance.party_id:
                queue_party_refresh(instance.party_id)

        if instance.record_type != FinancialRecord.RECORD_TYPE_INVOICE:
            if created:
                if instance.account_id:
                    queue_account_refresh(instance.account_id)
            else:
                if old_instance and old_instance.account_id and old_instance.account_id != instance.account_id:
                    queue_account_refresh(old_instance.account_id)
                if instance.account_id:
                    queue_account_refresh(instance.account_id)
        return

    # 1. Update Parties
    if created:
        if instance.party:
            BalanceService.refresh_party_balance(instance.party)
    else:
        old_party = old_instance.party if old_instance else None
        if old_party and old_party != instance.party:
            BalanceService.refresh_party_balance(old_party)
        if instance.party:
            BalanceService.refresh_party_balance(instance.party)

    # 2. Update CompanyAccounts (Invoice records are accruals and excluded from account balances)
    if instance.record_type != FinancialRecord.RECORD_TYPE_INVOICE:
        if created:
            if instance.account:
                BalanceService.refresh_account_balance(instance.account)
        else:
            old_account = old_instance.account if old_instance else None
            if old_account and old_account != instance.account:
                BalanceService.refresh_account_balance(old_account)
            if instance.account:
                BalanceService.refresh_account_balance(instance.account)


@receiver(post_delete, sender=FinancialRecord)
def update_balances_on_delete(sender, instance, **kwargs):
    """
    Update Party and CompanyAccount balances when a FinancialRecord is deleted.
    """
    if is_ledger_refresh_deferred():
        if instance.party_id:
            queue_party_refresh(instance.party_id)
        if instance.record_type != FinancialRecord.RECORD_TYPE_INVOICE and instance.account_id:
            queue_account_refresh(instance.account_id)
        return

    if instance.party_id:
        try:
            party = instance.party
            if party and not getattr(party, '_is_being_deleted', False):
                BalanceService.refresh_party_balance(party)
        except Party.DoesNotExist:
            pass

    if instance.record_type != FinancialRecord.RECORD_TYPE_INVOICE and instance.account_id:
        try:
            account = instance.account
            if account and not getattr(account, '_is_being_deleted', False):
                BalanceService.refresh_account_balance(account)
        except CompanyAccount.DoesNotExist:
            pass


@receiver(post_save, sender=FinancialRecord)
def update_trip_bill_caches_on_save(sender, instance, **kwargs):
    """Update Trip and Bill caches when a payment/deduction is recorded"""
    if getattr(instance, '_updating_financial_caches', False):
        return

    # Accrual records do not affect cash/deduction payment caches
    if instance.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
        return

    old_instance = getattr(instance, '_old_instance', None)
    if is_ledger_refresh_deferred():
        if instance.associated_trip_id:
            queue_trip_refresh(instance.associated_trip_id)
        if instance.associated_bill_id:
            queue_bill_refresh(instance.associated_bill_id)
        if old_instance:
            if old_instance.associated_trip_id and old_instance.associated_trip_id != instance.associated_trip_id:
                queue_trip_refresh(old_instance.associated_trip_id)
            if old_instance.associated_bill_id and old_instance.associated_bill_id != instance.associated_bill_id:
                queue_bill_refresh(old_instance.associated_bill_id)
        return

    # 1. Update current associations
    if instance.associated_trip:
        TripFinancialService.update_trip_financial_caches(instance.associated_trip)
    if instance.associated_bill:
        BillingService.update_bill_financial_caches(instance.associated_bill)

    # 2. Update OLD associations if they changed
    if old_instance:
        if old_instance.associated_trip and old_instance.associated_trip != instance.associated_trip:
            TripFinancialService.update_trip_financial_caches(old_instance.associated_trip)
        
        if old_instance.associated_bill and old_instance.associated_bill != instance.associated_bill:
            BillingService.update_bill_financial_caches(old_instance.associated_bill)


@receiver(post_delete, sender=FinancialRecord)
def update_trip_bill_caches_on_delete(sender, instance, **kwargs):
    """Update Trip and Bill caches when a payment/deduction is deleted"""
    if getattr(instance, '_updating_financial_caches', False):
        return

    # Accrual records do not affect cash/deduction payment caches
    if instance.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
        return

    if is_ledger_refresh_deferred():
        if instance.associated_trip_id:
            queue_trip_refresh(instance.associated_trip_id)
        if instance.associated_bill_id:
            queue_bill_refresh(instance.associated_bill_id)
        return

    if instance.associated_trip_id:
        try:
            trip = instance.associated_trip
            if trip and not getattr(trip, '_is_being_deleted', False):
                TripFinancialService.update_trip_financial_caches(trip)
        except Trip.DoesNotExist:
            pass
    
    if instance.associated_bill_id:
        try:
            bill = instance.associated_bill
            if bill and not getattr(bill, '_is_being_deleted', False):
                BillingService.update_bill_financial_caches(bill)
        except Bill.DoesNotExist:
            pass
