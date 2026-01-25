from django.test import TestCase, Client
from django.contrib.auth.models import User, Permission
from django.urls import reverse
from django.utils import timezone
from .models import Party, FinancialRecord, TransactionCategory
from trips.models import Trip
from fleet.models import Vehicle
from drivers.models import Driver

class PartyViewTest(TestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(username='manager', password='password')
        
        # Add permission
        perm_add = Permission.objects.get(codename='add_financialrecord')
        perm_change = Permission.objects.get(codename='change_financialrecord')
        self.user.user_permissions.add(perm_add, perm_change)
        
        self.driver_profile = Driver.objects.create(
            user=self.user,
            employee_id='D001',
            license_number='LIC123',
            phone_number='1234567890'
        )

        # Create Party
        self.party = Party.objects.create(
            name='Test Party',
            phone_number='1234567890',
            state='Test State'
        )
        
        # Create Vehicle
        self.vehicle = Vehicle.objects.create(
            registration_plate='TEST-001',
            make_model='Test Truck',
            purchase_date=timezone.now().date(),
            status=Vehicle.STATUS_ACTIVE
        )
        
        # Create Trip (Revenue = 10 * 100 = 1000)
        self.trip = Trip.objects.create(
            driver=self.driver_profile,
            vehicle=self.vehicle,
            party=self.party,
            weight=10,
            rate_per_ton=100,
            date=timezone.now(),
            status=Trip.STATUS_IN_PROGRESS,
            created_by=self.user
        )
        
        # Create categories
        self.income_cat = TransactionCategory.objects.create(name='Freight Income', type=TransactionCategory.TYPE_INCOME)
        self.payment_cat = TransactionCategory.objects.create(name='Party Payment', type=TransactionCategory.TYPE_INCOME)
        self.expense_cat = TransactionCategory.objects.create(name='Fuel Expense', type=TransactionCategory.TYPE_EXPENSE)
        
        self.client = Client()
        self.client.login(username='manager', password='password')

    def test_party_list(self):
        url = reverse('party-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Party')

    def test_party_create(self):
        url = reverse('party-create')
        data = {
            'name': 'New Party',
            'phone_number': '0987654321',
            'state': 'New State',
            'address': 'New Address'
        }
        response = self.client.post(url, data)
        # Should redirect to detail view
        self.assertEqual(response.status_code, 302)
        
        self.assertTrue(Party.objects.filter(name='New Party').exists())

    def test_party_detail(self):
        url = reverse('party-detail', args=[self.party.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Party')

    def test_party_update(self):
        url = reverse('party-update', args=[self.party.pk])
        data = {
            'name': 'Updated Party Name',
            'phone_number': '1111111111',
            'state': 'Updated State',
            'address': 'Updated Address'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        self.party.refresh_from_db()
        self.assertEqual(self.party.name, 'Updated Party Name')
        
    def test_financial_record_create_updates_trip_status(self):
        url = reverse('financialrecord-create')
        data = {
            'date': timezone.now().date(),
            'party': self.party.pk,
            'category': self.income_cat.id,
            'amount': 1000,
            'description': 'Test Income',
            'associated_trip': self.trip.pk
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        record = FinancialRecord.objects.filter(description='Test Income').first()
        self.assertIsNotNone(record)
        self.assertEqual(record.associated_trip, self.trip)
        
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.amount_received, 1000)
        self.assertEqual(self.trip.payment_status, Trip.PAYMENT_STATUS_PAID)

    def test_party_balance_calculation(self):
        # Initial state: Trip created with 1000 revenue. No payments.
        url = reverse('party-detail', args=[self.party.pk])
        response = self.client.get(url)
        self.assertEqual(response.context['total_billed'], 1000)
        self.assertEqual(response.context['total_received'], 0)
        self.assertEqual(response.context['balance'], 1000) # Party owes 1000

        # Make a payment of 500
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            category=self.payment_cat,
            amount=500,
            party=self.party,
            recorded_by=self.user
        )

        response = self.client.get(url)
        self.assertEqual(response.context['total_billed'], 1000)
        self.assertEqual(response.context['total_received'], 500)
        self.assertEqual(response.context['balance'], 500) # Party owes 500

    def test_party_list_balance(self):
        # Setup: Party has 1000 revenue, 0 paid. Balance should be 1000.
        # trip created in setUp has 1000 revenue.
        
        url = reverse('party-list')
        response = self.client.get(url)
        
        # Check context
        parties = list(response.context['parties'])
        party_in_list = next(p for p in parties if p.pk == self.party.pk)
        self.assertEqual(party_in_list.outstanding_balance, 1000)
        
        # Check content
        self.assertContains(response, '1000.00')
        
        # Add payment
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            category=self.payment_cat,
            amount=500,
            party=self.party,
            recorded_by=self.user
        )
        
        response = self.client.get(url)
        parties = list(response.context['parties'])
        party_in_list = next(p for p in parties if p.pk == self.party.pk)
        self.assertEqual(party_in_list.outstanding_balance, 500)
        self.assertContains(response, '500.00')
