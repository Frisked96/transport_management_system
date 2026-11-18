"""
BillItem model for Ledger application.
Supports multiple vehicle and line item rows on Halting bills, Credit Notes,
Debit Notes, and Standard Invoices.
"""
from decimal import Decimal
from django.db import models


class BillItem(models.Model):
    """
    Line item for Standard Invoices, Halting Bills, Credit Notes, and Debit Notes.
    Enables one bill to have multiple vehicles and item rows.
    """
    bill = models.ForeignKey(
        'ledger.Bill',
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name="Associated Bill"
    )
    vehicle = models.ForeignKey(
        'fleet.Vehicle',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bill_items',
        verbose_name="Vehicle"
    )
    vehicle_number = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Vehicle Number"
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Description / Remarks"
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        default=Decimal('1.000'),
        verbose_name="Quantity / Days / Weight"
    )
    rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        default=Decimal('0.00'),
        verbose_name="Rate"
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Amount"
    )
    lr_no = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="LR No"
    )

    class Meta:
        verbose_name = 'Bill Item'
        verbose_name_plural = 'Bill Items'
        ordering = ['id']

    def save(self, *args, **kwargs):
        if self.vehicle and not self.vehicle_number:
            self.vehicle_number = self.vehicle.registration_plate
        super().save(*args, **kwargs)

    @property
    def display_vehicle(self):
        if self.vehicle_number:
            return self.vehicle_number
        if self.vehicle:
            return self.vehicle.registration_plate
        return ""

    def __str__(self):
        veh = self.display_vehicle or "No Vehicle"
        return f"Bill #{self.bill_id} - {veh} - ₹{self.amount}"
