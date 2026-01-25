"""
Models for Trips application
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.db.models import Sum, Case, When, Value, F, DecimalField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from fleet.models import Vehicle
import re


from django.db.models import Sum, Case, When, Value, F, DecimalField

class TripQuerySet(models.QuerySet):
    def with_payment_info(self):
        """Annotate queryset with payment information for filtering and sorting"""
        from ledger.models import FinancialRecord, TripAllocation, TransactionCategory
        
        # Subquery for direct payments (Income types)
        direct_payments = FinancialRecord.objects.filter(
            associated_trip=OuterRef('pk'),
            category__type=TransactionCategory.TYPE_INCOME
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
            # We can't easily calculate revenue in SQL here because it involves multiplication of fields,
            # but we can do it:
            annotated_revenue = F('weight') * F('rate_per_ton')
        ).annotate(
            annotated_status = Case(
                When(annotated_received__gte=F('annotated_revenue'), annotated_revenue__gt=0, then=Value('Paid')),
                When(annotated_received__gt=0, then=Value('Partially Paid')),
                default=Value('Unpaid'),
                output_field=models.CharField()
            )
        )

class TripManager(models.Manager):
    def get_queryset(self):
        return TripQuerySet(self.model, using=self._db)
    
    def with_payment_info(self):
        return self.get_queryset().with_payment_info()

class Trip(models.Model):
    # ... (rest of model) ...
    objects = TripManager()

    """
    Trip model to manage transport operations.
    Refactored to single-leg structure.
    """
    
    # Status choices
    STATUS_IN_PROGRESS = 'In Progress'
    STATUS_COMPLETED = 'Completed'
    STATUS_CANCELLED = 'Cancelled'
    
    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]
    
    # Payment Status
    PAYMENT_STATUS_UNPAID = 'Unpaid'
    PAYMENT_STATUS_PARTIAL = 'Partially Paid'
    PAYMENT_STATUS_PAID = 'Paid'
    
    PAYMENT_STATUS_CHOICES = [
        (PAYMENT_STATUS_UNPAID, 'Unpaid'),
        (PAYMENT_STATUS_PARTIAL, 'Partially Paid'),
        (PAYMENT_STATUS_PAID, 'Paid'),
    ]

    # Unique trip identifier
    trip_number = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='Trip Number',
        blank=True
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
    
    start_odometer = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Start Odometer'
    )

    end_odometer = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='End Odometer'
    )

    # Date of the trip (replaces date from TripLeg)
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

    # Actual completion tracking
    actual_completion_datetime = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Actual Completion Date & Time'
    )
    
    # Status with choices
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_IN_PROGRESS,
        verbose_name='Trip Status'
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
            ('can_update_trip_status', 'Can update trip status'),
            ('can_view_driver_dashboard', 'Can access driver dashboard'),
            ('can_view_manager_dashboard', 'Can access manager dashboard'),
        ]
    
    def __str__(self):
        party_name = self.party.name if self.party else "Unknown"
        return f"{self.trip_number} - {party_name} ({self.vehicle.registration_plate})"
    
    def clean(self):
        """
        Validate trip logic
        """
        pass

    def sync_ledger_invoice(self):
        """
        Synchronize Trip Revenue to FinancialRecord as an Invoice.
        """
        from ledger.models import FinancialRecord, TransactionCategory

        # 1. Check if we have Revenue info
        has_revenue = self.weight is not None and self.rate_per_ton is not None

        # 2. Find existing invoice record
        invoice_qs = FinancialRecord.objects.filter(
            associated_trip=self,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        )

        if has_revenue:
            amount = self.weight * self.rate_per_ton

            # Ensure Category exists
            cat, _ = TransactionCategory.objects.get_or_create(
                name="Trip Revenue",
                defaults={'type': TransactionCategory.TYPE_INCOME, 'description': 'Auto-generated revenue from trips'}
            )

            if invoice_qs.exists():
                # Update existing
                record = invoice_qs.first()
                # Update fields if changed
                should_save = False
                if record.amount != amount:
                    record.amount = amount
                    should_save = True
                if record.party != self.party:
                    record.party = self.party
                    should_save = True
                if record.driver != self.driver:
                    record.driver = self.driver
                    should_save = True
                if record.date != self.date.date():
                    record.date = self.date.date()
                    should_save = True

                if should_save:
                    record.save()
            else:
                # Create new
                if amount >= 0:
                    FinancialRecord.objects.create(
                        associated_trip=self,
                        party=self.party,
                        driver=self.driver,
                        date=self.date.date(),
                        category=cat,
                        amount=amount,
                        record_type=FinancialRecord.RECORD_TYPE_INVOICE,
                        description=f"Invoice for Trip {self.trip_number}"
                    )
        else:
            # If revenue details removed, delete the invoice
            if invoice_qs.exists():
                invoice_qs.delete()

    def save(self, *args, **kwargs):
        """
        Override save to handle business logic
        """
        # Status logic
        if self.status == self.STATUS_COMPLETED and not self.actual_completion_datetime:
            self.actual_completion_datetime = timezone.now()
        elif self.status != self.STATUS_COMPLETED:
            self.actual_completion_datetime = None

        # Handle Trip Number generation and regeneration
        reg_plate = self.vehicle.registration_plate
        
        # If trip exists, check if vehicle changed
        if self.pk:
            old_instance = Trip.objects.get(pk=self.pk)
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
                # Handle cases where registration plate might contain dashes
                # but our format is PLATE-COUNT/MONTH/YEAR
                # We want to replace everything before the LAST dash if it doesn't match
                # Actually, our pattern is PLATE-X/Y/Z. Let's find the last dash.
                last_dash_idx = self.trip_number.rfind('-')
                if last_dash_idx != -1:
                    suffix = self.trip_number[last_dash_idx+1:]
                    self.trip_number = f"{reg_plate}-{suffix}"

        is_new = self._state.adding
        super().save(*args, **kwargs)

        # Sync to Ledger
        self.sync_ledger_invoice()

        if is_new:
            # Create default TripExpense entries
            TripExpense.objects.create(trip=self, name='Diesel', amount=0)
            TripExpense.objects.create(trip=self, name='Toll', amount=0)
    
    @property
    def start_date(self):
        """Alias for date, for backward compatibility"""
        return self.date

    @property
    def revenue(self):
        """Calculate revenue for this trip"""
        if self.weight and self.rate_per_ton:
            return self.weight * self.rate_per_ton
        return 0

    @property
    def amount_received(self):
        """Calculate total received from both direct links and allocations"""
        from ledger.models import FinancialRecord, TransactionCategory
        # 1. Direct links (Records with type='Income')
        direct = self.financial_records.filter(
            category__type=TransactionCategory.TYPE_INCOME
        ).exclude(record_type=FinancialRecord.RECORD_TYPE_INVOICE).aggregate(total=models.Sum('amount'))['total'] or 0
        
        # 2. M2M Allocations (Assumed to be income by nature)
        allocated = self.payment_allocations.aggregate(
            total=models.Sum('amount')
        )['total'] or 0
        
        return direct + allocated

    def check_and_close_trip(self):
        """
        Check if trip should be automatically closed.
        Conditions:
        1. Revenue > 0
        2. Fully Paid (Total Received >= Revenue)
        3. Trip Date has passed
        """
        if self.status == self.STATUS_COMPLETED:
            return

        # 1. Revenue
        revenue = self.revenue
        if revenue <= 0:
            return

        # 2. Date Check (Must be in past)
        if self.date > timezone.now():
            return

        # 3. Paid Amount
        received = self.amount_received

        if received >= revenue:
            self.status = self.STATUS_COMPLETED
            if not self.actual_completion_datetime:
                self.actual_completion_datetime = timezone.now()
            self.save(update_fields=['status', 'actual_completion_datetime'])

    @property
    def payment_status(self):
        """Calculate payment status dynamically"""
        received = self.amount_received
        rev = self.revenue
        
        if received >= rev and rev > 0:
            return self.PAYMENT_STATUS_PAID
        elif received > 0:
            return self.PAYMENT_STATUS_PARTIAL
        else:
            return self.PAYMENT_STATUS_UNPAID

    @property
    def outstanding_balance(self):
        """Calculate outstanding balance dynamically"""
        return self.revenue - self.amount_received

    @property
    def total_cost(self):
        """Calculate total cost (from TripExpense)"""
        return self.custom_expenses.aggregate(total=models.Sum('amount'))['total'] or 0


class TripExpense(models.Model):
    """
    Custom expenses associated with a trip
    """
    trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        related_name='custom_expenses',
        verbose_name='Trip'
    )
    
    name = models.CharField(
        max_length=200,
        verbose_name='Expense Name'
    )
    
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Amount'
    )
    
    notes = models.TextField(
        blank=True,
        verbose_name='Notes'
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Created At'
    )
    
    class Meta:
        verbose_name = 'Trip Expense'
        verbose_name_plural = 'Trip Expenses'
        ordering = ['created_at']
    
    def __str__(self):
        return f"{self.name} - {self.amount}"

