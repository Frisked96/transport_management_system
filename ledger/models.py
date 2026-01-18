"""
Models for Ledger application
"""
from django.db import models
from django.contrib.auth.models import User
from trips.models import Trip, TripLeg


class Party(models.Model):
    """
    Party/Client model for managing business entities
    """
    name = models.CharField(max_length=200, unique=True, verbose_name='Party Name')
    phone_number = models.CharField(max_length=20, blank=True, verbose_name='Phone Number')
    state = models.CharField(max_length=100, blank=True, verbose_name='State')
    address = models.TextField(blank=True, verbose_name='Address')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Party'
        verbose_name_plural = 'Parties'
        ordering = ['name']

    def __str__(self):
        return self.name


class Account(models.Model):
    """
    Company Financial Accounts (Bank, Cash, etc.)
    """
    name = models.CharField(max_length=200, unique=True, verbose_name='Account Name')
    account_number = models.CharField(max_length=50, blank=True, verbose_name='Account Number')
    description = models.TextField(blank=True, verbose_name='Description')
    opening_balance = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0, 
        verbose_name='Opening Balance'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Account'
        verbose_name_plural = 'Accounts'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def current_balance(self):
        """
        Calculate current balance: Opening Balance + Total Income - Total Expenses
        """
        income = self.financial_records.filter(
            category__in=[
                FinancialRecord.CATEGORY_FREIGHT_INCOME,
                FinancialRecord.CATEGORY_PARTY_PAYMENT
            ]
        ).aggregate(total=models.Sum('amount'))['total'] or 0
        
        expenses = self.financial_records.exclude(
            category__in=[
                FinancialRecord.CATEGORY_FREIGHT_INCOME,
                FinancialRecord.CATEGORY_PARTY_PAYMENT
            ]
        ).aggregate(total=models.Sum('amount'))['total'] or 0
        
        return self.opening_balance + income - expenses


class FinancialRecord(models.Model):
    """
    Financial record for managing income and expenses
    """
    
    # Category choices
    CATEGORY_FREIGHT_INCOME = 'Freight Income' # Billed Amount (Legacy/Invoice)
    CATEGORY_PARTY_PAYMENT = 'Party Payment'   # Payment Received from Party
    CATEGORY_FUEL_EXPENSE = 'Fuel Expense'
    CATEGORY_MAINTENANCE_EXPENSE = 'Maintenance Expense'
    CATEGORY_DRIVER_PAYMENT = 'Driver Payment'
    CATEGORY_OTHER = 'Other'
    
    CATEGORY_CHOICES = [
        (CATEGORY_FREIGHT_INCOME, 'Freight Income (Invoice)'),
        (CATEGORY_PARTY_PAYMENT, 'Party Payment (Received)'),
        (CATEGORY_FUEL_EXPENSE, 'Fuel Expense'),
        (CATEGORY_MAINTENANCE_EXPENSE, 'Maintenance Expense'),
        (CATEGORY_DRIVER_PAYMENT, 'Driver Payment'),
        (CATEGORY_OTHER, 'Other'),
    ]
    
    # Date of transaction
    date = models.DateField(
        verbose_name='Transaction Date'
    )

    # Company Account
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Company Account'
    )
    
    # Associated Party (for income/payments)
    party = models.ForeignKey(
        Party,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Associated Party'
    )
    
    # Associated Driver (for salary/allowances)
    driver = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Associated Driver'
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
    
    # Associated trip legs (for multi-leg payments)
    associated_legs = models.ManyToManyField(
        TripLeg,
        blank=True,
        related_name='financial_records',
        verbose_name='Associated Trip Legs'
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