"""
BillTrip through model for Ledger application.
"""
from django.db import models
from django.core.exceptions import ObjectDoesNotExist
from trips.models import Trip


class BillTrip(models.Model):
    """
    Through model for Bill and Trip to store LR No and Discount for each trip in a bill context.
    """
    bill = models.ForeignKey('ledger.Bill', on_delete=models.CASCADE, related_name='bill_trips')
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='bill_trips')
    lr_no = models.CharField(max_length=100, blank=True, null=True, verbose_name="LR No")
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Discount")
    
    class Meta:
        verbose_name = 'Bill Trip'
        verbose_name_plural = 'Bill Trips'
        unique_together = ('bill', 'trip')

    def __str__(self):
        try:
            bill_str = self.bill.bill_number if self.bill else 'No Bill'
        except ObjectDoesNotExist:
            bill_str = f"Bill #{self.bill_id}"
        try:
            trip_str = self.trip.trip_number if self.trip else 'No Trip'
        except ObjectDoesNotExist:
            trip_str = f"Trip #{self.trip_id}"
        return f"{bill_str} - {trip_str} (LR: {self.lr_no or 'N/A'})"
