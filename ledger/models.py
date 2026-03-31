"""
Models for Ledger application
"""
from django.db import models
from django.contrib.auth.models import User
from trips.models import Trip
from django.db.models import Sum, F, DecimalField
from django.db.models.functions import Coalesce
from decimal import Decimal

class Sequence(models.Model):
    """
    Sequence model for generating robust, gap-less numbers.
    Used for Trip Numbers and Financial Record Entry Numbers.
    """
    key = models.CharField(max_length=100, unique=True)
    value = models.PositiveIntegerField(default=0)

    @classmethod
    def next_value(cls, key):
        """
        Atomically increment and return the next value for a given key.
        """
        from django.db import transaction
        with transaction.atomic():
            seq, created = cls.objects.select_for_update().get_or_create(key=key)
            seq.value += 1
            seq.save()
            return seq.value

    def __str__(self):
        return f"{self.key}: {self.value}"

class Party(models.Model):
    """
    Party/Client model for managing business entities
    """
    name = models.CharField(max_length=200, unique=True, verbose_name='Party Name')
    phone_number = models.CharField(max_length=20, blank=True, verbose_name='Phone Number')
    state = models.CharField(max_length=100, blank=True, verbose_name='State')
    address = models.TextField(blank=True, verbose_name='Address')
    gstin = models.CharField(max_length=20, blank=True, verbose_name='GSTIN')
    
    # Structured Bank Details
    bank_name = models.CharField(max_length=200, blank=True, verbose_name='Bank Name')
    bank_branch = models.CharField(max_length=200, blank=True, verbose_name='Bank Branch')
    account_number = models.CharField(max_length=50, blank=True, verbose_name='Account Number')
    ifsc_code = models.CharField(max_length=20, blank=True, verbose_name='IFSC Code')
    account_holder_name = models.CharField(max_length=200, blank=True, verbose_name='Account Holder Name')
    
    bank_details = models.TextField(blank=True, verbose_name='Legacy Bank Details (Text)')
    opening_balance = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0, 
        verbose_name='Opening Balance'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Party'
        verbose_name_plural = 'Parties'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def current_balance(self):
        """
        Calculate current balance: 
        Opening Balance + Total Revenue (Invoices) - Total Received (Income + Deductions)
        """
        # Revenue is recorded as 'Invoice' type records
        revenue = self.financial_records.filter(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).aggregate(total=models.Sum('amount'))['total'] or 0

        # Payments received are 'Transaction' types with Income category or 'Deductions'
        received = self.financial_records.filter(
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
        ).filter(
            models.Q(category__type=TransactionCategory.TYPE_INCOME) | 
            models.Q(category__name='Deductions')
        ).aggregate(total=models.Sum('amount'))['total'] or 0

        return self.opening_balance + revenue - received

class TransactionCategory(models.Model):
    """
    Dynamic categories for financial records
    """
    TYPE_INCOME = 'Income'
    TYPE_EXPENSE = 'Expense'
    TYPE_CHOICES = [
        (TYPE_INCOME, 'Income (+)'),
        (TYPE_EXPENSE, 'Expense (-)'),
    ]
    name = models.CharField(max_length=100, unique=True)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_INCOME)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Transaction Category'
        verbose_name_plural = 'Transaction Categories'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.type})"

