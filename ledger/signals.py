"""
Signals for Ledger application.
"""
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from ledger.models import Bill, FinancialRecord, BillTrip, Party, CompanyAccount, TripAllocation, BillAllocation
from trips.models import Trip
from ledger.services import BalanceService, BillingService, TripFinancialService
from decimal import Decimal

@receiver(post_save, sender=Bill)
def update_original_bill_on_adjustment(sender, instance, **kwargs):
    """
    When an adjustment bill (Credit/Debit Note) is saved, 
    update the financial caches of the original bill.
    """
    if getattr(instance, '_updating_financial_caches', False):
        return

    if instance.original_bill:
        BillingService.update_bill_financial_caches(instance.original_bill)

@receiver(post_delete, sender=Bill)
def update_original_bill_on_adjustment_delete(sender, instance, **kwargs):
    """Update original bill caches when an adjustment is deleted"""
    if instance.original_bill:
        BillingService.update_bill_financial_caches(instance.original_bill)

@receiver(post_save, sender=Trip)
def update_bill_on_trip_change(sender, instance, **kwargs):
    """
    When a trip is updated, ensure any associated bills are synchronized.
    """
    if getattr(instance, '_updating_financial_caches', False):
        return

    # 1. Sync LR No to BillTrip context
    BillTrip.objects.filter(trip=instance).update(lr_no=instance.lr_no)

    # 2. Sync Bill totals to Ledger
    associated_bills = Bill.objects.filter(trips=instance)
    for bill in associated_bills:
        BillingService.sync_bill_to_ledger(bill)

@receiver(post_delete, sender=Bill)
def cleanup_financial_record_on_bill_delete(sender, instance, **kwargs):
    """
    Ensure the consolidated FinancialRecord is deleted when the Bill is deleted.
    """
    if getattr(instance, '_is_being_deleted', False):
        return

    FinancialRecord.objects.filter(
        associated_bill_id=instance.id,
        record_type=FinancialRecord.RECORD_TYPE_INVOICE
    ).delete()

@receiver(pre_save, sender=FinancialRecord)
def delete_old_financial_document_on_change(sender, instance, **kwargs):
    """
    Deletes the old supporting document from storage when a new one is uploaded.
    """
    if not instance.pk:
        return False

    try:
        old_file = FinancialRecord.objects.get(pk=instance.pk).document_ref
    except FinancialRecord.DoesNotExist:
        return False

    new_file = instance.document_ref
    if old_file and old_file != new_file:
        old_file.delete(save=False)

@receiver(post_delete, sender=FinancialRecord)
def delete_financial_document_on_delete(sender, instance, **kwargs):
    """
    Deletes the supporting document from storage when a FinancialRecord is deleted.
    """
    if instance.document_ref:
        instance.document_ref.delete(save=False)

@receiver(post_save, sender=BillTrip)
def sync_trip_ledger_on_billtrip_save(sender, instance, **kwargs):
    """
    When a trip is linked to a bill, its individual accrual should be deleted.
    """
    TripFinancialService.sync_trip_accrual(instance.trip)
    BillingService.sync_bill_to_ledger(instance.bill)

@receiver(post_delete, sender=BillTrip)
def sync_trip_ledger_on_billtrip_delete(sender, instance, **kwargs):
    """
    When a trip is unlinked from a bill, its individual accrual should be restored.
    """
    TripFinancialService.sync_trip_accrual(instance.trip)
    
    # Don't try to sync the bill if it's being deleted
    if getattr(instance.bill, '_is_being_deleted', False):
        return

    try:
        BillingService.sync_bill_to_ledger(instance.bill)
    except (Bill.DoesNotExist, Exception):
        pass

@receiver(pre_save, sender=Party)
def sync_party_balance_on_opening_change(sender, instance, **kwargs):
    """
    If opening_balance is changed, trigger a refresh of the cached balance.
    """
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
    if instance.pk:
        try:
            old_instance = CompanyAccount.objects.get(pk=instance.pk)
            if old_instance.opening_balance != instance.opening_balance:
                instance.current_balance_cached = instance._calculate_balance()
        except CompanyAccount.DoesNotExist:
            pass
    else:
        instance.current_balance_cached = instance.opening_balance

