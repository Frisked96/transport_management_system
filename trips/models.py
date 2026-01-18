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
    
    # Driver assignment (ForeignKey to User) - Optional now
    driver = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
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

    delivery_location = models.CharField(
        max_length=300,
        verbose_name='Delivery Location',
        blank=True
    )

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
    
    # Trip Expenses (Fixed)
    diesel_expense = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='Diesel Expense'
    )
    
    toll_expense = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='Toll Expense'
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
            # Use created_at if available (for re-numbering), else current time
            ref_date = self.created_at or timezone.now()
            
            # Helper to extract count from trip_number
            pattern = re.compile(r'^.*-(\d+)/(\d+)/(\d+)$')

            # 1. Global Sequence (Total)
            # Find last trip before this one (based on ref_date)
            # If self.pk is set (update), exclude self
            qs = Trip.objects.filter(vehicle=self.vehicle)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            
            # Use created_at for reliable ordering
            last_trip = qs.filter(created_at__lte=ref_date).order_by('created_at').last()
            
            total_count = 1
            if last_trip and last_trip.trip_number:
                match = pattern.match(last_trip.trip_number)
                if match:
                    try:
                        total_count = int(match.group(1)) + 1
                    except ValueError:
                        pass
            
            # 2. Month Sequence
            last_month_trip = qs.filter(
                created_at__month=ref_date.month,
                created_at__year=ref_date.year,
                created_at__lte=ref_date
            ).order_by('created_at').last()
            
            month_count = 1
            if last_month_trip and last_month_trip.trip_number:
                match = pattern.match(last_month_trip.trip_number)
                if match:
                    try:
                        month_count = int(match.group(2)) + 1
                    except ValueError:
                        pass

            # 3. Year Sequence
            last_year_trip = qs.filter(
                created_at__year=ref_date.year,
                created_at__lte=ref_date
            ).order_by('created_at').last()
            
            year_count = 1
            if last_year_trip and last_year_trip.trip_number:
                match = pattern.match(last_year_trip.trip_number)
                if match:
                    try:
                        year_count = int(match.group(3)) + 1
                    except ValueError:
                        pass

            # 4. Generate & Verify Uniqueness
            while True:
                candidate = f"{reg_plate}-{total_count}/{month_count}/{year_count}"
                # Check collision (excluding self)
                exists = Trip.objects.filter(trip_number=candidate)
                if self.pk:
                    exists = exists.exclude(pk=self.pk)
                
                if not exists.exists():
                    self.trip_number = candidate
                    break
                
                total_count += 1
        
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

        super().save(*args, **kwargs)
    
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
        ).aggregate(total=models.Sum('amount'))['total'] or 0
        
        # 2. M2M Allocations (Assumed to be income by nature)
        allocated = self.payment_allocations.aggregate(
            total=models.Sum('amount')
        )['total'] or 0
        
        return direct + allocated

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
        """Calculate total cost (diesel + toll + custom expenses)"""
        diesel = self.diesel_expense or 0
        toll = self.toll_expense or 0
        custom = self.custom_expenses.aggregate(total=models.Sum('amount'))['total'] or 0
        return diesel + toll + custom


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


@receiver(post_delete, sender=Trip)
def renumber_trips(sender, instance, **kwargs):
    """
    Renumber subsequent trips when a trip is deleted to fill the gap.
    """
    if not instance.created_at:
        return

    # Find all subsequent trips for the same vehicle
    subsequent_trips = Trip.objects.filter(
        vehicle=instance.vehicle,
        created_at__gt=instance.created_at
    ).order_by('created_at')

    # Regenerate numbers for each subsequent trip
    for trip in subsequent_trips:
        trip.trip_number = "" # Clear to trigger regeneration
        trip.save()