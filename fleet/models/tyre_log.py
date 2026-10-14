"""
TyreLog model for Fleet application.
"""
from django.db import models
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.utils import timezone
from .vehicle import Vehicle
from fleet.services import TYRE_POSITION_NAME_MAP


class TyreLog(models.Model):
    """
    History of tyre movements and repairs.
    Simplified: No odometer tracking for tyres.
    """
    ACTION_MOUNT = 'Mount'
    ACTION_DISMOUNT = 'Dismount'
    ACTION_ROTATION = 'Rotation'
    ACTION_REPAIR = 'Repair'
    ACTION_REMOLD = 'Remold'
    ACTION_SCRAP = 'Scrap'
    ACTION_STOCK = 'Stock'

    ACTION_CHOICES = [
        (ACTION_MOUNT, 'Mount'),
        (ACTION_DISMOUNT, 'Dismount'),
        (ACTION_ROTATION, 'Rotation'),
        (ACTION_REPAIR, 'Repair'),
        (ACTION_REMOLD, 'Remold'),
        (ACTION_SCRAP, 'Scrap'),
        (ACTION_STOCK, 'Stock'),
    ]

    tyre = models.ForeignKey('fleet.Tyre', on_delete=models.CASCADE, related_name='logs')
    date = models.DateField(default=timezone.now)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    position = models.CharField(
        max_length=50,
        blank=True,
        verbose_name='Position'
    )
    notes = models.TextField(blank=True)

    logged_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logged_tyre_actions',
        verbose_name='Logged By'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at', '-id']

    @property
    def position_display(self):
        """Returns clean human-readable position name"""
        return TYRE_POSITION_NAME_MAP.get(self.position, self.position)

    def __str__(self):
        try:
            tyre_str = str(self.tyre) if self.tyre else "No Tyre"
        except ObjectDoesNotExist:
            tyre_str = f"Tyre #{self.tyre_id}"
        return f"{tyre_str} - {self.action} on {self.date}"
