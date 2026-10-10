"""
CompanyAccount model for Ledger application
"""
from django.db import models


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

    # Deletion flag to prevent signals from trying to update a deleted object
    _is_being_deleted = False

    class Meta:
        verbose_name = 'Company Account'
        verbose_name_plural = 'Company Accounts'
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
        return BalanceService.refresh_account_balance(self)

    @property
    def current_balance_value(self):
        return self.current_balance_cached

    def _calculate_balance(self):
        """
        Numeric balance: Opening (Dr) + Debits (Income) - Credits (Expenses)
        """
        from ledger.services import BalanceService
        return BalanceService.refresh_account_balance(self)

    @property
    def current_balance(self):
        """Formatted balance with Dr/Cr"""
        val = self.current_balance_value
        if val > 0: return f"{abs(val):.2f} Dr"
        elif val < 0: return f"{abs(val):.2f} Cr"
        return "0.00"
