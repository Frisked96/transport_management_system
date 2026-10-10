"""
Trip model and managers for Trips application
"""
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.db.models import Case, When, Value, F, DecimalField, OuterRef
from fleet.models import Vehicle
from .route import Route


class TripQuerySet(models.QuerySet):
    def with_payment_info(self):
        """
        Ultra-lightweight payment info using cached fields.
        Backward compatible with previous annotation names.
        """
        return self.annotate(
            annotated_revenue=F('revenue_cached'),
            annotated_gst_amount=F('gst_amount_cached'),
            annotated_total_revenue=F('total_revenue_cached'),
            annotated_received=F('amount_received_cached'),
            annotated_outstanding=F('outstanding_balance_cached'),
            annotated_status=F('payment_status_cached')
        )

    def with_billing_info(self):
        """Annotate queryset with billing status and GST type"""
        from django.db.models import Exists, OuterRef, Case, When, Value, F, CharField
        # Import internally to avoid circular dependency
        from ledger.models import Bill

        return self.annotate(
            annotated_is_billed=Exists(
                Bill.objects.filter(trips=OuterRef('pk'), customer_bill__isnull=True).exclude(party__party_type='Creditor')
            ),
            annotated_is_creditor_billed=Exists(
                Bill.objects.filter(trips=OuterRef('pk')).filter(
                    models.Q(customer_bill__isnull=False) | models.Q(party__party_type='Creditor')
                )
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
    date = models.DateField(
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

    vendor_hire_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Vendor Hire Amount',
        default=0,
        help_text='Amount payable to the vendor/owner for attached vehicles'
    )

    # Additional notes
    notes = models.TextField(
        blank=True,
        verbose_name='Trip Notes'
    )

    # Cached Financial Fields
    revenue_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Revenue (Cached)')
    gst_amount_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='GST (Cached)')
    total_revenue_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total Revenue (Cached)')
    amount_received_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Amount Received (Cached)')
    outstanding_balance_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Outstanding (Cached)')
    payment_status_cached = models.CharField(max_length=20, default='Unpaid', verbose_name='Payment Status (Cached)')

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

    # Deletion flag to prevent signals from saving a deleted object
    _is_being_deleted = False
    
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
        try:
            party_name = self.party.name if self.party else "Unknown"
        except ObjectDoesNotExist:
            party_name = "Deleted Party"
        try:
            plate = self.vehicle.registration_plate if self.vehicle else "No Vehicle"
        except ObjectDoesNotExist:
            plate = "Deleted Vehicle"
        return f"{self.trip_number} - {party_name} ({plate})"
    
    @property
    def local_date(self):
        """
        Returns the trip date (direct alias for backward compatibility).
        """
        return self.date

    @property
    def local_date_only(self):
        """
        Returns the trip date object (direct alias for backward compatibility).
        """
        return self.date

    def sync_ledger_invoice(self):
        """
        Manage accrual-based revenue for this trip.
        """
        from ledger.services import TripFinancialService
        return TripFinancialService.sync_trip_accrual(self)

    def clean(self):
        """
        Custom validation to prevent changes to billed trips.
        """
        super().clean()
        if self.pk and self.is_billed:
            try:
                old_instance = getattr(self, '_old_instance', None)
                if old_instance is None:
                    old_instance = Trip.objects.get(pk=self.pk)
                    self._old_instance = old_instance
                
                financial_fields = ['weight', 'rate_per_ton', 'revenue_type', 'route', 'party']
                changed_fields = []
                for field in financial_fields:
                    if getattr(old_instance, field) != getattr(self, field):
                        changed_fields.append(field)
                
                if changed_fields:
                    # Use verbose names for the error message
                    field_names = []
                    for f in changed_fields:
                        try:
                            field_names.append(str(self._meta.get_field(f).verbose_name))
                        except:
                            field_names.append(f)

                    raise ValidationError(
                        f"Cannot change {', '.join(field_names)} as this trip is already billed. "
                        "Delete the associated bill first to make corrections."
                    )
            except Trip.DoesNotExist:
                pass

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        for attr in ('_is_billed_cache', '_associated_bill_cache', '_creditor_bill_cache', '_old_instance'):
            if hasattr(self, attr):
                delattr(self, attr)

    def delete(self, *args, **kwargs):
        """
        Override delete to set a flag that prevents signals from trying to save 
        this object after it's gone from the database.
        """
        self._is_being_deleted = True
        super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        """
        Override save to handle business logic
        """
        is_new = self._state.adding
        old_instance = getattr(self, '_old_instance', None)
        if not is_new and old_instance is None:
            try:
                old_instance = Trip.objects.get(pk=self.pk)
                self._old_instance = old_instance
            except Trip.DoesNotExist:
                # If the trip was deleted, we shouldn't be saving it
                return

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
        if not is_new and old_instance:
            vehicle_changed = (old_instance.vehicle_id != self.vehicle_id)
            if vehicle_changed:
                self.trip_number = "" # Clear to trigger regeneration
            self._date_changed = (old_instance.date != self.date)
            self._vehicle_changed = vehicle_changed
        else:
            self._date_changed = False
            self._vehicle_changed = False

        # Generate Trip Number if not present or cleared
        if not self.trip_number:
            from ledger.models import Sequence
            
            # Using Sequences for robust atomic numbering
            total_count = Sequence.next_value(f"trip_total_{self.vehicle.pk}")
            
            self.trip_number = f"{reg_plate}-{total_count}"
        
        # If trip_number already exists but vehicle plate changed (manual correction)
        # ensure the prefix matches the current plate
        elif not self.trip_number.startswith(reg_plate):
            parts = self.trip_number.rsplit('-', 1)
            if len(parts) > 1:
                last_dash_idx = self.trip_number.rfind('-')
                if last_dash_idx != -1:
                    suffix = self.trip_number[last_dash_idx+1:]
                    self.trip_number = f"{reg_plate}-{suffix}"

        # Restrict changing Party or financial fields if Trip is Billed
        if not is_new:
            if self.is_billed:
                # Check for financial changes
                financial_fields = ['weight', 'rate_per_ton', 'revenue_type', 'route']
                changed_fields = []
                for field in financial_fields:
                    if getattr(old_instance, field) != getattr(self, field):
                        changed_fields.append(field)
                
                if changed_fields:
                    raise ValidationError(
                        f"Cannot change {', '.join(changed_fields)} for Trip {self.trip_number} as it is already billed. "
                        "Delete the bill first to make corrections."
                    )

                if old_instance.party != self.party:
                    raise ValidationError(f"Cannot change Party for Trip {self.trip_number} as it is already billed.")

        # Update revenue caches before save unless already updating them
        if not getattr(self, '_updating_financial_caches', False):
            self._bypass_cache = True
            try:
                rev = self.revenue
                gst = self.gst_amount
                total_rev = rev + gst
                received = self.calculate_amount_received() if self.pk else Decimal('0')
                outstanding = total_rev - received
                
                if total_rev <= 0:
                    status = self.PAYMENT_STATUS_UNPAID
                elif received >= total_rev:
                    status = self.PAYMENT_STATUS_PAID
                elif received > 0:
                    status = self.PAYMENT_STATUS_PARTIAL
                else:
                    status = self.PAYMENT_STATUS_UNPAID

                self.revenue_cached = rev
                self.gst_amount_cached = gst
                self.total_revenue_cached = total_rev
                self.amount_received_cached = received
                self.outstanding_balance_cached = outstanding
                self.payment_status_cached = status
            finally:
                del self._bypass_cache

        
        # Perform the actual save
        super().save(*args, **kwargs)

        # Skip ledger sync if only updating financial caches
        update_fields = kwargs.get('update_fields')
        if update_fields:
            cache_fields = {
                'amount_received_cached', 'outstanding_balance_cached', 'payment_status_cached',
                'revenue_cached', 'gst_amount_cached', 'total_revenue_cached'
            }
            if all(field in cache_fields for field in update_fields):
                return

        # If it's a new trip or financial fields changed, we might need to sync with ledger
        # but sync_ledger_invoice already handles 'is_billed' check.
        
        # If vehicle changed, recalculate for the OLD vehicle
        if vehicle_changed:
            Trip.recalculate_vehicle_trip_numbers(old_instance.vehicle)

        # Sync to Ledger
        self.sync_ledger_invoice()

        # Clean up transient instance variables
        for attr in ('_is_billed_cache', '_associated_bill_cache', '_creditor_bill_cache', '_old_instance', '_date_changed', '_vehicle_changed'):
            if hasattr(self, attr):
                delattr(self, attr)

    def update_financial_caches(self):
        """
        Recalculate and update the cached received amount and outstanding balance.
        """
        from ledger.services import TripFinancialService
        return TripFinancialService.update_trip_financial_caches(self)

    def calculate_amount_received(self):
        """Helper to calculate amount received without using cached field"""
        from ledger.services import TripFinancialService
        return TripFinancialService.calculate_trip_received_amount(self)

    @classmethod
    def recalculate_vehicle_trip_numbers(cls, vehicle):
        """
        Recalculate and update all trip numbers for a specific vehicle.
        """
        from ledger.services import TripFinancialService
        return TripFinancialService.recalculate_vehicle_trip_numbers(vehicle)

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
    def is_billed(self):
        """Check if this trip is associated with any customer bill"""
        if not self.pk:
            return False
            
        if hasattr(self, 'annotated_is_billed'):
            return self.annotated_is_billed
        
        if hasattr(self, '_prefetched_objects_cache') and 'bills' in self._prefetched_objects_cache:
            return any(b.customer_bill_id is None and getattr(getattr(b, 'party', None), 'party_type', None) != 'Creditor' for b in self.bills.all())
            
        if not hasattr(self, '_is_billed_cache'):
            self._is_billed_cache = self.bills.filter(customer_bill__isnull=True).exclude(party__party_type='Creditor').exists()
        return self._is_billed_cache

    @property
    def is_creditor_billed(self):
        """Check if this trip is associated with a creditor bill"""
        if hasattr(self, 'annotated_is_creditor_billed'):
            return self.annotated_is_creditor_billed
        return bool(self.creditor_bill)

    @property
    def associated_bill(self):
        """Returns the customer bill associated with this trip"""
        if not self.pk or not self.is_billed:
            return None
            
        if hasattr(self, '_prefetched_objects_cache') and 'bills' in self._prefetched_objects_cache:
            for b in self.bills.all():
                if b.customer_bill_id is None and getattr(getattr(b, 'party', None), 'party_type', None) != 'Creditor':
                    return b
            return self.bills.all()[0] if self.bills.all() else None

        if not hasattr(self, '_associated_bill_cache'):
            self._associated_bill_cache = self.bills.filter(customer_bill__isnull=True).exclude(party__party_type='Creditor').first() or self.bills.first()
        return self._associated_bill_cache

    @property
    def creditor_bill(self):
        """Returns the creditor bill associated with this trip (if attached vehicle)"""
        if not self.pk:
            return None
            
        if hasattr(self, '_prefetched_objects_cache') and 'bills' in self._prefetched_objects_cache:
            for b in self.bills.all():
                if b.customer_bill_id is not None or getattr(getattr(b, 'party', None), 'party_type', None) == 'Creditor':
                    return b
            return None

        if not hasattr(self, '_creditor_bill_cache'):
            from django.db.models import Q
            self._creditor_bill_cache = self.bills.filter(
                Q(customer_bill__isnull=False) | Q(party__party_type='Creditor')
            ).first()
        return self._creditor_bill_cache

    @property
    def revenue(self):
        """Returns revenue, prioritizing cached value unless requested otherwise"""
        if getattr(self, '_bypass_cache', False):
            return self._calculate_revenue()
        if self.revenue_cached:
            return self.revenue_cached
        if hasattr(self, 'annotated_revenue'):
            return self.annotated_revenue
        return self._calculate_revenue()

    def _calculate_revenue(self):
        """Core logic for revenue calculation"""
        if self.revenue_type == self.REVENUE_FIXED:
            return self.rate_per_ton or 0
        if self.weight and self.rate_per_ton:
            return self.weight * self.rate_per_ton
        return 0

    @property
    def vendor_rate(self):
        """Returns the rate per ton for vendor hire, calculated from vendor_hire_amount or rate_per_ton"""
        if self.weight and self.weight > 0 and self.vendor_hire_amount:
            return (Decimal(str(self.vendor_hire_amount)) / Decimal(str(self.weight))).quantize(Decimal('0.01'))
        return self.rate_per_ton or Decimal('0.00')

    @property
    def gst_amount(self):
        """Returns GST amount, prioritizing cached value"""
        if getattr(self, '_bypass_cache', False):
            return self._calculate_gst_amount()
        if self.gst_amount_cached:
            return self.gst_amount_cached
        if hasattr(self, 'annotated_gst_amount'):
            return self.annotated_gst_amount
        return self._calculate_gst_amount()

    def _calculate_gst_amount(self):
        """Core logic for GST calculation"""
        from decimal import Decimal
        rev = self.revenue # This will use _bypass_cache if set on self
        
        # 1. Use Bill Rate if available
        bill = self.associated_bill
        if bill and bill.gst_rate:
            return rev * (Decimal(bill.gst_rate) / Decimal(100))
        
        # 2. If unbilled, check if route/snapshot is taxable
        is_taxable = False
        if self.gst_type_snapshot in ['GST', 'IGST']:
            is_taxable = True
        elif self.route and self.route.route_type in ['local', 'intra']:
            is_taxable = True
            
        if is_taxable:
            return rev * (Decimal('18') / Decimal('100'))
            
        return Decimal('0')

    @property
    def total_revenue(self):
        """Returns total revenue, prioritizing cached value"""
        if getattr(self, '_bypass_cache', False):
            return self.revenue + self.gst_amount
        if self.total_revenue_cached:
            return self.total_revenue_cached
        if hasattr(self, 'annotated_total_revenue'):
            return self.annotated_total_revenue
        return self.revenue + self.gst_amount

    @property
    def amount_received(self):
        """Returns amount received, prioritizing cached value"""
        if getattr(self, '_bypass_cache', False):
            return self.calculate_amount_received()
        if self.amount_received_cached:
            return self.amount_received_cached
        if hasattr(self, 'annotated_received'):
            return self.annotated_received
        return self.calculate_amount_received()

    @property
    def payment_status(self):
        """Returns payment status, prioritizing cached value"""
        if getattr(self, '_bypass_cache', False):
            return self._calculate_payment_status()
        if self.payment_status_cached:
            return self.payment_status_cached
        if hasattr(self, 'annotated_status'):
            return self.annotated_status
        return self._calculate_payment_status()

    def _calculate_payment_status(self):
        """Core logic for payment status"""
        received = self.amount_received
        total_rev = self.total_revenue
        if total_rev <= 0: return self.PAYMENT_STATUS_UNPAID
        if received >= total_rev: return self.PAYMENT_STATUS_PAID
        elif received > 0: return self.PAYMENT_STATUS_PARTIAL
        return self.PAYMENT_STATUS_UNPAID

    @property
    def outstanding_balance(self):
        """Returns outstanding balance, prioritizing cached value"""
        if getattr(self, '_bypass_cache', False):
            return self.total_revenue - self.amount_received
        if self.outstanding_balance_cached:
            return self.outstanding_balance_cached
        if hasattr(self, 'annotated_outstanding'):
            return self.annotated_outstanding
        return self.total_revenue - self.amount_received
