"""
Bill and BillTrip models and thread-local helpers for Ledger application
"""
import threading
from decimal import Decimal
from django.db import models, transaction
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.db.models import F
from trips.models import Trip
from .company import CompanyAccount
from .party import Party
from .category import TransactionCategory
from .financial_record import FinancialRecord

_bill_thread_local = threading.local()

def is_bill_deleting(bill_id):
    if not bill_id:
        return False
    deleting = getattr(_bill_thread_local, 'deleting_pks', None)
    return deleting is not None and bill_id in deleting

def mark_bill_deleting(bill_id):
    if not hasattr(_bill_thread_local, 'deleting_pks'):
        _bill_thread_local.deleting_pks = set()
    _bill_thread_local.deleting_pks.add(bill_id)

def unmark_bill_deleting(bill_id):
    if hasattr(_bill_thread_local, 'deleting_pks'):
        _bill_thread_local.deleting_pks.discard(bill_id)


class BillQuerySet(models.QuerySet):
    def with_payment_info(self):
        """
        Ultra-lightweight payment info using cached fields.
        Backward compatible with previous annotation names.
        """
        return self.annotate(
            annotated_subtotal=F('subtotal_cached'),
            annotated_gst_amount=F('gst_amount_cached'),
            annotated_total_amount=F('total_amount_cached'),
            annotated_received=F('amount_received_cached'),
            annotated_outstanding=F('outstanding_balance_cached'),
            annotated_status=F('payment_status_cached')
        )


class BillManager(models.Manager):
    def get_queryset(self):
        return BillQuerySet(self.model, using=self._db)
    
    def with_payment_info(self):
        return self.get_queryset().with_payment_info()


