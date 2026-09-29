"""
Signals for Trips application.
"""
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from trips.models import Trip

@receiver(post_delete, sender=Trip)
def recalculate_on_trip_delete(sender, instance, **kwargs):
    """
    Trigger recalculation of trip numbers for a vehicle when a trip is deleted.
    """
    try:
        if instance.vehicle and not getattr(instance.vehicle, '_is_being_deleted', False):
            Trip.recalculate_vehicle_trip_numbers(instance.vehicle)
    except Exception:
        pass

@receiver(post_save, sender=Trip)
def recalculate_on_trip_update(sender, instance, created, **kwargs):
    """
    Trigger recalculation if date was changed (affecting sequence).
    """
    if created:
        if instance.vehicle and Trip.objects.filter(vehicle=instance.vehicle, date__gt=instance.date).exists():
            Trip.recalculate_vehicle_trip_numbers(instance.vehicle)
        return

    update_fields = kwargs.get('update_fields')
    if update_fields:
        if 'date' not in update_fields and 'vehicle' not in update_fields:
            return

    if getattr(instance, '_updating_financial_caches', False):
        return

    # If we know date and vehicle did not change, skip expensive recalculation
    date_changed = getattr(instance, '_date_changed', None)
    vehicle_changed = getattr(instance, '_vehicle_changed', None)
    if date_changed is False and vehicle_changed is False:
        return

    if instance.vehicle:
        Trip.recalculate_vehicle_trip_numbers(instance.vehicle)
