"""
Models for Ledger application
"""
from django.db import models
from django.contrib.auth.models import User
from trips.models import Trip
from django.db.models import F, Value, Max
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
    Party/Client/Vendor model for managing business entities
    """
    TYPE_DEBTOR = 'Debtor'
    TYPE_CREDITOR = 'Creditor'
    TYPE_CHOICES = [
        (TYPE_DEBTOR, 'Customer (Debtor)'),
        (TYPE_CREDITOR, 'Vendor/Supplier (Creditor)'),
    ]

    name = models.CharField(max_length=200, unique=True, verbose_name='Party Name')
    party_type = models.CharField(
        max_length=20, 
        choices=TYPE_CHOICES, 
        default=TYPE_DEBTOR,
        verbose_name='Party Type'
    )
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
    
    # Denormalized Balance Fields
    total_debit_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0, 
        verbose_name='Total Billed/Debit'
    )
    total_credit_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0, 
        verbose_name='Total Received/Credit'
    )
    current_balance_cached = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0, 
        verbose_name='Current Balance'
    )

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
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def refresh_balance(self):
        """
        Recalculate and update the cached balance fields from scratch.
        """
        from ledger.services import BalanceService
        return BalanceService.refresh_party_balance(self)

    @property
    def total_debit(self):
        return self.total_debit_amount

    def _calculate_total_debit(self):
        """
        Total Debits: Opening Balance (if positive) + Debits (Revenue/Invoices/Notes)
        """
        base = self.opening_balance if self.opening_balance > 0 else Decimal('0')
        
        # We use Python-side summation to ensure accuracy with complex properties
        # while using select_related to keep it efficient.
        records = self.financial_records.select_related('category', 'associated_bill__category').all()
        debits = sum((r.debit_amount or Decimal('0')) for r in records)
        
        return base + debits

    @property
    def total_credit(self):
        return self.total_credit_amount

    def _calculate_total_credit(self):
        """
        Total Credits: Opening Balance (if negative) + Credits (Payments/Notes)
        """
        base = abs(self.opening_balance) if self.opening_balance < 0 else Decimal('0')
        
        # We use Python-side summation to ensure accuracy with complex properties
        # while using select_related to keep it efficient.
        records = self.financial_records.select_related('category', 'associated_bill__category').all()
        credits = sum((r.credit_amount or Decimal('0')) for r in records)
        
        return base + credits

    @property
    def current_balance_value(self):
        return self.current_balance_cached

    @property
    def current_balance(self):
        val = self.current_balance_value
        if val > 0: return f"{abs(val):.2f} Dr"
        elif val < 0: return f"{abs(val):.2f} Cr"
        return "0.00"

    @property
    def total_billed(self): return self.total_debit
    @property
    def total_received(self): return self.total_credit

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
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = 'Transaction Category'
        verbose_name_plural = 'Transaction Categories'
        ordering = ['-created_at']

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
    invoice_prefix = models.CharField(max_length=50, default="INV/{YYYY}/", help_text="Prefix for invoice numbers. Use {YYYY} for year.")
    cn_prefix = models.CharField(max_length=50, default="CN-{YYYY}/", help_text="Prefix for Credit Notes. Use {YYYY} for year.")
    dn_prefix = models.CharField(max_length=50, default="DN-{YYYY}/", help_text="Prefix for Debit Notes. Use {YYYY} for year.")
    invoice_suffix = models.CharField(max_length=50, blank=True, help_text="Optional suffix for invoice numbers.")
    invoice_padding = models.PositiveIntegerField(default=4, help_text="Number of digits for the sequence (e.g. 4 for 0001)")
    invoice_sequence_start = models.PositiveIntegerField(default=1, help_text="Start the sequence from this number")

    # Denormalized Balance Fields
    current_balance_cached = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0, 
        verbose_name='Current Balance'
    )

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
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def refresh_balance(self):
        """
        Recalculate and update the cached balance fields from scratch.
        """
        from ledger.services import BalanceService
        return BalanceService.refresh_account_balance(self)

    @property
    def current_balance_value(self):
        return self.current_balance_cached

    def _calculate_balance(self):
        """
        Numeric balance: Opening (Dr) + Debits (Income) - Credits (Expenses)
        """
        from ledger.services import BalanceService
        # Note: _calculate_balance is still used in signals for pre_save logic
        # We can keep a simplified version here or move it entirely to service.
        # For now, let's keep it for compatibility but maybe delegate.
        return BalanceService.refresh_account_balance(self)

    @property
    def current_balance(self):
        """Formatted balance with Dr/Cr"""
        val = self.current_balance_value
        if val > 0: return f"{abs(val):.2f} Dr"
        elif val < 0: return f"{abs(val):.2f} Cr"
        return "0.00"

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
    tds_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='TDS %'
    )
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='recorded_financials',
        verbose_name='Recorded By'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    @classmethod
    def resequence_entry_numbers(cls):
        """
        Resequence all entry numbers to remove gaps.
        """
        from ledger.services import LedgerService
        return LedgerService.resequence_financial_records()

    def save(self, *args, **kwargs):
        if not self.entry_number:
            self.entry_number = Sequence.next_value('financial_record_entry_number')
        
        # Auto-populate party from trip if missing
        if self.associated_trip and not self.party:
            self.party = self.associated_trip.party
            
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        If a ledger entry representing an Invoice is deleted, the Bill itself should be deleted.
        """
        if self.record_type == self.RECORD_TYPE_INVOICE and self.associated_bill:
            # We must be careful not to recurse. super().delete() should be called 
            # after deleting the bill if Bill doesn't CASCADE back here.
            # But Bill DOES CASCADE back here. So deleting the bill will delete this record.
            bill = self.associated_bill
            # Set to None to prevent CASCADE from trying to delete an already-deleting instance
            self.associated_bill = None 
            bill.delete()
        else:
            super().delete(*args, **kwargs)
        
        # Resequence after deletion
        FinancialRecord.resequence_entry_numbers()

    class Meta:
        verbose_name = 'Financial Record'
        verbose_name_plural = 'Financial Records'
        ordering = ['-date']
        indexes = [
            models.Index(fields=['date', 'created_at']),
            models.Index(fields=['account', 'date']),
            models.Index(fields=['party', 'date']),
            models.Index(fields=['driver', 'date']),
            models.Index(fields=['record_type', 'date']),
        ]
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
    def is_deduction(self):
        return self.category.name in ['Deductions', 'TDS', 'Shortage'] if self.category else False

    @property
    def debit_amount(self):
        """
        Returns amount if it is a Debit for the primary entity in context.
        """
        # Perspective of the Party
        if self.party:
            # Special handling for Credit/Debit Note labels in Invoices
            is_credit_note = (
                self.associated_bill and 
                self.associated_bill.category and 
                self.associated_bill.category.name == 'Credit Note'
            )
            is_debit_note = (
                self.associated_bill and 
                self.associated_bill.category and 
                self.associated_bill.category.name == 'Debit Note'
            )
            is_payment_out = self.category.name == 'Payment Out' if self.category else False
            is_deduction = self.is_deduction

            if self.party.party_type == Party.TYPE_DEBTOR:
                # Debtors: Invoices are usually Debits (+). Credit Notes are Credits (-).
                if self.is_invoice:
                    if is_credit_note: return None
                    return self.amount
                # Expenses/Transactions:
                if self.is_expense and not self.is_deduction:
                    return self.amount
            else: # CREDITOR
                # Creditors: Payments/Income/Debit Notes are Debits (+).
                # Liability decreases (Debit): Payment Out, Deductions, and general Income
                if (self.is_income and not self.is_invoice) or is_deduction or is_payment_out:
                    return self.amount
                if self.is_invoice and (is_credit_note or is_debit_note):
                    # Debit Note for Creditor reduces debt (Debit).
                    if is_debit_note: return self.amount
            return None

        # Perspective of the Company Account (Asset)
        if self.is_income and not self.is_invoice:
            return self.amount
        return None

    @property
    def credit_amount(self):
        """
        Returns amount if it is a Credit for the primary entity in context.
        """
        # Perspective of the Party
        if self.party:
            is_credit_note = (
                self.associated_bill and 
                self.associated_bill.category and 
                self.associated_bill.category.name == 'Credit Note'
            )
            is_debit_note = (
                self.associated_bill and 
                self.associated_bill.category and 
                self.associated_bill.category.name == 'Debit Note'
            )
            is_payment_out = self.category.name == 'Payment Out' if self.category else False
            is_deduction = self.is_deduction

            if self.party.party_type == Party.TYPE_DEBTOR:
                # Debtors: Payments/Income/Credit Notes are Credits (-).
                if (self.is_income and not self.is_invoice) or self.is_deduction:
                    return self.amount
                if self.is_invoice and is_credit_note:
                    return self.amount
            else: # CREDITOR
                # Creditors: Invoices are usually Credits (-). Liability increases.
                if self.is_invoice:
                    if is_debit_note: return None
                    return self.amount
                # General Expenses (NOT payment out/deduction) increase liability (Credit)
                if self.is_expense and not (is_deduction or is_payment_out):
                    return self.amount
                if self.is_invoice and is_credit_note:
                    return self.amount
            return None

        # Perspective of the Company Account (Asset)
        if self.is_expense or self.is_invoice:
            return self.amount
        return None


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

