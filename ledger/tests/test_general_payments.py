from django.test import TestCase, Client
from django.utils import timezone
from django.db import models
from django.urls import reverse
import datetime
from decimal import Decimal
from ledger.models import (
    Bill, Party, CompanyAccount, TransactionCategory, FinancialRecord,
    TripAllocation, BillAllocation
)
from ledger.services import BalanceService, BillingService, TripFinancialService
from django.contrib.auth.models import User, Permission
from trips.models import Trip, Route
from fleet.models import Vehicle

class FIFOAdvanceAndBillSettlementTests(TestCase):
    """
    Tests for automatic FIFO settlement of bills and advance payments:
    1. Advance payments made before bills are created automatically settle new bills upon creation.
    2. General payment entries made after bills (or backdated to an older date) settle oldest bills first (FIFO).
    3. Explicit bill selection overrides FIFO.
    4. Deletion of payments cascades and restores bill balances.
    """
    def setUp(self):
        self.user = User.objects.create_user('fifo_user', 'fifo@test.com', 'pass123')
        self.client.login(username='fifo_user', password='pass123')

        self.company = CompanyAccount.objects.create(
            name="Alpha Transport Ltd",
            opening_balance=Decimal('1000000.00'),
            invoice_prefix="ALPHA-{YYYY}-",
            invoice_sequence_start=1
        )
        self.vendor = Party.objects.create(
            name="Vendor Transport Co",
            party_type=Party.TYPE_CREDITOR,
            opening_balance=Decimal('0.00')
        )
        self.cat_payment_out, _ = TransactionCategory.objects.get_or_create(
            name='Payment Out',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.cat_standard, _ = TransactionCategory.objects.get_or_create(
            name='Standard',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )

    def test_advance_payment_settles_new_bills_upon_creation(self):
        """
        Verify that an advance payment made before any bills exist automatically
        settles subsequently created bills in FIFO order until the advance is exhausted.
        """
        # Step 1: Make an advance payment of 50,000 on 2026-08-01 (no bills exist yet)
        adv_payment = FinancialRecord.objects.create(
            date=datetime.date(2026, 8, 1),
            account=self.company,
            party=self.vendor,
            category=self.cat_payment_out,
            amount=Decimal('50000.00'),
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            recorded_by=self.user
        )

        # No bills exist, so advance payment has 0 allocations initially
        self.assertEqual(adv_payment.bill_allocations.count(), 0)

        # Step 2: Create Bill 1 on 2026-08-10 with total 30,000
        bill_1 = Bill.objects.create(
            bill_number="BILL-001",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.vendor,
            date=datetime.date(2026, 8, 10),
            amount_override=Decimal('30000.00'),
            gst_rate=0,
            use_roundoff=False
        )

        # Bill 1 should be automatically 100% settled upon creation by the advance payment!
        bill_1.refresh_from_db()
        self.assertEqual(bill_1.amount_received, Decimal('30000.00'))
        self.assertEqual(bill_1.outstanding_balance, Decimal('0.00'))
        self.assertEqual(bill_1.payment_status, Bill.PAYMENT_STATUS_PAID)
        self.assertEqual(bill_1.payment_allocations.count(), 1)
        self.assertEqual(bill_1.payment_allocations.first().amount, Decimal('30000.00'))

        # Advance payment now has 20,000 unallocated remaining
        total_allocated = adv_payment.bill_allocations.aggregate(total=models.Sum('amount'))['total']
        self.assertEqual(total_allocated, Decimal('30000.00'))

        # Step 3: Create Bill 2 on 2026-08-15 with total 40,000
        bill_2 = Bill.objects.create(
            bill_number="BILL-002",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.vendor,
            date=datetime.date(2026, 8, 15),
            amount_override=Decimal('40000.00'),
            gst_rate=0,
            use_roundoff=False
        )

        # Bill 2 should receive the remaining 20,000 from the advance payment!
        bill_2.refresh_from_db()
        self.assertEqual(bill_2.amount_received, Decimal('20000.00'))
        self.assertEqual(bill_2.outstanding_balance, Decimal('20000.00'))
        self.assertEqual(bill_2.payment_status, Bill.PAYMENT_STATUS_PARTIAL)
        self.assertEqual(bill_2.payment_allocations.count(), 1)
        self.assertEqual(bill_2.payment_allocations.first().amount, Decimal('20000.00'))

        # Advance payment is now fully allocated (30,000 + 20,000 = 50,000)
        total_allocated = adv_payment.bill_allocations.aggregate(total=models.Sum('amount'))['total']
        self.assertEqual(total_allocated, Decimal('50000.00'))

    def test_general_payment_settles_existing_bills_fifo_even_if_older_date(self):
        """
        Verify that a general payment entry made after bills exist (even with an older/backdated date)
        settles the oldest unpaid bills first in strict FIFO order.
        """
        # Create 3 bills for the vendor
        bill_1 = Bill.objects.create(
            bill_number="BILL-A",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.vendor,
            date=datetime.date(2026, 8, 5),
            amount_override=Decimal('40000.00'),
            gst_rate=0,
            use_roundoff=False
        )
        bill_2 = Bill.objects.create(
            bill_number="BILL-B",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.vendor,
            date=datetime.date(2026, 8, 12),
            amount_override=Decimal('30000.00'),
            gst_rate=0,
            use_roundoff=False
        )
        bill_3 = Bill.objects.create(
            bill_number="BILL-C",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.vendor,
            date=datetime.date(2026, 8, 20),
            amount_override=Decimal('25000.00'),
            gst_rate=0,
            use_roundoff=False
        )

        # Now record a general payment of 55,000 with backdated date 2026-08-01 (older than bills)
        payment = FinancialRecord.objects.create(
            date=datetime.date(2026, 8, 1),
            account=self.company,
            party=self.vendor,
            category=self.cat_payment_out,
            amount=Decimal('55000.00'),
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            recorded_by=self.user
        )

        # Trigger FIFO allocation for general payment
        BillingService.allocate_payment_fifo(payment_record=payment)

        # Bill 1 (oldest: 2026-08-05) should get 40,000 -> Paid
        bill_1.refresh_from_db()
        self.assertEqual(bill_1.amount_received, Decimal('40000.00'))
        self.assertEqual(bill_1.outstanding_balance, Decimal('0.00'))
        self.assertEqual(bill_1.payment_status, Bill.PAYMENT_STATUS_PAID)

        # Bill 2 (next oldest: 2026-08-12) should get remaining 15,000 -> Partially Paid
        bill_2.refresh_from_db()
        self.assertEqual(bill_2.amount_received, Decimal('15000.00'))
        self.assertEqual(bill_2.outstanding_balance, Decimal('15000.00'))
        self.assertEqual(bill_2.payment_status, Bill.PAYMENT_STATUS_PARTIAL)

        # Bill 3 (newest: 2026-08-20) should remain untouched -> Unpaid
        bill_3.refresh_from_db()
        self.assertEqual(bill_3.amount_received, Decimal('0.00'))
        self.assertEqual(bill_3.outstanding_balance, Decimal('25000.00'))
        self.assertEqual(bill_3.payment_status, Bill.PAYMENT_STATUS_UNPAID)

    def test_explicit_bill_selection_overrides_fifo(self):
        """
        Verify that if the user explicitly associates a payment with a specific bill,
        FIFO does not run and only that specific bill receives payment.
        """
        bill_old = Bill.objects.create(
            bill_number="BILL-OLD",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.vendor,
            date=datetime.date(2026, 8, 1),
            amount_override=Decimal('50000.00'),
            gst_rate=0,
            use_roundoff=False
        )
        bill_new = Bill.objects.create(
            bill_number="BILL-NEW",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.vendor,
            date=datetime.date(2026, 8, 20),
            amount_override=Decimal('30000.00'),
            gst_rate=0,
            use_roundoff=False
        )

        # Explicitly pay the NEW bill
        payment = FinancialRecord.objects.create(
            date=datetime.date(2026, 8, 25),
            account=self.company,
            party=self.vendor,
            associated_bill=bill_new,
            category=self.cat_payment_out,
            amount=Decimal('30000.00'),
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            recorded_by=self.user
        )

        # Allocate FIFO should do nothing because associated_bill is set
        BillingService.allocate_payment_fifo(payment_record=payment)

        bill_old.refresh_from_db()
        bill_new.refresh_from_db()

        # Older bill is still unpaid
        self.assertEqual(bill_old.outstanding_balance, Decimal('50000.00'))
        self.assertEqual(bill_old.payment_status, Bill.PAYMENT_STATUS_UNPAID)

        # Newer bill is paid
        self.assertEqual(bill_new.amount_received, Decimal('30000.00'))
        self.assertEqual(bill_new.outstanding_balance, Decimal('0.00'))
        self.assertEqual(bill_new.payment_status, Bill.PAYMENT_STATUS_PAID)

    def test_deleting_payment_restores_bill_balances(self):
        """
        Verify that deleting a payment record cascades and restores the outstanding
        balances and statuses of all bills it was allocated to.
        """
        bill = Bill.objects.create(
            bill_number="BILL-RESTORE",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.vendor,
            date=datetime.date(2026, 8, 5),
            amount_override=Decimal('20000.00'),
            gst_rate=0,
            use_roundoff=False
        )

        payment = FinancialRecord.objects.create(
            date=datetime.date(2026, 8, 5),
            account=self.company,
            party=self.vendor,
            category=self.cat_payment_out,
            amount=Decimal('20000.00'),
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            recorded_by=self.user
        )
        BillingService.allocate_payment_fifo(payment_record=payment)

        bill.refresh_from_db()
        self.assertEqual(bill.outstanding_balance, Decimal('0.00'))
        self.assertEqual(bill.payment_status, Bill.PAYMENT_STATUS_PAID)

        # Delete the payment record
        payment.delete()

        bill.refresh_from_db()
        self.assertEqual(bill.amount_received, Decimal('0.00'))
        self.assertEqual(bill.outstanding_balance, Decimal('20000.00'))
        self.assertEqual(bill.payment_status, Bill.PAYMENT_STATUS_UNPAID)













