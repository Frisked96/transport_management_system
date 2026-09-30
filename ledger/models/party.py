"""
Party model for Ledger application
"""
from decimal import Decimal
from django.db import models


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

    # Deletion flag to prevent signals from trying to update a deleted object
    _is_being_deleted = False

    class Meta:
        verbose_name = 'Party'
        verbose_name_plural = 'Parties'
        ordering = ['-created_at']

    def delete(self, *args, **kwargs):
        self._is_being_deleted = True
        super().delete(*args, **kwargs)

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

    def _calculate_totals(self):
        """Single-pass calculation of total debits and credits from financial records."""
        base_debit = self.opening_balance if self.opening_balance > 0 else Decimal('0')
        base_credit = abs(self.opening_balance) if self.opening_balance < 0 else Decimal('0')
        records = self.financial_records.select_related('category', 'associated_bill__category').all()
        debit_sum = Decimal('0')
        credit_sum = Decimal('0')
        for r in records:
            debit_sum += (r.debit_amount or Decimal('0'))
            credit_sum += (r.credit_amount or Decimal('0'))
        return base_debit + debit_sum, base_credit + credit_sum

    def _calculate_total_debit(self):
        """Total Debits: Opening Balance (if positive) + Debits (Revenue/Invoices/Notes)"""
        debits, _ = self._calculate_totals()
        return debits

    @property
    def total_credit(self):
        return self.total_credit_amount

    def _calculate_total_credit(self):
        """Total Credits: Opening Balance (if negative) + Credits (Payments/Notes)"""
        _, credits = self._calculate_totals()
        return credits

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
