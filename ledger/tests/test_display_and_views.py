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

class TripDateFormattingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='admin', email='admin@test.com', password='password')
        self.party = Party.objects.create(name="Test Debtor", party_type=Party.TYPE_DEBTOR)
        from fleet.models import Vehicle
        from trips.models import Route, Trip
        self.vehicle = Vehicle.objects.create(registration_plate="MH12AB1234", purchase_date=timezone.now().date())
        self.route = Route.objects.create(pickup_location="City A", delivery_location="City B", default_rate=Decimal('500.00'))
        
        from trips.forms import TripForm
        form_data = {
            'date': '2026-09-01',
            'vehicle': self.vehicle.id,
            'party': self.party.id,
            'route': self.route.id,
            'revenue_type': Trip.REVENUE_PER_TON,
            'rate_per_ton': Decimal('500.00'),
            'weight': Decimal('10.00'),
            'vendor_hire_amount': Decimal('0.00'),
        }
        form = TripForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        self.trip = form.save()
        self.trip.refresh_from_db()

    def test_trip_local_date_properties(self):
        """
        Ensure Trip.local_date and Trip.local_date_only return the date in Asia/Kolkata
        even though stored in UTC.
        """
        self.assertEqual(self.trip.local_date_only.isoformat(), '2026-09-01')
        self.assertEqual(self.trip.local_date.strftime('%d/%m/%Y'), '01/09/2026')

    def test_get_party_unpaid_trips_uses_local_date(self):
        """
        Test that get_party_unpaid_trips returns 01/09/2026 and not 31/08/2026.
        """
        self.client.force_login(self.user)
        response = self.client.get(f'/ledger/ajax/get-party-unpaid-trips/?party_id={self.party.id}')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(len(data['trips']) > 0)
        label = data['trips'][0]['label']
        self.assertIn('01/09/2026', label)
        self.assertNotIn('31/08/2026', label)

    def test_get_party_unbilled_trips_uses_local_date(self):
        """
        Test that get_party_unbilled_trips returns '01 Sep 2026' and not '31 Aug 2026'.
        """
        self.client.force_login(self.user)
        response = self.client.get(f'/ledger/ajax/get-party-unbilled-trips/?party_id={self.party.id}')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(len(data['trips']) > 0)
        date_str = data['trips'][0]['date']
        self.assertEqual(date_str, '01 Sep 2026')


