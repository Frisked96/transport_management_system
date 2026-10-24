"""
TollEntry model and upload path for Ledger application.
"""
import os
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from transport_mgmt.utils import get_local_time_str
from django.core.exceptions import ValidationError
from .toll_account import TollAccount


def toll_entry_upload_path(instance, filename):
    """
    Upload path for individual toll receipts.
    Format: toll_records/entries/<vehicle_plate>/<filename>
    """
    ext = os.path.splitext(filename)[1]
    name_root = os.path.splitext(filename)[0]
    safe_root = "".join([c for c in name_root if c.isalnum() or c in (' ', '_', '-')]).strip().replace(' ', '_')
    timestamp = get_local_time_str('%Y%m%d_%H%M%S')
    new_filename = f"{safe_root}_{timestamp}{ext}" if safe_root else f"toll_{timestamp}{ext}"
    plate = instance.vehicle.registration_plate.replace(' ', '_') if instance.vehicle_id and instance.vehicle else 'general'
    return os.path.join('toll_records', 'entries', plate, new_filename)


class TollEntry(models.Model):
    """
    Individual toll charge incurred by a vehicle.
    Deducted strictly from the Toll Account balance, not from any Company Account.
    """
    toll_account = models.ForeignKey(
        TollAccount,
        on_delete=models.CASCADE,
        related_name='entries',
        verbose_name='Toll Account'
    )
    vehicle = models.ForeignKey(
        'fleet.Vehicle',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='toll_entries',
        verbose_name='Vehicle (Optional)'
    )
    date = models.DateField(default=timezone.now, verbose_name='Toll Date')
    time = models.TimeField(null=True, blank=True, verbose_name='Toll Time')
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Toll Amount'
    )
    toll_plaza = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Toll Plaza / Location'
    )
    associated_trip = models.ForeignKey(
        'trips.Trip',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='toll_entries',
        verbose_name='Associated Trip (Optional)'
    )
    transaction_reference = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Slip No / FASTag Txn ID'
    )
    receipt_document = models.FileField(
        upload_to=toll_entry_upload_path,
        blank=True,
        null=True,
        verbose_name='Toll Slip / Receipt'
    )
    description = models.TextField(blank=True, verbose_name='Remarks / Notes')
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_toll_entries',
        verbose_name='Created By'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Toll Entry'
        verbose_name_plural = 'Toll Entries'
        ordering = ['-date', '-time', '-created_at']
        indexes = [
            models.Index(fields=['date', 'time']),
            models.Index(fields=['toll_account', 'date']),
            models.Index(fields=['vehicle', 'date']),
        ]

    def __str__(self):
        veh_str = f" - {self.vehicle.registration_plate}" if self.vehicle else ""
        plaza_str = f" at {self.toll_plaza}" if self.toll_plaza else ""
        return f"₹{self.amount}{veh_str}{plaza_str} on {self.date}"

    def clean(self):
        if self.amount is not None and self.amount <= Decimal('0.00'):
            raise ValidationError({'amount': 'Toll amount must be greater than zero.'})
