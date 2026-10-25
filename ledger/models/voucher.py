"""
FinancialVoucher model for general application-wide voucher and transaction verification.
"""
import os
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from transport_mgmt.utils import get_local_time_str, get_local_date
from .party import Party
from .company import CompanyAccount
from .sequence import Sequence


def voucher_upload_path(instance, filename):
    """Upload path for voucher supporting documents"""
    safe_ref = str(instance.voucher_number or 'voucher').replace('/', '-').replace('\\', '-')
    ext = os.path.splitext(filename)[1]
    timestamp = get_local_time_str('%Y%m%d_%H%M%S')
    return os.path.join('vouchers', safe_ref, f"doc_{timestamp}{ext}")


class VoucherEntityList(list):
    """
    Lightweight list subclass providing QuerySet-like convenience helpers
    (.count(), .exists(), .first(), .last()) without triggering database queries.
    """
    def count(self, *args):
        if not args:
            return len(self)
        return super().count(*args)

    def exists(self):
        return len(self) > 0

    def first(self):
        return self[0] if self else None

    def last(self):
        return self[-1] if self else None


class FinancialVoucher(models.Model):
    """
    Application-wide voucher entity grouping one or more financial records or transaction
    actions (e.g. single-trip payment + TDS + shortage, multi-trip settlements,
    vendor payments, operating expenses, driver advances, compliance renewals).
    Issues unique reference numbers (REF-YYMM-#####) and tracks verification status.
    """
    STATUS_PENDING = 'pending'
    STATUS_VERIFIED = 'verified'
    STATUS_FLAGGED = 'flagged'
    STATUS_DELETED = 'deleted'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending Verification'),
        (STATUS_VERIFIED, 'Verified'),
        (STATUS_FLAGGED, 'Flagged / Discrepancy'),
        (STATUS_DELETED, 'Deleted / Cancelled'),
    ]

    VOUCHER_TYPE_TRIP_PAYMENT = 'trip_payment'
    VOUCHER_TYPE_BILL_PAYMENT = 'bill_payment'
    VOUCHER_TYPE_PAYMENT_OUT = 'payment_out'
    VOUCHER_TYPE_EXPENSE = 'expense'
    VOUCHER_TYPE_DOCUMENT_RENEWAL = 'document_renewal'
    VOUCHER_TYPE_TOLL_RECHARGE = 'toll_recharge'
    VOUCHER_TYPE_DRIVER_SETTLEMENT = 'driver_settlement'
    VOUCHER_TYPE_BILL_INVOICE = 'bill_invoice'
    VOUCHER_TYPE_TRIP_ACCRUAL = 'trip_accrual'
    VOUCHER_TYPE_TYRE_PURCHASE = 'tyre_purchase'
    VOUCHER_TYPE_DIRECT = 'direct_entry'
    VOUCHER_TYPE_INTER_PARTY_SETTLEMENT = 'inter_party_settlement'
    VOUCHER_TYPE_OTHER = 'other'

    VOUCHER_TYPE_CHOICES = [
        (VOUCHER_TYPE_TRIP_PAYMENT, 'Trip Settlement'),
        (VOUCHER_TYPE_BILL_PAYMENT, 'Bill Settlement'),
        (VOUCHER_TYPE_PAYMENT_OUT, 'Payment Out (Vendor / Lorry Hire)'),
        (VOUCHER_TYPE_EXPENSE, 'Operating Expense'),
        (VOUCHER_TYPE_DOCUMENT_RENEWAL, 'Document Renewal'),
        (VOUCHER_TYPE_TOLL_RECHARGE, 'Toll Recharge'),
        (VOUCHER_TYPE_DRIVER_SETTLEMENT, 'Driver Settlement'),
        (VOUCHER_TYPE_BILL_INVOICE, 'Customer / Creditor Bill'),
        (VOUCHER_TYPE_TRIP_ACCRUAL, 'Trip Revenue Accrual'),
        (VOUCHER_TYPE_TYRE_PURCHASE, 'Tyre Purchase / Service'),
        (VOUCHER_TYPE_DIRECT, 'Direct Payment / Inflow'),
        (VOUCHER_TYPE_INTER_PARTY_SETTLEMENT, 'Inter-Party Contra Settlement'),
        (VOUCHER_TYPE_OTHER, 'Other'),
    ]

    voucher_number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        verbose_name='Reference #'
    )
    date = models.DateField(verbose_name='Transaction Date')
    voucher_type = models.CharField(
        max_length=30,
        choices=VOUCHER_TYPE_CHOICES,
        default=VOUCHER_TYPE_DIRECT,
        verbose_name='Voucher Type'
    )
    party = models.ForeignKey(
        Party,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vouchers',
        verbose_name='Party / Entity'
    )
    payer_party = models.ForeignKey(
        Party,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payer_vouchers',
        verbose_name='Payer / Remitter'
    )
    account = models.ForeignKey(
        CompanyAccount,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='vouchers',
        verbose_name='Company Account'
    )
    driver = models.ForeignKey(
        'drivers.Driver',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vouchers',
        verbose_name='Associated Driver'
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_vouchers',
        verbose_name='Created By'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Created At')

    verification_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name='Verification Status'
    )
    verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_vouchers',
        verbose_name='Verified By'
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Verified At'
    )
    verification_notes = models.TextField(
        blank=True,
        verbose_name='Reviewer Remarks'
    )

    # Soft delete and audit fields
    deleted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deleted_vouchers',
        verbose_name='Deleted By'
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Deleted At'
    )
    deletion_reason = models.TextField(
        blank=True,
        default='',
        verbose_name='Deletion Reason'
    )
    audit_log = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Audit Log'
    )

    # Cached financial totals
    total_payment_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Bank / Cash Payment'
    )
    total_tds_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='TDS Deducted'
    )
    total_deduction_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Deductions / Shortage'
    )
    total_settled_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Total Settled Amount'
    )

    description = models.TextField(blank=True, verbose_name='Narration / Description')
    document_ref = models.FileField(
        upload_to=voucher_upload_path,
        null=True,
        blank=True,
        verbose_name='Supporting Document'
    )

    class Meta:
        verbose_name = 'Financial Voucher'
        verbose_name_plural = 'Financial Vouchers'
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['verification_status', 'date']),
            models.Index(fields=['created_by', 'date']),
            models.Index(fields=['party', 'date']),
            models.Index(fields=['voucher_type', 'date']),
        ]

    def __str__(self):
        return f"{self.voucher_number} - {self.get_voucher_type_display()} ({self.date})"

    def delete(self, *args, **kwargs):
        """Vouchers are permanent audit containers and cannot be hard deleted."""
        raise PermissionDenied("Financial vouchers are immutable audit containers and cannot be deleted.")

    def save(self, *args, **kwargs):
        # Enforce reference number immutability on update
        if self.pk:
            orig = FinancialVoucher.objects.filter(pk=self.pk).values('voucher_number').first()
            if orig and orig['voucher_number'] != self.voucher_number:
                self.voucher_number = orig['voucher_number']
        super().save(*args, **kwargs)

    @property
    def all_records(self):
        """Returns all records belonging to this voucher including soft-deleted ones"""
        if hasattr(self, 'prefetched_all_records'):
            return VoucherEntityList(self.prefetched_all_records)
        if hasattr(self, '_cached_all_records'):
            return self._cached_all_records
        from ledger.models import FinancialRecord
        self._cached_all_records = VoucherEntityList(FinancialRecord.all_objects.filter(voucher=self).select_related(
            'category', 'account', 'party', 'associated_trip__vehicle', 'associated_trip__route',
            'associated_trip__party', 'associated_bill__party', 'associated_bill__category',
            'associated_bill__original_bill', 'associated_bill__customer_bill', 'deleted_by'
        ).prefetch_related(
            'allocations__trip__vehicle', 'allocations__trip__route', 'allocations__trip__party',
            'bill_allocations__bill__party', 'bill_allocations__bill__category',
            'bill_allocations__bill__original_bill', 'bill_allocations__bill__customer_bill'
        ))
        return self._cached_all_records

    def soft_delete(self, user=None, reason=''):
        from ledger.services import VoucherService
        return VoucherService.soft_delete_voucher(self, user=user, reason=reason)

    def restore(self, user=None, reason=''):
        from ledger.services import VoucherService
        return VoucherService.restore_voucher(self, user=user, reason=reason)

    @classmethod
    def generate_voucher_number(cls, date=None):
        """
        Atomically generate reference number formatted as REF-YYMM-##### (Option A).
        E.g. REF-2610-00001
        """
        if date is None:
            date = get_local_date()
        yymm = date.strftime('%y%m')
        seq_key = f"voucher_ref_{yymm}"
        seq_val = Sequence.next_value(seq_key)
        return f"REF-{yymm}-{seq_val:05d}"

    def update_totals(self, save=True):
        """
        Recalculate cached totals from associated active financial records.
        Distinguishes cash/bank payments from revenue/invoice accruals.
        """
        from ledger.models import FinancialRecord
        records = list(self.financial_records.select_related('category').all())
        payment_amt = Decimal('0.00')
        tds_amt = Decimal('0.00')
        ded_amt = Decimal('0.00')
        invoice_amt = Decimal('0.00')

        for r in records:
            cat_name = (r.category.name if r.category else '').lower()
            amt = r.amount or Decimal('0.00')
            if r.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
                invoice_amt += amt
            elif 'tds' in cat_name:
                tds_amt += amt
            elif 'deduction' in cat_name or 'shortage' in cat_name:
                ded_amt += amt
            else:
                payment_amt += amt

        self.total_payment_amount = payment_amt
        self.total_tds_amount = tds_amt
        self.total_deduction_amount = ded_amt
        
        if self.voucher_type in [self.VOUCHER_TYPE_TRIP_ACCRUAL, self.VOUCHER_TYPE_BILL_INVOICE]:
            self.total_settled_amount = invoice_amt
        elif self.voucher_type == self.VOUCHER_TYPE_INTER_PARTY_SETTLEMENT:
            self.total_payment_amount = Decimal('0.00')
            self.total_settled_amount = (payment_amt / 2) if payment_amt > 0 else payment_amt
        else:
            self.total_settled_amount = payment_amt + tds_amt + ded_amt

        if save:
            self.save(update_fields=[
                'total_payment_amount',
                'total_tds_amount',
                'total_deduction_amount',
                'total_settled_amount'
            ])

    @cached_property
    def affected_trips(self):
        """
        Returns all trips affected by this voucher, whether directly or via allocations.
        Leverages prefetched objects in memory to avoid N+1 queries.
        """
        from trips.models import Trip
        trips_dict = {}
        missing_ids = set()

        for r in self.all_records:
            if r.associated_trip_id:
                if getattr(r, 'associated_trip', None):
                    trips_dict[r.associated_trip_id] = r.associated_trip
                else:
                    missing_ids.add(r.associated_trip_id)
            for alloc in r.allocations.all():
                if getattr(alloc, 'trip', None):
                    trips_dict[alloc.trip_id] = alloc.trip
                else:
                    missing_ids.add(alloc.trip_id)

        if missing_ids:
            for trip in Trip.objects.filter(id__in=missing_ids).select_related('vehicle', 'party', 'route'):
                trips_dict[trip.id] = trip

        return VoucherEntityList(trips_dict.values())

    @cached_property
    def affected_bills(self):
        """
        Returns all bills affected by this voucher, directly or via bill allocations.
        Leverages prefetched objects in memory to avoid N+1 queries.
        """
        from ledger.models import Bill
        bills_dict = {}
        missing_ids = set()

        for r in self.all_records:
            if r.associated_bill_id:
                if getattr(r, 'associated_bill', None):
                    bills_dict[r.associated_bill_id] = r.associated_bill
                else:
                    missing_ids.add(r.associated_bill_id)
            for alloc in r.bill_allocations.all():
                if getattr(alloc, 'bill', None):
                    bills_dict[alloc.bill_id] = alloc.bill
                else:
                    missing_ids.add(alloc.bill_id)

        if missing_ids:
            for b in Bill.objects.filter(id__in=missing_ids).select_related('party', 'category', 'original_bill', 'customer_bill'):
                bills_dict[b.id] = b

        return VoucherEntityList(bills_dict.values())

    @cached_property
    def is_credit_note(self):
        """Checks if this voucher contains any Credit Note bills or records."""
        return any(
            (b.category and b.category.name == 'Credit Note')
            for b in self.affected_bills
        ) or any(
            (r.category and r.category.name == 'Credit Note')
            for r in self.all_records
        )

    @cached_property
    def is_debit_note(self):
        """Checks if this voucher contains any Debit Note bills or records."""
        return any(
            (b.category and b.category.name == 'Debit Note')
            for b in self.affected_bills
        ) or any(
            (r.category and r.category.name == 'Debit Note')
            for r in self.all_records
        )

    @cached_property
    def settlement_breakdown(self):
        """
        Returns structured per-trip settlement details showing starting revenue,
        allocated payment, TDS, deductions, and resulting balance.
        Only applicable for settlement / payment vouchers. Accruals and invoices
        represent earned revenue and do not settle trips.
        """
        from ledger.models import FinancialRecord
        if self.voucher_type in [self.VOUCHER_TYPE_TRIP_ACCRUAL, self.VOUCHER_TYPE_BILL_INVOICE]:
            return []

        trips = list(self.affected_trips)
        if not trips:
            return []

        breakdown = []
        for trip in trips:
            # Calculate contributions from this voucher for this specific trip
            p_allocated = Decimal('0.00')
            t_allocated = Decimal('0.00')
            d_allocated = Decimal('0.00')

            for r in self.all_records:
                if r.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
                    # Invoice accruals are earned revenue, not cash/bank settlement
                    continue

                cat_name = (r.category.name if r.category else '').lower()
                amt = Decimal('0.00')

                # Check direct association
                if r.associated_trip_id == trip.id:
                    amt = r.amount or Decimal('0.00')
                else:
                    # Check allocation
                    for a in r.allocations.all():
                        if a.trip_id == trip.id:
                            amt += a.amount or Decimal('0.00')

                if 'tds' in cat_name:
                    t_allocated += amt
                elif 'deduction' in cat_name or 'shortage' in cat_name:
                    d_allocated += amt
                else:
                    p_allocated += amt

            total_voucher_contribution = p_allocated + t_allocated + d_allocated
            if total_voucher_contribution <= Decimal('0.00'):
                # Skip trips that did not have payment/settlement applied in this voucher
                continue

            is_zero = (trip.outstanding_balance_cached <= Decimal('0.00'))

            breakdown.append({
                'trip': trip,
                'trip_number': trip.trip_number,
                'lr_no': trip.lr_no,
                'vehicle_plate': trip.vehicle.registration_plate if trip.vehicle else '—',
                'route': str(trip.route) if trip.route else '—',
                'total_revenue': trip.total_revenue_cached,
                'payment_amount': p_allocated,
                'tds_amount': t_allocated,
                'deduction_amount': d_allocated,
                'total_settled_in_voucher': total_voucher_contribution,
                'remaining_balance': trip.outstanding_balance_cached,
                'payment_status': trip.payment_status_cached,
                'is_zero_balance': is_zero,
            })

        return breakdown
