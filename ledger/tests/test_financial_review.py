"""
Automated test suite for FinancialVoucher generation, submission bundling,
and the Financial Entry Review & Verification system.
"""
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User, Permission
from django.utils import timezone
import json

from ledger.models import (
    FinancialVoucher,
    FinancialRecord,
    TransactionCategory,
    CompanyAccount,
    Party,
    TripAllocation,
)
from trips.models import Trip, Route
from fleet.models import Vehicle


class FinancialReviewTestCase(TestCase):
    def setUp(self):
        # Create users
        self.operator_user = User.objects.create_user(
            username='akash',
            password='password123'
        )
        # Grant operator add/change financial record permission and view_party
        perm_add = Permission.objects.get(codename='add_financialrecord')
        perm_change = Permission.objects.get(codename='change_financialrecord')
        perm_view = Permission.objects.get(codename='can_view_financial_records')
        perm_party = Permission.objects.get(codename='view_party')
        perm_trip = Permission.objects.get(codename='view_trip')
        self.operator_user.user_permissions.add(perm_add, perm_change, perm_view, perm_party, perm_trip)

        # Create manager/reviewer user
        self.manager_user = User.objects.create_user(
            username='manager',
            password='password123'
        )
        perm_verify = Permission.objects.get(codename='can_verify_financial_records')
        self.manager_user.user_permissions.add(perm_view, perm_verify)

        # Create unprivileged user
        self.regular_user = User.objects.create_user(
            username='viewer',
            password='password123'
        )
        self.regular_user.user_permissions.add(perm_view)

        # Setup ledger master data
        self.company_account = CompanyAccount.objects.create(
            name='Test Operating Bank',
            bank_name='HDFC Bank',
            account_number='1234567890',
            opening_balance=Decimal('1000000.00')
        )
        self.debtor_party = Party.objects.create(
            name='Test Debtor Ltd',
            party_type=Party.TYPE_DEBTOR,
            current_balance_cached=Decimal('0.00')
        )

        self.category_payment, _ = TransactionCategory.objects.get_or_create(
            name='Trip Payment',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.category_tds, _ = TransactionCategory.objects.get_or_create(
            name='TDS',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.category_deductions, _ = TransactionCategory.objects.get_or_create(
            name='Deductions',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )

        # Vehicle and Route
        self.vehicle = Vehicle.objects.create(
            registration_plate='CG04TEST01',
            ownership=Vehicle.OWNERSHIP_OWNED,
            company_account=self.company_account
        )
        self.route = Route.objects.create(
            pickup_location='Raipur',
            delivery_location='Bilaspur',
            default_rate=Decimal('1000.00'),
            route_type='none'
        )

        # Unpaid Trip
        self.trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor_party,
            route=self.route,
            weight=Decimal('50.00'),
            rate_per_ton=Decimal('1000.00'),
            date=timezone.now().date(),
            revenue_type=Trip.REVENUE_PER_TON
        )
        self.trip.refresh_from_db()

    def test_voucher_generation_format(self):
        """Verify voucher number generation follows REF-YYMM-##### (Option A)"""
        now = timezone.now().date()
        v_num = FinancialVoucher.generate_voucher_number(now)
        expected_prefix = f"REF-{now.strftime('%y%m')}-"
        self.assertTrue(v_num.startswith(expected_prefix))
        self.assertEqual(len(v_num), len(expected_prefix) + 5)

    def test_single_trip_settlement_creates_voucher_and_groups_records(self):
        """
        Verify that submitting a trip payment with TDS and shortage deductions
        creates a single FinancialVoucher with status Pending, links all child records,
        and clears the trip balance to zero.
        """
        self.client.login(username='akash', password='password123')

        post_data = {
            'date': timezone.now().date().strftime('%Y-%m-%d'),
            'record_type': 'Transaction',
            'category': self.category_payment.pk,
            'party': self.debtor_party.pk,
            'account': self.company_account.pk,
            'associated_trip': self.trip.pk,
            'amount': '48000.00',
            'tds_amount': '1000.00',
            'deduction_amount': '1000.00',
            'deduction_notes': 'Transit shortage',
            'description': 'Payment from client for trip',
        }

        response = self.client.post(reverse('financialrecord-create'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # Verify voucher was created
        voucher = FinancialVoucher.objects.filter(party=self.debtor_party).first()
        self.assertIsNotNone(voucher)
        self.assertEqual(voucher.created_by, self.operator_user)
        self.assertEqual(voucher.verification_status, FinancialVoucher.STATUS_PENDING)
        self.assertEqual(voucher.voucher_type, FinancialVoucher.VOUCHER_TYPE_TRIP_PAYMENT)
        self.assertEqual(voucher.total_payment_amount, Decimal('48000.00'))
        self.assertEqual(voucher.total_tds_amount, Decimal('1000.00'))
        self.assertEqual(voucher.total_deduction_amount, Decimal('1000.00'))
        self.assertEqual(voucher.total_settled_amount, Decimal('50000.00'))

        # Verify all records are linked to the voucher
        records = list(voucher.financial_records.all())
        self.assertEqual(len(records), 3) # Payment + TDS + Shortage
        for r in records:
            self.assertEqual(r.voucher, voucher)
            self.assertEqual(r.verification_status, FinancialVoucher.STATUS_PENDING)
            self.assertEqual(r.recorded_by, self.operator_user)

        # Verify trip balance became zero
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.outstanding_balance_cached, Decimal('0.00'))
        self.assertEqual(self.trip.payment_status_cached, Trip.PAYMENT_STATUS_PAID)

        # Verify settlement breakdown property
        breakdown = voucher.settlement_breakdown
        self.assertEqual(len(breakdown), 1)
        self.assertEqual(breakdown[0]['trip_number'], self.trip.trip_number)
        self.assertEqual(breakdown[0]['payment_amount'], Decimal('48000.00'))
        self.assertEqual(breakdown[0]['tds_amount'], Decimal('1000.00'))
        self.assertEqual(breakdown[0]['deduction_amount'], Decimal('1000.00'))
        self.assertEqual(breakdown[0]['remaining_balance'], Decimal('0.00'))
        self.assertTrue(breakdown[0]['is_zero_balance'])

    def test_multi_trip_distribution_voucher(self):
        """Verify multi-trip distribution creates a unified voucher linking multiple trip allocations"""
        self.client.login(username='akash', password='password123')

        # Create second trip
        trip2 = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor_party,
            route=self.route,
            weight=Decimal('30.00'),
            rate_per_ton=Decimal('1000.00'),
            date=timezone.now().date(),
            revenue_type=Trip.REVENUE_PER_TON
        )

        distribution_data = [
            {'trip_id': self.trip.id, 'payment': 49000, 'tds': 1000, 'deduction': 0},
            {'trip_id': trip2.id, 'payment': 29400, 'tds': 600, 'deduction': 0},
        ]

        post_data = {
            'date': timezone.now().date().strftime('%Y-%m-%d'),
            'record_type': 'Transaction',
            'category': self.category_payment.pk,
            'party': self.debtor_party.pk,
            'account': self.company_account.pk,
            'payment_distribution': json.dumps(distribution_data),
            'amount': '78400.00',
            'tds_amount': '1600.00',
        }

        response = self.client.post(reverse('financialrecord-create'), data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        voucher = FinancialVoucher.objects.filter(party=self.debtor_party).order_by('-id').first()
        self.assertIsNotNone(voucher)
        self.assertEqual(voucher.total_payment_amount, Decimal('78400.00'))
        self.assertEqual(voucher.total_tds_amount, Decimal('1600.00'))
        self.assertEqual(voucher.total_settled_amount, Decimal('80000.00'))

        # Verify affected trips count
        self.assertEqual(voucher.affected_trips.count(), 2)

        # Verify settlement breakdown
        breakdown = voucher.settlement_breakdown
        self.assertEqual(len(breakdown), 2)
        for b in breakdown:
            self.assertEqual(b['remaining_balance'], Decimal('0.00'))
            self.assertTrue(b['is_zero_balance'])

    def test_verify_action_by_manager(self):
        """Verify that a manager can verify a voucher and all child records via POST"""
        # Create a voucher with status pending
        voucher_ref = FinancialVoucher.generate_voucher_number()
        voucher = FinancialVoucher.objects.create(
            voucher_number=voucher_ref,
            date=timezone.now().date(),
            voucher_type=FinancialVoucher.VOUCHER_TYPE_DIRECT,
            party=self.debtor_party,
            created_by=self.operator_user,
            verification_status=FinancialVoucher.STATUS_PENDING
        )
        rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            category=self.category_payment,
            amount=Decimal('1000.00'),
            voucher=voucher,
            recorded_by=self.operator_user,
            verification_status=FinancialVoucher.STATUS_PENDING
        )

        self.client.login(username='manager', password='password123')
        verify_url = reverse('verify-financial-entry', kwargs={'pk': voucher.pk})

        # Test AJAX verify
        response = self.client.post(verify_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['status'], 'verified')

        # Check DB state
        voucher.refresh_from_db()
        rec.refresh_from_db()
        self.assertEqual(voucher.verification_status, FinancialVoucher.STATUS_VERIFIED)
        self.assertEqual(voucher.verified_by, self.manager_user)
        self.assertIsNotNone(voucher.verified_at)
        self.assertEqual(rec.verification_status, FinancialVoucher.STATUS_VERIFIED)
        self.assertEqual(rec.verified_by, self.manager_user)

    def test_flag_action_by_manager(self):
        """Verify that a manager can flag a voucher with remarks"""
        voucher_ref = FinancialVoucher.generate_voucher_number()
        voucher = FinancialVoucher.objects.create(
            voucher_number=voucher_ref,
            date=timezone.now().date(),
            party=self.debtor_party,
            created_by=self.operator_user,
            verification_status=FinancialVoucher.STATUS_PENDING
        )

        self.client.login(username='manager', password='password123')
        flag_url = reverse('flag-financial-entry', kwargs={'pk': voucher.pk})

        response = self.client.post(
            flag_url,
            data={'notes': 'TDS deduction mismatch of Rs 50'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'flagged')

        voucher.refresh_from_db()
        self.assertEqual(voucher.verification_status, FinancialVoucher.STATUS_FLAGGED)
        self.assertEqual(voucher.verification_notes, 'TDS deduction mismatch of Rs 50')

    def test_bulk_verify_action(self):
        """Verify bulk verification approves multiple vouchers simultaneously"""
        v1 = FinancialVoucher.objects.create(
            voucher_number=FinancialVoucher.generate_voucher_number(),
            date=timezone.now().date(),
            created_by=self.operator_user,
            verification_status=FinancialVoucher.STATUS_PENDING
        )
        v2 = FinancialVoucher.objects.create(
            voucher_number=FinancialVoucher.generate_voucher_number(),
            date=timezone.now().date(),
            created_by=self.operator_user,
            verification_status=FinancialVoucher.STATUS_PENDING
        )

        self.client.login(username='manager', password='password123')
        bulk_url = reverse('bulk-verify-financial-entries')

        response = self.client.post(bulk_url, data={'voucher_ids': [v1.pk, v2.pk]}, follow=True)
        self.assertEqual(response.status_code, 200)

        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual(v1.verification_status, FinancialVoucher.STATUS_VERIFIED)
        self.assertEqual(v2.verification_status, FinancialVoucher.STATUS_VERIFIED)

    def test_unprivileged_user_cannot_verify(self):
        """Verify that a user without verify permissions is denied access"""
        voucher = FinancialVoucher.objects.create(
            voucher_number=FinancialVoucher.generate_voucher_number(),
            date=timezone.now().date(),
            created_by=self.operator_user,
            verification_status=FinancialVoucher.STATUS_PENDING
        )

        self.client.login(username='viewer', password='password123')
        verify_url = reverse('verify-financial-entry', kwargs={'pk': voucher.pk})

        response = self.client.post(verify_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 403)

        voucher.refresh_from_db()
        self.assertEqual(voucher.verification_status, FinancialVoucher.STATUS_PENDING)
