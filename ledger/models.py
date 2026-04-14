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

    @property
    def total_debit(self):
        """
        Total Debits: Opening Balance (if positive) + Invoices + Manual Expenses
        """
        base = self.opening_balance if self.opening_balance > 0 else Decimal('0')
        invoices = self.financial_records.filter(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            associated_bill__isnull=False
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
        manual_debits = self.financial_records.filter(
            category__type=TransactionCategory.TYPE_EXPENSE
        ).exclude(
            models.Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) | 
            models.Q(category__name='Deductions')
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
        return base + invoices + manual_debits

    @property
    def total_credit(self):
        """
        Total Credits: Opening Balance (if negative) + Income Transactions + Deductions
        """
        base = abs(self.opening_balance) if self.opening_balance < 0 else Decimal('0')
        credits = self.financial_records.filter(
            models.Q(category__type=TransactionCategory.TYPE_INCOME) | 
            models.Q(category__name='Deductions')
        ).exclude(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).aggregate(total=models.Sum('amount'))['total'] or Decimal('0')
        return base + credits

    @property
    def current_balance_value(self):
        return self.total_debit - self.total_credit

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
    invoice_suffix = models.CharField(max_length=50, blank=True, help_text="Optional suffix for invoice numbers.")
    invoice_padding = models.PositiveIntegerField(default=4, help_text="Number of digits for the sequence (e.g. 4 for 0001)")
    invoice_sequence_start = models.PositiveIntegerField(default=1, help_text="Start the sequence from this number")

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

    @property
    def current_balance_value(self):
        """
        Numeric balance: Opening (Dr) + Debits (Income) - Credits (Expenses)
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
            return # Bill.delete will have deleted this record via CASCADE
        
        super().delete(*args, **kwargs)

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
    def is_deduction(self):
        return self.category.name == 'Deductions' if self.category else False

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
                if (self.is_income and not self.is_invoice) or self.is_deduction:
                    return self.amount
                if self.is_invoice and (is_credit_note or is_debit_note):
                    # Credit Note for Creditor increases debt (Credit), 
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

            if self.party.party_type == Party.TYPE_DEBTOR:
                # Debtors: Payments/Income/Credit Notes are Credits (-).
                if (self.is_income and not self.is_invoice) or self.is_deduction:
                    return self.amount
                if self.is_invoice and is_credit_note:
                    return self.amount
            else: # CREDITOR
                # Creditors: Invoices are usually Credits (-). Debit Notes are Debits (+).
                if self.is_invoice:
                    if is_debit_note: return None
                    return self.amount
                if self.is_expense and not self.is_deduction:
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

class Bill(models.Model):
    """
    Bill/Invoice Document aggregating multiple trips or standard items.
    """
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
    GST_TYPE_CHOICES = [
        (GST_TYPE_GST, 'GST'),
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
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    category = models.ForeignKey(TransactionCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='bills', verbose_name="Bill Category")

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
        
        # 2. Handle Invoice Number and Sequence
        if not self.bill_number and self.issuer:
            # Generate and increment sequence
            self.bill_number = self.generate_next_number()
        elif self.bill_number and self.issuer:
            # Manually provided number - sync sequence so we don't use it again
            self.sync_sequence_with_bill_number()
            
        super().save(*args, **kwargs)

    def sync_sequence_with_bill_number(self):
        """Extract numeric part of current bill_number and ensure sequence is at least that high."""
        if not self.issuer or not self.bill_number:
            return
            
        import datetime
        now = datetime.datetime.now()
        prefix = self.issuer.invoice_prefix.replace("{YYYY}", str(now.year))
        suffix = self.issuer.invoice_suffix
        
        num_str = self.bill_number
        if num_str.startswith(prefix):
            num_str = num_str[len(prefix):]
        if suffix and num_str.endswith(suffix):
            num_str = num_str[:-len(suffix)]
            
        try:
            # Extract digits only from numeric part (e.g. "0006" -> 6)
            import re
            numeric_match = re.search(r'(\d+)', num_str)
            if numeric_match:
                val = int(numeric_match.group(1))
                seq_key = f"bill_sequence_{self.issuer.pk}"
                seq, _ = Sequence.objects.get_or_create(key=seq_key, defaults={'value': 0})
                if val > seq.value:
                    seq.value = val
                    seq.save()
        except (ValueError, TypeError):
            pass

    def peek_next_number(self):
        """Show what the next number would be without incrementing sequence."""
        if not self.issuer:
            return None
            
        import datetime
        now = datetime.datetime.now()
        seq_key = f"bill_sequence_{self.issuer.pk}"
        
        seq = Sequence.objects.filter(key=seq_key).first()
        if not seq:
            current_val = self.issuer.invoice_sequence_start - 1
        else:
            current_val = seq.value
            
        next_val = current_val + 1
        prefix = self.issuer.invoice_prefix.replace("{YYYY}", str(now.year))
        padding = self.issuer.invoice_padding
        suffix = self.issuer.invoice_suffix
        
        return f"{prefix}{next_val:0{padding}d}{suffix}"

    def generate_next_number(self):
        """Helper to generate the next invoice number based on issuer settings"""
        if not self.issuer:
            return None
            
        import datetime
        now = datetime.datetime.now()
        
        # Get Sequence per Issuer
        seq_key = f"bill_sequence_{self.issuer.pk}"
        
        # Check if sequence exists; if not, initialize with sequence_start - 1
        if not Sequence.objects.filter(key=seq_key).exists():
            Sequence.objects.create(key=seq_key, value=self.issuer.invoice_sequence_start - 1)
            
        seq_val = Sequence.next_value(seq_key)
        
        # Format using issuer granular fields
        prefix = self.issuer.invoice_prefix.replace("{YYYY}", str(now.year))
        padding = self.issuer.invoice_padding
        suffix = self.issuer.invoice_suffix
        
        return f"{prefix}{seq_val:0{padding}d}{suffix}"

    def delete(self, *args, **kwargs):
        """
        Custom delete for Bill.
        Clean up all related ledger entries (including trip-level payments).
        """
        # 1. Direct Bill Financial Records (already handled by CASCADE, but good to be explicit if needed)
        # self.financial_records.all().delete()
        
        # 2. Trip-level entries related to this bill
        if self.bill_type == self.TYPE_TRIP:
            trips = self.trips.all()
            # Delete direct payments linked to these trips
            FinancialRecord.objects.filter(associated_trip__in=trips).delete()
            # Delete allocations linked to these trips
            TripAllocation.objects.filter(trip__in=trips).delete()

        super().delete(*args, **kwargs)

    def sync_to_ledger(self):
        """
        Main entry point to synchronize this invoice to the ledger.
        """
        self.update_ledger_records()

    def update_ledger_records(self):
        """
        Create/Update a single consolidated 'Invoice' type record in the ledger 
        representing the entire bill.
        """
        # Determine the category: use self.category if set (Standard Invoices), 
        # otherwise default to 'Trip Payment'
        category = self.category
        if not category:
            category, _ = TransactionCategory.objects.get_or_create(
                name='Trip Payment',
                type=TransactionCategory.TYPE_INCOME
            )

        # Amount always includes GST
        total_revenue = self.total_amount

        # Description varies by bill type
        if self.bill_type == self.TYPE_TRIP:
            description = f"Invoice {self.bill_number or 'Draft'} for {self.trips.count()} trips"
        else:
            description = f"{category.name} {self.bill_number or 'Draft'}: {self.item_type or ''}"

        # Find or create consolidated invoice record
        inv_record, created = FinancialRecord.objects.get_or_create(
            associated_bill=self,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            defaults={
                'date': self.date,
                'account': self.issuer,
                'party': self.party,
                'category': category,
                'amount': total_revenue,
                'description': description,
            }
        )

        if not created:
            inv_record.date = self.date
            inv_record.account = self.issuer
            inv_record.party = self.party
            inv_record.category = category
            inv_record.amount = total_revenue
            inv_record.description = description
            inv_record.save()

    @property
    def amount_received(self):
        """
        Calculate total received for this bill.
        Includes:
        1. Direct payments to this bill (Income / Deductions)
        2. Deductions (regardless of category name, if type is correct)
        3. For Trip-based bills: Payments allocated to individual trips
        """
        # 1. Direct links to this bill (Exclude Invoices as they are debits)
        direct = self.financial_records.exclude(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).filter(
            models.Q(category__type=TransactionCategory.TYPE_INCOME) | 
            models.Q(category__name="Deductions")
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
                associated_trip__in=self.trips.all()
            ).exclude(
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).filter(
                models.Q(category__type=TransactionCategory.TYPE_INCOME) | 
                models.Q(category__name="Deductions")
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            trip_payments += direct_trip_payments

        return direct + trip_payments

    @property
    def outstanding_balance(self):
        return self.total_amount - self.amount_received

    @property
    def payment_status(self):
        total = self.total_amount
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
        if self.gst_rate > 0:
            return self.gst_amount / 2
        return 0

    @property
    def sgst_amount(self):
        if self.gst_rate > 0:
            return self.gst_amount / 2
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
