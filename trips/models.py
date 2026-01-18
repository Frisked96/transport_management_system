"""
Models for Trips application
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from fleet.models import Vehicle


class Trip(models.Model):
    """
    Trip model to manage transport operations
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
    
    # Unique trip identifier
    trip_number = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='Trip Number',
        blank=True
    )
    
    # Driver assignment (ForeignKey to User)
    driver = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='assigned_trips',
        verbose_name='Assigned Driver'
    )
    
    # Vehicle assignment (ForeignKey to Vehicle)
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name='trips',
        verbose_name='Assigned Vehicle'
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
        ordering = ['-created_at']
        permissions = [
            ('can_view_all_trips', 'Can view all trips'),
            ('can_update_trip_status', 'Can update trip status'),
            ('can_view_driver_dashboard', 'Can access driver dashboard'),
            ('can_view_manager_dashboard', 'Can access manager dashboard'),
        ]
    
    def __str__(self):
        return f"{self.trip_number} ({self.vehicle.registration_plate})"
    
    def clean(self):
        """
        Validate trip logic
        """
        if not self.pk:  # Only on creation
            # Check for active trips for this vehicle
            # Active means not Completed and not Cancelled
            active_trips = Trip.objects.filter(
                vehicle=self.vehicle,
                status=self.STATUS_IN_PROGRESS
            )

            if active_trips.exists():
                raise ValidationError({
                    'vehicle': f"Vehicle {self.vehicle.registration_plate} is currently on an active trip ({active_trips.first().trip_number}). Please close that trip first."
                })

    def save(self, *args, **kwargs):
        """
        Override save to handle business logic
        """
        # Run validation
        if not self.pk:
            self.clean()

            # Generate Trip Number
            # Format: {RegPlate}-{Total}/{Month}/{Year}
            # Total: Total trips for this vehicle
            # Month: This month's trips
            # Year: This year's trips

            now = timezone.now()

            # Count previous trips
            # We use created_at for historical count.
            total_count = Trip.objects.filter(vehicle=self.vehicle).count() + 1

            month_count = Trip.objects.filter(
                vehicle=self.vehicle,
                created_at__month=now.month,
                created_at__year=now.year
            ).count() + 1

            year_count = Trip.objects.filter(
                vehicle=self.vehicle,
                created_at__year=now.year
            ).count() + 1

            self.trip_number = f"{self.vehicle.registration_plate}-{total_count}/{month_count}/{year_count}"


        # Status logic
        if self.status == self.STATUS_COMPLETED and not self.actual_completion_datetime:
            self.actual_completion_datetime = timezone.now()
        elif self.status != self.STATUS_COMPLETED:
            self.actual_completion_datetime = None
        
        super().save(*args, **kwargs)
    
    @property
    def start_date(self):
        """Get the start date from the first leg"""
        first_leg = self.legs.order_by('date').first()
        if first_leg:
            return first_leg.date
        return self.created_at

    @property
    def end_date(self):
        """Get the end date from the last leg"""
        last_leg = self.legs.order_by('-date').first()
        if last_leg:
            return last_leg.date
        return None

    @property
    def is_overdue(self):
        """Check if trip is overdue"""
        if self.status == self.STATUS_COMPLETED:
            return False
        if self.start_date:
             return self.start_date < timezone.now()
        return False
    
    @property
    def duration_display(self):
        """Display trip duration if completed"""
        start = self.start_date
        end = self.end_date
        
        if start and end and end >= start:
            duration = end - start
            days = duration.days
            hours = duration.seconds // 3600
            minutes = (duration.seconds % 3600) // 60
            
            if days > 0:
                return f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
        return "N/A"

    @property
    def total_weight(self):
        """Calculate total weight from all legs"""
        return self.legs.aggregate(total=models.Sum('weight'))['total'] or 0

    @property
    def total_revenue(self):
        """Calculate total estimated revenue from all legs"""
        revenue = 0
        for leg in self.legs.all():
            revenue += leg.revenue
        return revenue

    @property
    def total_cost(self):
        """Calculate total cost (diesel + toll + custom expenses)"""
        diesel = self.diesel_expense or 0
        toll = self.toll_expense or 0
        custom = self.custom_expenses.aggregate(total=models.Sum('amount'))['total'] or 0
        return diesel + toll + custom
    
    # Trip Expenses
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


class TripLeg(models.Model):
    """
    Sub-trip model representing a leg of a trip
    """
    trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        related_name='legs',
        verbose_name='Parent Trip'
    )

    party = models.ForeignKey(
        'ledger.Party',
        on_delete=models.PROTECT,
        verbose_name='Party',
        null=True,
        blank=True
    )

    pickup_location = models.CharField(
        max_length=300,
        verbose_name='Pickup Location'
    )

    delivery_location = models.CharField(
        max_length=300,
        verbose_name='Delivery Location'
    )

    weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Weight (Tons)',
        help_text='Load weight in Metric Tons'
    )

    price_per_ton = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Price per Ton',
        default=0
    )

    date = models.DateTimeField(
        verbose_name='Date',
        null=True,
        blank=True
    )
    
    # Payment Status
    PAYMENT_STATUS_UNPAID = 'Unpaid'
    PAYMENT_STATUS_PARTIAL = 'Partially Paid'
    PAYMENT_STATUS_PAID = 'Paid'
    
    PAYMENT_STATUS_CHOICES = [
        (PAYMENT_STATUS_UNPAID, 'Unpaid'),
        (PAYMENT_STATUS_PARTIAL, 'Partially Paid'),
        (PAYMENT_STATUS_PAID, 'Paid'),
    ]
    
    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default=PAYMENT_STATUS_UNPAID,
        verbose_name='Payment Status'
    )
    
    amount_received = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name='Amount Received'
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Created At'
    )

    class Meta:
        verbose_name = 'Trip Leg'
        verbose_name_plural = 'Trip Legs'
        ordering = ['date']

    def __str__(self):
        party_name = self.party.name if self.party else "Unknown"
        return f"{self.trip.trip_number} - {party_name} ({self.pickup_location} to {self.delivery_location})"

    @property
    def revenue(self):
        """Calculate revenue for this leg"""
        if self.weight and self.price_per_ton:
            return self.weight * self.price_per_ton
        return 0

    @property
    def outstanding_balance(self):
        """Calculate outstanding balance"""
        return self.revenue - self.amount_received
