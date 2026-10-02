import os
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from django.core import signing
from ledger.models import (
    Sequence,
    Party,
    TransactionCategory,
    CompanyAccount,
    FinancialRecord,
    TripAllocation,
    BillAllocation,
    Bill,
    BillTrip,
    financial_record_upload_path,
)
from fleet.models import Vehicle
from trips.models import Trip


class SequenceModelTests(TestCase):
    def test_sequence_next_value_atomic_increment(self):
        """Verify Sequence.next_value creates and atomically increments counters."""
        val1 = Sequence.next_value('test_invoice_seq')
        val2 = Sequence.next_value('test_invoice_seq')
        val3 = Sequence.next_value('test_invoice_seq')
        self.assertEqual(val1, 1)
        self.assertEqual(val2, 2)
        self.assertEqual(val3, 3)

    def test_sequence_str(self):
        """Verify Sequence.__str__ returns '{key}: {value}'."""
        seq = Sequence.objects.create(key='test_counter', value=42)
        self.assertEqual(str(seq), 'test_counter: 42')


class PartyModelTests(TestCase):
    def setUp(self):
        self.party_debtor = Party.objects.create(
            name='Alpha Transporters',
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('5000.00'),
            current_balance_cached=Decimal('5000.00')
        )
        self.party_creditor = Party.objects.create(
            name='Bharat Petroleum Vendor',
            party_type=Party.TYPE_CREDITOR,
            opening_balance=Decimal('-8000.00'),
            current_balance_cached=Decimal('-8000.00')
        )

    def test_party_str(self):
        """Verify Party __str__ returns the party name."""
        self.assertEqual(str(self.party_debtor), 'Alpha Transporters')

    def test_party_current_balance_formatting(self):
        """Verify formatted Dr / Cr string based on current_balance_cached."""
        # Positive balance -> Dr
        self.assertEqual(self.party_debtor.current_balance, '5000.00 Dr')
        # Negative balance -> Cr
        self.assertEqual(self.party_creditor.current_balance, '8000.00 Cr')
        # Zero balance -> 0.00
        self.party_debtor.current_balance_cached = Decimal('0.00')
        self.assertEqual(self.party_debtor.current_balance, '0.00')

    def test_party_delete_flag(self):
        """Verify Party.delete sets _is_being_deleted flag."""
        party = self.party_debtor
        party.delete()
        self.assertTrue(party._is_being_deleted)


class TransactionCategoryModelTests(TestCase):
    def test_category_str_and_choices(self):
        """Verify TransactionCategory __str__ and choices."""
        cat_inc = TransactionCategory.objects.create(name='Freight Income', type=TransactionCategory.TYPE_INCOME)
        cat_exp = TransactionCategory.objects.create(name='Diesel Fuel', type=TransactionCategory.TYPE_EXPENSE)
        self.assertEqual(str(cat_inc), 'Freight Income (Income)')
        self.assertEqual(str(cat_exp), 'Diesel Fuel (Expense)')


class CompanyAccountModelTests(TestCase):
    def setUp(self):
        self.account = CompanyAccount.objects.create(
            name='Main Transport Company',
            opening_balance=Decimal('100000.00'),
            current_balance_cached=Decimal('100000.00'),
            invoice_prefix='INV-{YYYY}/',
            invoice_padding=4
        )

    def test_company_account_str(self):
        """Verify CompanyAccount __str__ returns firm name."""
        self.assertEqual(str(self.account), 'Main Transport Company')

    def test_company_account_balance_formatting(self):
        """Verify CompanyAccount current_balance formatting."""
        self.assertEqual(self.account.current_balance, '100000.00 Dr')
        self.account.current_balance_cached = Decimal('-25000.00')
        self.assertEqual(self.account.current_balance, '25000.00 Cr')
        self.account.current_balance_cached = Decimal('0.00')
        self.assertEqual(self.account.current_balance, '0.00')

    def test_company_account_delete_flag(self):
        """Verify CompanyAccount.delete sets _is_being_deleted flag."""
        acc = self.account
        acc.delete()
        self.assertTrue(acc._is_being_deleted)


class FinancialRecordModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='accountant_ramesh', password='password123')
        self.account = CompanyAccount.objects.create(name='Firm Operating AC')
        self.party_debtor = Party.objects.create(name='Debtor Logistics Ltd', party_type=Party.TYPE_DEBTOR)
        self.party_creditor = Party.objects.create(name='Creditor Fuel Station', party_type=Party.TYPE_CREDITOR)
        self.vehicle = Vehicle.objects.create(registration_plate='MH 12 FR 0001')
        self.trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party_debtor,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('20000.00')
        )
        self.category_income = TransactionCategory.objects.get_or_create(
            name='Trip Payment',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )[0]
        self.category_expense = TransactionCategory.objects.get_or_create(
            name='Office Expense',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )[0]
        self.category_tds = TransactionCategory.objects.get_or_create(
            name='TDS',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )[0]

    def test_financial_record_upload_path_priority(self):
        """Verify financial_record_upload_path organizes files by entity hierarchy."""
        # 1. Trip priority
        fr_trip = FinancialRecord(associated_trip=self.trip)
        path_trip = financial_record_upload_path(fr_trip, 'pod.pdf')
        expected_trip_id = str(self.trip.trip_number).replace(' ', '_')
        self.assertIn(os.path.join('financial_records', 'trips', expected_trip_id), path_trip)

        # 2. Party priority
        fr_party = FinancialRecord(party=self.party_debtor)
        path_party = financial_record_upload_path(fr_party, 'receipt.pdf')
        self.assertIn(os.path.join('financial_records', 'parties', 'Debtor_Logistics_Ltd'), path_party)

        # 3. Miscellaneous fallback
        fr_misc = FinancialRecord()
        path_misc = financial_record_upload_path(fr_misc, 'doc.pdf')
        self.assertIn(os.path.join('financial_records', 'miscellaneous', 'general'), path_misc)

    def test_financial_record_auto_entry_number(self):
        """Verify FinancialRecord auto-allocates an entry_number upon saving."""
        rec1 = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party_debtor,
            category=self.category_income,
            amount=Decimal('5000.00')
        )
        rec2 = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party_debtor,
            category=self.category_income,
            amount=Decimal('8000.00')
        )
        self.assertIsNotNone(rec1.entry_number)
        self.assertIsNotNone(rec2.entry_number)
        self.assertEqual(rec2.entry_number, rec1.entry_number + 1)

    def test_financial_record_auto_populate_party_from_trip(self):
        """Verify FinancialRecord auto-fills party from associated_trip if omitted."""
        rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            category=self.category_income,
            amount=Decimal('4000.00'),
            associated_trip=self.trip
        )
        self.assertEqual(rec.party, self.party_debtor)

    def test_financial_record_deductions_clear_account(self):
        """Verify non-bank deductions (TDS, Deductions, Shortage) force account=None."""
        rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party_debtor,
            category=self.category_tds,
            amount=Decimal('500.00')
        )
        self.assertIsNone(rec.account)

    def test_financial_record_str(self):
        """Verify FinancialRecord __str__ formatting across trip, bill, and generic entries."""
        rec_generic = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            category=self.category_expense,
            amount=Decimal('1500.00')
        )
        self.assertEqual(str(rec_generic), 'Office Expense - 1500.00')

        rec_trip = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            category=self.category_income,
            amount=Decimal('10000.00'),
            associated_trip=self.trip
        )
        self.assertEqual(str(rec_trip), f"Trip Payment - Trip: {self.trip.trip_number} - 10000.00")

    def test_financial_record_properties(self):
        """Verify is_income, is_expense, is_invoice, is_deduction, and signed_amount."""
        rec_inc = FinancialRecord(category=self.category_income, amount=Decimal('5000.00'))
        self.assertTrue(rec_inc.is_income)
        self.assertFalse(rec_inc.is_expense)
        self.assertEqual(rec_inc.signed_amount, Decimal('5000.00'))

        rec_exp = FinancialRecord(category=self.category_expense, amount=Decimal('2000.00'))
        self.assertFalse(rec_exp.is_income)
        self.assertTrue(rec_exp.is_expense)
        self.assertEqual(rec_exp.signed_amount, Decimal('-2000.00'))

        rec_tds = FinancialRecord(category=self.category_tds, amount=Decimal('200.00'))
        self.assertTrue(rec_tds.is_deduction)

    def test_debit_credit_amount_debtor_perspective(self):
        """Verify debit_amount and credit_amount from the perspective of a Debtor."""
        # Invoice for Debtor -> Debit
        rec_inv = FinancialRecord(
            party=self.party_debtor,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            category=self.category_income,
            amount=Decimal('10000.00')
        )
        self.assertEqual(rec_inv.debit_amount, Decimal('10000.00'))
        self.assertIsNone(rec_inv.credit_amount)

        # Payment received from Debtor -> Credit
        rec_pay = FinancialRecord(
            party=self.party_debtor,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            category=self.category_income,
            amount=Decimal('6000.00')
        )
        self.assertIsNone(rec_pay.debit_amount)
        self.assertEqual(rec_pay.credit_amount, Decimal('6000.00'))

    def test_debit_credit_amount_creditor_perspective(self):
        """Verify debit_amount and credit_amount from the perspective of a Creditor."""
        # Invoice from Creditor -> Credit (Liability increases)
        rec_inv = FinancialRecord(
            party=self.party_creditor,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            category=self.category_expense,
            amount=Decimal('15000.00')
        )
        self.assertIsNone(rec_inv.debit_amount)
        self.assertEqual(rec_inv.credit_amount, Decimal('15000.00'))

        # Payment to Creditor -> Debit (Liability decreases)
        cat_pay_out = TransactionCategory.objects.get_or_create(
            name='Payment Out',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )[0]
        rec_pay = FinancialRecord(
            party=self.party_creditor,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            category=cat_pay_out,
            amount=Decimal('8000.00')
        )
        self.assertEqual(rec_pay.debit_amount, Decimal('8000.00'))
        self.assertIsNone(rec_pay.credit_amount)

    def test_debit_credit_amount_company_account_perspective(self):
        """Verify debit_amount and credit_amount from Company Account perspective."""
        # Income to company account -> Debit (Asset increases)
        rec_inc = FinancialRecord(
            account=self.account,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            category=self.category_income,
            amount=Decimal('12000.00')
        )
        self.assertEqual(rec_inc.debit_amount, Decimal('12000.00'))

        # Expense from company account -> Credit (Asset decreases)
        rec_exp = FinancialRecord(
            account=self.account,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            category=self.category_expense,
            amount=Decimal('4000.00')
        )
        self.assertEqual(rec_exp.credit_amount, Decimal('4000.00'))


