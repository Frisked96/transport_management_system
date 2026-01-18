"""
Models for Ledger application
"""
from django.db import models
from django.contrib.auth.models import User
from trips.models import Trip

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

    class Meta:
        verbose_name = 'Transaction Category'
        verbose_name_plural = 'Transaction Categories'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.type})"

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
        income = self.financial_records.filter(category__type=TransactionCategory.TYPE_INCOME).aggregate(total=models.Sum('amount'))['total'] or 0
        expenses = self.financial_records.filter(category__type=TransactionCategory.TYPE_EXPENSE).aggregate(total=models.Sum('amount'))['total'] or 0
        return self.opening_balance + income - expenses

class FinancialRecord(models.Model):
    """
    Financial record for managing income and expenses
    """
    date = models.DateField(verbose_name='Transaction Date')
    account = models.ForeignKey(
        Account,
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
        User,
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
        upload_to='financial_docs/%Y/%m/',
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

    def save(self, *args, **kwargs):
        if not self.entry_number:
            last_entry = FinancialRecord.objects.all().order_by('entry_number').last()
            if last_entry and last_entry.entry_number:
                self.entry_number = last_entry.entry_number + 1
            else:
                self.entry_number = 1
        super().save(*args, **kwargs)

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
            return f"{self.category.name if self.category else 'No Category'} - {self.associated_trip.trip_number} - {self.amount}"
        return f"{self.category.name if self.category else 'No Category'} - {self.amount}"

    @property
    def is_income(self):
        return self.category.type == TransactionCategory.TYPE_INCOME if self.category else False

    @property
    def is_expense(self):
        return self.category.type == TransactionCategory.TYPE_EXPENSE if self.category else False

    @property
    def signed_amount(self):
        if self.is_expense:
            return -abs(self.amount)
        return abs(self.amount)

from django.db.models.signals import post_delete
from django.dispatch import receiver
from .utils import recalculate_trip_payment_status

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

@receiver(post_delete, sender=FinancialRecord)
def update_trip_on_record_delete(sender, instance, **kwargs):
    if instance.associated_trip:
        recalculate_trip_payment_status(instance.associated_trip)

@receiver(post_delete, sender=TripAllocation)
def update_trip_on_allocation_delete(sender, instance, **kwargs):
    if instance.trip:
        recalculate_trip_payment_status(instance.trip)