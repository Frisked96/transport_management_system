import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from fleet.models import Vehicle
from ledger.models import (
    Bill,
    BillTrip,
    CompanyAccount,
    FinancialRecord,
    Party,
    TransactionCategory,
)
from ledger.services import BalanceService
from ledger.signals import deferred_ledger_refresh
from trips.models import Route, Trip


class LedgerPerformanceOptimizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='perf_admin',
            email='perf@example.com',
            password='password123',
        )
        self.client = Client()
        self.client.force_login(self.user)

        self.account = CompanyAccount.objects.create(
            name='Perf Transport Co',
            opening_balance=Decimal('10000.00'),
        )
        self.party = Party.objects.create(
            name='Perf Client Ltd',
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('0.00'),
        )
        self.cat_trip, _ = TransactionCategory.objects.get_or_create(
            name='Trip Payment',
            defaults={'type': TransactionCategory.TYPE_INCOME},
        )
        self.cat_inv, _ = TransactionCategory.objects.get_or_create(
            name='Invoice Payment',
            defaults={'type': TransactionCategory.TYPE_INCOME},
        )
        self.vehicle = Vehicle.objects.create(registration_plate='MH12PERF1')
        self.route = Route.objects.create(
            pickup_location='Pune',
            delivery_location='Mumbai',
            default_rate=Decimal('1000.00'),
        )

    def _create_trip(self, weight=Decimal('10.00')):
        return Trip.objects.create(
            date=timezone.now().date(),
            vehicle=self.vehicle,
            route=self.route,
            party=self.party,
            weight=weight,
            rate_per_ton=Decimal('1000.00'),
            gst_type_snapshot=Bill.GST_TYPE_NONE,
            created_by=self.user,
        )

    def test_bill_list_view_avoids_n_plus_one_and_annotates_trips_count(self):
        def create_bill_with_trips(num_trips):
            bill = Bill.objects.create(
                issuer=self.account,
                party=self.party,
                date=timezone.now().date(),
                bill_type=Bill.TYPE_TRIP,
            )
            for _ in range(num_trips):
                BillTrip.objects.create(bill=bill, trip=self._create_trip())
            bill.refresh_from_db()
            return bill

        # Warm up ActiveUserMiddleware last_seen update so subsequent queries are deterministic
        self.client.get(reverse('bill-list'))

        b1 = create_bill_with_trips(2)
        with CaptureQueriesContext(connection) as ctx_small:
            resp_small = self.client.get(reverse('bill-list'))
        self.assertEqual(resp_small.status_code, 200)

        bills = [b1] + [create_bill_with_trips(idx + 1) for idx in range(2, 6)]
        with CaptureQueriesContext(connection) as ctx_large:
            resp_large = self.client.get(reverse('bill-list'))

        self.assertEqual(resp_large.status_code, 200)
        self.assertEqual(len(ctx_small), len(ctx_large))
        self.assertLessEqual(len(ctx_large), 12)

        page_bills = list(resp_large.context['bills'])
        self.assertEqual(len(page_bills), 5)
        counts_by_id = {b.id: b.trips_count for b in page_bills}
        for b in bills:
            self.assertEqual(counts_by_id[b.id], b.trips.count())

    def test_get_party_bills_unpaid_only_filters_paid_and_keeps_included(self):
        paid_bill = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('5000.00'),
            use_roundoff=False,
        )
        rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=self.cat_inv,
            amount=Decimal('5000.00'),
            associated_bill=paid_bill,
        )
        unpaid_bill = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('7500.00'),
            use_roundoff=False,
        )

        # unpaid_only=true skips paid_bill at SQL level
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(
                f"{reverse('get-party-bills')}?party_id={self.party.id}&unpaid_only=true"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(len(ctx), 5)
        self.assertEqual(resp.status_code, 200)
        ids = [b['id'] for b in resp.json()['bills']]
        self.assertEqual(ids, [unpaid_bill.id])

        # When editing the payment on paid_bill, record_id brings paid_bill back with adjusted balance
        resp_edit = self.client.get(
            f"{reverse('get-party-bills')}?party_id={self.party.id}&unpaid_only=true&record_id={rec.id}"
        )
        self.assertEqual(resp_edit.status_code, 200)
        bills_by_id = {b['id']: b for b in resp_edit.json()['bills']}
        self.assertIn(paid_bill.id, bills_by_id)
        self.assertIn(unpaid_bill.id, bills_by_id)
        self.assertEqual(bills_by_id[paid_bill.id]['outstanding'], 5000.0)

    def test_deferred_ledger_refresh_batches_party_and_account_updates(self):
        trips = [self._create_trip() for _ in range(4)]
        dist = [
            {
                'trip_id': t.id,
                'payment': 2000,
                'tds': 100,
                'deduction': 50,
            }
            for t in trips
        ]

        with patch.object(
            BalanceService,
            'refresh_party_balance',
            wraps=BalanceService.refresh_party_balance,
        ) as mock_party_refresh, patch.object(
            BalanceService,
            'refresh_account_balance',
            wraps=BalanceService.refresh_account_balance,
        ) as mock_acct_refresh:
            resp = self.client.post(
                reverse('financialrecord-create'),
                data={
                    'date': timezone.now().date().isoformat(),
                    'record_type': FinancialRecord.RECORD_TYPE_TRANSACTION,
                    'account': self.account.id,
                    'party': self.party.id,
                    'category': self.cat_trip.id,
                    'amount': '8000.00',
                    'tds_amount': '400.00',
                    'deduction_amount': '200.00',
                    'payment_distribution': json.dumps(dist),
                },
            )
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(mock_party_refresh.call_count, 1)
            self.assertEqual(mock_acct_refresh.call_count, 1)

        self.party.refresh_from_db()
        self.account.refresh_from_db()
        # 4 trips * 10,000 = 40,000; settled 8,000 + 400 + 200 = 8,600 => 31,400 due
        self.assertEqual(self.party.current_balance_cached, Decimal('31400.00'))
        self.assertEqual(self.account.current_balance_cached, Decimal('18000.00'))
        for t in trips:
            t.refresh_from_db()
            self.assertEqual(t.amount_received_cached, Decimal('2150.00'))
            self.assertEqual(t.outstanding_balance_cached, Decimal('7850.00'))

    def test_deferred_ledger_refresh_clears_state_on_exception(self):
        with self.assertRaises(RuntimeError):
            with deferred_ledger_refresh():
                FinancialRecord.objects.create(
                    date=timezone.now().date(),
                    account=self.account,
                    party=self.party,
                    category=self.cat_trip,
                    amount=Decimal('100.00'),
                )
                raise RuntimeError('boom')

        # Subsequent saves outside context should work normally without leaking state
        with patch.object(
            BalanceService,
            'refresh_party_balance',
            wraps=BalanceService.refresh_party_balance,
        ) as mock_party_refresh:
            FinancialRecord.objects.create(
                date=timezone.now().date(),
                account=self.account,
                party=self.party,
                category=self.cat_trip,
                amount=Decimal('250.00'),
            )
            self.assertEqual(mock_party_refresh.call_count, 1)

    def test_financial_record_update_batches_refresh_and_updates_caches(self):
        b1 = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('4000.00'),
            use_roundoff=False,
        )
        b2 = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('6000.00'),
            use_roundoff=False,
        )
        resp_create = self.client.post(
            reverse('financialrecord-create'),
            data={
                'date': timezone.now().date().isoformat(),
                'record_type': FinancialRecord.RECORD_TYPE_TRANSACTION,
                'account': self.account.id,
                'party': self.party.id,
                'category': self.cat_inv.id,
                'amount': '3000.00',
                'bill_distribution': json.dumps([
                    {'bill_id': b1.id, 'amount': 2000},
                    {'bill_id': b2.id, 'amount': 1000},
                ]),
            },
        )
        self.assertEqual(resp_create.status_code, 302)
        rec = FinancialRecord.objects.order_by('-id').first()

        with patch.object(
            BalanceService,
            'refresh_party_balance',
            wraps=BalanceService.refresh_party_balance,
        ) as mock_party_refresh, patch.object(
            BalanceService,
            'refresh_account_balance',
            wraps=BalanceService.refresh_account_balance,
        ) as mock_acct_refresh:
            resp_update = self.client.post(
                reverse('financialrecord-update', kwargs={'pk': rec.pk}),
                data={
                    'date': timezone.now().date().isoformat(),
                    'record_type': FinancialRecord.RECORD_TYPE_TRANSACTION,
                    'account': self.account.id,
                    'party': self.party.id,
                    'category': self.cat_inv.id,
                    'amount': '5000.00',
                    'bill_distribution': json.dumps([
                        {'bill_id': b1.id, 'payment': 4000},
                        {'bill_id': b2.id, 'payment': 1000},
                    ]),
                },
            )
            self.assertEqual(resp_update.status_code, 302)
            self.assertEqual(mock_party_refresh.call_count, 1)
            self.assertEqual(mock_acct_refresh.call_count, 1)

        b1.refresh_from_db()
        b2.refresh_from_db()
        self.party.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(b1.outstanding_balance_cached, Decimal('0.00'))
        self.assertEqual(b1.payment_status_cached, Bill.PAYMENT_STATUS_PAID)
        self.assertEqual(b2.outstanding_balance_cached, Decimal('5000.00'))
        self.assertEqual(self.party.current_balance_cached, Decimal('5000.00'))
        self.assertEqual(self.account.current_balance_cached, Decimal('15000.00'))
