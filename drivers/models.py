"""
Models for Drivers application
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Driver(models.Model):
    """
    Driver profile model extending User
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='driver_profile',
        verbose_name='User Account'
    )

    employee_id = models.CharField(
        max_length=20,
        unique=True,
        verbose_name='Employee ID'
    )

    license_number = models.CharField(
        max_length=50,
        verbose_name='License Number'
    )

    phone_number = models.CharField(
        max_length=20,
        verbose_name='Phone Number'
    )

    address = models.TextField(
        verbose_name='Address',
        blank=True
    )

    joined_date = models.DateField(
        default=timezone.now,
        verbose_name='Joined Date'
    )

    class Meta:
        verbose_name = 'Driver'
        verbose_name_plural = 'Drivers'
        permissions = [
            ('can_view_all_drivers', 'Can view all drivers'),
            ('can_manage_driver_finance', 'Can manage driver finances'),
        ]

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.employee_id})"

    @property
    def current_balance(self):
        """
        Calculates the current pocket balance.
        Positive: Company owes Driver.
        Negative: Driver owes Company.
        """
        return self.transactions.aggregate(balance=models.Sum('amount'))['balance'] or 0

    @property
    def abs_current_balance(self):
        """
        Returns the absolute value of the current balance.
        """
        return abs(self.current_balance)


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

    def __str__(self):
        return f"{self.driver} - {self.transaction_type} - {self.amount}"
