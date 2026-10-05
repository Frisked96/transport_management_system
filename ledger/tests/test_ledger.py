from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from ledger.models import Bill, Party, CompanyAccount, TransactionCategory, FinancialRecord, TripAllocation
from ledger.services import BalanceService, BillingService, TripFinancialService
from django.contrib.auth.models import User, Permission
from trips.models import Trip, Route
from fleet.models import Vehicle

class BillAdjustmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser')
        self.party = Party.objects.create(name="Test Party")
        self.issuer = CompanyAccount.objects.create(
            name="Test Issuer",
            invoice_prefix="INV-{YYYY}/",
            cn_prefix="CN-{YYYY}/",
            dn_prefix="DN-{YYYY}/"
        )
        self.category_invoice = TransactionCategory.objects.get_or_create(name='Standard', type='Income')[0]
        self.category_cn = TransactionCategory.objects.get_or_create(name='Credit Note', type='Income')[0]

    def test_credit_note_reassignment_updates_caches(self):
        """
        Test that moving a Credit Note from one bill to another updates the 
        outstanding balance of BOTH bills correctly.
        """
        # 1. Create two Bills
        bill_a = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('1000.00'),
            category=self.category_invoice
        )
        
        bill_b = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('2000.00'),
            category=self.category_invoice
        )

        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('1000.00'))
        self.assertEqual(bill_b.outstanding_balance_cached, Decimal('2000.00'))

        # 2. Create a Credit Note for Bill A
        cn = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('100.00'),
            category=self.category_cn,
            original_bill=bill_a
        )

        bill_a.refresh_from_db()
        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('900.00'))
        
        # 3. Reassign CN to Bill B
        cn.original_bill = bill_b
        cn.save()

        bill_a.refresh_from_db()
        bill_b.refresh_from_db()
        
        # Bill A should be restored to 1000.00
        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('1000.00'))
        # Bill B should be reduced to 1900.00
        self.assertEqual(bill_b.outstanding_balance_cached, Decimal('1900.00'))

    def test_credit_note_deletion_updates_cache(self):
        """
        Test that deleting a Credit Note updates the original bill's cache.
        """
        bill_a = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('1000.00'),
            category=self.category_invoice
        )

        cn = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('100.00'),
            category=self.category_cn,
            original_bill=bill_a
        )

        bill_a.refresh_from_db()
        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('900.00'))

        # Delete the CN
        cn.delete()
        
        bill_a.refresh_from_db()
        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('1000.00'))


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


class LedgerHandoffAndAccrualTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('acc_admin', 'acc@test.com', 'password')
        self.account = CompanyAccount.objects.create(name='Primary Corp Account', invoice_prefix='INV-{YYYY}/')
        self.party = Party.objects.create(name='Zenith Logistics', party_type=Party.TYPE_DEBTOR)
        self.vehicle = Vehicle.objects.create(registration_plate='MH 14 ZZ 1111')
        self.route = Route.objects.create(
            pickup_location='Nashik',
            delivery_location='Surat',
            route_type=Route.ROUTE_TYPE_LOCAL,
            default_rate=Decimal('1000.00')
        )

    def test_unbilled_trip_accrual_lifecycle_and_bill_restoration(self):
        """
        Verify the complete hand-off lifecycle:
        1. Unbilled trip creates individual accrual FinancialRecord.
        2. Billing the trip deletes individual accrual and creates consolidated Bill accrual.
        3. Deleting the Bill restores the individual trip accrual.
        """
        # 1. Create Trip (20 tons * 1000 = 20,000 revenue + 18% GST = 23,600 total)
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            route=self.route,
            revenue_type=Trip.REVENUE_PER_TON,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1000.00')
        )

        trip_accrual = FinancialRecord.objects.filter(
            associated_trip=trip,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            party=self.party
        ).first()
        self.assertIsNotNone(trip_accrual)
        self.assertEqual(trip_accrual.amount, Decimal('23600.00'))

        # 2. Add Trip to a Bill
        bill = Bill.objects.create(
            issuer=self.account,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            gst_rate=Bill.GST_RATE_18
        )
        bill.trips.add(trip)
        bill.sync_to_ledger()

        # Individual trip accrual should now be DELETED
        self.assertFalse(
            FinancialRecord.objects.filter(
                associated_trip=trip,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).exists()
        )

        # Consolidated Bill invoice record should now EXIST
        bill_accrual = FinancialRecord.objects.filter(
            associated_bill=bill,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            party=self.party
        ).first()
        self.assertIsNotNone(bill_accrual)
        self.assertEqual(bill_accrual.amount, bill.rounded_total)

        # 3. Delete the Bill -> Individual trip accrual should be RESTORED
        bill_id = bill.pk
        bill.delete()

        # Consolidated Bill record should be DELETED
        self.assertFalse(
            FinancialRecord.objects.filter(
                associated_bill_id=bill_id,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).exists()
        )

        # Individual trip accrual should be RESTORED
        restored_trip_accrual = FinancialRecord.objects.filter(
            associated_trip=trip,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE,
            party=self.party
        ).first()
        self.assertIsNotNone(restored_trip_accrual)
        self.assertEqual(restored_trip_accrual.amount, Decimal('23600.00'))