class CompanyAccount(models.Model):
    """
    Company Financial Accounts / Firms.
    Each account represents a separate firm/entity.
    """
    name = models.CharField(max_length=200, unique=True, verbose_name='Firm Name')
    address = models.TextField(blank=True, verbose_name='Firm Address')
    phone_number = models.CharField(max_length=20, blank=True, verbose_name='Phone Number')
    gstin = models.CharField(max_length=20, blank=True, verbose_name='GSTIN')
    pan = models.CharField(max_length=20, blank=True, verbose_name='PAN')
    
    # Primary Bank Details for this Firm
    bank_name = models.CharField(max_length=200, blank=True, verbose_name='Bank Name')
    bank_branch = models.CharField(max_length=200, blank=True, verbose_name='Bank Branch')
    account_number = models.CharField(max_length=50, blank=True, verbose_name='Account Number')
    ifsc_code = models.CharField(max_length=20, blank=True, verbose_name='IFSC Code')
    account_holder_name = models.CharField(max_length=200, blank=True, verbose_name='Account Holder Name')
    
    # Bill Generation Details
    authorized_signatory = models.CharField(max_length=200, blank=True, verbose_name="Authorized Signatory")
    invoice_template = models.CharField(max_length=100, default="INV/{YYYY}/{SEQ}", help_text="Use {YYYY} for Year, {SEQ} for Sequence Number")

    opening_balance = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0, 
        verbose_name='Opening Balance'
    )
    description = models.TextField(blank=True, verbose_name='Notes/Description')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Company Account'
        verbose_name_plural = 'Company Accounts'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def current_balance(self):
        """
        Calculate current balance: Opening Balance + Total Income - Total Expenses
        Excludes 'Invoice' type records as they are accruals, not cash flow.
        Excludes 'Deductions' as they are non-cash revenue reductions.
        """
        income = self.financial_records.filter(
            category__type=TransactionCategory.TYPE_INCOME
        ).exclude(
            models.Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) | 
            models.Q(category__name='Deductions')
        ).aggregate(total=models.Sum('amount'))['total'] or 0

        expenses = self.financial_records.filter(
            category__type=TransactionCategory.TYPE_EXPENSE
        ).exclude(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).aggregate(total=models.Sum('amount'))['total'] or 0

        return self.opening_balance + income - expenses

def financial_record_upload_path(instance, filename):
    """
    Determines the upload path for a financial record document.
    Format: financial_records/<type>/<identifier>/<filename>
    """
    import os
    
    # Priority-based identification
    if instance.associated_trip:
        folder = 'trips'
        identifier = str(instance.associated_trip.trip_number)
    elif instance.associated_bill:
        folder = 'bills'
        identifier = instance.associated_bill.bill_number or f"draft_{instance.associated_bill.pk}"
    elif instance.party:
        folder = 'parties'
        identifier = instance.party.name
    elif instance.driver:
        folder = 'drivers'
        identifier = instance.driver.employee_id or instance.driver.name
    else:
        folder = 'miscellaneous'
        identifier = 'general'

    # Sanitize identifier for path use
    safe_identifier = str(identifier).replace(' ', '_').replace('/', '-').replace('\\', '-')
    
    return os.path.join('financial_records', folder, safe_identifier, filename)

