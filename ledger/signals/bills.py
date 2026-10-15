"""
Bill and BillTrip signals.
"""
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from ledger.models import Bill, FinancialRecord, BillTrip, is_bill_deleting
from trips.models import Trip
from ledger.services import BillingService, TripFinancialService


@receiver(pre_save, sender=Bill)
def track_bill_changes(sender, instance, **kwargs):
    """
    Captures old state to handle original_bill changes for adjustments.
    """
    if instance.pk:
        try:
            instance._old_instance = Bill.objects.get(pk=instance.pk)
        except Bill.DoesNotExist:
            instance._old_instance = None
    else:
        instance._old_instance = None


@receiver(post_save, sender=Bill)
def update_original_bill_on_adjustment(sender, instance, **kwargs):
    """
    When an adjustment bill (Credit/Debit Note) is saved, 
    update the financial caches of the original bill.
    """
    if getattr(instance, '_updating_financial_caches', False):
        return

    # Update current original bill
    if instance.original_bill:
        BillingService.update_bill_financial_caches(instance.original_bill)

    # Update old original bill if it changed
    old_instance = getattr(instance, '_old_instance', None)
    if old_instance and old_instance.original_bill_id and old_instance.original_bill_id != instance.original_bill_id:
        try:
            old_bill = old_instance.original_bill
            if old_bill:
                BillingService.update_bill_financial_caches(old_bill)
        except Bill.DoesNotExist:
            pass


@receiver(post_delete, sender=Bill)
def update_original_bill_on_adjustment_delete(sender, instance, **kwargs):
    """Update original bill caches when an adjustment is deleted"""
    if instance.original_bill_id:
        try:
            if instance.original_bill:
                BillingService.update_bill_financial_caches(instance.original_bill)
        except Bill.DoesNotExist:
            pass


@receiver(post_save, sender=Trip)
def update_bill_on_trip_change(sender, instance, **kwargs):
    """
    When a trip is updated, ensure any associated bills are synchronized.
    """
    if getattr(instance, '_updating_financial_caches', False):
        return

    # Only sync bills if the trip is actually billed
    if not instance.is_billed:
        return

    # 1. Sync LR No to BillTrip context
    BillTrip.objects.filter(trip=instance).update(lr_no=instance.lr_no)

    # 2. Sync Bill totals to Ledger
    associated_bills = Bill.objects.filter(trips=instance)
    for bill in associated_bills:
        # Calling save() is the most robust way to refresh all caches and sync to ledger
        bill.save()


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


@receiver(post_save, sender=BillTrip)
def sync_trip_ledger_on_billtrip_save(sender, instance, **kwargs):
    """
    When a trip is linked to a bill, its individual accrual should be deleted.
    """
    bill = instance.bill
    if getattr(bill, '_suppress_billtrip_sync', False):
        return

    TripFinancialService.sync_trip_accrual(instance.trip)
    # Refresh the bill's cached totals and sync to ledger
    bill.save()


@receiver(post_delete, sender=BillTrip)
def sync_trip_ledger_on_billtrip_delete(sender, instance, **kwargs):
    """
    When a trip is unlinked from a bill, its individual accrual should be restored.
    """
    if getattr(instance, '_suppress_billtrip_sync', False):
        return

    if not instance.trip_id:
        return

    try:
        trip = instance.trip
        # Don't try to sync if the trip itself is being deleted
        if getattr(trip, '_is_being_deleted', False):
            return
        TripFinancialService.sync_trip_accrual(trip)
    except Trip.DoesNotExist:
        return
    
    # Get the actual bill object from the instance cache if possible, or fetch it
    bill = None
    if 'bill' in instance._state.fields_cache:
        bill = instance.bill
    elif instance.bill_id:
        try:
            bill = Bill.objects.get(pk=instance.bill_id)
        except Bill.DoesNotExist:
            pass

    # Don't try to sync the bill if it's being deleted or suppressing sync
    if bill and (getattr(bill, '_is_being_deleted', False) or getattr(bill, '_suppress_billtrip_sync', False)):
        return
        
    # Also check if there's a global flag or we know it's being deleted
    if is_bill_deleting(instance.bill_id) or (hasattr(Bill, '_deleting_pks') and instance.bill_id in Bill._deleting_pks):
        return

    try:
        if bill:
            # Refresh caches and sync to ledger
            bill.save()
    except Exception:
        pass
