"""
TollAccount model for Ledger application.
"""
from decimal import Decimal
from django.db import models, transaction
from django.utils import timezone


class TollAccount(models.Model):
    """
    Dedicated Toll / FASTag Account / Wallet.
    Maintains a pre-funded pool of money used by fleet vehicles.
    """
    name = models.CharField(max_length=100, unique=True, verbose_name='Account / Wallet Name')
    provider = models.CharField(max_length=100, blank=True, verbose_name='Provider / Bank')
    account_number = models.CharField(max_length=100, blank=True, verbose_name='Wallet ID / Customer ID')
    
    # Initial Starting Balance
    opening_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Initial Starting Balance',
        help_text='Existing FASTag balance on system setup/migration.'
    )
    opening_balance_date = models.DateField(
        default=timezone.now,
        verbose_name='Starting Balance Date'
    )

    # Cached Running Balance
    current_balance_cached = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Current Balance'
    )

    is_default = models.BooleanField(
        default=True,
        verbose_name='Default Account',
        help_text='Used as default choice for toll entries and recharges.'
    )
    notes = models.TextField(blank=True, verbose_name='Notes / Description')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Updated At')

    class Meta:
        verbose_name = 'Toll Account'
        verbose_name_plural = 'Toll Accounts'
        ordering = ['-is_default', 'name']

    def __str__(self):
        provider_str = f" ({self.provider})" if self.provider else ""
        return f"{self.name}{provider_str}"

    def save(self, *args, **kwargs):
        # If this account is marked default, unset default on other accounts
        if self.is_default:
            TollAccount.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)
        
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if is_new:
            self.refresh_balance()

    def refresh_balance(self):
        """
        Recalculates current_balance_cached from opening balance + recharges - toll entries.
        """
        from django.db.models import Sum
        with transaction.atomic():
            acc = TollAccount.objects.select_for_update().get(pk=self.pk)
            total_recharges = acc.recharges.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            total_spent = acc.entries.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            new_balance = acc.opening_balance + total_recharges - total_spent
            acc.current_balance_cached = new_balance
            acc.save(update_fields=['current_balance_cached'])
            self.current_balance_cached = new_balance
            return new_balance

    @property
    def total_recharged(self):
        from django.db.models import Sum
        return self.recharges.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    @property
    def total_spent(self):
        from django.db.models import Sum
        return self.entries.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    @property
    def current_balance_value(self):
        return self.current_balance_cached

    @property
    def formatted_balance(self):
        val = self.current_balance_cached
        if val >= 0:
            return f"₹{val:,.2f}"
        return f"-₹{abs(val):,.2f} (Overdrawn)"