class FinancialRecord(models.Model):
    """
    Financial record for managing income and expenses
    """

    # Record Type choices
    RECORD_TYPE_TRANSACTION = 'Transaction'
    RECORD_TYPE_INVOICE = 'Invoice'
    RECORD_TYPE_GENERAL = 'General'
    RECORD_TYPE_CHOICES = [
        (RECORD_TYPE_TRANSACTION, 'Transaction'),
        (RECORD_TYPE_INVOICE, 'Invoice'),
        (RECORD_TYPE_GENERAL, 'General/Miscellaneous'),
    ]

    date = models.DateField(verbose_name='Transaction Date')
    account = models.ForeignKey(
        CompanyAccount,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Company Account'
    )
    party = models.ForeignKey(
        Party,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Associated Party'
    )
    driver = models.ForeignKey(
        'drivers.Driver',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Associated Driver'
    )
    associated_trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Associated Trip'
    )
    associated_bill = models.ForeignKey(
        'Bill',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Associated Bill'
    )

    record_type = models.CharField(
        max_length=20,
        choices=RECORD_TYPE_CHOICES,
        default=RECORD_TYPE_TRANSACTION,
        verbose_name='Record Type'
    )

    category = models.ForeignKey(
        TransactionCategory,
        on_delete=models.PROTECT,
        related_name='financial_records',
        verbose_name='Category'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Amount'
    )
    entry_number = models.PositiveIntegerField(
        unique=True,
        null=True,
        blank=True,
        verbose_name='Entry #'
    )
    description = models.TextField(verbose_name='Description', blank=True)
    document_ref = models.FileField(
        upload_to=financial_record_upload_path,
        null=True,
        blank=True,
        verbose_name='Supporting Document'
    )
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='recorded_financials',
        verbose_name='Recorded By'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    def save(self, *args, **kwargs):
        if not self.entry_number:
            self.entry_number = Sequence.next_value('financial_record_entry_number')
        super().save(*args, **kwargs)

        # Check trip closure if associated and this is a payment (Transaction)
        if self.record_type == self.RECORD_TYPE_TRANSACTION:
            if self.associated_trip:
                self.associated_trip.check_and_close_trip()
            elif self.associated_bill and self.associated_bill.bill_type == Bill.TYPE_TRIP:
                # If a bill is paid (even partially), check its trips
                for trip in self.associated_bill.trips.all():
                    trip.check_and_close_trip()

    class Meta:
        verbose_name = 'Financial Record'
        verbose_name_plural = 'Financial Records'
        ordering = ['-date']
        permissions = [
            ('can_view_financial_records', 'Can view financial records'),
            ('can_manage_financial_records', 'Can manage financial records'),
        ]

    def __str__(self):
        category_name = self.category.name if self.category else 'No Category'
        if self.associated_trip:
            return f"{category_name} - Trip: {self.associated_trip.trip_number} - {self.amount}"
        if self.associated_bill:
            bill_num = self.associated_bill.bill_number or "Draft Bill"
            return f"{category_name} - Bill: {bill_num} - {self.amount}"
        return f"{category_name} - {self.amount}"

    @property
    def linked_bill(self):
        """Returns associated bill or bill from allocations"""
        if self.associated_bill:
            return self.associated_bill
        
        # If no direct bill, check if it's a trip payment with allocations
        first_alloc = self.allocations.select_related('trip').first()
        if first_alloc and first_alloc.trip.associated_bill:
            return first_alloc.trip.associated_bill
        
        # Finally check if direct associated trip has a bill
        if self.associated_trip and self.associated_trip.associated_bill:
            return self.associated_trip.associated_bill
            
        return None

    @property
    def linked_trip(self):
        """Returns associated trip or first trip from allocations"""
        if self.associated_trip:
            return self.associated_trip
        
        first_alloc = self.allocations.select_related('trip').first()
        if first_alloc:
            return first_alloc.trip
            
        return None

    @property
    def is_income(self):
        return self.category.type == TransactionCategory.TYPE_INCOME if self.category else False

    @property
    def is_expense(self):
        return self.category.type == TransactionCategory.TYPE_EXPENSE if self.category else False

    @property
    def is_invoice(self):
        return self.record_type == self.RECORD_TYPE_INVOICE

    @property
    def signed_amount(self):
        if self.is_expense:
            return -abs(self.amount)
        return abs(self.amount)

class TripAllocation(models.Model):
    financial_record = models.ForeignKey(
        FinancialRecord,
        on_delete=models.CASCADE,
        related_name='allocations',
        verbose_name='Financial Record'
    )
    trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        related_name='payment_allocations',
        verbose_name='Trip'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Allocated Amount'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Trip Allocation'
        verbose_name_plural = 'Trip Allocations'
        unique_together = ('financial_record', 'trip')

    def __str__(self):
        return f"{self.financial_record} -> {self.trip.trip_number}: {self.amount}"

