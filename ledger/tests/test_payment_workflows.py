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

class TripPaymentWorkflowTests(TestCase):
    def setUp(self):
        super().setUp()
        from django.test import Client
        self.client = Client()
        self.user = User.objects.create_superuser(username='superadmin2', email='super2@test.com', password='password123')
        self.client.login(username='superadmin2', password='password123')

        self.account = CompanyAccount.objects.create(
            name="Primary Account",
            opening_balance=Decimal('10000.00')
        )
        self.party = Party.objects.create(name="Delta Logistics", party_type=Party.TYPE_DEBTOR)
        self.cat_trip_payment, _ = TransactionCategory.objects.get_or_create(
            name='Trip Payment',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )

        from trips.models import Route, Trip
        from fleet.models import Vehicle
        self.vehicle1 = Vehicle.objects.create(registration_plate="MH12AB1001")
        self.vehicle2 = Vehicle.objects.create(registration_plate="MH12CD2002")
        self.route = Route.objects.create(
            pickup_location="Pune",
            delivery_location="Goa",
            default_rate=Decimal('5000.00'),
            route_type=Route.ROUTE_TYPE_NONE
        )

        self.trip1 = Trip.objects.create(
            trip_number="TRIP-TEST-201",
            date=timezone.now().date(),
            vehicle=self.vehicle1,
            party=self.party,
            route=self.route,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('5000.00')
        )
        self.trip1.refresh_from_db()

        self.trip2 = Trip.objects.create(
            trip_number="TRIP-TEST-202",
            date=timezone.now().date(),
            vehicle=self.vehicle2,
            party=self.party,
            route=self.route,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('8000.00')
        )
        self.trip2.refresh_from_db()

    def test_ajax_get_trip_balance(self):
        """Test ajax endpoint get-trip-balance returns trip metadata and balance"""
        from django.urls import reverse
        resp = self.client.get(f"{reverse('get-trip-balance')}?trip_id={self.trip1.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['id'], self.trip1.id)
        self.assertEqual(data['vehicle'], "MH12AB1001")
        self.assertEqual(data['total'], 5000.00)
        self.assertEqual(data['balance'], 5000.00)

    def test_ajax_get_party_unpaid_trips(self):
        """Test ajax endpoint get-party-unpaid-trips returns enriched trip list"""
        from django.urls import reverse
        resp = self.client.get(f"{reverse('get-party-unpaid-trips')}?party_id={self.party.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['trips']), 2)
        trip_ids = [t['id'] for t in data['trips']]
        self.assertIn(self.trip1.id, trip_ids)
        self.assertIn(self.trip2.id, trip_ids)

    def test_single_trip_payment_with_tds_and_deductions(self):
        """Test recording bank payment, TDS, and deductions for a single trip"""
        from django.urls import reverse
        from ledger.models import FinancialRecord
        from ledger.services import BalanceService

        initial_acc_bal = BalanceService.refresh_account_balance(self.account)

        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.party.id,
            'category': self.cat_trip_payment.id,
            'associated_trip': self.trip1.id,
            'amount': '4000.00',
            'tds_amount': '500.00',
            'deduction_amount': '500.00',
            'deduction_notes': 'Shortage 2 bags',
            'description': 'Payment with TDS and shortage'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        # 1. Verify 3 financial records exist
        bank_rec = FinancialRecord.objects.filter(
            associated_trip=self.trip1,
            category=self.cat_trip_payment,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
        ).first()
        self.assertIsNotNone(bank_rec)
        self.assertEqual(bank_rec.amount, Decimal('4000.00'))
        self.assertEqual(bank_rec.account, self.account)

        tds_rec = FinancialRecord.objects.filter(associated_trip=self.trip1, category__name='TDS').first()
        self.assertIsNotNone(tds_rec)
        self.assertEqual(tds_rec.amount, Decimal('500.00'))
        self.assertIsNone(tds_rec.account)

        ded_rec = FinancialRecord.objects.filter(associated_trip=self.trip1, category__name='Deductions').first()
        self.assertIsNotNone(ded_rec)
        self.assertEqual(ded_rec.amount, Decimal('500.00'))
        self.assertIn('Shortage 2 bags', ded_rec.description)
        self.assertIsNone(ded_rec.account)

        # 2. Verify Trip1 is marked Paid
        self.trip1.refresh_from_db()
        self.assertEqual(self.trip1.amount_received, Decimal('5000.00'))
        self.assertEqual(self.trip1.outstanding_balance, Decimal('0.00'))
        self.assertEqual(self.trip1.payment_status, 'Paid')

        # 3. Verify CompanyAccount only credited bank payment
        new_acc_bal = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(new_acc_bal, initial_acc_bal + Decimal('4000.00'))

    def test_single_trip_save_and_next_redirect(self):
        """Test clicking 'Save & Next' (_save_same_party) keeps party, account, date, and category in URL"""
        from django.urls import reverse

        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.party.id,
            'category': self.cat_trip_payment.id,
            'associated_trip': self.trip1.id,
            'amount': '5000.00',
            '_save_same_party': '1'
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertEqual(resp.status_code, 302)
        redirect_url = resp.url
        self.assertIn(f'party={self.party.id}', redirect_url)
        self.assertIn(f'account={self.account.id}', redirect_url)
        self.assertIn(f'category={self.cat_trip_payment.id}', redirect_url)

    def test_multi_trip_payment_custom_allocations(self):
        """Test multi-trip payment distribution creates payment, TDS, and Deductions allocations"""
        import json
        from django.urls import reverse
        from ledger.models import FinancialRecord, TripAllocation
        from ledger.services import BalanceService

        initial_acc_bal = BalanceService.refresh_account_balance(self.account)

        distribution_data = [
            {
                'trip_id': self.trip1.id,
                'payment': 4200.00,
                'tds': 400.00,
                'deduction': 400.00,
                'deduction_notes': 'Shortage 1 bag'
            },
            {
                'trip_id': self.trip2.id,
                'payment': 7000.00,
                'tds': 500.00,
                'deduction': 500.00,
                'deduction_notes': 'Bank charge'
            }
        ]

        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.party.id,
            'category': self.cat_trip_payment.id,
            'amount': '11200.00',
            'payment_distribution': json.dumps(distribution_data)
        }

        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        # 1. Bank payment allocations
        parent_payment = FinancialRecord.objects.filter(account=self.account, category=self.cat_trip_payment).first()
        self.assertIsNotNone(parent_payment)
        self.assertEqual(parent_payment.amount, Decimal('11200.00'))

        p_allocs = TripAllocation.objects.filter(financial_record=parent_payment)
        self.assertEqual(p_allocs.count(), 2)
        self.assertEqual(p_allocs.get(trip=self.trip1).amount, Decimal('4200.00'))
        self.assertEqual(p_allocs.get(trip=self.trip2).amount, Decimal('7000.00'))

        # 2. TDS allocations
        parent_tds = FinancialRecord.objects.filter(account=None, category__name='TDS', party=self.party).first()
        self.assertIsNotNone(parent_tds)
        self.assertEqual(parent_tds.amount, Decimal('900.00'))

        tds_allocs = TripAllocation.objects.filter(financial_record=parent_tds)
        self.assertEqual(tds_allocs.count(), 2)
        self.assertEqual(tds_allocs.get(trip=self.trip1).amount, Decimal('400.00'))
        self.assertEqual(tds_allocs.get(trip=self.trip2).amount, Decimal('500.00'))

        # 3. Deductions allocations
        parent_ded = FinancialRecord.objects.filter(account=None, category__name='Deductions', party=self.party).first()
        self.assertIsNotNone(parent_ded)
        self.assertEqual(parent_ded.amount, Decimal('900.00'))

        ded_allocs = TripAllocation.objects.filter(financial_record=parent_ded)
        self.assertEqual(ded_allocs.count(), 2)
        self.assertEqual(ded_allocs.get(trip=self.trip1).amount, Decimal('400.00'))
        self.assertEqual(ded_allocs.get(trip=self.trip2).amount, Decimal('500.00'))

        # 4. Verify Trip 1 and Trip 2 statuses
        self.trip1.refresh_from_db()
        self.assertEqual(self.trip1.amount_received, Decimal('5000.00'))
        self.assertEqual(self.trip1.payment_status, 'Paid')

        self.trip2.refresh_from_db()
        self.assertEqual(self.trip2.amount_received, Decimal('8000.00'))
        self.assertEqual(self.trip2.payment_status, 'Paid')

        # 5. Verify Company Account balance increased only by bank payment
        new_acc_bal = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(new_acc_bal, initial_acc_bal + Decimal('11200.00'))


