"""
Driver model for Drivers application
"""
from django.db import models
from django.core.exceptions import ObjectDoesNotExist
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
        verbose_name='Employee ID',
        blank=True,
        null=True
    )

    license_number = models.CharField(
        max_length=50,
        verbose_name='License Number',
        blank=True,
        null=True
    )

    phone_number = models.CharField(
        max_length=20,
        verbose_name='Phone Number',
        blank=True,
        null=True
    )

    address = models.TextField(
        verbose_name='Address',
        blank=True
    )

    joined_date = models.DateField(
        default=timezone.now,
        verbose_name='Joined Date'
    )

    # Denormalized Balance Fields
    current_balance_cached = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0, 
        verbose_name='Current Balance'
    )

    # Deletion flag to prevent signals from trying to update a deleted object
    _is_being_deleted = False

    @property
    def name(self):
        """Returns the full name or username of the driver"""
        return self.user.get_full_name() or self.user.username

    def refresh_balance(self):
        """
        Recalculate and update the cached balance fields from scratch.
        """
        self._refreshing_balance = True
        try:
            self.current_balance_cached = self.transactions.aggregate(balance=models.Sum('amount'))['balance'] or 0
            self.save(update_fields=['current_balance_cached'])
        finally:
            del self._refreshing_balance

    def delete(self, *args, **kwargs):
        self._is_being_deleted = True
        super().delete(*args, **kwargs)

    class Meta:
        verbose_name = 'Driver'
        verbose_name_plural = 'Drivers'
        ordering = ['-joined_date', '-id']
        permissions = [
            ('can_view_all_drivers', 'Can view all drivers'),
            ('can_manage_driver_finance', 'Can manage driver finances'),
        ]

    def __str__(self):
        try:
            name = self.user.get_full_name() or self.user.username if self.user else "Unknown User"
        except ObjectDoesNotExist:
            name = f"User #{self.user_id}"
        if self.employee_id:
            return f"{name} ({self.employee_id})"
        return name

    @property
    def current_balance(self):
        """
        Calculates the current pocket balance.
        Positive: Company owes Driver.
        Negative: Driver owes Company.
        """
        # Prefer cached if available
        if hasattr(self, '_refreshing_balance'):
            return self.transactions.aggregate(balance=models.Sum('amount'))['balance'] or 0
        return self.current_balance_cached

    @property
    def abs_current_balance(self):
        """
        Returns the absolute value of the current balance.
        """
        return abs(self.current_balance)
