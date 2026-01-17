"""
Models for Ledger application
"""
from django.db import models
from django.contrib.auth.models import User
from trips.models import Trip


class FinancialRecord(models.Model):
    """
    Financial record for managing income and expenses
    """
    
    # Category choices
    CATEGORY_FREIGHT_INCOME = 'Freight Income'
    CATEGORY_FUEL_EXPENSE = 'Fuel Expense'
    CATEGORY_MAINTENANCE_EXPENSE = 'Maintenance Expense'
    CATEGORY_DRIVER_PAYMENT = 'Driver Payment'
    CATEGORY_OTHER = 'Other'
    
    CATEGORY_CHOICES = [
        (CATEGORY_FREIGHT_INCOME, 'Freight Income'),
        (CATEGORY_FUEL_EXPENSE, 'Fuel Expense'),
        (CATEGORY_MAINTENANCE_EXPENSE, 'Maintenance Expense'),
        (CATEGORY_DRIVER_PAYMENT, 'Driver Payment'),
        (CATEGORY_OTHER, 'Other'),
    ]
    
    # Date of transaction
    date = models.DateField(
        verbose_name='Transaction Date'
    )
    
    # Associated trip (optional)
    associated_trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Associated Trip'
    )
    
    # Category of transaction
    category = models.CharField(
        max_length=30,
        choices=CATEGORY_CHOICES,
        verbose_name='Category'
    )
    
    # Amount (positive for income, negative for expense)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Amount'
    )
    
    # Description
    description = models.TextField(
        verbose_name='Description'
    )
    
    # Document reference (file upload - optional)
    document_ref = models.FileField(
        upload_to='financial_docs/%Y/%m/',
        null=True,
        blank=True,
        verbose_name='Supporting Document'
    )
    
    # Who recorded this entry
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='recorded_financials',
        verbose_name='Recorded By'
    )
    
    class Meta:
        verbose_name = 'Financial Record'
        verbose_name_plural = 'Financial Records'
        ordering = ['-date']
        permissions = [
            ('can_view_financial_records', 'Can view financial records'),
            ('can_manage_financial_records', 'Can manage financial records'),
        ]
    
    def __str__(self):
        if self.associated_trip:
            return f"{self.category} - {self.associated_trip.trip_number} - {self.amount}"
        return f"{self.category} - {self.amount}"
    
    @property
    def is_income(self):
        """Check if record is income"""
        return self.category == self.CATEGORY_FREIGHT_INCOME
    
    @property
    def is_expense(self):
        """Check if record is expense"""
        return self.category in [
            self.CATEGORY_FUEL_EXPENSE,
            self.CATEGORY_MAINTENANCE_EXPENSE,
            self.CATEGORY_DRIVER_PAYMENT,
            self.CATEGORY_OTHER
        ]
    
    @property
    def signed_amount(self):
        """Return amount with proper sign (positive for income, negative for expense)"""
        if self.is_expense:
            return -abs(self.amount)
        return abs(self.amount)