class Bill(models.Model):
    """
    Bill/Invoice Document aggregating multiple trips or standard items.
    """
    objects = BillManager()
    TYPE_TRIP = 'Trip'
    TYPE_STANDARD = 'Standard'
    TYPE_CHOICES = [
        (TYPE_TRIP, 'Trip-based Invoice'),
        (TYPE_STANDARD, 'Standard Invoice'),
    ]

    PAYMENT_STATUS_UNPAID = 'Unpaid'
    PAYMENT_STATUS_PARTIAL = 'Partially Paid'
    PAYMENT_STATUS_PAID = 'Paid'
    PAYMENT_STATUS_CHOICES = [
        (PAYMENT_STATUS_UNPAID, 'Unpaid'),
        (PAYMENT_STATUS_PARTIAL, 'Partially Paid'),
        (PAYMENT_STATUS_PAID, 'Paid'),
    ]

    GST_RATE_0 = 0
    GST_RATE_5 = 5
    GST_RATE_18 = 18
    GST_CHOICES = [
        (GST_RATE_0, '0% GST'),
        (GST_RATE_5, '5% GST'),
        (GST_RATE_18, '18% GST'),
    ]

    GST_TYPE_GST = 'GST'
    GST_TYPE_IGST = 'IGST'
    GST_TYPE_NONE = 'NONE'
    GST_TYPE_CHOICES = [
        (GST_TYPE_GST, 'GST'),
        (GST_TYPE_IGST, 'IGST'),
        (GST_TYPE_NONE, 'Non-GST'),
    ]

    bill_number = models.CharField(max_length=50, unique=True, blank=True, null=True, verbose_name="Full Invoice Number")
    bill_no = models.PositiveIntegerField(null=True, blank=True, verbose_name="Invoice No")
    bill_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_TRIP, verbose_name="Bill Type")
    issuer = models.ForeignKey(CompanyAccount, on_delete=models.PROTECT, related_name='bills', verbose_name="Issued From", null=True)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='bills', verbose_name="Bill To")
    date = models.DateField(verbose_name="Invoice Date", db_index=True)
    
    # Trip-based bills
    trips = models.ManyToManyField(Trip, through='BillTrip', related_name='bills', verbose_name="Included Trips", blank=True)
    
    # Standard bills
    item_type = models.CharField(max_length=200, blank=True, null=True, verbose_name="Item Type/Description")
    standard_weight = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, verbose_name="Standard Weight")
    standard_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Standard Rate")
    amount_override = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Subtotal Amount (Manual)")
    
    gst_rate = models.PositiveIntegerField(choices=GST_CHOICES, default=GST_RATE_0, verbose_name="GST Rate (%)")
    gst_type = models.CharField(max_length=10, choices=GST_TYPE_CHOICES, default=GST_TYPE_GST, verbose_name="GST Type")
    
    # Snapshot fields for Company Details at time of invoice
    invoice_company_name = models.CharField(max_length=200, blank=True, verbose_name="Company Name (Snapshot)")
    invoice_company_address = models.TextField(blank=True, verbose_name="Company Address (Snapshot)")
    invoice_company_mobile = models.CharField(max_length=20, blank=True, verbose_name="Company Mobile (Snapshot)")
    invoice_company_gstin = models.CharField(max_length=20, blank=True, verbose_name="Company GSTIN (Snapshot)")
    invoice_company_authorized_signatory = models.CharField(max_length=200, blank=True, verbose_name="Authorized Signatory (Snapshot)")
    
    # Bank Details Snapshot
    invoice_bank_name = models.CharField(max_length=200, blank=True, verbose_name="Bank Name (Snapshot)")
    invoice_bank_branch = models.CharField(max_length=200, blank=True, verbose_name="Bank Branch (Snapshot)")
    invoice_bank_account = models.CharField(max_length=50, blank=True, verbose_name="Bank Account (Snapshot)")
    invoice_bank_ifsc = models.CharField(max_length=20, blank=True, verbose_name="Bank IFSC (Snapshot)")
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_bills',
        verbose_name="Created By"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    category = models.ForeignKey(TransactionCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='bills', verbose_name="Bill Category")
    original_bill = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='adjustment_bills', verbose_name="Against Invoice")
    manual_original_bill_number = models.CharField(max_length=100, blank=True, null=True, verbose_name="Manual Against Invoice No")
    manual_original_bill_date = models.DateField(blank=True, null=True, verbose_name="Manual Against Invoice Date")
    creditor_invoice_number = models.CharField(max_length=100, blank=True, null=True, verbose_name="Creditor Invoice Number")
    creditor_invoice_date = models.DateField(blank=True, null=True, verbose_name="Creditor Invoice Date")
    customer_bill = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='creditor_bills', verbose_name="Customer Bill Reference")
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Discount")
    use_roundoff = models.BooleanField(default=True, verbose_name="Use Round Off")

    # Cached Financial Fields
    subtotal_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Subtotal (Cached)')
    gst_amount_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='GST (Cached)')
    total_amount_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total Amount (Cached)')
    amount_received_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Amount Received (Cached)')
    outstanding_balance_cached = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Outstanding (Cached)')
    payment_status_cached = models.CharField(max_length=20, default='Unpaid', verbose_name='Payment Status (Cached)')

    @classmethod
    def get_next_available_no(cls, issuer, date=None, category=None):
        """Finds the next numeric invoice number for the specific prefix series."""
        from ledger.services import BillingService
        return BillingService.get_next_available_no(issuer, date, category)

    def get_prefix(self, date=None):
        """Returns the invoice prefix for this bill based on its issuer, date, and category."""
        if not self.issuer:
            return ""
        
        from django.utils import timezone
        dt = date or self.date or timezone.now()
        year = dt.year

        if self.category:
            if self.category.name == 'Credit Note':
                return self.issuer.cn_prefix.replace("{YYYY}", str(year))
            elif self.category.name == 'Debit Note':
                return self.issuer.dn_prefix.replace("{YYYY}", str(year))
        
        return self.issuer.invoice_prefix.replace("{YYYY}", str(year))

    def save(self, *args, **kwargs):
        # 1. Snapshot Company Details from Issuer
        if self.issuer and not self.invoice_company_name:
            self.invoice_company_name = self.issuer.name
            self.invoice_company_address = self.issuer.address
            self.invoice_company_mobile = self.issuer.phone_number
            self.invoice_company_gstin = self.issuer.gstin
            self.invoice_company_authorized_signatory = self.issuer.authorized_signatory
            self.invoice_bank_name = self.issuer.bank_name
            self.invoice_bank_branch = self.issuer.bank_branch
            self.invoice_bank_account = self.issuer.account_number
            self.invoice_bank_ifsc = self.issuer.ifsc_code
        
        # 2. Handle Invoice Numbering
        if self.customer_bill:
            cust_no = self.customer_bill.bill_number or f"DRAFT-{self.customer_bill.pk}"
            if not self.bill_number or self.bill_number.startswith("CR-DRAFT-"):
                candidate_no = f"CR-{cust_no}"
                if self.party_id and Bill.objects.filter(bill_number=candidate_no).exclude(pk=self.pk).exists():
                    self.bill_number = f"CR-{self.party_id}-{cust_no}"
                else:
                    self.bill_number = candidate_no
        elif self.party and self.party.party_type == Party.TYPE_CREDITOR:
            if not self.bill_number:
                self.bill_number = f"CR-{self.pk or 'NEW'}"
        elif self.issuer:
            if not self.bill_no:
                self.bill_no = self.get_next_available_no(self.issuer, self.date, self.category)
            
            # Update the full string representation
            prefix = self.get_prefix()
            padding = self.issuer.invoice_padding
            suffix = self.issuer.invoice_suffix
            self.bill_number = f"{prefix}{self.bill_no:0{padding}d}{suffix}"
        
        # Update revenue caches before save
        self._bypass_cache = True
        try:
            self.subtotal_cached = self.subtotal
            self.gst_amount_cached = self.gst_amount
            self.total_amount_cached = self.rounded_total
            
            # Update payment caches as well
            self.amount_received_cached = self.calculate_amount_received()
            self.outstanding_balance_cached = self.total_amount_cached - self.amount_received_cached
            
            total = self.total_amount_cached
            received = self.amount_received_cached
            if total <= 0:
                self.payment_status_cached = self.PAYMENT_STATUS_UNPAID
            elif received >= total:
                self.payment_status_cached = self.PAYMENT_STATUS_PAID
            elif received > 0:
                self.payment_status_cached = self.PAYMENT_STATUS_PARTIAL
            else:
                self.payment_status_cached = self.PAYMENT_STATUS_UNPAID
        finally:
            del self._bypass_cache
        
        is_new = self.pk is None
            
        super().save(*args, **kwargs)
        
        # Skip ledger sync if only updating financial caches
        update_fields = kwargs.get('update_fields')
        if update_fields:
            cache_fields = {
                'amount_received_cached', 'outstanding_balance_cached', 'payment_status_cached',
                'subtotal_cached', 'gst_amount_cached', 'total_amount_cached'
            }
            if all(field in cache_fields for field in update_fields):
                return
                
        # 3. Ensure ledger is in sync (handles date, amount, issuer changes)
        # Note: For trip-based bills, self.trips might be empty on FIRST save 
        # (before form.save_m2m), but subsequent saves or BillTrip signals will handle it.
        self.sync_to_ledger()

    def update_financial_caches(self):
        """Recalculate and update cached received and outstanding amounts for the bill"""
        from ledger.services import BillingService
        return BillingService.update_bill_financial_caches(self)

    def calculate_amount_received(self):
        """Helper to calculate amount received without using cached field"""
        from ledger.services import BillingService
        return BillingService.calculate_bill_received_amount(self)

    def delete(self, *args, **kwargs):
        """
        Custom delete for Bill.
        Ensure individual trip accruals are restored and the consolidated record is removed.
        """
        if not self.pk:
            return super().delete(*args, **kwargs)

        with transaction.atomic():
            self._is_being_deleted = True
            mark_bill_deleting(self.pk)

            try:
                orig_bill = self.original_bill
                party = self.party

                # If this is a customer bill, delete linked creditor bills first
                for cb in list(self.creditor_bills.all()):
                    cb.delete()

                affected_trips = list(self.trips.all())

                # Delete only the consolidated invoice record associated with this bill
                FinancialRecord.objects.filter(
                    associated_bill=self,
                    record_type=FinancialRecord.RECORD_TYPE_INVOICE
                ).delete()

                super().delete(*args, **kwargs)

                # Re-sync trips to restore their individual accruals now that they are unbilled
                for trip in affected_trips:
                    from ledger.services import TripFinancialService
                    TripFinancialService.sync_trip_accrual(trip)

                if orig_bill:
                    from ledger.services import BillingService
                    BillingService.update_bill_financial_caches(orig_bill)
                if party:
                    party.refresh_balance()
            finally:
                unmark_bill_deleting(self.pk)
                if hasattr(self, '_is_being_deleted'):
                    del self._is_being_deleted

    def sync_to_ledger(self):
        """
        Main entry point to synchronize this invoice to the ledger.
        """
        from ledger.services import BillingService
        return BillingService.sync_bill_to_ledger(self)

    @property
    def is_creditor_bill(self):
        """Returns True if the bill is for a creditor/vendor or linked to a customer bill."""
        return bool(self.customer_bill_id or (self.party and self.party.party_type == Party.TYPE_CREDITOR))

    @property
    def display_invoice_number(self):
        """Returns creditor invoice number if present, otherwise bill number or Draft."""
        if self.is_creditor_bill:
            return self.creditor_invoice_number or self.bill_number or "Draft"
        return self.bill_number or "Draft"

    @property
    def is_adjustment(self):
        """Returns True if the bill is a Credit Note or Debit Note adjustment."""
        return self.category and self.category.name in ['Credit Note', 'Debit Note']

    @property
    def subtotal(self):
        """Returns subtotal, prioritizing cached value unless requested otherwise"""
        if getattr(self, '_bypass_cache', False):
            return self._calculate_subtotal()
        if self.subtotal_cached:
            return self.subtotal_cached
        if hasattr(self, 'annotated_subtotal'):
            return self.annotated_subtotal
        return self._calculate_subtotal()

    def _calculate_subtotal(self):
        """Core logic for subtotal calculation"""
        if self.bill_type == self.TYPE_STANDARD:
            base = 0
            if self.amount_override is not None:
                base = self.amount_override
            elif self.standard_weight and self.standard_rate:
                base = self.standard_weight * self.standard_rate
            return max(0, base - (self.discount or 0))

        if not self.pk:
            return Decimal('0')

        is_creditor = self.is_creditor_bill

        trip_subtotal = Decimal('0')
        for bt in self.bill_trips.select_related('trip').all():
            if is_creditor:
                trip_subtotal += (Decimal(str(bt.trip.vendor_hire_amount or 0)) - Decimal(str(bt.discount or 0)))
            else:
                trip_subtotal += (Decimal(str(bt.trip.revenue or 0)) - Decimal(str(bt.discount or 0)))
        return max(Decimal('0'), trip_subtotal - (Decimal(str(self.discount or 0))))

    @property
    def gst_amount(self):
        """Returns GST amount, prioritizing cached value"""
        if getattr(self, '_bypass_cache', False):
            return self.subtotal * (Decimal(self.gst_rate) / Decimal(100))
        if self.gst_amount_cached:
            return self.gst_amount_cached
        if hasattr(self, 'annotated_gst_amount'):
            return self.annotated_gst_amount
        return self.subtotal * (Decimal(self.gst_rate) / Decimal(100))

    @property
    def total_amount(self):
        """Returns total amount, prioritizing cached value"""
        if getattr(self, '_bypass_cache', False):
            return self.subtotal + self.gst_amount
        if self.total_amount_cached:
            return self.total_amount_cached
        if hasattr(self, 'annotated_total_amount'):
            return self.annotated_total_amount
        return self.subtotal + self.gst_amount

    @property
    def rounded_total(self):
        """Returns rounded total, prioritizing cached value"""
        if getattr(self, '_bypass_cache', False):
            if not self.use_roundoff:
                return self.total_amount
            return self.total_amount.quantize(Decimal('1'), rounding='ROUND_HALF_UP')
        if self.total_amount_cached:
            return self.total_amount_cached # rounded_total is stored in total_amount_cached
            
        if not self.use_roundoff:
            return self.total_amount
        return self.total_amount.quantize(Decimal('1'), rounding='ROUND_HALF_UP')

    @property
    def amount_received(self):
        """Returns amount received, prioritizing cached value"""
        if self.amount_received_cached:
            return self.amount_received_cached
        if hasattr(self, 'annotated_received'):
            return self.annotated_received
        return self.calculate_amount_received()

    @property
    def outstanding_balance(self):
        """Returns outstanding balance, prioritizing cached value"""
        if self.outstanding_balance_cached:
            return self.outstanding_balance_cached
        if hasattr(self, 'annotated_outstanding'):
            return self.annotated_outstanding or Decimal('0.00')
        return (self.rounded_total or Decimal('0.00')) - (self.amount_received or Decimal('0.00'))

    @property
    def payment_status(self):
        """Returns payment status, prioritizing cached value"""
        if self.payment_status_cached:
            return self.payment_status_cached
            
        total = self.rounded_total
        received = self.amount_received
        if total <= 0: return self.PAYMENT_STATUS_UNPAID
        if received >= total: return self.PAYMENT_STATUS_PAID
        elif received > 0: return self.PAYMENT_STATUS_PARTIAL
        return self.PAYMENT_STATUS_UNPAID

    @property
    def cgst_amount(self):
        if self.gst_rate > 0:
            return self.gst_amount / 2
        return 0

    @property
    def sgst_amount(self):
        if self.gst_rate > 0:
            return self.gst_amount / 2
        return 0

    @property
    def igst_amount(self):
        if self.gst_rate > 0:
            return self.gst_amount
        return 0

    @property
    def share_token(self):
        """Returns a cryptographically signed unguessable token for public sharing."""
        from django.core import signing
        return signing.dumps(self.pk, salt='bill-share')

    def get_trip_gst(self, trip):
        """Calculate GST amount for a specific trip in this bill context"""
        if not trip.revenue or self.gst_rate == 0:
            return 0
        return trip.revenue * (Decimal(self.gst_rate) / Decimal(100))

    def __str__(self):
        try:
            party_name = self.party.name if self.party else 'No Party'
        except ObjectDoesNotExist:
            party_name = 'Deleted Party'
        return f"{self.display_invoice_number} - {party_name}"
    
    description = models.TextField(blank=True, verbose_name="Item Description",
                                   help_text="Description shown on invoice (e.g., destination/material)")
    hsn_code = models.CharField(max_length=20, default="996511", verbose_name="HSN Code")
    reverse_charge = models.BooleanField(default=False, verbose_name="Reverse Charge")

    @property
    def trips_count(self):
        if not self.pk:
            return 0
        if hasattr(self, '_prefetched_objects_cache') and 'trips' in self._prefetched_objects_cache:
            return len(self.trips.all())
        return self.trips.count()

    @property
    def total_weight(self):
        if self.bill_type == self.TYPE_STANDARD:
            return self.standard_weight or 0
        
        if not self.pk:
            return 0
        
        if hasattr(self, '_prefetched_objects_cache') and 'trips' in self._prefetched_objects_cache:
            return sum((t.weight or 0) for t in self.trips.all())
        return self.trips.aggregate(total=models.Sum('weight'))['total'] or 0

    @property
    def roundoff(self):
        if not self.use_roundoff:
            return Decimal('0')
        return self.rounded_total - self.total_amount


class BillTrip(models.Model):
    """
    Through model for Bill and Trip to store LR No and Discount for each trip in a bill context.
    """
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='bill_trips')
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