class AllocationModelTests(TestCase):
    def setUp(self):
        self.account = CompanyAccount.objects.create(name='Allocation Firm')
        self.party = Party.objects.create(name='Alloc Party', party_type=Party.TYPE_DEBTOR)
        self.veh = Vehicle.objects.create(registration_plate='MH 12 AL 1111')
        self.trip = Trip.objects.create(
            vehicle=self.veh,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.bill = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('15000.00')
        )
        self.cat = TransactionCategory.objects.get_or_create(name='Payment', defaults={'type': 'Income'})[0]
        self.record = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=self.cat,
            amount=Decimal('25000.00')
        )

    def test_trip_allocation_str_and_unique_together(self):
        """Verify TripAllocation __str__ and uniqueness constraint."""
        alloc = TripAllocation.objects.create(
            financial_record=self.record,
            trip=self.trip,
            amount=Decimal('10000.00')
        )
        self.assertIn(f"-> {self.trip.trip_number}: 10000.00", str(alloc))

        # Duplicate assignment should raise integrity error
        with self.assertRaises(Exception):
            TripAllocation.objects.create(
                financial_record=self.record,
                trip=self.trip,
                amount=Decimal('5000.00')
            )

    def test_bill_allocation_str_and_unique_together(self):
        """Verify BillAllocation __str__ and uniqueness constraint."""
        alloc = BillAllocation.objects.create(
            financial_record=self.record,
            bill=self.bill,
            amount=Decimal('15000.00')
        )
        self.assertIn(f"-> {self.bill.bill_number}: 15000.00", str(alloc))

        with self.assertRaises(Exception):
            BillAllocation.objects.create(
                financial_record=self.record,
                bill=self.bill,
                amount=Decimal('2000.00')
            )

    def test_allocation_cascade_on_record_delete(self):
        """Verify deleting FinancialRecord cascades and removes allocations."""
        TripAllocation.objects.create(financial_record=self.record, trip=self.trip, amount=Decimal('5000.00'))
        BillAllocation.objects.create(financial_record=self.record, bill=self.bill, amount=Decimal('5000.00'))
        self.assertEqual(self.record.allocations.count(), 1)
        self.assertEqual(self.record.bill_allocations.count(), 1)

        self.record.delete()
        self.assertEqual(TripAllocation.objects.filter(trip=self.trip).count(), 0)
        self.assertEqual(BillAllocation.objects.filter(bill=self.bill).count(), 0)


