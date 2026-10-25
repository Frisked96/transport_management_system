"""
Dedicated tests for optimized financial entry workflows and intent-driven forms.
"""
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User

from ledger.models import (
    FinancialRecord, FinancialVoucher, Party, CompanyAccount,
    TransactionCategory, Bill, TripAllocation, BillAllocation
)
from ledger.services import BalanceService
from trips.models import Trip, Route
from fleet.models import Vehicle


class FinancialEntryWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='flow_admin',
            email='flow@test.com',
            password='password123'
        )
        self.client.login(username='flow_admin', password='password123')

        self.account = CompanyAccount.objects.create(
            name="Main HDFC Account",
            opening_balance=Decimal('50000.00')
        )
        self.customer = Party.objects.create(
            name="Acme Logistics",
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('0.00')
        )
        self.vendor = Party.objects.create(
            name="Bharat Freight Movers",
            party_type=Party.TYPE_CREDITOR,
            opening_balance=Decimal('0.00')
        )

        self.cat_trip_payment, _ = TransactionCategory.objects.get_or_create(
            name='Trip Payment',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_payment_in, _ = TransactionCategory.objects.get_or_create(
            name='Payment In',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_payment_out, _ = TransactionCategory.objects.get_or_create(
            name='Payment Out',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.cat_expense, _ = TransactionCategory.objects.get_or_create(
            name='Maintenance',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )

        self.vehicle = Vehicle.objects.create(registration_plate="MH12AB9999")
        self.route = Route.objects.create(
            pickup_location="Mumbai",
            delivery_location="Pune",
            default_rate=Decimal('10000.00'),
            route_type=Route.ROUTE_TYPE_NONE
        )
        self.trip1 = Trip.objects.create(
            trip_number="TRIP-FLW-101",
            date=timezone.now().date(),
            vehicle=self.vehicle,
            party=self.customer,
            route=self.route,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.trip1.refresh_from_db()

    def test_form_context_enrichment(self):
        """Verify FinancialRecordCreateView provides enriched JSON context and initial mode"""
        resp = self.client.get(reverse('financialrecord-create'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('accounts_json', resp.context)
        self.assertIn('parties_json', resp.context)
        self.assertIn('categories_json', resp.context)
        self.assertEqual(resp.context['initial_mode'], 'customer')

        # Deep-link with debtor party
        resp_cust = self.client.get(f"{reverse('financialrecord-create')}?party={self.customer.id}")
        self.assertEqual(resp_cust.context['initial_mode'], 'customer')

        # Deep-link with creditor party
        resp_vend = self.client.get(f"{reverse('financialrecord-create')}?party={self.vendor.id}")
        self.assertEqual(resp_vend.context['initial_mode'], 'vendor')

        # Deep-link with expense category
        resp_exp = self.client.get(f"{reverse('financialrecord-create')}?category={self.cat_expense.id}")
        self.assertEqual(resp_exp.context['initial_mode'], 'expense')

    def test_customer_receipt_single_trip_settlement(self):
        """Test recording a customer receipt against a single trip with TDS and shortage"""
        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.customer.id,
            'category': self.cat_trip_payment.id,
            'associated_trip': self.trip1.id,
            'amount': '8500.00',
            'tds_amount': '1000.00',
            'deduction_amount': '500.00',
            'deduction_notes': 'Shortage 1 carton',
            'description': 'Customer full settlement with TDS & shortage'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        # Verify Bank payment record
        bank_rec = FinancialRecord.objects.filter(
            associated_trip=self.trip1,
            category=self.cat_trip_payment,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
        ).first()
        self.assertIsNotNone(bank_rec)
        self.assertEqual(bank_rec.amount, Decimal('8500.00'))
        self.assertEqual(bank_rec.account, self.account)

        # Verify TDS record
        tds_rec = FinancialRecord.objects.filter(
            associated_trip=self.trip1,
            category__name='TDS'
        ).first()
        self.assertIsNotNone(tds_rec)
        self.assertEqual(tds_rec.amount, Decimal('1000.00'))
        self.assertIsNone(tds_rec.account)

        # Verify Deduction record
        ded_rec = FinancialRecord.objects.filter(
            associated_trip=self.trip1,
            category__name='Deductions'
        ).first()
        self.assertIsNotNone(ded_rec)
        self.assertEqual(ded_rec.amount, Decimal('500.00'))
        self.assertIn('Shortage 1 carton', ded_rec.description)
        self.assertIsNone(ded_rec.account)

        # Verify Trip is marked paid
        self.trip1.refresh_from_db()
        self.assertEqual(self.trip1.amount_received, Decimal('10000.00'))
        self.assertEqual(self.trip1.outstanding_balance, Decimal('0.00'))
        self.assertEqual(self.trip1.payment_status, 'Paid')

        # Verify bank account only credited bank payment
        acc_bal = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(acc_bal, Decimal('58500.00'))

    def test_customer_receipt_multi_trip_distribution(self):
        """Test multi-trip distribution creates explicit allocations and updates statuses"""
        trip2 = Trip.objects.create(
            trip_number="TRIP-FLW-102",
            date=timezone.now().date(),
            vehicle=self.vehicle,
            party=self.customer,
            route=self.route,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('15000.00')
        )
        trip2.refresh_from_db()

        dist = [
            {'trip_id': self.trip1.id, 'payment': 9000.00, 'tds': 1000.00, 'deduction': 0, 'deduction_notes': ''},
            {'trip_id': trip2.id, 'payment': 13500.00, 'tds': 1500.00, 'deduction': 0, 'deduction_notes': ''}
        ]

        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.customer.id,
            'category': self.cat_trip_payment.id,
            'amount': '22500.00',
            'payment_distribution': json.dumps(dist)
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        self.trip1.refresh_from_db()
        trip2.refresh_from_db()
        self.assertEqual(self.trip1.payment_status, 'Paid')
        self.assertEqual(trip2.payment_status, 'Paid')

    def test_direct_operating_expense_entry(self):
        """Test creating a direct company expense without any party or trip"""
        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'General',
            'account': self.account.id,
            'category': self.cat_expense.id,
            'amount': '3500.00',
            'description': 'Workshop machinery service'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        rec = FinancialRecord.objects.filter(
            category=self.cat_expense,
            amount=Decimal('3500.00')
        ).first()
        self.assertIsNotNone(rec)
        self.assertIsNone(rec.party)
        self.assertIsNone(rec.associated_trip)
        self.assertEqual(rec.account, self.account)

        # Account balance must decrease
        acc_bal = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(acc_bal, Decimal('46500.00'))

    def test_customer_receipt_on_account_advance(self):
        """Test on-account advance payment reduces customer balance without trip/bill link"""
        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.customer.id,
            'category': self.cat_payment_in.id,
            'amount': '20000.00',
            'description': 'Lump sum advance payment from client'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        rec = FinancialRecord.objects.filter(
            party=self.customer,
            category=self.cat_payment_in,
            amount=Decimal('20000.00')
        ).first()
        self.assertIsNotNone(rec)
        self.assertIsNone(rec.associated_trip)
        self.assertIsNone(rec.associated_bill)

        # Bank increases by 20,000
        acc_bal = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(acc_bal, Decimal('70000.00'))

    def test_save_and_next_parameter_retention(self):
        """Test clicking 'Save & Next' redirects with party, account, date, and category in URL"""
        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.customer.id,
            'category': self.cat_trip_payment.id,
            'associated_trip': self.trip1.id,
            'amount': '10000.00',
            '_save_same_party': '1'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertEqual(resp.status_code, 302)
        redirect_url = resp.url
        self.assertIn(f'party={self.customer.id}', redirect_url)
        self.assertIn(f'account={self.account.id}', redirect_url)
        self.assertIn(f'category={self.cat_trip_payment.id}', redirect_url)

    def test_vendor_payment_against_bill(self):
        """Test vendor payment reduces vendor payable and marks bill paid"""
        vendor_bill = Bill.objects.create(
            bill_number="CR-2026-001",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.account,
            party=self.vendor,
            date=timezone.now().date(),
            amount_override=Decimal('12000.00'),
            category=self.cat_payment_out,
            gst_rate=0
        )
        vendor_bill.sync_to_ledger()
        vendor_bill.refresh_from_db()

        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.vendor.id,
            'category': self.cat_payment_out.id,
            'associated_bill': vendor_bill.id,
            'amount': '12000.00',
            'description': 'Full payment for vendor bill'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        vendor_bill.refresh_from_db()
        self.assertEqual(vendor_bill.outstanding_balance, Decimal('0.00'))
        self.assertEqual(vendor_bill.payment_status, Bill.PAYMENT_STATUS_PAID)

    def test_driver_settlement_advance(self):
        """Test recording an advance to a driver"""
        from django.contrib.auth.models import Group
        driver_group, _ = Group.objects.get_or_create(name='driver')
        driver_user = User.objects.create_user(username='driver_ramesh', password='password123')
        driver_user.groups.add(driver_group)

        from drivers.models import Driver
        driver_profile = Driver.objects.create(user=driver_user, employee_id="DRV-001")

        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'driver': driver_profile.id,
            'category': self.cat_expense.id,
            'amount': '3000.00',
            'description': 'Enroute trip advance for fuel'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        rec = FinancialRecord.objects.filter(
            driver=driver_profile,
            amount=Decimal('3000.00')
        ).first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.account, self.account)

    def test_inter_party_contra_setoff(self):
        """Test inter-party contra set-off between vendor and customer with zero bank movement"""
        initial_acc_bal = BalanceService.refresh_account_balance(self.account)

        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'party': self.vendor.id,
            'is_inter_party_adjustment': 'on',
            'adjustment_target_party': self.customer.id,
            'category': self.cat_payment_out.id,
            'amount': '5000.00',
            'description': 'Mutual contra set-off'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        # Verify contra records created
        contra_recs = FinancialRecord.objects.filter(is_inter_party_adjustment=True)
        self.assertEqual(contra_recs.count(), 2)

        # Bank balance should remain completely untouched
        new_acc_bal = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(new_acc_bal, initial_acc_bal)

    def test_customer_receipt_trip_with_shortage_and_charges_blocks(self):
        """Test trip settlement with distinct shortage and commission/charges deductions"""
        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.customer.id,
            'category': self.cat_trip_payment.id,
            'associated_trip': self.trip1.id,
            'amount': '8000.00',
            'tds_amount': '1000.00',
            'deduction_amount': '1000.00',
            'deduction_notes': 'Shortage: ₹600.00, Charges: ₹400.00',
            'description': 'Settlement with shortage and commission charges'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        ded_rec = FinancialRecord.objects.filter(
            associated_trip=self.trip1,
            category__name='Deductions'
        ).first()
        self.assertIsNotNone(ded_rec)
        self.assertEqual(ded_rec.amount, Decimal('1000.00'))
        self.assertIn('Shortage: ₹600.00', ded_rec.description)
        self.assertIn('Charges: ₹400.00', ded_rec.description)

        self.trip1.refresh_from_db()
        self.assertEqual(self.trip1.payment_status, 'Paid')

    def test_customer_receipt_invoice_with_manual_rounded_tds(self):
        """Test invoice payment with custom rounded-off manual TDS entry"""
        cust_bill = Bill.objects.create(
            bill_number="INV-2026-901",
            bill_type=Bill.TYPE_STANDARD,
            issuer=self.account,
            party=self.customer,
            date=timezone.now().date(),
            amount_override=Decimal('50000.00'),
            category=self.cat_payment_in,
            gst_rate=0
        )
        cust_bill.sync_to_ledger()
        cust_bill.refresh_from_db()

        # Party arbitrarily rounded off TDS to 985.00 instead of 1000.00, paid 49,015.00
        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.customer.id,
            'category': self.cat_payment_in.id,
            'associated_bill': cust_bill.id,
            'amount': '49015.00',
            'tds_amount': '985.00',
            'description': 'Invoice settlement with custom rounded TDS'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        cust_bill.refresh_from_db()
        self.assertEqual(cust_bill.outstanding_balance, Decimal('0.00'))
        self.assertEqual(cust_bill.payment_status, Bill.PAYMENT_STATUS_PAID)


