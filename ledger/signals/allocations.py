"""
TripAllocation and BillAllocation signals.
"""
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from ledger.models import Bill, TripAllocation, BillAllocation
from trips.models import Trip
from ledger.services import BillingService, TripFinancialService


@receiver(pre_save, sender=BillAllocation)
def track_bill_allocation_changes(sender, instance, **kwargs):
    """Track old bill in BillAllocation"""
    if instance.pk:
        try:
            instance._old_instance = BillAllocation.objects.get(pk=instance.pk)
        except BillAllocation.DoesNotExist:
            instance._old_instance = None
    else:
        instance._old_instance = None


@receiver(post_save, sender=BillAllocation)
def update_bill_caches_on_alloc_save(sender, instance, **kwargs):
    """Update Bill caches when a bill allocation is created/updated"""
    if getattr(instance, '_updating_financial_caches', False):
        return

    if not getattr(instance.bill, '_is_being_deleted', False):
        BillingService.update_bill_financial_caches(instance.bill)
    
    # Update old bill if it changed
    old_instance = getattr(instance, '_old_instance', None)
    if old_instance and old_instance.bill_id and old_instance.bill_id != instance.bill_id:
        try:
            old_bill = old_instance.bill
            if old_bill and not getattr(old_bill, '_is_being_deleted', False):
                BillingService.update_bill_financial_caches(old_bill)
        except Bill.DoesNotExist:
            pass


@receiver(pre_save, sender=TripAllocation)
def track_trip_allocation_changes(sender, instance, **kwargs):
    """Track old trip in TripAllocation"""
    if instance.pk:
        try:
            instance._old_instance = TripAllocation.objects.get(pk=instance.pk)
        except TripAllocation.DoesNotExist:
            instance._old_instance = None
    else:
        instance._old_instance = None


@receiver(post_save, sender=TripAllocation)
def update_trip_bill_caches_on_alloc_save(sender, instance, **kwargs):
    """Update Trip and Bill caches when an allocation is created/updated"""
    if getattr(instance, '_updating_financial_caches', False):
        return

    TripFinancialService.update_trip_financial_caches(instance.trip)
    if instance.trip.associated_bill:
        BillingService.update_bill_financial_caches(instance.trip.associated_bill)
    
    # Update old trip if it changed
    old_instance = getattr(instance, '_old_instance', None)
    if old_instance and old_instance.trip_id and old_instance.trip_id != instance.trip_id:
        try:
            old_trip = old_instance.trip
            if old_trip and not getattr(old_trip, '_is_being_deleted', False):
                TripFinancialService.update_trip_financial_caches(old_trip)
                if old_trip.associated_bill and not getattr(old_trip.associated_bill, '_is_being_deleted', False):
                    BillingService.update_bill_financial_caches(old_trip.associated_bill)
        except Trip.DoesNotExist:
            pass


@receiver(post_delete, sender=TripAllocation)
def update_trip_bill_caches_on_alloc_delete(sender, instance, **kwargs):
    """Update Trip and Bill caches when an allocation is deleted"""
    if getattr(instance, '_updating_financial_caches', False):
        return

    if instance.trip_id:
        try:
            trip = instance.trip
            if not getattr(trip, '_is_being_deleted', False):
                TripFinancialService.update_trip_financial_caches(trip)
                if trip.associated_bill and not getattr(trip.associated_bill, '_is_being_deleted', False):
                    BillingService.update_bill_financial_caches(trip.associated_bill)
        except Trip.DoesNotExist:
            pass


@receiver(post_delete, sender=BillAllocation)
def update_bill_caches_on_alloc_delete(sender, instance, **kwargs):
    """Update Bill caches when a bill allocation is deleted"""
    if instance.bill_id:
        try:
            bill = instance.bill
            if not getattr(bill, '_is_being_deleted', False):
                BillingService.update_bill_financial_caches(bill)
        except Bill.DoesNotExist:
            pass
