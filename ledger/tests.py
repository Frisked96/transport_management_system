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