class Bill(models.Model):
    """
    Bill/Invoice Document aggregating multiple trips or standard items.
    """
    STATUS_DRAFT = 'Draft'
    STATUS_FINAL = 'Final'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_FINAL, 'Final'),
    ]

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

    GST_TYPE_INTRA = 'INTRA'
    GST_TYPE_INTER = 'INTER'
    GST_TYPE_CHOICES = [
        (GST_TYPE_INTRA, 'Intra-state'),
        (GST_TYPE_INTER, 'Inter-state'),
    ]

    bill_number = models.CharField(max_length=50, unique=True, blank=True, null=True, verbose_name="Invoice Number")
    bill_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_TRIP, verbose_name="Bill Type")
    issuer = models.ForeignKey(CompanyAccount, on_delete=models.PROTECT, related_name='bills', verbose_name="Issued From", null=True)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='bills', verbose_name="Bill To")
    date = models.DateField(verbose_name="Invoice Date")
    
    # Trip-based bills
    trips = models.ManyToManyField(Trip, through='BillTrip', related_name='bills', verbose_name="Included Trips", blank=True)
    
    # Standard bills
    item_type = models.CharField(max_length=200, blank=True, null=True, verbose_name="Item Type/Description")
    amount_override = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Subtotal Amount (Manual)")
    
    gst_rate = models.PositiveIntegerField(choices=GST_CHOICES, default=GST_RATE_0, verbose_name="GST Rate (%)")
    gst_type = models.CharField(max_length=10, choices=GST_TYPE_CHOICES, default=GST_TYPE_INTRA, verbose_name="GST Type")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    
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
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
        
        # 2. Generate Bill Number if missing
        if not self.bill_number and self.issuer:
            # Use template from issuer or default
            template = self.issuer.invoice_template or "INV/{YYYY}/{SEQ}"
            
            import datetime
            now = datetime.datetime.now()
            
            # Get Sequence per Issuer
            seq_key = f"bill_sequence_{self.issuer.pk}"
            seq_val = Sequence.next_value(seq_key)
            
            # Format
            num = template.replace("{YYYY}", str(now.year)).replace("{SEQ}", f"{seq_val:04d}")
            self.bill_number = num
            
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        Custom delete to ensure associated trips are updated in the ledger.
        """
        # Get list of trips before deleting
        trips = list(self.trips.all())
        super().delete(*args, **kwargs)
        
        # After bill is deleted, trips should have their individual revenue records restored
        for trip in trips:
            trip.sync_ledger_invoice()

    def sync_to_ledger(self):
        """
        Main entry point to synchronize this invoice and its trips to the ledger.
        Must be called AFTER ManyToMany relationships are established.
        """
        self.update_ledger_records()
        if self.bill_type == self.TYPE_TRIP:
            self.sync_trips_to_ledger()

    def sync_trips_to_ledger(self):
        """
        Trigger sync_ledger_invoice for all trips associated with this bill.
        This ensures individual Trip Payment records are removed if the trip is now billed.
        """
        for trip in self.trips.all():
            trip.sync_ledger_invoice()

    def update_ledger_records(self):
        """
        Synchronize the full Invoice record to the ledger.
        For Final bills, the amount includes GST.
        """
        # 1. Handle Consolidated Invoice Record (Total = Subtotal + GST if Final)
        # Create this for both Draft and Final bills to replace individual trip entries immediately.
        should_have_invoice = True
        inv_record = FinancialRecord.objects.filter(associated_bill=self, record_type=FinancialRecord.RECORD_TYPE_INVOICE).first()

        if should_have_invoice:
            inv_cat, _ = TransactionCategory.objects.get_or_create(
                name="Trip Payment",
                defaults={'type': TransactionCategory.TYPE_INCOME, 'description': 'Revenue from trips'}
            )

            # Use different description for Draft vs Final
            status_tag = f" ({self.status})" if self.status == self.STATUS_DRAFT else ""

            # Use Total Amount (including GST) for Final bills, otherwise just Subtotal
            amount = self.total_amount if self.status == self.STATUS_FINAL else self.subtotal

            gst_note = f" (Incl. GST ₹{self.gst_amount})" if self.status == self.STATUS_FINAL and self.gst_amount > 0 else ""
            
            bill_desc = self.item_type if self.bill_type == self.TYPE_STANDARD else f"Trip-based Invoice"
            description = f"Invoice {self.bill_number or 'Draft'}: {bill_desc}{status_tag}{gst_note}"

            FinancialRecord.objects.update_or_create(
                associated_bill=self,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE,
                defaults={
                    'category': inv_cat,
                    'party': self.party,
                    'date': self.date,
                    'amount': amount,
                    'description': description
                }
            )
        elif inv_record:
            inv_record.delete()

    @property
    def amount_received(self):
        """
        Calculate total received for this bill.
        Includes:
        1. Direct payments to this bill (Invoice Payment)
        2. Deductions (recorded as expenses against this bill)
        3. For Trip-based bills: Payments allocated to individual trips
        """
        # 1. Direct payments & deductions linked to this bill
        direct_income = self.financial_records.filter(
            category__type=TransactionCategory.TYPE_INCOME,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        deductions = self.financial_records.filter(
            category__name="Deductions",
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
        ).aggregate(total=Sum('amount'))['total'] or 0

        # 2. Trip-based allocations
        trip_payments = 0
        if self.bill_type == self.TYPE_TRIP:
            # We need to find all TripAllocations for trips in this bill
            trip_payments = TripAllocation.objects.filter(
                trip__in=self.trips.all()
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            # Also consider direct trip payments not through allocations (if any exist)
            direct_trip_payments = FinancialRecord.objects.filter(
                associated_trip__in=self.trips.all(),
                category__type=TransactionCategory.TYPE_INCOME,
                record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            trip_payments += direct_trip_payments

        return direct_income + deductions + trip_payments

    @property
    def outstanding_balance(self):
        total = self.total_amount if self.status == self.STATUS_FINAL else self.subtotal
        return total - self.amount_received

    @property
    def payment_status(self):
        total = self.total_amount if self.status == self.STATUS_FINAL else self.subtotal
        received = self.amount_received
        
        if total <= 0:
            return self.PAYMENT_STATUS_UNPAID
        
        if received >= total:
            return self.PAYMENT_STATUS_PAID
        elif received > 0:
            return self.PAYMENT_STATUS_PARTIAL
        else:
            return self.PAYMENT_STATUS_UNPAID

    @property
    def cgst_amount(self):
        if self.gst_rate > 0 and self.gst_type == self.GST_TYPE_INTRA:
            return self.gst_amount / 2
        return 0

    @property
    def sgst_amount(self):
        if self.gst_rate > 0 and self.gst_type == self.GST_TYPE_INTRA:
            return self.gst_amount / 2
        return 0

    @property
    def igst_amount(self):
        if self.gst_rate > 0 and self.gst_type == self.GST_TYPE_INTER:
            return self.gst_amount
        return 0

    def get_trip_gst(self, trip):
        """Calculate GST amount for a specific trip in this bill context"""
        if not trip.revenue or self.gst_rate == 0:
            return 0
        return trip.revenue * (Decimal(self.gst_rate) / Decimal(100))

    def get_trip_total(self, trip):
        """Calculate Total amount (Revenue + GST) for a specific trip"""
        rev = trip.revenue or 0
        return rev + self.get_trip_gst(trip)

    def __str__(self):
        return f"{self.bill_number or 'Draft'} - {self.party.name}"
    
    description = models.TextField(blank=True, verbose_name="Item Description",
                                   help_text="Description shown on invoice (e.g., destination/material)")
    hsn_code = models.CharField(max_length=20, default="996511", verbose_name="HSN Code")
    reverse_charge = models.BooleanField(default=False, verbose_name="Reverse Charge")

    @property
    def trips_count(self):
        return self.trips.count()

    @property
    def total_weight(self):
        return self.trips.aggregate(total=models.Sum('weight'))['total'] or 0

    @property
    def subtotal(self):
        if self.bill_type == self.TYPE_STANDARD:
            return self.amount_override or 0
        # revenue = weight * rate_per_ton
        return self.trips.aggregate(
            total=models.Sum(models.F('weight') * models.F('rate_per_ton'))
        )['total'] or 0

    @property
    def gst_amount(self):
        return self.subtotal * (Decimal(self.gst_rate) / Decimal(100))

    @property
    def total_amount(self):
        return self.subtotal + self.gst_amount

    @property
    def rounded_total(self):
        # Round to nearest whole rupee
        return self.total_amount.quantize(Decimal('1'), rounding='ROUND_HALF_UP')

    @property
    def roundoff(self):
        return self.rounded_total - self.total_amount

class BillTrip(models.Model):
    """
    Through model for Bill and Trip to store LR No for each trip in a bill context.
    """
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='bill_trips')
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='bill_trips')
    lr_no = models.CharField(max_length=100, blank=True, null=True, verbose_name="LR No")
    
    class Meta:
        verbose_name = 'Bill Trip'
        verbose_name_plural = 'Bill Trips'
        unique_together = ('bill', 'trip')

    def __str__(self):
        return f"{self.bill.bill_number} - {self.trip.trip_number} (LR: {self.lr_no or 'N/A'})"
