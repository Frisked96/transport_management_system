"""
Models for Trips application
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Sum, Case, When, Value, F, DecimalField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from fleet.models import Vehicle
import re

class TripQuerySet(models.QuerySet):
    def with_payment_info(self):
        """Annotate queryset with payment information for filtering and sorting"""
        from ledger.models import FinancialRecord, TripAllocation, TransactionCategory
        
        # Subquery for direct payments (Income types or Deductions)
        direct_payments = FinancialRecord.objects.filter(
            associated_trip=OuterRef('pk')
        ).exclude(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).filter(
            models.Q(category__type=TransactionCategory.TYPE_INCOME) | 
            models.Q(category__name='Deductions')
        ).values('associated_trip').annotate(
            total=Sum('amount')
        ).values('total')

        # Subquery for allocations
        allocations = TripAllocation.objects.filter(
            trip=OuterRef('pk')
        ).values('trip').annotate(
            total=Sum('amount')
        ).values('total')

        return self.annotate(
            annotated_received = Coalesce(Subquery(direct_payments), Value(0), output_field=DecimalField()) + 
                                 Coalesce(Subquery(allocations), Value(0), output_field=DecimalField()),
            annotated_revenue = Case(
                When(revenue_type='fixed', then=F('rate_per_ton')),
                default=F('weight') * F('rate_per_ton'),
                output_field=DecimalField()
            )
        ).annotate(
            annotated_status = Case(
                When(annotated_received__gte=F('annotated_revenue'), annotated_revenue__gt=0, then=Value('Paid')),
                When(annotated_received__gt=0, then=Value('Partially Paid')),
                default=Value('Unpaid'),
                output_field=models.CharField()
            )
        )

    def with_billing_info(self):
        """Annotate queryset with billing status"""
        from django.db.models import Exists, OuterRef
        # Import internally to avoid circular dependency
        from ledger.models import Bill

        return self.annotate(
            annotated_is_billed=Exists(
                Bill.objects.filter(trips=OuterRef('pk'))
            )
        )

class TripManager(models.Manager):
    def get_queryset(self):
        return TripQuerySet(self.model, using=self._db)
    
    def with_payment_info(self):
        return self.get_queryset().with_payment_info()

    def with_billing_info(self):
        return self.get_queryset().with_billing_info()

class Trip(models.Model):
    """
    Trip model to manage transport operations.
    Simplified: No operational expenses, fuel, odometer, or manual status.
    Status is derived from payment.
    """
    objects = TripManager()

    # Payment Status (for legacy reference/labels)
    PAYMENT_STATUS_UNPAID = 'Unpaid'
    PAYMENT_STATUS_PARTIAL = 'Partially Paid'
    PAYMENT_STATUS_PAID = 'Paid'
    
    PAYMENT_STATUS_CHOICES = [
        (PAYMENT_STATUS_UNPAID, 'Unpaid'),
        (PAYMENT_STATUS_PARTIAL, 'Partially Paid'),
        (PAYMENT_STATUS_PAID, 'Paid'),
    ]

    # Revenue type choices
    REVENUE_PER_TON = 'per_ton'
    REVENUE_FIXED = 'fixed'
    
    REVENUE_TYPE_CHOICES = [
        (REVENUE_PER_TON, 'Per Ton'),
        (REVENUE_FIXED, 'Fixed'),
    ]

    # Unique trip identifier
    trip_number = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='Trip Number',
        blank=True
    )
    
    # Revenue type
    revenue_type = models.CharField(
        max_length=10,
        choices=REVENUE_TYPE_CHOICES,
        default=REVENUE_PER_TON,
        verbose_name='Revenue Type'
    )
    
    # Driver assignment (ForeignKey to Driver)
    driver = models.ForeignKey(
        'drivers.Driver',
        on_delete=models.SET_NULL,
        related_name='assigned_trips',
        verbose_name='Assigned Driver',
        null=True,
        blank=True
    )
    
    # Vehicle assignment (ForeignKey to Vehicle)
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name='trips',
        verbose_name='Assigned Vehicle'
    )
    
    # Date of the trip
    date = models.DateTimeField(
        verbose_name='Trip Date',
        default=timezone.now
    )

    # Party details
    party = models.ForeignKey(
        'ledger.Party',
        on_delete=models.PROTECT,
        verbose_name='Party',
        null=True,
        blank=True
    )

    pickup_location = models.CharField(
        max_length=300,
        verbose_name='Pickup Location',
        blank=True
    )
    pickup_lat = models.DecimalField(max_digits=18, decimal_places=10, null=True, blank=True)
    pickup_lng = models.DecimalField(max_digits=18, decimal_places=10, null=True, blank=True)

    delivery_location = models.CharField(
        max_length=300,
        verbose_name='Delivery Location',
        blank=True
    )
    delivery_lat = models.DecimalField(max_digits=18, decimal_places=10, null=True, blank=True)
    delivery_lng = models.DecimalField(max_digits=18, decimal_places=10, null=True, blank=True)

    weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Weight (Tons)',
        help_text='Load weight in Metric Tons'
    )

    rate_per_ton = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Rate per Ton',
        default=0
    )

    # Additional notes
    notes = models.TextField(
        blank=True,
        verbose_name='Trip Notes'
    )
    
    # Audit fields
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_trips',
        verbose_name='Created By'
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Created At'
    )
    
    class Meta:
        verbose_name = 'Trip'
        verbose_name_plural = 'Trips'
        ordering = ['-date', '-created_at']
        permissions = [
            ('can_view_all_trips', 'Can view all trips'),
            ('can_view_driver_dashboard', 'Can access driver dashboard'),
            ('can_view_manager_dashboard', 'Can access manager dashboard'),
        ]
    
    def __str__(self):
        party_name = self.party.name if self.party else "Unknown"
        return f"{self.trip_number} - {party_name} ({self.vehicle.registration_plate})"
    
    def sync_ledger_invoice(self):
        """
        Stop creating Trip Payment Invoices in the ledger.
        If any existing invoice records exist for this trip, delete them.
        """
        from ledger.models import FinancialRecord

        # Find and delete existing invoice record for this trip
        # (Debits now only come from formal Bills/Invoices)
        FinancialRecord.objects.filter(
            associated_trip=self,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).delete()

    def save(self, *args, **kwargs):
        """
        Override save to handle business logic
        """
        is_new = self._state.adding
        old_instance = None
        if not is_new:
            old_instance = Trip.objects.get(pk=self.pk)

        # Handle Trip Number generation and regeneration
        reg_plate = self.vehicle.registration_plate
        
        # If trip exists, check if vehicle changed
        if not is_new:
            if old_instance.vehicle != self.vehicle:
                # Vehicle changed, clear trip_number to trigger regeneration
                self.trip_number = ""

        # Generate Trip Number if not present or cleared
        if not self.trip_number:
            from ledger.models import Sequence

            # Use created_at if available (for re-numbering), else current time
            ref_date = self.created_at or timezone.now()
            
            # Using Sequences for robust atomic numbering
            total_count = Sequence.next_value(f"trip_total_{self.vehicle.pk}")
            month_count = Sequence.next_value(f"trip_month_{self.vehicle.pk}_{ref_date.year}_{ref_date.month}")
            year_count = Sequence.next_value(f"trip_year_{self.vehicle.pk}_{ref_date.year}")
            
            self.trip_number = f"{reg_plate}-{total_count}/{month_count}/{year_count}"
        
        # If trip_number already exists but vehicle plate changed (manual correction)
        # ensure the prefix matches the current plate
        elif not self.trip_number.startswith(reg_plate):
            parts = self.trip_number.rsplit('-', 1)
            if len(parts) > 1:
                last_dash_idx = self.trip_number.rfind('-')
                if last_dash_idx != -1:
                    suffix = self.trip_number[last_dash_idx+1:]
                    self.trip_number = f"{reg_plate}-{suffix}"

        # Perform the actual save
        super().save(*args, **kwargs)

        # Sync to Ledger
        self.sync_ledger_invoice()
    
    @property
    def start_date(self):
        """Alias for date, for backward compatibility"""
        return self.date

    @property
    def revenue(self):
        """Calculate revenue for this trip"""
        if self.revenue_type == self.REVENUE_FIXED:
            return self.rate_per_ton or 0
        
        if self.weight and self.rate_per_ton:
            return self.weight * self.rate_per_ton
        return 0

    @property
    def is_billed(self):
        """Check if this trip is associated with any bill"""
        if hasattr(self, 'annotated_is_billed'):
            return self.annotated_is_billed
        return self.bills.exists()

    @property
    def associated_bill(self):
        """Returns the first associated bill (if any)"""
        return self.bills.first()

    @property
    def gst_amount(self):
        """
        Calculate GST amount for this trip based on its associated bill.
        If no bill exists or GST rate is 0, returns 0.
        """
        bill = self.associated_bill
        if not bill or bill.gst_rate == 0:
            return 0
        
        # Only apply GST if the bill is FINALized
        from ledger.models import Bill
        if bill.status != Bill.STATUS_FINAL:
            return 0

        from decimal import Decimal
        return self.revenue * (Decimal(bill.gst_rate) / Decimal(100))

    @property
    def total_revenue(self):
        """Total revenue including GST (if billed)"""
        return self.revenue + self.gst_amount

    @property
    def amount_received(self):
        """Calculate total received (Payments + Deductions) from direct links and allocations"""
        from ledger.models import FinancialRecord, TransactionCategory, Bill
        # 1. Direct links (Records with type='Income' OR category='Deductions')
        # Exclude Invoice type as they are debits
        direct = self.financial_records.exclude(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).filter(
            models.Q(category__type=TransactionCategory.TYPE_INCOME) | models.Q(category__name='Deductions')
        ).aggregate(total=models.Sum('amount'))['total'] or 0
        
        # 2. M2M Allocations (Assumed to be income or deduction adjustments by nature)
        allocated = self.payment_allocations.aggregate(
            total=models.Sum('amount')
        )['total'] or 0
        
        # 3. If part of a PAID bill, consider it fully received for the billed amount
        billed_received = 0
        bill = self.associated_bill
        if bill and bill.payment_status == Bill.PAYMENT_STATUS_PAID:
            # If the bill is paid, this trip's share is fully covered
            billed_received = self.total_revenue
            # But we must avoid double counting if it was also partially paid via allocations
            return billed_received

        return direct + allocated

    @property
    def payment_status(self):
        """Calculate payment status dynamically based on Total Revenue (incl GST)"""
        received = self.amount_received
        total_rev = self.total_revenue
        
        if total_rev <= 0:
            return self.PAYMENT_STATUS_UNPAID

        if received >= total_rev:
            return self.PAYMENT_STATUS_PAID
        elif received > 0:
            return self.PAYMENT_STATUS_PARTIAL
        else:
            return self.PAYMENT_STATUS_UNPAID

    @property
    def outstanding_balance(self):
        """Calculate outstanding balance dynamically based on Total Revenue (incl GST)"""
        return self.total_revenue - self.amount_received