class FinancialRecordDisplayTests(TestCase):
    def setUp(self):
        super().setUp()
        from django.test import Client
        self.client = Client()
        self.user = User.objects.create_superuser(username='superadmin', email='super@test.com', password='password123')
        self.client.login(username='superadmin', password='password123')
        
        self.account = CompanyAccount.objects.create(
            name="Main Firm Account",
            account_number="1234567890",
            bank_name="HDFC Bank"
        )
        self.party = Party.objects.create(name="Acme Logistics", party_type=Party.TYPE_DEBTOR, gstin="27AAACA1234A1Z1")
        self.category = TransactionCategory.objects.create(name="Payment In", type=TransactionCategory.TYPE_INCOME)
        
        from trips.models import Route, Trip
        from fleet.models import Vehicle
        self.vehicle = Vehicle.objects.create(registration_plate="MH04XY9999")
        self.route = Route.objects.create(pickup_location="Mumbai", delivery_location="Pune", default_rate=Decimal('1000.00'))
        self.trip = Trip.objects.create(
            trip_number="TRIP-TEST-101",
            date=timezone.now().date(),
            vehicle=self.vehicle,
            party=self.party,
            route=self.route,
            revenue_type=Trip.REVENUE_PER_TON,
            rate_per_ton=Decimal('500.00'),
            weight=Decimal('10.00')
        )
        self.trip.refresh_from_db()
        self.bill = Bill.objects.create(
            bill_number="INV-2026-001",
            date=timezone.now().date(),
            party=self.party,
            issuer=self.account,
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('5000.00')
        )
        self.bill.refresh_from_db()

    def test_reference_entity_properties(self):
        """Test reference_entity and refrence_entity properties on FinancialRecord"""
        from ledger.models import FinancialRecord
        from drivers.models import Driver
        # 1. With party
        rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=self.category,
            amount=Decimal('5000.00'),
            associated_bill=self.bill,
            associated_trip=self.trip
        )
        self.assertEqual(rec.reference_entity, self.party)
        self.assertEqual(rec.refrence_entity, self.party)
        self.assertEqual(rec.reference_entity_name, "Acme Logistics")
        self.assertEqual(rec.linked_bill, self.bill)
        self.assertEqual(rec.linked_trip, self.trip)

        # 2. With driver
        driver_profile = Driver.objects.create(user=self.user)
        rec_driver = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            driver=driver_profile,
            category=self.category,
            amount=Decimal('1000.00')
        )
        self.assertEqual(rec_driver.reference_entity, driver_profile)
        self.assertEqual(rec_driver.refrence_entity, driver_profile)

    def test_financial_record_list_shows_account_party_bill_trip(self):
        """Test financial record list displays Company Account, Party, and Associated Bill/Trip"""
        from django.urls import reverse
        from ledger.models import FinancialRecord
        self.trip.refresh_from_db()
        self.bill.refresh_from_db()
        rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=self.category,
            amount=Decimal('5000.00'),
            associated_bill=self.bill,
            associated_trip=self.trip
        )
        
        response = self.client.get(reverse('financialrecord-list'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        
        # Verify Company Account is displayed
        self.assertIn('Main Firm Account', content)
        # Verify Associated Party is displayed
        self.assertIn('Acme Logistics', content)
        # Verify Associated Bill is displayed
        self.assertIn(self.bill.bill_number, content)
        # Verify Associated Trip is displayed
        self.assertIn(self.trip.trip_number, content)

    def test_financial_record_list_account_and_party_filtering(self):
        """Test filtering by account and party in financial records list"""
        from django.urls import reverse
        from ledger.models import FinancialRecord
        other_account = CompanyAccount.objects.create(name="Secondary Account")
        other_party = Party.objects.create(name="Beta Transport", party_type=Party.TYPE_CREDITOR)

        rec1 = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=self.category,
            amount=Decimal('1000.00')
        )
        rec2 = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=other_account,
            party=other_party,
            category=self.category,
            amount=Decimal('2000.00')
        )

        list_url = reverse('financialrecord-list')
        # Filter by other_account
        resp_other_acc = self.client.get(f'{list_url}?account={other_account.id}')
        self.assertEqual(resp_other_acc.status_code, 200)
        self.assertEqual(len(resp_other_acc.context['financial_records']), 1)
        self.assertEqual(resp_other_acc.context['financial_records'][0].id, rec2.id)

        # Filter by self.account
        resp_acc = self.client.get(f'{list_url}?account={self.account.id}')
        self.assertEqual(resp_acc.status_code, 200)
        self.assertNotIn(rec2, resp_acc.context['financial_records'])
        self.assertTrue(all(r.account == self.account for r in resp_acc.context['financial_records']))

        # Filter by party
        resp_party = self.client.get(f'{list_url}?party={other_party.id}')
        self.assertEqual(resp_party.status_code, 200)
        self.assertEqual(len(resp_party.context['financial_records']), 1)
        self.assertEqual(resp_party.context['financial_records'][0].id, rec2.id)

    def test_financial_record_detail_shows_account_party_bill_trip(self):
        """Test financial record detail page displays Company Account, Associated Party, Bill and Trip"""
        from django.urls import reverse
        from ledger.models import FinancialRecord
        self.trip.refresh_from_db()
        self.bill.refresh_from_db()
        rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=self.category,
            amount=Decimal('5000.00'),
            associated_bill=self.bill,
            associated_trip=self.trip
        )
        
        response = self.client.get(reverse('financialrecord-detail', kwargs={'pk': rec.pk}))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        
        # Verify Company Account section
        self.assertIn('Company Account', content)
        self.assertIn('Main Firm Account', content)
        self.assertIn('1234567890', content)
        
        # Verify Associated Party section
        self.assertIn('Associated Party', content)
        self.assertIn('Acme Logistics', content)
        self.assertIn('27AAACA1234A1Z1', content)
        
        # Verify Associated Bill and Trip
        self.assertIn(self.bill.bill_number, content)
        self.assertIn(self.trip.trip_number, content)

    def test_recorded_by_visible_only_to_superuser(self):
        """Verify that 'Recorded By' audit info is only rendered for superusers"""
        from django.urls import reverse
        from ledger.models import FinancialRecord

        rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=self.category,
            amount=Decimal('1500.00'),
            recorded_by=self.user
        )

        detail_url = reverse('financialrecord-detail', kwargs={'pk': rec.pk})

        # 1. As superuser: 'Recorded By' is present
        resp_admin = self.client.get(detail_url)
        self.assertEqual(resp_admin.status_code, 200)
        self.assertIn('Recorded By', resp_admin.content.decode('utf-8'))

        # 2. As non-superuser staff: 'Recorded By' is NOT present
        normal_user = User.objects.create_user(username='staffuser', password='password123')
        perm = Permission.objects.get(codename='can_view_financial_records')
        normal_user.user_permissions.add(perm)
        self.client.login(username='staffuser', password='password123')
        resp_normal = self.client.get(detail_url)
        self.assertEqual(resp_normal.status_code, 200)
        self.assertNotIn('Recorded By', resp_normal.content.decode('utf-8'))

    def test_deductions_tds_reduce_party_balance_and_not_in_company_account_ledger(self):
        """
        Verify that entries like Deductions, TDS, and Shortage:
        1. Reduce the party's outstanding balance (credited against party).
        2. Are NOT shown in the Company Account ledger (account_detail view).
        3. Do NOT affect the Company Account's cash balance.
        4. Automatically have account set to None on save.
        """
        from django.urls import reverse
        from ledger.models import FinancialRecord, TransactionCategory
        from ledger.services import BalanceService

        self.client.login(username='superadmin', password='password123')

        # Initial party balance from setUp
        self.party.refresh_balance()
        initial_party_balance = self.party.current_balance_cached
        self.assertGreater(initial_party_balance, Decimal('0'))

        # Initial account balance
        initial_account_balance = BalanceService.refresh_account_balance(self.account)

        tds_cat, _ = TransactionCategory.objects.get_or_create(
            name='TDS', defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        ded_cat, _ = TransactionCategory.objects.get_or_create(
            name='Deductions', defaults={'type': TransactionCategory.TYPE_INCOME}
        )

        # Create TDS entry (even if attempted to associate with self.account)
        tds_rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=tds_cat,
            amount=Decimal('400.00')
        )

        # Create Deductions entry
        ded_rec = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=ded_cat,
            amount=Decimal('250.00')
        )

        # 1. Verify account is cleared to None because they are non-bank adjustments
        tds_rec.refresh_from_db()
        ded_rec.refresh_from_db()
        self.assertIsNone(tds_rec.account)
        self.assertIsNone(ded_rec.account)

        # 2. Verify party balance is reduced by TDS (400) + Deductions (250) = 650
        self.party.refresh_balance()
        new_party_balance = self.party.current_balance_cached
        self.assertEqual(new_party_balance, initial_party_balance - Decimal('650.00'))

        # 3. Verify company account balance is NOT affected
        new_account_balance = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(new_account_balance, initial_account_balance)

        # 4. Verify Company Account ledger view excludes TDS and Deductions
        account_url = reverse('account-detail', kwargs={'pk': self.account.pk})
        resp = self.client.get(account_url)
        self.assertEqual(resp.status_code, 200)

        displayed_records = resp.context['financial_records']
        self.assertNotIn(tds_rec, displayed_records)
        self.assertNotIn(ded_rec, displayed_records)




