"""
Allocation models for Ledger application
"""
from django.db import models
from django.core.exceptions import ObjectDoesNotExist
from trips.models import Trip
from .financial_record import FinancialRecord


class TripAllocation(models.Model):
    financial_record = models.ForeignKey(
        FinancialRecord,
        on_delete=models.CASCADE,
        related_name='allocations',
        verbose_name='Financial Record'
    )
    trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        related_name='payment_allocations',
        verbose_name='Trip'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Allocated Amount'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Trip Allocation'
        verbose_name_plural = 'Trip Allocations'
        unique_together = ('financial_record', 'trip')

    def __str__(self):
        try:
            fr_str = str(self.financial_record) if self.financial_record_id else 'No FR'
        except ObjectDoesNotExist:
            fr_str = f"FR #{self.financial_record_id}"
        try:
            trip_str = self.trip.trip_number if self.trip_id else 'No Trip'
        except ObjectDoesNotExist:
            trip_str = f"Trip #{self.trip_id}"
        return f"{fr_str} -> {trip_str}: {self.amount}"


class BillAllocation(models.Model):
    financial_record = models.ForeignKey(
        FinancialRecord,
        on_delete=models.CASCADE,
        related_name='bill_allocations',
        verbose_name='Financial Record'
    )
    bill = models.ForeignKey(
        'Bill',
        on_delete=models.CASCADE,
        related_name='payment_allocations',
        verbose_name='Bill'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Allocated Amount'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Bill Allocation'
        verbose_name_plural = 'Bill Allocations'
        unique_together = ('financial_record', 'bill')

    def __str__(self):
        try:
            fr_str = str(self.financial_record) if self.financial_record_id else 'No FR'
        except ObjectDoesNotExist:
            fr_str = f"FR #{self.financial_record_id}"
        try:
            bill_str = self.bill.bill_number if self.bill_id else 'No Bill'
        except ObjectDoesNotExist:
            bill_str = f"Bill #{self.bill_id}"
        return f"{fr_str} -> {bill_str}: {self.amount}"