class BillAllocation(models.Model):
    financial_record = models.ForeignKey(
        FinancialRecord,
        on_delete=models.CASCADE,
        related_name='bill_allocations',
        verbose_name='Financial Record'
    )
    bill = models.ForeignKey(
        'Bill',
        on_delete=models.CASCADE,
        related_name='payment_allocations',
        verbose_name='Bill'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Allocated Amount'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Bill Allocation'
        verbose_name_plural = 'Bill Allocations'
        unique_together = ('financial_record', 'bill')

    def __str__(self):
        return f"{self.financial_record} -> {self.bill.bill_number}: {self.amount}"

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
    date = models.DateField(verbose_name="Invoice Date")
    
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
        if self.issuer:
            if not self.bill_no:
                self.bill_no = self.get_next_available_no(self.issuer, self.date)
            
            # Update the full string representation
            prefix = self.get_prefix()
            padding = self.issuer.invoice_padding
            suffix = self.issuer.invoice_suffix
            self.bill_number = f"{prefix}{self.bill_no:0{padding}d}{suffix}"
        
        # Update revenue caches before save
        self.subtotal_cached = self.subtotal
        self.gst_amount_cached = self.gst_amount
        self.total_amount_cached = self.rounded_total
        
        is_new = self.pk is None
        if is_new:
             self.outstanding_balance_cached = self.total_amount_cached
             self.payment_status_cached = self.PAYMENT_STATUS_UNPAID
            
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
        # Get list of trips before they are unlinked
        if not self.pk:
            return super().delete(*args, **kwargs)

        self._is_being_deleted = True
        try:
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
        finally:
            if hasattr(self, '_is_being_deleted'):
                del self._is_being_deleted

    def sync_to_ledger(self):
        """
        Main entry point to synchronize this invoice to the ledger.
        """
        from ledger.services import BillingService
        return BillingService.sync_bill_to_ledger(self)

    @property
    def is_adjustment(self):
        """Returns True if the bill is a Credit Note or Debit Note adjustment."""
        return self.category and self.category.name in ['Credit Note', 'Debit Note']

    @property
    def subtotal(self):
        """Returns subtotal, prioritizing cached value"""
        if self.subtotal_cached:
            return self.subtotal_cached
            
        if hasattr(self, 'annotated_subtotal'):
            return self.annotated_subtotal

        if self.bill_type == self.TYPE_STANDARD:
            base = 0
            if self.amount_override is not None:
                base = self.amount_override
            elif self.standard_weight and self.standard_rate:
                base = self.standard_weight * self.standard_rate
            return max(0, base - (self.discount or 0))

        if not self.pk:
            return Decimal('0')

        trip_subtotal = 0
        for bt in self.bill_trips.all():
            trip_subtotal += (bt.trip.revenue - (bt.discount or 0))
        return max(0, trip_subtotal - (self.discount or 0))

    @property
    def gst_amount(self):
        """Returns GST amount, prioritizing cached value"""
        if self.gst_amount_cached:
            return self.gst_amount_cached
        if hasattr(self, 'annotated_gst_amount'):
            return self.annotated_gst_amount
        return self.subtotal * (Decimal(self.gst_rate) / Decimal(100))

    @property
    def total_amount(self):
        """Returns total amount, prioritizing cached value"""
        if self.total_amount_cached:
            return self.total_amount_cached
        if hasattr(self, 'annotated_total_amount'):
            return self.annotated_total_amount
        return self.subtotal + self.gst_amount

    @property
    def rounded_total(self):
        """Returns rounded total, prioritizing cached value"""
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
        return f"{self.bill.bill_number} - {self.trip.trip_number} (LR: {self.lr_no or 'N/A'})"