class FinancialBalanceInvariantTests(TestCase):
    def setUp(self):
        self.account = CompanyAccount.objects.create(
            name='Treasury Account',
            opening_balance=Decimal('50000.00'),
            invoice_prefix='INV-{YYYY}/',
            cn_prefix='CN-{YYYY}/'
        )
        self.debtor = Party.objects.create(
            name='Debtor Global Traders',
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('10000.00')
        )
        self.creditor = Party.objects.create(
            name='Creditor Fuel Station',
            party_type=Party.TYPE_CREDITOR,
            opening_balance=Decimal('5000.00')
        )
        self.cat_income, _ = TransactionCategory.objects.get_or_create(
            name='Standard Income',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_expense, _ = TransactionCategory.objects.get_or_create(
            name='Diesel Expense',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.cat_payment_in, _ = TransactionCategory.objects.get_or_create(
            name='Payment In',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_payment_out, _ = TransactionCategory.objects.get_or_create(
            name='Payment Out',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.cat_tds, _ = TransactionCategory.objects.get_or_create(
            name='TDS',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_deduction, _ = TransactionCategory.objects.get_or_create(
            name='Deductions',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )

    def test_debtor_party_balance_invariants(self):
        """
        Verify debtor balance calculation invariant:
        Balance = Opening Balance + Invoices (Debits) - Payments/TDS/Deductions/Credit Notes (Credits)
        """
        # Initial: opening balance = 10,000
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('10000.00'))

        # 1. Invoice generated: 40,000 (Debit) -> Balance = 50,000
        inv = Bill.objects.create(
            issuer=self.account,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('40000.00'),
            category=self.cat_income
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('50000.00'))

        # 2. Bank Payment received: 25,000 (Credit) -> Balance = 25,000
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('25000.00')
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('25000.00'))

        # 3. TDS deduction: 1,000 (Credit) -> Balance = 24,000
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.debtor,
            category=self.cat_tds,
            amount=Decimal('1000.00')
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('24000.00'))

        # 4. Shortage/Damage deduction: 500 (Credit) -> Balance = 23,500
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.debtor,
            category=self.cat_deduction,
            amount=Decimal('500.00')
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('23500.00'))

        # 5. Credit Note issued against invoice: 3,500 (Credit) -> Balance = 20,000
        cat_cn, _ = TransactionCategory.objects.get_or_create(
            name='Credit Note',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        cn = Bill.objects.create(
            issuer=self.account,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('3500.00'),
            category=cat_cn,
            original_bill=inv
        )
        self.debtor.refresh_balance()
        self.assertEqual(self.debtor.current_balance_cached, Decimal('20000.00'))
        self.assertEqual(self.debtor.total_debit_amount - self.debtor.total_credit_amount, Decimal('20000.00'))

    def test_company_account_cash_balance_invariants(self):
        """
        Verify Company Account balance invariant:
        Balance = Opening Balance + Cash Incomes - Cash Expenses.
        Accrual records (Invoices) and non-cash adjustments (TDS, Deductions) are excluded.
        """
        initial_balance = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(initial_balance, Decimal('50000.00'))

        # 1. Real Cash/Bank Receipt: +20,000
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('20000.00')
        )
        bal_after_inc = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(bal_after_inc, Decimal('70000.00'))

        # 2. Real Cash/Bank Expense: -15,000
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.creditor,
            category=self.cat_expense,
            amount=Decimal('15000.00')
        )
        bal_after_exp = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(bal_after_exp, Decimal('55000.00'))

        # 3. Create an Accrual Invoice for 100,000 -> MUST NOT affect cash balance
        Bill.objects.create(
            issuer=self.account,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('100000.00'),
            category=self.cat_income
        )
        bal_after_inv = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(bal_after_inv, Decimal('55000.00'))

        # 4. TDS and Deductions -> MUST NOT affect cash balance
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.debtor,
            category=self.cat_tds,
            amount=Decimal('2000.00')
        )
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.debtor,
            category=self.cat_deduction,
            amount=Decimal('1500.00')
        )
        bal_final = BalanceService.refresh_account_balance(self.account)
        self.assertEqual(bal_final, Decimal('55000.00'))


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


class CreditorAttachedVehicleBillingTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_superuser(username='adminuser', password='password123')
        self.client.force_login(self.user)

        self.issuer = CompanyAccount.objects.create(
            name="Alpha Logistics",
            invoice_prefix="INV-{YYYY}/",
            cn_prefix="CN-{YYYY}/",
            dn_prefix="DN-{YYYY}/"
        )
        self.debtor = Party.objects.create(name="Customer Corp", party_type='Debtor')
        self.creditor = Party.objects.create(
            name="Vendor Trucking Services",
            party_type='Creditor',
            bank_name="HDFC Bank",
            account_number="9876543210",
            ifsc_code="HDFC0001234",
            bank_branch="Kolkata"
        )

        self.cat_trip_payment, _ = TransactionCategory.objects.get_or_create(name='Trip Payment', type='Income')
        self.cat_lorry_hire, _ = TransactionCategory.objects.get_or_create(name='Lorry Hire', type='Expense')
        self.cat_payment_out, _ = TransactionCategory.objects.get_or_create(name='Payment Out', type='Expense')
        self.cat_tds, _ = TransactionCategory.objects.get_or_create(name='TDS', type='Expense')

        self.vehicle = Vehicle.objects.create(
            registration_plate="WB01AB9999",
            ownership='Attached',
            vendor=self.creditor
        )
        self.route = Route.objects.create(pickup_location="Kolkata", delivery_location="Patna")

    def test_attached_vehicle_trip_accrual_and_creditor_bill_creation(self):
        """
        Verify that:
        1. Trip creation on attached vehicle creates trip accruals in both debtor and creditor ledgers.
        2. Customer bill creation consolidates both and generates a linked Creditor Bill.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1500.00'),
            vendor_hire_amount=Decimal('22000.00'),
            date=timezone.now().date()
        )

        # Accrual records before customer bill
        debtor_record = FinancialRecord.objects.filter(associated_trip=trip, party=self.debtor).first()
        creditor_record = FinancialRecord.objects.filter(associated_trip=trip, party=self.creditor).first()
        self.assertIsNotNone(debtor_record)
        self.assertIsNotNone(creditor_record)
        self.assertEqual(creditor_record.amount, Decimal('22000.00'))
        self.assertEqual(trip.is_billed, False)
        self.assertEqual(trip.is_creditor_billed, False)

        # Create customer bill
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            creditor_invoice_number="VENDOR-INV-101",
            creditor_invoice_date=timezone.now().date()
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        # Check linked Creditor Bill was generated
        self.assertEqual(customer_bill.creditor_bills.count(), 1)
        creditor_bill = customer_bill.creditor_bills.first()
        self.assertEqual(creditor_bill.customer_bill, customer_bill)
        self.assertEqual(creditor_bill.party, self.creditor)
        self.assertEqual(creditor_bill.creditor_invoice_number, "VENDOR-INV-101")
        self.assertEqual(creditor_bill.display_invoice_number, "VENDOR-INV-101")
        self.assertEqual(creditor_bill.subtotal, Decimal('22000.00'))
        self.assertEqual(creditor_bill.rounded_total, Decimal('22000.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('22000.00'))
        self.assertEqual(creditor_bill.payment_status, 'Unpaid')

        # Check Trip status
        trip.refresh_from_db()
        self.assertTrue(trip.is_billed)
        self.assertTrue(trip.is_creditor_billed)
        self.assertEqual(trip.associated_bill, customer_bill)
        self.assertEqual(trip.creditor_bill, creditor_bill)

        # Check trip accruals removed and replaced with consolidated entries
        self.assertFalse(FinancialRecord.objects.filter(associated_trip=trip).exists())
        debtor_bill_record = FinancialRecord.objects.filter(associated_bill=customer_bill, party=self.debtor).first()
        self.assertIsNotNone(debtor_bill_record)
        creditor_bill_record = FinancialRecord.objects.filter(associated_bill=creditor_bill, party=self.creditor).first()
        self.assertIsNotNone(creditor_bill_record)
        self.assertEqual(creditor_bill_record.amount, Decimal('22000.00'))
        self.assertEqual(creditor_bill_record.category, self.cat_lorry_hire)

    def test_creditor_bill_payments_and_tds_settlement(self):
        """
        Verify that recording Payment Out and TDS reduces creditor bill outstanding
        and transitions payment status appropriately.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1500.00'),
            vendor_hire_amount=Decimal('25000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            creditor_invoice_number="VEND-202"
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('25000.00'))

        # Record partial payment out: ₹15,000
        p1 = FinancialRecord.objects.create(
            party=self.creditor,
            account=self.issuer,
            record_type='Expense',
            category=self.cat_payment_out,
            amount=Decimal('15000.00'),
            associated_bill=creditor_bill,
            date=timezone.now().date()
        )
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('15000.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('10000.00'))
        self.assertEqual(creditor_bill.payment_status, 'Partially Paid')

        # Record TDS: ₹2,500
        p2 = FinancialRecord.objects.create(
            party=self.creditor,
            account=self.issuer,
            record_type='Expense',
            category=self.cat_tds,
            amount=Decimal('2500.00'),
            associated_bill=creditor_bill,
            date=timezone.now().date()
        )
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('17500.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('7500.00'))
        self.assertEqual(creditor_bill.payment_status, 'Partially Paid')

        # Record final payment out: ₹7,500
        p3 = FinancialRecord.objects.create(
            party=self.creditor,
            account=self.issuer,
            record_type='Expense',
            category=self.cat_payment_out,
            amount=Decimal('7500.00'),
            associated_bill=creditor_bill,
            date=timezone.now().date()
        )
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('25000.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('0.00'))
        self.assertEqual(creditor_bill.payment_status, 'Paid')

    def test_customer_bill_deletion_cascades_and_restores_accruals(self):
        """
        Verify that deleting the customer bill deletes the linked Creditor Bill
        and automatically restores unbilled trip accruals for both Debtor and Creditor.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            vendor_hire_amount=Decimal('8000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            creditor_invoice_number="VEND-303"
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()
        creditor_bill_id = creditor_bill.pk

        # Delete customer bill
        customer_bill.delete()

        # Creditor bill must be deleted
        self.assertFalse(Bill.objects.filter(pk=creditor_bill_id).exists())

        # Trip must be restored to unbilled
        trip.refresh_from_db()
        self.assertFalse(trip.is_billed)
        self.assertFalse(trip.is_creditor_billed)
        self.assertIsNone(trip.associated_bill)
        self.assertIsNone(trip.creditor_bill)

        # Accruals restored
        self.assertTrue(FinancialRecord.objects.filter(associated_trip=trip, party=self.debtor).exists())
        self.assertTrue(FinancialRecord.objects.filter(associated_trip=trip, party=self.creditor).exists())

    def test_creditor_bill_views_and_filters(self):
        """
        Test bill-list filtering, bill-detail rendering, and party-detail tabs for creditor billing.
        """
        from django.urls import reverse
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('15.00'),
            rate_per_ton=Decimal('2000.00'),
            vendor_hire_amount=Decimal('24000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            creditor_invoice_number="VEND-VIEW-999"
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()

        # 1. bill-list with bill_kind=customer
        resp_cust = self.client.get(reverse('bill-list') + '?bill_kind=customer')
        self.assertEqual(resp_cust.status_code, 200)
        self.assertContains(resp_cust, customer_bill.bill_number)
        self.assertNotContains(resp_cust, "VEND-VIEW-999")

        # 2. bill-list with bill_kind=vendor
        resp_vend = self.client.get(reverse('bill-list') + '?bill_kind=vendor')
        self.assertEqual(resp_vend.status_code, 200)
        self.assertContains(resp_vend, "VEND-VIEW-999")
        self.assertContains(resp_vend, "Vendor Bill")

        # 3. Search by creditor invoice number
        resp_search = self.client.get(reverse('bill-list') + '?search=VEND-VIEW-999')
        self.assertEqual(resp_search.status_code, 200)
        self.assertContains(resp_search, "VEND-VIEW-999")

        # 4. Customer bill detail renders linked creditor bill
        resp_cust_detail = self.client.get(reverse('bill-detail', kwargs={'pk': customer_bill.pk}))
        self.assertEqual(resp_cust_detail.status_code, 200)
        self.assertContains(resp_cust_detail, "Linked Vendor Bills (Attached)")
        self.assertContains(resp_cust_detail, "VEND-VIEW-999")

        # 5. Creditor bill detail renders vendor bill format
        resp_vend_detail = self.client.get(reverse('bill-detail', kwargs={'pk': creditor_bill.pk}))
        self.assertEqual(resp_vend_detail.status_code, 200)
        self.assertContains(resp_vend_detail, "Vendor Bill / Lorry Hire Invoice")
        self.assertContains(resp_vend_detail, "VEND-VIEW-999")
        self.assertContains(resp_vend_detail, customer_bill.bill_number)

        # 6. Creditor party detail renders operations and invoices
        resp_party = self.client.get(reverse('party-detail', kwargs={'pk': self.creditor.pk}))
        self.assertEqual(resp_party.status_code, 200)
        self.assertContains(resp_party, "Operations")
        self.assertContains(resp_party, "Invoices")
        self.assertContains(resp_party, "VEND-VIEW-999")

    def test_migrate_attached_creditor_bills_dry_run(self):
        """
        Verify that migrate_attached_creditor_bills with --dry-run previews
        without persisting any creditor bills or hire amounts.
        """
        from django.core.management import call_command
        import io

        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('2000.00'),
            vendor_hire_amount=Decimal('0.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            gst_rate=18,
            gst_type=Bill.GST_TYPE_GST
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        # Initially 0 creditor bills
        self.assertEqual(Bill.objects.filter(customer_bill__isnull=False).count(), 0)

        out = io.StringIO()
        call_command('migrate_attached_creditor_bills', '--dry-run', stdout=out)

        # Still 0 creditor bills after dry-run
        self.assertEqual(Bill.objects.filter(customer_bill__isnull=False).count(), 0)
        trip.refresh_from_db()
        self.assertEqual(trip.vendor_hire_amount, Decimal('0.00'))
        self.assertIn("DRY-RUN COMPLETE", out.getvalue())

    def test_migrate_attached_creditor_bills_commit(self):
        """
        Verify that migrate_attached_creditor_bills with --commit generates
        creditor bills including GST, backfills hire amounts, and updates vendor balances.
        Also verifies idempotency on repeated execution.
        """
        from django.core.management import call_command
        import io

        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1500.00'),
            vendor_hire_amount=Decimal('0.00'),
            date=timezone.now().date()
        )
        # Revenue = 20 * 1500 = 30,000
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            gst_rate=18,
            gst_type=Bill.GST_TYPE_GST
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        out = io.StringIO()
        call_command('migrate_attached_creditor_bills', '--commit', stdout=out)

        # 1. Trip vendor_hire_amount backfilled to base revenue (30,000)
        trip.refresh_from_db()
        self.assertEqual(trip.vendor_hire_amount, Decimal('30000.00'))

        # 2. Creditor bill created with 18% GST
        creditor_bills = Bill.objects.filter(customer_bill=customer_bill)
        self.assertEqual(creditor_bills.count(), 1)
        cr_bill = creditor_bills.first()
        self.assertEqual(cr_bill.party, self.creditor)
        self.assertEqual(cr_bill.subtotal_cached, Decimal('30000.00'))
        self.assertEqual(cr_bill.gst_rate, 18)
        self.assertEqual(cr_bill.gst_type, Bill.GST_TYPE_GST)
        # 30000 * 18% = 5400 GST, Total = 35400
        self.assertEqual(cr_bill.gst_amount_cached, Decimal('5400.00'))
        self.assertEqual(cr_bill.total_amount_cached, Decimal('35400.00'))

        # 3. FinancialRecord has full amount with GST
        fr = FinancialRecord.objects.filter(associated_bill=cr_bill, party=self.creditor).first()
        self.assertIsNotNone(fr)
        self.assertEqual(fr.amount, Decimal('35400.00'))

        # 4. Vendor balance reflects full credit including GST
        self.creditor.refresh_from_db()
        self.assertEqual(self.creditor.total_credit_amount, Decimal('35400.00'))
        self.assertEqual(self.creditor.current_balance_cached, Decimal('-35400.00'))

        # 5. Idempotent re-run
        call_command('migrate_attached_creditor_bills', '--commit', stdout=out)
        self.assertEqual(Bill.objects.filter(customer_bill=customer_bill).count(), 1)
        self.assertEqual(FinancialRecord.objects.filter(associated_bill=cr_bill, party=self.creditor).count(), 1)
        self.creditor.refresh_from_db()
        self.assertEqual(self.creditor.current_balance_cached, Decimal('-35400.00'))

    def test_auto_mirror_customer_credit_note_as_creditor_debit_note(self):
        """
        Verify that creating a Customer Credit Note against a customer bill with attached trips
        automatically generates a mirrored Debit Note for the vendor, properly reducing
        the vendor bill's outstanding balance and vendor ledger balance.
        Also verifies deletion of the Customer CN cascades and restores vendor balance.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1000.00'),
            vendor_hire_amount=Decimal('20000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            gst_rate=18,
            gst_type=Bill.GST_TYPE_GST
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()
        self.assertEqual(creditor_bill.total_amount_cached, Decimal('23600.00')) # 20000 + 3600 GST
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('23600.00'))

        # Create Customer Credit Note for Shortage: ₹2,000 subtotal + 18% GST = ₹2,360
        cat_cn = TransactionCategory.objects.get(name='Credit Note')
        cust_cn = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            original_bill=customer_bill,
            bill_type=Bill.TYPE_STANDARD,
            category=cat_cn,
            date=timezone.now().date(),
            item_type="SHORTAGE",
            amount_override=Decimal('2000.00'),
            gst_rate=18,
            gst_type=Bill.GST_TYPE_GST
        )
        cust_cn.sync_to_ledger()

        # 1. Mirrored Debit Note created for Creditor
        mirrored_dn = Bill.objects.filter(customer_bill=cust_cn, party=self.creditor).first()
        self.assertIsNotNone(mirrored_dn)
        self.assertEqual(mirrored_dn.original_bill, creditor_bill)
        self.assertEqual(mirrored_dn.category.name, 'Debit Note')
        self.assertEqual(mirrored_dn.total_amount_cached, Decimal('23600.00') if False else Decimal('2360.00'))
        self.assertEqual(mirrored_dn.item_type, 'SHORTAGE')

        # 2. Creditor Bill outstanding reduced
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('2360.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('21240.00')) # 23600 - 2360

        # 3. Vendor balance updated (Net Liability: 23600 - 2360 = 21240 Cr)
        self.creditor.refresh_from_db()
        self.assertEqual(self.creditor.total_debit_amount, Decimal('2360.00'))
        self.assertEqual(self.creditor.total_credit_amount, Decimal('23600.00'))
        self.assertEqual(self.creditor.current_balance_cached, Decimal('-21240.00'))

        # 4. Deleting Customer Credit Note cascades and deletes mirrored Debit Note
        cust_cn_pk = cust_cn.pk
        cust_cn.delete()
        self.assertFalse(Bill.objects.filter(customer_bill_id=cust_cn_pk).exists())

        # Parent creditor bill restored
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('0.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('23600.00'))

        # Vendor balance restored
        self.creditor.refresh_from_db()
        self.assertEqual(self.creditor.current_balance_cached, Decimal('-23600.00'))


class PartyStatementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='statement_admin', password='password')
        self.client.force_login(self.user)

        self.account_a = CompanyAccount.objects.create(
            name="Firm Alpha Logistics",
            address="123 Alpha Road",
            gstin="22AAAAA0000A1Z5",
            phone_number="9876543210"
        )
        self.account_b = CompanyAccount.objects.create(
            name="Firm Beta Transport",
            address="456 Beta Street",
            gstin="22BBBBB0000B1Z6",
            phone_number="9123456780"
        )

        self.party = Party.objects.create(
            name="Universal Client",
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('1000.00')
        )

        self.cat_payment = TransactionCategory.objects.get_or_create(
            name='Payment In',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )[0]

        # Transaction with Account A
        self.record_a = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account_a,
            party=self.party,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            category=self.cat_payment,
            amount=Decimal('500.00'),
            description="Payment to Alpha"
        )

        # Transaction with Account B
        self.record_b = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account_b,
            party=self.party,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
            category=self.cat_payment,
            amount=Decimal('700.00'),
            description="Payment to Beta"
        )

    def test_party_detail_view_includes_company_accounts(self):
        """Party detail view context should provide company_accounts for statement selector."""
        response = self.client.get(f'/ledger/parties/{self.party.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('company_accounts', response.context)
        accounts_in_ctx = list(response.context['company_accounts'])
        self.assertIn(self.account_a, accounts_in_ctx)
        self.assertIn(self.account_b, accounts_in_ctx)

    def test_party_statement_default_account(self):
        """Without account query param, statement defaults to first company account."""
        response = self.client.get(f'/ledger/parties/{self.party.pk}/statement/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('company', response.context)
        self.assertIn(response.context['company'], [self.account_a, self.account_b])
        # All transactions across firms are present
        statement_rows = response.context['statement_rows']
        descriptions = [r['description'] for r in statement_rows]
        self.assertIn("Payment to Alpha", descriptions)
        self.assertIn("Payment to Beta", descriptions)

    def test_party_statement_selected_account(self):
        """With account query param, statement header uses the specified company account."""
        response = self.client.get(f'/ledger/parties/{self.party.pk}/statement/?account={self.account_b.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['company'], self.account_b)
        self.assertContains(response, "Firm Beta Transport")
        self.assertContains(response, "456 Beta Street")
        self.assertContains(response, "22BBBBB0000B1Z6")
        # All transactions across firms are present
        statement_rows = response.context['statement_rows']
        descriptions = [r['description'] for r in statement_rows]
        self.assertIn("Payment to Alpha", descriptions)
        self.assertIn("Payment to Beta", descriptions)

    def test_party_statement_custom_header_name(self):
        """Optional header_name parameter overrides the company title in the statement."""
        response = self.client.get(f'/ledger/parties/{self.party.pk}/statement/?account={self.account_a.pk}&header_name=Custom+Alpha+Corp')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['custom_header_name'], "Custom Alpha Corp")
        self.assertContains(response, "Custom Alpha Corp")












