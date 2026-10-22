"""
TollRecharge model and upload path for Ledger application.
"""
import os
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .toll_account import TollAccount


def toll_recharge_upload_path(instance, filename):
    """
    Upload path for toll recharge documents.
    Format: toll_records/recharges/<account_id>/<filename>
    """
    ext = os.path.splitext(filename)[1]
    name_root = os.path.splitext(filename)[0]
    safe_root = "".join([c for c in name_root if c.isalnum() or c in (' ', '_', '-')]).strip().replace(' ', '_')
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{safe_root}_{timestamp}{ext}" if safe_root else f"recharge_{timestamp}{ext}"
    account_id = instance.toll_account_id or 'general'
    return os.path.join('toll_records', 'recharges', str(account_id), new_filename)


class TollRecharge(models.Model):
    """
    Lumpsum top-up / recharge into a Toll Account from a Company Account.
    Reduces the Company Account's bank balance and increases the Toll Account balance.
    """
    MODE_BANK_TRANSFER = 'Bank Transfer / NEFT / RTGS'
    MODE_NET_BANKING = 'Net Banking'
    MODE_UPI = 'UPI'
    MODE_DEBIT_CARD = 'Debit Card'
    MODE_CHEQUE = 'Cheque'
    MODE_OTHER = 'Other'

    PAYMENT_MODE_CHOICES = [
        (MODE_BANK_TRANSFER, 'Bank Transfer / NEFT / RTGS'),
        (MODE_NET_BANKING, 'Net Banking'),
        (MODE_UPI, 'UPI'),
        (MODE_DEBIT_CARD, 'Debit Card'),
        (MODE_CHEQUE, 'Cheque'),
        (MODE_OTHER, 'Other'),
    ]

    toll_account = models.ForeignKey(
        TollAccount,
        on_delete=models.CASCADE,
        related_name='recharges',
        verbose_name='Toll Account (Destination)'
    )
    company_account = models.ForeignKey(
        'ledger.CompanyAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='toll_recharges',
        verbose_name='Company Account (Source)',
        help_text='Leave blank for unallocated recharges imported from FASTag statements. Assign later via Reconciliation.'
    )
    date = models.DateField(default=timezone.now, verbose_name='Recharge Date')
    time = models.TimeField(null=True, blank=True, verbose_name='Recharge Time')
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Recharge Amount'
    )
    payment_mode = models.CharField(
        max_length=50,
        choices=PAYMENT_MODE_CHOICES,
        default=MODE_BANK_TRANSFER,
        verbose_name='Payment Mode'
    )
    reference_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='UTR / Txn ID / Reference'
    )
    receipt_document = models.FileField(
        upload_to=toll_recharge_upload_path,
        blank=True,
        null=True,
        verbose_name='Recharge Receipt / Slip'
    )
    description = models.TextField(blank=True, verbose_name='Remarks / Description')
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_toll_recharges',
        verbose_name='Created By'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    class Meta:
        verbose_name = 'Toll Recharge'
        verbose_name_plural = 'Toll Recharges'
        ordering = ['-date', '-time', '-created_at']
        indexes = [
            models.Index(fields=['date', 'time']),
            models.Index(fields=['toll_account', 'date']),
            models.Index(fields=['company_account', 'date']),
        ]

    def __str__(self):
        source = self.company_account.name if self.company_account else 'Unallocated'
        return f"Top-up ₹{self.amount} to {self.toll_account.name} on {self.date} ({source})"

    def clean(self):
        if self.amount is not None and self.amount <= Decimal('0.00'):
            raise ValidationError({'amount': 'Recharge amount must be greater than zero.'})

    def sync_financial_record(self):
        """
        Creates or updates a FinancialRecord with category 'Toll Recharge' (Expense).
        If company_account is assigned, links to that CompanyAccount.
        If company_account is None (unallocated), links with account=None so that all
        real-world debits/credits are fully captured in the financial reports and general
        ledger even before a specific firm is allocated.
        """
        from ledger.models import FinancialRecord, TransactionCategory

        category, _ = TransactionCategory.objects.get_or_create(
            name='Toll Recharge',
            defaults={
                'type': TransactionCategory.TYPE_EXPENSE,
                'description': 'Lumpsum recharge for Toll / FASTag balance'
            }
        )

        record = FinancialRecord.objects.filter(associated_toll_recharge=self).first()
        desc = f"Toll top-up: {self.toll_account.name}"
        if self.reference_number:
            desc += f" (Ref: {self.reference_number})"
        if self.description:
            desc += f" - {self.description}"

        if record:
            updated = False
            if record.account != self.company_account:
                record.account = self.company_account
                updated = True
            if record.amount != self.amount:
                record.amount = self.amount
                updated = True
            if record.date != self.date:
                record.date = self.date
                updated = True
            if record.description != desc:
                record.description = desc
                updated = True
            if record.category != category:
                record.category = category
                updated = True
            if self.receipt_document and record.document_ref != self.receipt_document:
                record.document_ref = self.receipt_document
                updated = True
            if updated:
                record.save()
        else:
            FinancialRecord.objects.create(
                account=self.company_account,
                category=category,
                record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
                amount=self.amount,
                date=self.date,
                associated_toll_recharge=self,
                recorded_by=self.created_by,
                description=desc,
                document_ref=self.receipt_document if self.receipt_document else None
            )
