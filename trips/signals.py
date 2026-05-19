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
    if not getattr(instance.vehicle, '_is_being_deleted', False):
        Trip.recalculate_vehicle_trip_numbers(instance.vehicle)

@receiver(post_save, sender=Trip)
def recalculate_on_trip_update(sender, instance, created, **kwargs):
    """
    Trigger recalculation if date was changed (affecting sequence).
    """
    if not created:
        # For simplicity and robust sequencing, we'll run it on any update.
        Trip.recalculate_vehicle_trip_numbers(instance.vehicle)
