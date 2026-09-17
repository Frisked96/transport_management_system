from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from ledger.models import Bill, Party, CompanyAccount, TransactionCategory
from django.contrib.auth.models import User

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




