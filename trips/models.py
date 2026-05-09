"""
Models for Trips application
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Sum, Case, When, Value, F, DecimalField, OuterRef, Subquery, ExpressionWrapper
from django.db.models.functions import Coalesce
from fleet.models import Vehicle
import re

class TripQuerySet(models.QuerySet):
    def with_payment_info(self):
        """
        Simplified annotation to avoid parser stack overflow.
        Basic fields for list view filtering/sorting.
        """
        from ledger.models import FinancialRecord, TripAllocation, TransactionCategory, Bill
        
        # Subquery for direct payments (Income types or Deductions)
        direct_payments = FinancialRecord.objects.filter(
            associated_trip=OuterRef('pk')
        ).exclude(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).filter(
            models.Q(category__type=TransactionCategory.TYPE_INCOME) | 
            models.Q(category__name__in=['Deductions', 'TDS', 'Shortage'])
        ).values('associated_trip').annotate(
            total=Sum('amount')
        ).values('total')

        # Subquery for allocations
        allocations = TripAllocation.objects.filter(
            trip=OuterRef('pk')
        ).values('trip').annotate(
            total=Sum('amount')
        ).values('total')

        # Subquery for associated bill's GST rate
        bill_gst_rate = Bill.objects.filter(trips=OuterRef('pk')).values('gst_rate')[:1]

        # Basic revenue and received amount
        qs = self.annotate(
            direct_received = Coalesce(Subquery(direct_payments), Value(0, output_field=DecimalField()), output_field=DecimalField()) + 
                              Coalesce(Subquery(allocations), Value(0, output_field=DecimalField()), output_field=DecimalField()),
            annotated_revenue = Case(
                When(revenue_type='fixed', then=F('rate_per_ton')),
                default=F('weight') * F('rate_per_ton'),
                output_field=DecimalField()
            ),
            # Use bill rate if available
            annotated_gst_rate = Case(
                When(bills__isnull=False, then=Coalesce(Subquery(bill_gst_rate), Value(0, output_field=DecimalField()), output_field=DecimalField())),
                When(route__route_type__in=['local', 'intra'], then=Value(18.0, output_field=DecimalField())),
                When(gst_type_snapshot__in=['GST', 'IGST'], then=Value(18.0, output_field=DecimalField())),
                default=Value(0, output_field=DecimalField()),
                output_field=DecimalField()
            )
        )

        # Calculate GST and Total Revenue
        qs = qs.annotate(
            annotated_gst_amount = ExpressionWrapper(F('annotated_revenue') * F('annotated_gst_rate') / Value(100), output_field=DecimalField()),
            annotated_total_revenue = ExpressionWrapper(F('annotated_revenue') + (F('annotated_revenue') * F('annotated_gst_rate') / Value(100)), output_field=DecimalField())
        )

        # Calculate Outstanding and Status
        return qs.annotate(
            annotated_received = F('direct_received'), # Proportional bill logic handled in Python/Properties
            annotated_outstanding = F('annotated_total_revenue') - F('direct_received'),
            annotated_status = Case(
                When(direct_received__gte=F('annotated_total_revenue'), annotated_total_revenue__gt=0, then=Value('Paid')),
                When(direct_received__gt=0, then=Value('Partially Paid')),
                default=Value('Unpaid'),
                output_field=models.CharField()
            )
        )

    def with_billing_info(self):
        """Annotate queryset with billing status and GST type"""
        from django.db.models import Exists, OuterRef, Case, When, Value, F, CharField
        # Import internally to avoid circular dependency
        from ledger.models import Bill

        return self.annotate(
            annotated_is_billed=Exists(
                Bill.objects.filter(trips=OuterRef('pk'))
            ),
            annotated_gst_type=Case(
                When(gst_type_snapshot__gt='', then=F('gst_type_snapshot')),
                When(route__route_type='intra', then=Value('IGST')),
                When(route__route_type='none', then=Value('NONE')),
                default=Value('GST'),
                output_field=CharField()
            )
        )

class TripManager(models.Manager):
    def get_queryset(self):
        return TripQuerySet(self.model, using=self._db)
    
    def with_payment_info(self):
        return self.get_queryset().with_payment_info()

    def with_billing_info(self):
        return self.get_queryset().with_billing_info()

class Route(models.Model):
    """
    Pre-defined routes with pickup and delivery locations.
    Also defines if the route is local (GST) or intra/interstate (IGST).
    """
    pickup_location = models.CharField(
        max_length=300,
        verbose_name='Pickup Location'
    )
    delivery_location = models.CharField(
        max_length=300,
        verbose_name='Delivery Location'
    )
    
    ROUTE_TYPE_LOCAL = 'local'
    ROUTE_TYPE_INTRA = 'intra'
    ROUTE_TYPE_NONE = 'none'
    ROUTE_TYPE_CHOICES = [
        (ROUTE_TYPE_LOCAL, 'Local (GST)'),
        (ROUTE_TYPE_INTRA, 'Intra/Interstate (IGST)'),
        (ROUTE_TYPE_NONE, 'Non-GST'),
    ]
    route_type = models.CharField(
        max_length=10, 
        choices=ROUTE_TYPE_CHOICES, 
        default=ROUTE_TYPE_LOCAL,
        verbose_name='Route Type'
    )

    default_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='Default Rate',
        help_text='Suggested rate for trips on this route'
    )

    class Meta:
        verbose_name = 'Route'
        verbose_name_plural = 'Routes'
        unique_together = ['pickup_location', 'delivery_location', 'route_type']

    def __str__(self):
        return f"{self.pickup_location} to {self.delivery_location} ({self.get_route_type_display()})"

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

    # LR Number
    lr_no = models.CharField(
        max_length=100,
        verbose_name='LR No',
        blank=True,
        null=True
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
        verbose_name='Party'
    )

    route = models.ForeignKey(
        Route,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Route'
    )

    # Snapshot Fields (Ensure historical integrity)
    gst_type_snapshot = models.CharField(
        max_length=10,
        blank=True,
        verbose_name='GST Type (Snapshot)',
        help_text='IGST or GST. Snapshotted from Route at creation.'
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

    can_be_grouped = models.BooleanField(
        default=True,
        verbose_name='Can be Grouped',
        help_text='Whether this trip can be grouped with others in a bill'
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
        indexes = [
            models.Index(fields=['date', 'created_at']),
            models.Index(fields=['vehicle', 'date']),
            models.Index(fields=['party', 'date']),
            models.Index(fields=['driver', 'date']),
        ]
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
        Manage accrual-based revenue for this trip.
        - If NOT billed: Create/Update a 'Trip Payment' invoice record in the ledger.
        - If Billed: Delete the individual trip record (Bill handles the consolidated accrual).
        """
        from ledger.models import FinancialRecord, TransactionCategory, CompanyAccount

        # If trip is billed, individual trip accruals should be removed
        if self.is_billed:
            FinancialRecord.objects.filter(
                associated_trip=self,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).delete()
            return

        # If no revenue or no party, no accrual
        if not self.revenue or not self.party:
            FinancialRecord.objects.filter(
                associated_trip=self,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).delete()
            return

        # Get default category
        category, _ = TransactionCategory.objects.get_or_create(
            name='Trip Payment',
            type=TransactionCategory.TYPE_INCOME
        )

        # Get default company account (issuer)
        account = CompanyAccount.objects.first()
        if not account:
             return

        # Find or create individual trip invoice record
        FinancialRecord.objects.update_or_create(
            associated_trip=self,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            defaults={
                'date': self.date,
                'account': account,
                'party': self.party,
                'category': category,
                'amount': self.revenue, # Subtotal only for unbilled
                'description': f"Accrual for Trip {self.trip_number}",
            }
        )

    def save(self, *args, **kwargs):
        """
        Override save to handle business logic
        """
        is_new = self._state.adding
        old_instance = None
        if not is_new:
            old_instance = Trip.objects.get(pk=self.pk)

        # Sync locations from route if provided
        if self.route:
            self.pickup_location = self.route.pickup_location
            self.delivery_location = self.route.delivery_location
            if not self.gst_type_snapshot:
                from ledger.models import Bill
                if self.route.route_type == Route.ROUTE_TYPE_INTRA:
                    self.gst_type_snapshot = Bill.GST_TYPE_IGST
                elif self.route.route_type == Route.ROUTE_TYPE_NONE:
                    self.gst_type_snapshot = Bill.GST_TYPE_NONE
                else:
                    self.gst_type_snapshot = Bill.GST_TYPE_GST

        # Handle Trip Number generation and regeneration
        reg_plate = self.vehicle.registration_plate
        
        # If trip exists, check if vehicle changed
        vehicle_changed = False
        if not is_new and old_instance.vehicle != self.vehicle:
            vehicle_changed = True
            self.trip_number = "" # Clear to trigger regeneration

        # Generate Trip Number if not present or cleared
        if not self.trip_number:
            from ledger.models import Sequence

            # Use created_at if available (for re-numbering), else current time
            ref_date = self.date or timezone.now()
            
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

        # Restrict changing Party if Trip is Billed
        if not is_new and old_instance.party != self.party:
            if self.is_billed:
                raise ValidationError(f"Cannot change Party for Trip {self.trip_number} as it is already billed.")

        # Perform the actual save
        super().save(*args, **kwargs)

        # If vehicle changed, recalculate for the OLD vehicle
        if vehicle_changed:
            Trip.recalculate_vehicle_trip_numbers(old_instance.vehicle)

        # Sync to Ledger
        self.sync_ledger_invoice()

    @classmethod
    def recalculate_vehicle_trip_numbers(cls, vehicle):
        """
        Recalculate and update all trip numbers for a specific vehicle to ensure gap-less sequencing.
        """
        from ledger.models import Sequence
        
        # Order by date first, then by created_at to maintain chronological order
        trips = cls.objects.filter(vehicle=vehicle).order_by('date', 'created_at')
        reg_plate = vehicle.registration_plate
        
        # Track counts
        total_count = 0
        monthly_counts = {} # Key: (year, month)
        yearly_counts = {}  # Key: year
        
        trips_to_update = []
        
        for trip in trips:
            total_count += 1
            
            # Use trip date
            ref_date = trip.date
            year, month = ref_date.year, ref_date.month
            
            monthly_key = (year, month)
            monthly_counts[monthly_key] = monthly_counts.get(monthly_key, 0) + 1
            yearly_counts[year] = yearly_counts.get(year, 0) + 1
            
            new_number = f"{reg_plate}-{total_count}/{monthly_counts[monthly_key]}/{yearly_counts[year]}"
            
            if trip.trip_number != new_number:
                trip.trip_number = new_number
                trips_to_update.append(trip)
        
        if trips_to_update:
            from django.db import transaction
            import uuid
            with transaction.atomic():
                # Step 1: Set to temporary unique numbers to avoid collisions during bulk update
                # This is necessary because SQLite checks unique constraints immediately
                temp_nums = {t.pk: t.trip_number for t in trips_to_update}
                for trip in trips_to_update:
                    trip.trip_number = f"TEMP-{uuid.uuid4().hex[:8]}-{trip.pk}"
                cls.objects.bulk_update(trips_to_update, ['trip_number'])
                
                # Step 2: Set to final numbers
                for trip in trips_to_update:
                    trip.trip_number = temp_nums[trip.pk]
                cls.objects.bulk_update(trips_to_update, ['trip_number'])
        
        # Update sequences to match the new state so future trips continue correctly
        Sequence.objects.filter(key=f"trip_total_{vehicle.pk}").update(value=total_count)
        for (year, month), val in monthly_counts.items():
            Sequence.objects.filter(key=f"trip_month_{vehicle.pk}_{year}_{month}").update(value=val)
        for year, val in yearly_counts.items():
            Sequence.objects.filter(key=f"trip_year_{vehicle.pk}_{year}").update(value=val)

    @property
    def gst_type(self):
        """Returns GST type based on Snapshot, falling back to Route"""
        if self.gst_type_snapshot:
            return self.gst_type_snapshot
            
        from ledger.models import Bill
        if self.route:
            if self.route.route_type == Route.ROUTE_TYPE_INTRA:
                return Bill.GST_TYPE_IGST
            elif self.route.route_type == Route.ROUTE_TYPE_NONE:
                return Bill.GST_TYPE_NONE
        return Bill.GST_TYPE_GST

    @property
    def start_date(self):
        """Alias for date, for backward compatibility"""
        return self.date

    @property
    def revenue(self):
        """Calculate revenue for this trip"""
        if hasattr(self, 'annotated_revenue'):
            return self.annotated_revenue
            
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
        
        if hasattr(self, '_prefetched_objects_cache') and 'bills' in self._prefetched_objects_cache:
            return len(self.bills.all()) > 0
            
        return self.bills.exists()

    @property
    def associated_bill(self):
        """Returns the first associated bill (if any)"""
        if hasattr(self, '_prefetched_objects_cache') and 'bills' in self._prefetched_objects_cache:
            bills = self.bills.all()
            return bills[0] if bills else None
        return self.bills.first()

    @property
    def gst_amount(self):
        """
        Calculate GST amount for this trip.
        If billed: uses bill's rate.
        If unbilled but taxable: uses 18% standard rate.
        """
        if hasattr(self, 'annotated_gst_amount'):
            return self.annotated_gst_amount

        from decimal import Decimal
        
        # 1. Use Bill Rate if available
        bill = self.associated_bill
        if bill and bill.gst_rate:
            return self.revenue * (Decimal(bill.gst_rate) / Decimal(100))
        
        # 2. If unbilled, check if route/snapshot is taxable
        is_taxable = False
        if self.gst_type_snapshot in ['GST', 'IGST']:
            is_taxable = True
        elif self.route and self.route.route_type in ['local', 'intra']:
            is_taxable = True
            
        if is_taxable:
            return self.revenue * (Decimal('18') / Decimal('100'))
            
        return Decimal('0')

    @property
    def total_revenue(self):
        """Total revenue including GST (if billed)"""
        if hasattr(self, 'annotated_total_revenue'):
            return self.annotated_total_revenue
        return self.revenue + self.gst_amount

    @property
    def amount_received(self):
        """Calculate total received (Payments + Deductions) from direct links and allocations"""
        if hasattr(self, 'annotated_received'):
            return self.annotated_received

        from ledger.models import FinancialRecord, TransactionCategory, Bill
        
        # Check for prefetch cache
        is_records_prefetched = hasattr(self, '_prefetched_objects_cache') and 'financial_records' in self._prefetched_objects_cache
        is_allocations_prefetched = hasattr(self, '_prefetched_objects_cache') and 'payment_allocations' in self._prefetched_objects_cache

        # 1. Direct links
        if is_records_prefetched:
            direct = sum(
                r.amount for r in self.financial_records.all()
                if r.record_type != FinancialRecord.RECORD_TYPE_INVOICE and
                (r.category.type == TransactionCategory.TYPE_INCOME or r.category.name in ["Deductions", "TDS", "Shortage"])
            )
        else:
            direct = self.financial_records.exclude(
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).filter(
                models.Q(category__type=TransactionCategory.TYPE_INCOME) | 
                models.Q(category__name__in=["Deductions", "TDS", "Shortage"])
            ).aggregate(total=models.Sum('amount'))['total'] or 0
        
        # 2. M2M Allocations
        if is_allocations_prefetched:
            allocated = sum(a.amount for a in self.payment_allocations.all())
        else:
            allocated = self.payment_allocations.aggregate(
                total=models.Sum('amount')
            )['total'] or 0
        
        # 3. Share of Bill Payments/Adjustments
        bill = self.associated_bill
        if bill:
            if bill.payment_status == Bill.PAYMENT_STATUS_PAID:
                return self.total_revenue
            
            # Use Bill.amount_received logic but only for direct bill payments
            # (Trip allocations are already counted in 'allocated' above)
            
            # Direct payments to bill
            direct_bill = bill.financial_records.exclude(
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).filter(
                models.Q(category__type=TransactionCategory.TYPE_INCOME) | 
                models.Q(category__name__in=["Deductions", "TDS", "Shortage", "Credit Note", "Debit Note"])
            ).aggregate(total=models.Sum('amount'))['total'] or 0
            
            # Adjustments
            adjustments = 0
            for adj in bill.adjustment_bills.select_related('category').all():
                if adj.category:
                    if adj.category.name == 'Credit Note':
                        adjustments += adj.rounded_total
                    elif adj.category.name == 'Debit Note':
                        adjustments -= adj.rounded_total
            
            bill_pool = direct_bill + adjustments
            if bill_pool > 0:
                bill_total = bill.rounded_total
                if bill_total > 0:
                    share = (self.total_revenue / bill_total) * bill_pool
                    return direct + allocated + share

        return direct + allocated

    @property
    def payment_status(self):
        """Calculate payment status dynamically based on Total Revenue (incl GST)"""
        if hasattr(self, 'annotated_status'):
            return self.annotated_status

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
        if hasattr(self, 'annotated_outstanding'):
            return self.annotated_outstanding
        return self.total_revenue - self.amount_received


# --- Signals ---
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

@receiver(post_delete, sender=Trip)
def recalculate_on_trip_delete(sender, instance, **kwargs):
    """
    Trigger recalculation of trip numbers for a vehicle when a trip is deleted.
    """
    # Use a small delay or ensure we don't trigger recursively if it were save
    # But for delete it's straightforward.
    Trip.recalculate_vehicle_trip_numbers(instance.vehicle)

@receiver(post_save, sender=Trip)
def recalculate_on_trip_update(sender, instance, created, **kwargs):
    """
    Trigger recalculation if date was changed (affecting sequence).
    Vehicle change is already handled in save() override.
    """
    if not created:
        # Check if date changed
        # Since we don't have easy access to 'old' instance here without another query
        # and trip numbers are chronological, any update might justify a sync.
        # However, to be efficient, we only do it if the number would actually change.
        # For simplicity and robust sequencing, we'll run it.
        Trip.recalculate_vehicle_trip_numbers(instance.vehicle)