class ViewOptimizationAndEndpointsTests(TestCase):
    def setUp(self):
        from django.test import Client
        self.client = Client()
        self.admin = User.objects.create_superuser('ops_mgr', 'ops@test.com', 'pass123')
        self.client.login(username='ops_mgr', password='pass123')

        self.account = CompanyAccount.objects.create(name='Main Operational A/C', invoice_prefix='INV-{YYYY}/')
        self.party = Party.objects.create(name='Global Freight Corp', party_type=Party.TYPE_DEBTOR)
        self.cat_income, _ = TransactionCategory.objects.get_or_create(
            name='Freight Revenue',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_payment_in, _ = TransactionCategory.objects.get_or_create(
            name='Payment In',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )

        self.bill = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('25000.00'),
            category=self.cat_income
        )

        self.payment = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=self.cat_payment_in,
            amount=Decimal('10000.00'),
            associated_bill=self.bill
        )

    def test_get_party_bills_endpoint(self):
        """Test AJAX endpoint get-party-bills returns valid JSON with bills and pending balances"""
        from django.urls import reverse
        resp = self.client.get(f"{reverse('get-party-bills')}?party_id={self.party.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('bills', data)
        self.assertTrue(len(data['bills']) > 0)
        self.assertEqual(data['bills'][0]['id'], self.bill.id)

    def test_financial_summary_report_endpoint(self):
        """Test financial_summary_report view renders aggregations and category breakdowns correctly"""
        from django.urls import reverse
        resp = self.client.get(reverse('financial-summary'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('monthly_income', resp.context)
        self.assertIn('category_breakdown', resp.context)
        self.assertIn('monthly_net_incl_gst', resp.context)
        # Verify category breakdown includes Payment In
        cat_names = [c['name'] for c in resp.context['category_breakdown']]
        self.assertIn('Payment In', cat_names)

    def test_financial_record_list_party_dashboard_optimization(self):
        """Test FinancialRecordListView party dashboard batch populates last payment date"""
        from django.urls import reverse
        resp = self.client.get(reverse('financialrecord-list'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('party_dashboard', resp.context)
        dashboard_entry = next((p for p in resp.context['party_dashboard'] if p['id'] == self.party.id), None)
        self.assertIsNotNone(dashboard_entry)
        self.assertEqual(dashboard_entry['last_payment_date'], self.payment.date)

    def test_print_combined_bill_endpoint(self):
        """Test print-combined-bill endpoint renders invoice for printing when authenticated"""
        from django.urls import reverse
        resp = self.client.get(reverse('print-combined-bill', kwargs={'pk': self.bill.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.bill.bill_number)

    def test_print_combined_bill_requires_login(self):
        """Ensure unauthenticated access to sequential print-combined-bill is blocked and redirected to login"""
        from django.test import Client
        from django.urls import reverse
        anon_client = Client()
        resp = anon_client.get(reverse('print-combined-bill', kwargs={'pk': self.bill.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.url)

    def test_shared_bill_endpoint_public_access(self):
        """Ensure recipient can view shared invoice via cryptographically signed token without logging in"""
        from django.test import Client
        from django.urls import reverse
        anon_client = Client()
        token = self.bill.share_token
        self.assertTrue(token)
        resp = anon_client.get(reverse('shared-bill', kwargs={'token': token}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.bill.bill_number)

    def test_shared_bill_invalid_token_returns_404(self):
        """Ensure invalid or tampered token returns 404 Not Found"""
        from django.test import Client
        from django.urls import reverse
        anon_client = Client()
        resp = anon_client.get(reverse('shared-bill', kwargs={'token': 'tampered-or-invalid-token'}))
        self.assertEqual(resp.status_code, 404)

    def test_bill_detail_three_or_fewer_trips_renders_individual_freight_and_no_annexure(self):
        """
        Verify that for bills with 3 or fewer trips:
        - The invoice details show individual freight charges per trip.
        - Does NOT show consolidated charges text.
        - Does NOT show the annexure card.
        """
        from django.urls import reverse
        vehicle = Vehicle.objects.create(registration_plate="PB10XY1234")
        route = Route.objects.create(pickup_location="Delhi", delivery_location="Mumbai")
        trip1 = Trip.objects.create(
            vehicle=vehicle,
            party=self.party,
            route=route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1500.00')
        )
        trip2 = Trip.objects.create(
            vehicle=vehicle,
            party=self.party,
            route=route,
            weight=Decimal('12.00'),
            rate_per_ton=Decimal('1500.00')
        )
        bill = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_income
        )
        bill.trips.add(trip1, trip2)
        bill.sync_to_ledger()

        resp = self.client.get(reverse('bill-detail', kwargs={'pk': bill.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Freight Charges: PB10XY1234")
        self.assertNotContains(resp, "Consolidated charges")
        self.assertNotContains(resp, "invoice-annexure-card")

    def test_bill_detail_more_than_three_trips_renders_consolidated_and_annexure(self):
        """
        Verify that for bills with more than 3 trips:
        - The invoice details show consolidated charges summary.
        - The annexure card is rendered in the bill detail view.
        - The print-combined-bill view renders the annexure.
        """
        from django.urls import reverse
        vehicle = Vehicle.objects.create(registration_plate="PB10AB5678")
        route = Route.objects.create(pickup_location="Delhi", delivery_location="Jaipur")
        trips = [
            Trip.objects.create(
                vehicle=vehicle,
                party=self.party,
                route=route,
                weight=Decimal('15.00'),
                rate_per_ton=Decimal('1200.00')
            )
            for _ in range(4)
        ]
        bill = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_income
        )
        bill.trips.add(*trips)
        bill.sync_to_ledger()

        # Web detail view
        resp = self.client.get(reverse('bill-detail', kwargs={'pk': bill.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Consolidated charges for 4 trip(s)")
        self.assertContains(resp, "invoice-annexure-card")
        self.assertContains(resp, "Annexure")

        # Print view
        resp_print = self.client.get(reverse('print-combined-bill', kwargs={'pk': bill.pk}))
        self.assertEqual(resp_print.status_code, 200)
        self.assertContains(resp_print, "Annexure to Invoice")


