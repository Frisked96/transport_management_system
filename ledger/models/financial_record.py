"""
FinancialRecord model and upload path for Ledger application
"""
import os
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from trips.models import Trip
from .sequence import Sequence
from .party import Party
from .category import TransactionCategory
from .company import CompanyAccount


def financial_record_upload_path(instance, filename):
    """
    Determines the upload path for a financial record document.
    Format: financial_records/<type>/<identifier>/<safe_root>_<timestamp><ext>
    """
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
    ext = os.path.splitext(filename)[1]
    name_root = os.path.splitext(filename)[0]
    safe_root = "".join([c for c in name_root if c.isalnum() or c in (' ', '_', '-')]).strip().replace(' ', '_')
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{safe_root}_{timestamp}{ext}" if safe_root else f"doc_{timestamp}{ext}"
    
    return os.path.join('financial_records', folder, safe_identifier, new_filename)


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
    associated_tyre = models.ForeignKey(
        'fleet.Tyre',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='financial_records',
        verbose_name='Associated Tyre'
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
    tds_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='TDS %'
    )
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='recorded_financials',
        verbose_name='Recorded By'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    @classmethod
    def resequence_entry_numbers(cls):
        """
        Resequence all entry numbers to remove gaps.
        """
        from ledger.services import LedgerService
        return LedgerService.resequence_financial_records()

    def save(self, *args, **kwargs):
        if not self.entry_number:
            self.entry_number = Sequence.next_value('financial_record_entry_number')
        
        # Auto-populate party from trip if missing
        if self.associated_trip and not self.party:
            self.party = self.associated_trip.party
            
        # Deductions, TDS, Shortage are non-bank adjustments: ensure account is None
        if self.is_deduction:
            self.account = None

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
        else:
            super().delete(*args, **kwargs)
        
        # Resequence after deletion
        FinancialRecord.resequence_entry_numbers()

    class Meta:
        verbose_name = 'Financial Record'
        verbose_name_plural = 'Financial Records'
        ordering = ['-date']
        indexes = [
            models.Index(fields=['date', 'created_at']),
            models.Index(fields=['account', 'date']),
            models.Index(fields=['party', 'date']),
            models.Index(fields=['driver', 'date']),
            models.Index(fields=['record_type', 'date']),
        ]
        permissions = [
            ('can_view_financial_records', 'Can view financial records'),
            ('can_manage_financial_records', 'Can manage financial records'),
        ]

    def __str__(self):
        try:
            category_name = self.category.name if self.category else 'No Category'
        except ObjectDoesNotExist:
            category_name = 'No Category'

        if self.associated_trip_id:
            try:
                trip = self.associated_trip
                if trip:
                    return f"{category_name} - Trip: {trip.trip_number} - {self.amount}"
            except ObjectDoesNotExist:
                return f"{category_name} - Trip: #{self.associated_trip_id} - {self.amount}"

        if self.associated_bill_id:
            try:
                bill = self.associated_bill
                if bill:
                    bill_num = bill.bill_number or "Draft Bill"
                    return f"{category_name} - Bill: {bill_num} - {self.amount}"
            except ObjectDoesNotExist:
                return f"{category_name} - Bill: #{self.associated_bill_id} - {self.amount}"

        return f"{category_name} - {self.amount}"

    @property
    def linked_bill(self):
        """Returns associated bill or bill from allocations"""
        try:
            if self.associated_bill:
                return self.associated_bill
        except ObjectDoesNotExist:
            pass
        
        # If no direct bill, check if it's a trip payment with allocations
        try:
            first_alloc = self.allocations.select_related('trip').first()
            if first_alloc and first_alloc.trip and first_alloc.trip.associated_bill:
                return first_alloc.trip.associated_bill
        except ObjectDoesNotExist:
            pass
        
        # Finally check if direct associated trip has a bill
        try:
            if self.associated_trip and self.associated_trip.associated_bill:
                return self.associated_trip.associated_bill
        except ObjectDoesNotExist:
            pass
            
        return None

    @property
    def linked_trip(self):
        """Returns associated trip or first trip from allocations"""
        try:
            if self.associated_trip:
                return self.associated_trip
        except ObjectDoesNotExist:
            pass
        
        try:
            first_alloc = self.allocations.select_related('trip').first()
            if first_alloc:
                return first_alloc.trip
        except ObjectDoesNotExist:
            pass
            
        return None

    @property
    def reference_entity(self):
        """Returns party, driver, or None as the reference entity"""
        try:
            if self.party:
                return self.party
        except ObjectDoesNotExist:
            pass
        try:
            if self.driver:
                return self.driver
        except ObjectDoesNotExist:
            pass
        return None

    @property
    def refrence_entity(self):
        """Alias for reference_entity"""
        return self.reference_entity

    @property
    def reference_entity_name(self):
        """Returns the human-readable name of the reference entity"""
        try:
            if self.party:
                return self.party.name
        except ObjectDoesNotExist:
            pass
        try:
            if self.driver:
                return self.driver.get_full_name() or self.driver.username
        except ObjectDoesNotExist:
            pass
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
        return self.category.name in ['Deductions', 'TDS', 'Shortage'] if self.category else False

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
            is_payment_out = self.category.name == 'Payment Out' if self.category else False
            is_deduction = self.is_deduction

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
                # Liability decreases (Debit): Payment Out, Deductions, and general Income
                if (self.is_income and not self.is_invoice) or is_deduction or is_payment_out:
                    return self.amount
                if self.is_invoice and (is_credit_note or is_debit_note):
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
            is_payment_out = self.category.name == 'Payment Out' if self.category else False
            is_deduction = self.is_deduction

            if self.party.party_type == Party.TYPE_DEBTOR:
                # Debtors: Payments/Income/Credit Notes are Credits (-).
                if (self.is_income and not self.is_invoice) or self.is_deduction:
                    return self.amount
                if self.is_invoice and is_credit_note:
                    return self.amount
            else: # CREDITOR
                # Creditors: Invoices are usually Credits (-). Liability increases.
                if self.is_invoice:
                    if is_debit_note: return None
                    return self.amount
                # General Expenses (NOT payment out/deduction) increase liability (Credit)
                if self.is_expense and not (is_deduction or is_payment_out):
                    return self.amount
                if self.is_invoice and is_credit_note:
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