class BillModelTests(TestCase):
    def setUp(self):
        self.issuer = CompanyAccount.objects.create(
            name='Southern Logistics Firm',
            address='123 Freight Road, Chennai',
            phone_number='9840012345',
            gstin='33AAAAA0000A1Z5',
            authorized_signatory='A. Kumar',
            bank_name='HDFC Bank',
            bank_branch='Anna Nagar',
            account_number='50200012345678',
            ifsc_code='HDFC0001234',
            invoice_prefix='SL/{YYYY}/',
            invoice_padding=4
        )
        self.party = Party.objects.create(name='Global Freight Corp', party_type=Party.TYPE_DEBTOR)
        self.vehicle = Vehicle.objects.create(registration_plate='TN 01 BL 9009')

    def test_bill_snapshot_company_details_on_creation(self):
        """Verify Bill snapshots issuer firm and bank details at creation."""
        bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('50000.00')
        )
        self.assertEqual(bill.invoice_company_name, 'Southern Logistics Firm')
        self.assertEqual(bill.invoice_company_address, '123 Freight Road, Chennai')
        self.assertEqual(bill.invoice_company_gstin, '33AAAAA0000A1Z5')
        self.assertEqual(bill.invoice_company_authorized_signatory, 'A. Kumar')
        self.assertEqual(bill.invoice_bank_name, 'HDFC Bank')
        self.assertEqual(bill.invoice_bank_account, '50200012345678')
        self.assertEqual(bill.invoice_bank_ifsc, 'HDFC0001234')

        # Snapshot principle: changing issuer account later does not alter bill's snapshot
        self.issuer.name = 'Updated Firm Name'
        self.issuer.bank_name = 'ICICI Bank'
        self.issuer.save()
        bill.refresh_from_db()
        self.assertEqual(bill.invoice_company_name, 'Southern Logistics Firm')
        self.assertEqual(bill.invoice_bank_name, 'HDFC Bank')

    def test_bill_number_auto_generation(self):
        """Verify auto-generation of bill_number with format '{prefix}{seq:04d}{suffix}'."""
        today = timezone.now().date()
        bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=today,
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('10000.00')
        )
        expected_prefix = f"SL/{today.year}/"
        self.assertTrue(bill.bill_number.startswith(expected_prefix))
        self.assertTrue(bill.bill_number.endswith('0001'))

    def test_bill_subtotal_standard_weight_and_rate_with_discount(self):
        """Verify subtotal calculation in standard bill using weight * rate minus discount."""
        bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            standard_weight=Decimal('25.500'),
            standard_rate=Decimal('2000.00'),
            discount=Decimal('1000.00')
        )
        # subtotal = (25.5 * 2000) - 1000 = 51000 - 1000 = 50000
        self.assertEqual(bill.subtotal, Decimal('50000.00'))

    def test_bill_subtotal_trip_based_with_trip_discount(self):
        """Verify subtotal calculation in trip bill aggregates trip revenues minus discounts."""
        trip1 = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('20000.00')
        )
        trip2 = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('30000.00')
        )
        bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            discount=Decimal('2000.00')
        )
        BillTrip.objects.create(bill=bill, trip=trip1, discount=Decimal('1000.00'))
        BillTrip.objects.create(bill=bill, trip=trip2, discount=Decimal('0.00'))

        # subtotal = (20000 - 1000) + (30000 - 0) - 2000 = 19000 + 30000 - 2000 = 47000
        self.assertEqual(bill.subtotal, Decimal('47000.00'))

    def test_bill_gst_splits(self):
        """Verify calculation of gst_amount, cgst_amount, sgst_amount, and igst_amount."""
        bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('10000.00'),
            gst_rate=18,
            gst_type=Bill.GST_TYPE_GST
        )
        self.assertEqual(bill.gst_amount, Decimal('1800.00'))
        self.assertEqual(bill.cgst_amount, Decimal('900.00'))
        self.assertEqual(bill.sgst_amount, Decimal('900.00'))
        self.assertEqual(bill.igst_amount, Decimal('1800.00'))

    def test_bill_roundoff_logic(self):
        """Verify roundoff and rounded_total with use_roundoff True vs False."""
        # 100.50 + 18% GST (18.09) = 118.59
        bill_with_roundoff = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('100.50'),
            gst_rate=18,
            use_roundoff=True
        )
        self.assertEqual(bill_with_roundoff.rounded_total, Decimal('119.00'))
        # With bypass cache active, verify accurate mathematical roundoff difference
        bill_with_roundoff._bypass_cache = True
        self.assertEqual(bill_with_roundoff.total_amount, Decimal('118.59'))
        self.assertEqual(bill_with_roundoff.roundoff, Decimal('0.41'))
        del bill_with_roundoff._bypass_cache

        bill_no_roundoff = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('100.50'),
            gst_rate=18,
            use_roundoff=False
        )
        self.assertEqual(bill_no_roundoff.rounded_total, Decimal('118.59'))
        self.assertEqual(bill_no_roundoff.roundoff, Decimal('0.00'))

    def test_bill_share_token_signing(self):
        """Verify share_token generates cryptographically verifiable signed token."""
        bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('5000.00')
        )
        token = bill.share_token
        self.assertIsNotNone(token)
        unpacked_id = signing.loads(token, salt='bill-share')
        self.assertEqual(unpacked_id, bill.pk)

    def test_bill_delete_restores_trip_accruals(self):
        """Verify deleting a bill removes the consolidated record and restores trip accruals."""
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('25000.00')
        )
        bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP
        )
        BillTrip.objects.create(bill=bill, trip=trip)
        bill.save() # Triggers sync_to_ledger

        # Deleting bill should restore trip accrual
        bill_pk = bill.pk
        bill.delete()
        self.assertFalse(Bill.objects.filter(pk=bill_pk).exists())
        
        # Trip should have its individual accrual record restored
        trip_accrual = FinancialRecord.objects.filter(
            associated_trip=trip,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).first()
        self.assertIsNotNone(trip_accrual)


class BillTripModelTests(TestCase):
    def setUp(self):
        self.account = CompanyAccount.objects.create(name='BT Account')
        self.party = Party.objects.create(name='BT Party', party_type=Party.TYPE_DEBTOR)
        self.veh = Vehicle.objects.create(registration_plate='MH 12 BT 2020')
        self.trip = Trip.objects.create(
            vehicle=self.veh,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('12000.00')
        )
        self.bill = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP
        )

    def test_bill_trip_str_and_unique_together(self):
        """Verify BillTrip __str__ and uniqueness constraint."""
        bt = BillTrip.objects.create(bill=self.bill, trip=self.trip, lr_no='LR-999')
        self.assertIn(f"(LR: LR-999)", str(bt))

        with self.assertRaises(Exception):
            BillTrip.objects.create(bill=self.bill, trip=self.trip)
