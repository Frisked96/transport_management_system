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

    def test_ajax_get_party_unpaid_trips_with_record_id_and_include_trip_id(self):
        """Test that get_party_unpaid_trips includes a trip even if marked Paid when editing its record"""
        # 1. Pay trip1 in full with a Trip Payment record
        rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            record_type='Transaction',
            account=self.account,
            party=self.party,
            category=self.cat_trip_payment,
            associated_trip=self.trip1,
            amount=Decimal('5000.00'),
            recorded_by=self.user
        )
        self.trip1.refresh_from_db()
        self.assertEqual(self.trip1.payment_status, 'Paid')
        self.assertEqual(self.trip1.outstanding_balance, Decimal('0.00'))

        # 2. Querying without record_id should exclude trip1 because it's Paid
        resp1 = self.client.get(f"{reverse('get-party-unpaid-trips')}?party_id={self.party.id}")
        self.assertEqual(resp1.status_code, 200)
        trips_unpaid = resp1.json()['trips']
        self.assertNotIn(self.trip1.id, [t['id'] for t in trips_unpaid])

        # 3. Querying WITH record_id should include trip1 with its pre-record pending balance (5000.00)
        resp2 = self.client.get(f"{reverse('get-party-unpaid-trips')}?party_id={self.party.id}&record_id={rec.id}")
        self.assertEqual(resp2.status_code, 200)
        trips_editing = resp2.json()['trips']
        trip1_data = next((t for t in trips_editing if t['id'] == self.trip1.id), None)
        self.assertIsNotNone(trip1_data)
        self.assertEqual(trip1_data['balance'], 5000.00)
        self.assertIn('Pending: ₹5,000.00', trip1_data['label'])

        # 4. Querying WITH include_trip_id should also include trip1
        resp3 = self.client.get(f"{reverse('get-party-unpaid-trips')}?party_id={self.party.id}&include_trip_id={self.trip1.id}")
        self.assertEqual(resp3.status_code, 200)
        self.assertIn(self.trip1.id, [t['id'] for t in resp3.json()['trips']])

    def test_direct_deductions_creation_and_editing_for_trip(self):
        """Test creating and editing a transaction directly under Deductions category attached to a trip"""
        cat_deductions, _ = TransactionCategory.objects.get_or_create(
            name='Deductions',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )

        initial_trip_bal = self.trip1.outstanding_balance # 5000.00

        # 1. Create a direct deduction record for trip1
        post_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'party': self.party.id,
            'category': cat_deductions.id,
            'associated_trip': self.trip1.id,
            'amount': '800.00',
            'description': 'Shortage deduction of 800'
        }
        resp = self.client.post(reverse('financialrecord-create'), post_data)
        self.assertIn(resp.status_code, [302, 200])

        ded_rec = FinancialRecord.objects.filter(
            party=self.party,
            associated_trip=self.trip1,
            category=cat_deductions
        ).first()
        self.assertIsNotNone(ded_rec)
        self.assertEqual(ded_rec.amount, Decimal('800.00'))
        self.assertIsNone(ded_rec.account) # Non-bank adjustment

        # Verify trip caches updated
        self.trip1.refresh_from_db()
        self.assertEqual(self.trip1.amount_received, Decimal('800.00'))
        self.assertEqual(self.trip1.outstanding_balance, Decimal('4200.00'))

        # 2. Get edit view for this deduction record
        edit_url = reverse('financialrecord-update', kwargs={'pk': ded_rec.pk})
        get_resp = self.client.get(edit_url)
        self.assertEqual(get_resp.status_code, 200)
        form = get_resp.context['form']
        # Verify trip1 is in the form queryset and initial value
        self.assertIn(self.trip1, form.fields['associated_trip'].queryset)
        self.assertEqual(form.initial.get('associated_trip'), self.trip1.id)

        # 3. Edit the deduction record (change amount to 1000)
        edit_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'party': self.party.id,
            'category': cat_deductions.id,
            'associated_trip': self.trip1.id,
            'amount': '1000.00',
            'description': 'Updated shortage deduction of 1000'
        }
        post_edit_resp = self.client.post(edit_url, edit_data)
        self.assertIn(post_edit_resp.status_code, [302, 200])

        ded_rec.refresh_from_db()
        self.assertEqual(ded_rec.amount, Decimal('1000.00'))
        self.assertEqual(ded_rec.associated_trip, self.trip1)

        # Verify trip caches updated to reflect new deduction amount
        self.trip1.refresh_from_db()
        self.assertEqual(self.trip1.amount_received, Decimal('1000.00'))
        self.assertEqual(self.trip1.outstanding_balance, Decimal('4000.00'))

    def test_edit_trip_payment_retains_trip_and_updates_caches(self):
        """Test editing a Trip Payment record where trip is marked Paid does not lose the trip"""
        # 1. Pay trip1 in full
        pay_rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            record_type='Transaction',
            account=self.account,
            party=self.party,
            category=self.cat_trip_payment,
            associated_trip=self.trip1,
            amount=Decimal('5000.00'),
            recorded_by=self.user
        )
        self.trip1.refresh_from_db()
        self.assertEqual(self.trip1.payment_status, 'Paid')

        # 2. Open edit form
        edit_url = reverse('financialrecord-update', kwargs={'pk': pay_rec.pk})
        get_resp = self.client.get(edit_url)
        self.assertEqual(get_resp.status_code, 200)
        form = get_resp.context['form']
        # Verify trip1 is in the queryset despite being marked Paid
        self.assertIn(self.trip1, form.fields['associated_trip'].queryset)

        # 3. Submit edit with reduced payment amount (4000)
        edit_data = {
            'date': timezone.now().date().isoformat(),
            'record_type': 'Transaction',
            'account': self.account.id,
            'party': self.party.id,
            'category': self.cat_trip_payment.id,
            'associated_trip': self.trip1.id,
            'amount': '4000.00',
            'description': 'Reduced payment'
        }
        post_resp = self.client.post(edit_url, edit_data)
        self.assertIn(post_resp.status_code, [302, 200])

        pay_rec.refresh_from_db()
        self.assertEqual(pay_rec.amount, Decimal('4000.00'))
        self.assertEqual(pay_rec.associated_trip, self.trip1)

        # Trip should now have 4000 received and 1000 outstanding (Partial)
        self.trip1.refresh_from_db()
        self.assertEqual(self.trip1.amount_received, Decimal('4000.00'))
        self.assertEqual(self.trip1.outstanding_balance, Decimal('1000.00'))
        self.assertEqual(self.trip1.payment_status, Trip.PAYMENT_STATUS_PARTIAL)