@receiver(pre_save, sender=FinancialRecord)
def track_financial_record_changes(sender, instance, **kwargs):
    """
    Captures old state to handle party/account changes.
    """
    if instance.pk:
        try:
            instance._old_instance = FinancialRecord.objects.get(pk=instance.pk)
        except FinancialRecord.DoesNotExist:
            instance._old_instance = None
    else:
        instance._old_instance = None

@receiver(post_save, sender=FinancialRecord)
def update_balances_on_save(sender, instance, created, **kwargs):
    """
    Update Party and CompanyAccount balances when a FinancialRecord is saved.
    """
    # 1. Update Parties
    if created:
        if instance.party:
            BalanceService.refresh_party_balance(instance.party)
    else:
        old_party = getattr(instance, '_old_instance').party if getattr(instance, '_old_instance') else None
        if old_party:
            BalanceService.refresh_party_balance(old_party)
        if instance.party and instance.party != old_party:
            BalanceService.refresh_party_balance(instance.party)
        elif instance.party:
            BalanceService.refresh_party_balance(instance.party)

    # 2. Update CompanyAccounts
    if created:
        if instance.account:
            BalanceService.refresh_account_balance(instance.account)
    else:
        old_account = getattr(instance, '_old_instance').account if getattr(instance, '_old_instance') else None
        if old_account:
            BalanceService.refresh_account_balance(old_account)
        if instance.account and instance.account != old_account:
            BalanceService.refresh_account_balance(instance.account)
        elif instance.account:
            BalanceService.refresh_account_balance(instance.account)

@receiver(post_delete, sender=FinancialRecord)
def update_balances_on_delete(sender, instance, **kwargs):
    """
    Update Party and CompanyAccount balances when a FinancialRecord is deleted.
    """
    if instance.party:
        BalanceService.refresh_party_balance(instance.party)

    if instance.account:
        BalanceService.refresh_account_balance(instance.account)

@receiver(post_save, sender=FinancialRecord)
def update_trip_bill_caches_on_save(sender, instance, **kwargs):
    """Update Trip and Bill caches when a payment/deduction is recorded"""
    if getattr(instance, '_updating_financial_caches', False):
        return

    # 1. Update current associations
    if instance.associated_trip:
        TripFinancialService.update_trip_financial_caches(instance.associated_trip)
    if instance.associated_bill:
        BillingService.update_bill_financial_caches(instance.associated_bill)

    # 2. Update OLD associations if they changed
    old_instance = getattr(instance, '_old_instance', None)
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

    if instance.associated_trip:
        TripFinancialService.update_trip_financial_caches(instance.associated_trip)
    if instance.associated_bill:
        BillingService.update_bill_financial_caches(instance.associated_bill)

@receiver(post_save, sender=TripAllocation)
def update_trip_bill_caches_on_alloc_save(sender, instance, **kwargs):
    """Update Trip and Bill caches when an allocation is created/updated"""
    if getattr(instance, '_updating_financial_caches', False):
        return

    TripFinancialService.update_trip_financial_caches(instance.trip)
    if instance.trip.associated_bill:
        BillingService.update_bill_financial_caches(instance.trip.associated_bill)

@receiver(post_delete, sender=TripAllocation)
def update_trip_bill_caches_on_alloc_delete(sender, instance, **kwargs):
    """Update Trip and Bill caches when an allocation is deleted"""
    if getattr(instance, '_updating_financial_caches', False):
        return

    TripFinancialService.update_trip_financial_caches(instance.trip)
    if instance.trip.associated_bill:
        BillingService.update_bill_financial_caches(instance.trip.associated_bill)

@receiver(post_save, sender=BillAllocation)
def update_bill_caches_on_alloc_save(sender, instance, **kwargs):
    """Update Bill caches when a bill allocation is created/updated"""
    BillingService.update_bill_financial_caches(instance.bill)

@receiver(post_delete, sender=BillAllocation)
def update_bill_caches_on_alloc_delete(sender, instance, **kwargs):
    """Update Bill caches when a bill allocation is deleted"""
    BillingService.update_bill_financial_caches(instance.bill)
