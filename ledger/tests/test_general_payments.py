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
from ledger.services import BalanceService, BillingService
from django.contrib.auth.models import User, Permission
from trips.models import Trip, Route
from fleet.models import Vehicle

class GeneralPaymentAndBillAllocationTests(TestCase):
    """
    Tests ensuring payment entries behave as general entries that reduce party balance
    rather than automatically allocating across bills (FIFO removed):
    1. Advance payments made before bills are created do NOT auto-settle subsequent bills.
    2. General payment entries made after bills exist do NOT auto-settle existing bills.
    3. Explicit bill selection directly links and settles the selected bill.
    """
    def setUp(self):
        self.user = User.objects.create_user('test_user', 'test@test.com', 'pass123')
        self.client.login(username='test_user', password='pass123')

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
        self.customer = Party.objects.create(
            name="Customer Logistics Ltd",
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('0.00')
        )
        self.cat_payment_out, _ = TransactionCategory.objects.get_or_create(
            name='Payment Out',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.cat_payment_in, _ = TransactionCategory.objects.get_or_create(
            name='Payment In',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_standard, _ = TransactionCategory.objects.get_or_create(
            name='Standard',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )

    def test_advance_payment_does_not_settle_subsequent_bills(self):
        """
        Verify that a general payment (e.g. advance) made before bills exist
        does NOT automatically settle subsequently created bills.
        """
        # Step 1: Make a general payment of 50,000
        adv_payment = FinancialRecord.objects.create(
            date=datetime.date(2026, 8, 1),
            account=self.company,
            party=self.vendor,
            category=self.cat_payment_out,
            amount=Decimal('50000.00'),
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            recorded_by=self.user
        )

        self.assertEqual(adv_payment.bill_allocations.count(), 0)

        # Step 2: Create a bill for 30,000 on 2026-08-10
        bill1 = Bill.objects.create(
            bill_number="BILL-001",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.vendor,
            date=datetime.date(2026, 8, 10),
            amount_override=Decimal('30000.00'),
            gst_rate=0,
            use_roundoff=False
        )
        bill1.sync_to_ledger()

        bill1.refresh_from_db()
        adv_payment.refresh_from_db()

        # Bill must NOT be marked paid; no bill allocations should exist
        self.assertEqual(bill1.amount_received, Decimal('0.00'))
        self.assertEqual(bill1.outstanding_balance, Decimal('30000.00'))
        self.assertEqual(bill1.payment_status, Bill.PAYMENT_STATUS_UNPAID)
        self.assertEqual(adv_payment.bill_allocations.count(), 0)
        self.assertNotIn("Paid across invoices", adv_payment.description or "")

    def test_general_payment_does_not_settle_existing_bills(self):
        """
        Verify that a general payment in/out made after bills exist
        does not automatically allocate or mark bills as paid.
        """
        bill1 = Bill.objects.create(
            bill_number="BILL-DEBT-001",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.customer,
            date=datetime.date(2026, 8, 5),
            amount_override=Decimal('40000.00'),
            gst_rate=0,
            use_roundoff=False
        )
        bill1.sync_to_ledger()

        # Create general Payment In of 40,000
        payment = FinancialRecord.objects.create(
            date=datetime.date(2026, 8, 15),
            account=self.company,
            party=self.customer,
            category=self.cat_payment_in,
            amount=Decimal('40000.00'),
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            recorded_by=self.user
        )

        bill1.refresh_from_db()
        self.assertEqual(bill1.amount_received, Decimal('0.00'))
        self.assertEqual(bill1.outstanding_balance, Decimal('40000.00'))
        self.assertEqual(bill1.payment_status, Bill.PAYMENT_STATUS_UNPAID)
        self.assertEqual(payment.bill_allocations.count(), 0)

    def test_explicit_bill_selection_settles_bill(self):
        """
        Verify that explicitly linking a payment to a bill via associated_bill
        correctly settles that specific bill.
        """
        bill = Bill.objects.create(
            bill_number="BILL-DIRECT-001",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.customer,
            date=datetime.date(2026, 8, 5),
            amount_override=Decimal('25000.00'),
            gst_rate=0,
            use_roundoff=False
        )
        bill.sync_to_ledger()

        payment = FinancialRecord.objects.create(
            date=datetime.date(2026, 8, 15),
            account=self.company,
            party=self.customer,
            category=self.cat_payment_in,
            amount=Decimal('25000.00'),
            associated_bill=bill,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            recorded_by=self.user
        )

        bill.refresh_from_db()
        self.assertEqual(bill.amount_received, Decimal('25000.00'))
        self.assertEqual(bill.outstanding_balance, Decimal('0.00'))
        self.assertEqual(bill.payment_status, Bill.PAYMENT_STATUS_PAID)

    def test_cleanup_fifo_allocations_command(self):
        """
        Verify that the cleanup_fifo_allocations management command deletes
        erroneous allocations and recalculates bill outstanding balance back to unpaid.
        """
        from django.core.management import call_command
        import io

        bill = Bill.objects.create(
            bill_number="BILL-CLEANUP-001",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.company,
            party=self.customer,
            date=datetime.date(2026, 8, 5),
            amount_override=Decimal('15000.00'),
            gst_rate=0,
            use_roundoff=False
        )
        bill.sync_to_ledger()

        payment = FinancialRecord.objects.create(
            date=datetime.date(2026, 8, 1),
            account=self.company,
            party=self.customer,
            category=self.cat_payment_in,
            amount=Decimal('15000.00'),
            description="Payment advance Paid across invoices: BILL-CLEANUP-001",
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            recorded_by=self.user
        )

        # Simulate erroneous BillAllocation created by FIFO
        BillAllocation.objects.create(
            financial_record=payment,
            bill=bill,
            amount=Decimal('15000.00')
        )
        bill.refresh_from_db()
        self.assertEqual(bill.payment_status, Bill.PAYMENT_STATUS_PAID)

        # Run command with --apply and --clean-descriptions
        out = io.StringIO()
        call_command('cleanup_fifo_allocations', party='Customer Logistics', apply=True, clean_descriptions=True, stdout=out)

        # Verify allocation removed and bill restored
        bill.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(bill.amount_received, Decimal('0.00'))
        self.assertEqual(bill.outstanding_balance, Decimal('15000.00'))
        self.assertEqual(bill.payment_status, Bill.PAYMENT_STATUS_UNPAID)
        self.assertEqual(payment.bill_allocations.count(), 0)
        self.assertEqual(payment.description, "Payment advance")

