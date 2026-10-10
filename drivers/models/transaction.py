"""
DriverTransaction model and signals for Drivers application
"""
from django.db import models
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import F
from .driver import Driver


class DriverTransaction(models.Model):
    """
    Financial transactions for the driver (Pocket/Wallet)
    """

    # Transaction Types
    TYPE_SALARY = 'Salary'          # Credit (+)
    TYPE_ALLOWANCE = 'Allowance'    # Credit (+) (Per Diem / Entitlement)
    TYPE_LOAN = 'Loan'              # Debit (-) (Driver takes money)
    TYPE_PAYMENT = 'Payment'        # Debit (-) (Company pays Driver)
    TYPE_REPAYMENT = 'Repayment'    # Credit (+) (Driver pays Company)
    TYPE_OTHER = 'Other'            # Manual

    TYPE_CHOICES = [
        (TYPE_SALARY, 'Salary Credit (+)'),
        (TYPE_ALLOWANCE, 'Allowance Credit (+)'),
        (TYPE_LOAN, 'Loan (Debit -)'),
        (TYPE_PAYMENT, 'Payment (Debit -)'),
        (TYPE_REPAYMENT, 'Repayment (Credit +)'),
        (TYPE_OTHER, 'Other'),
    ]

    driver = models.ForeignKey(
        Driver,
        on_delete=models.CASCADE,
        related_name='transactions',
        verbose_name='Driver'
    )

    date = models.DateField(
        default=timezone.now,
        verbose_name='Transaction Date'
    )

    transaction_type = models.CharField(
        max_length=30,
        choices=TYPE_CHOICES,
        verbose_name='Type'
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Amount',
        help_text='Positive: Company owes Driver. Negative: Driver owes Company.'
    )

    description = models.TextField(
        verbose_name='Description',
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Created At'
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_driver_transactions',
        verbose_name='Created By'
    )

    class Meta:
        verbose_name = 'Driver Transaction'
        verbose_name_plural = 'Driver Transactions'
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['date', 'created_at']),
            models.Index(fields=['driver', 'date']),
            models.Index(fields=['transaction_type']),
        ]

    def __str__(self):
        try:
            driver_str = str(self.driver) if self.driver else "No Driver"
        except ObjectDoesNotExist:
            driver_str = f"Driver #{self.driver_id}"
        return f"{driver_str} - {self.transaction_type} - {self.amount}"


# --- Signals ---

@receiver(post_save, sender=DriverTransaction)
def update_driver_balance_on_save(sender, instance, created, **kwargs):
    """
    Update Driver balance when a transaction is saved.
    """
    if getattr(instance.driver, '_is_being_deleted', False):
        return

    if created:
        Driver.objects.filter(pk=instance.driver_id).update(
            current_balance_cached=F('current_balance_cached') + instance.amount
        )
    else:
        instance.driver.refresh_balance()


@receiver(post_delete, sender=DriverTransaction)
def update_driver_balance_on_delete(sender, instance, **kwargs):
    """
    Update Driver balance when a transaction is deleted.
    """
    if getattr(instance.driver, '_is_being_deleted', False):
        return

    Driver.objects.filter(pk=instance.driver_id).update(
        current_balance_cached=F('current_balance_cached') - instance.amount
    )
