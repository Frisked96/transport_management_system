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
        ).exclude(record_type=FinancialRecord.RECORD_TYPE_INVOICE).values('associated_trip').annotate(
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

    diesel_liters = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Diesel Liters'
    )

    diesel_total_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Diesel Total Cost'
    )

    diesel_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Diesel Rate (Price/Ltr)'
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
        # Check if we are adding a new trip or changing the vehicle of an existing trip
        check_vehicle = False
        if self._state.adding:
            check_vehicle = True
        else:
            try:
                old_instance = Trip.objects.get(pk=self.pk)
                if old_instance.vehicle != self.vehicle:
                    check_vehicle = True
            except Trip.DoesNotExist:
                check_vehicle = True
                
        if check_vehicle and self.vehicle:
            # Check if there are any uncompleted trips for this vehicle
            uncompleted_trips = Trip.objects.filter(
                vehicle=self.vehicle
            ).exclude(status=self.STATUS_COMPLETED)
            
            if not self._state.adding:
                uncompleted_trips = uncompleted_trips.exclude(pk=self.pk)
            
            if uncompleted_trips.exists():
                latest_uncompleted = uncompleted_trips.order_by('-date').first()
                trip_num = latest_uncompleted.trip_number or "Unknown"
                raise ValidationError(
                    f"Cannot create or assign a trip to vehicle {self.vehicle.registration_plate} "
                    f"because it has an uncompleted trip ({trip_num}). "
                    "Please mark the old trip as completed first."
                )

        if self.start_odometer is not None and self.end_odometer is not None:
            if self.end_odometer < self.start_odometer:
                # Raising as a non-field error (string) to prevent ValueError 
                # in forms that don't include both odometer fields.
                raise ValidationError(
                    f"End Odometer ({self.end_odometer}) cannot be less than Start Odometer ({self.start_odometer}). "
                    "Did you enter the distance covered instead of the actual odometer reading? "
                    "Please correct the odometer readings in the 'Edit Trip' page."
                )

    def sync_ledger_invoice(self):
        """
        Synchronize Trip Payment to FinancialRecord as an Invoice.
        If the trip is part of a finalized Bill, the individual trip record is removed
        in favor of the consolidated Bill record.
        """
        from ledger.models import FinancialRecord, TransactionCategory

        # 1. Check if we have Revenue info
        has_revenue = self.weight is not None and self.rate_per_ton is not None

        # 2. Check if already part of a bill (any status)
        is_billed = self.bills.exists()

        # 3. Find existing invoice record
        invoice_qs = FinancialRecord.objects.filter(
            associated_trip=self,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        )

        # If it's part of a bill, we DON'T want an individual trip invoice
        # because the Bill (Invoice) will have its own record.
        if is_billed or not has_revenue:
            if invoice_qs.exists():
                invoice_qs.delete()
            return

        # Otherwise, maintain individual record
        amount = self.weight * self.rate_per_ton
        cat, _ = TransactionCategory.objects.get_or_create(
            name="Trip Payment",
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

    def sync_fuel_log(self):
        """Synchronize diesel fields to fleet.FuelLog"""
        from fleet.models import FuelLog
        
        # Check if we have enough data to create a fuel log
        if self.diesel_liters and self.diesel_total_cost and self.diesel_liters > 0:
            FuelLog.objects.update_or_create(
                trip=self,
                defaults={
                    'vehicle': self.vehicle,
                    'date': self.date.date(),
                    'liters': self.diesel_liters,
                    'rate': self.diesel_rate or 0,
                    'total_cost': self.diesel_total_cost,
                    'odometer': self.start_odometer or self.vehicle.current_odometer or 0
                }
            )
        else:
            # If data is missing or zeroed out, remove any existing fuel log for this trip
            FuelLog.objects.filter(trip=self).delete()

    def save(self, *args, **kwargs):
        """
        Override save to handle business logic
        """
        is_new = self._state.adding
        old_instance = None
        if not is_new:
            old_instance = Trip.objects.get(pk=self.pk)

        # 1. Start Odometer Logic: Default to vehicle's current odometer on creation
        if is_new and not self.start_odometer:
            self.start_odometer = self.vehicle.current_odometer

        # 2. Status logic: Handle completion datetime
        if self.status == self.STATUS_COMPLETED and not self.actual_completion_datetime:
            self.actual_completion_datetime = timezone.now()
        elif self.status != self.STATUS_COMPLETED:
            self.actual_completion_datetime = None

        # 3. Diesel Calculation Logic
        if self.diesel_liters:
            from decimal import Decimal
            liters = Decimal(str(self.diesel_liters))
            
            if self.diesel_rate and not self.diesel_total_cost:
                self.diesel_total_cost = liters * Decimal(str(self.diesel_rate))
            elif self.diesel_total_cost and not self.diesel_rate:
                if liters > 0:
                    self.diesel_rate = Decimal(str(self.diesel_total_cost)) / liters

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

        # 3. Post-save logic: Update Vehicle Odometer and Tyre KM
        if self.end_odometer:
            # Update vehicle's current odometer if this is the most recent trip or has highest odo
            if self.end_odometer > self.vehicle.current_odometer:
                self.vehicle.current_odometer = self.end_odometer
                self.vehicle.save(update_fields=['current_odometer'])

        if self.status == self.STATUS_COMPLETED and self.end_odometer:
            # Update Tyres total_km
            if self.start_odometer and self.end_odometer > self.start_odometer:
                distance = self.end_odometer - self.start_odometer
                
                # Check if we should update (avoid double counting if already completed)
                should_update_tyres = False
                if is_new:
                    should_update_tyres = True
                elif old_instance.status != self.STATUS_COMPLETED:
                    should_update_tyres = True
                elif old_instance.end_odometer != self.end_odometer or old_instance.start_odometer != self.start_odometer:
                    # If odo changed, we need to adjust the difference
                    old_distance = (old_instance.end_odometer or 0) - (old_instance.start_odometer or 0)
                    distance_diff = distance - old_distance
                    for tyre in self.vehicle.tyres.all():
                        tyre.total_km += distance_diff
                        tyre.save(update_fields=['total_km'])
                
                if should_update_tyres:
                    for tyre in self.vehicle.tyres.all():
                        tyre.total_km += distance
                        tyre.save(update_fields=['total_km'])

        # 4. Chain Update: If end_odometer changed, update the next trip's start_odometer
        if not is_new and old_instance.end_odometer != self.end_odometer:
            next_trip = Trip.objects.filter(
                vehicle=self.vehicle,
                date__gt=self.date
            ).order_by('date').first()
            
            if next_trip:
                next_trip.start_odometer = self.end_odometer
                next_trip.save(update_fields=['start_odometer'])

        # Sync to Ledger
        self.sync_ledger_invoice()

        # Sync Fuel Log
        update_fields = kwargs.get('update_fields')
        if update_fields is None or any(f in update_fields for f in ['diesel_liters', 'diesel_total_cost', 'diesel_rate', 'date', 'vehicle', 'start_odometer']):
            self.sync_fuel_log()

        if is_new:
            # Create default TripExpense entries
            TripExpense.objects.create(trip=self, name='Diesel', amount=self.diesel_total_cost or 0)
            TripExpense.objects.create(trip=self, name='Toll', amount=0)
        else:
            # Update Diesel expense if total_cost changed
            diesel_exp = TripExpense.objects.filter(trip=self, name='Diesel').first()
            if diesel_exp:
                if diesel_exp.amount != (self.diesel_total_cost or 0):
                    diesel_exp.amount = self.diesel_total_cost or 0
                    diesel_exp.save(update_fields=['amount'])
            else:
                TripExpense.objects.create(trip=self, name='Diesel', amount=self.diesel_total_cost or 0)
    
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
        direct = self.financial_records.filter(
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
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

    def check_and_close_trip(self):
        """
        Check if trip should be automatically closed.
        Conditions:
        1. Total Revenue > 0
        2. Fully Paid (Total Received >= Total Revenue OR associated Bill is PAID)
        3. Trip Date has passed
        """
        if self.status == self.STATUS_COMPLETED:
            return

        # 1. Total Revenue (Incl GST if applicable)
        total_rev = self.total_revenue
        if total_rev <= 0:
            return

        # 2. Date Check (Must be in past)
        if self.date > timezone.now():
            return

        # 3. Paid Amount
        received = self.amount_received
        
        from ledger.models import Bill
        bill = self.associated_bill
        is_bill_paid = bill and bill.payment_status == Bill.PAYMENT_STATUS_PAID

        if received >= total_rev or is_bill_paid:
            self.status = self.STATUS_COMPLETED
            if not self.actual_completion_datetime:
                self.actual_completion_datetime = timezone.now()
            self.save(update_fields=['status', 'actual_completion_datetime'])

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

    @property
    def total_cost(self):
        """Calculate total cost (from TripExpense)"""
        return self.custom_expenses.aggregate(total=models.Sum('amount'))['total'] or 0

    @property
    def net_profit_excl_gst(self):
        """Calculate net profit for this trip (Revenue Excl. GST - Total Cost)"""
        return self.revenue - self.total_cost

    @property
    def net_profit_incl_gst(self):
        """Calculate net profit for this trip (Revenue Incl. GST - Total Cost)"""
        return self.total_revenue - self.total_cost

    @property
    def net_profit(self):
        """Alias for backward compatibility"""
        return self.net_profit_excl_gst

    @property
    def total_diesel_liters(self):
        """Total liters used in trip"""
        return self.diesel_liters or 0

    @property
    def total_fuel_cost(self):
        """Total cost of fuel for this trip"""
        return self.diesel_total_cost or 0

    @property
    def distance_covered(self):
        """Distance covered in KM"""
        if self.start_odometer is not None and self.end_odometer is not None:
            return max(0, self.end_odometer - self.start_odometer)
        return 0

    @property
    def diesel_average(self):
        """Diesel average (mileage) in KM/L"""
        liters = self.total_diesel_liters
        distance = self.distance_covered
        if liters > 0 and distance > 0:
            return distance / liters
        return 0


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

    def save(self, *args, **kwargs):
        """Sync diesel expense back to Trip model fields if it matches 'Diesel'"""
        super().save(*args, **kwargs)
        if self.name == 'Diesel' and self.trip:
            # Only update if the value has actually changed to avoid infinite recursion
            if self.trip.diesel_total_cost != self.amount:
                self.trip.diesel_total_cost = self.amount
                # We use save(update_fields) to trigger the Trip.save logic 
                # but only for this field.
                self.trip.save(update_fields=['diesel_total_cost'])

@receiver(post_delete, sender=Trip)
def delete_related_fuel_log(sender, instance, **kwargs):
    """Ensure FuelLog is deleted when Trip is deleted"""
    from fleet.models import FuelLog
    FuelLog.objects.filter(trip=instance).delete()

