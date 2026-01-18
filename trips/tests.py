from django.test import TestCase, Client
from django.contrib.auth.models import User, Permission
from django.urls import reverse
from django.utils import timezone
from fleet.models import Vehicle
from trips.models import Trip, TripExpense
from ledger.models import Party

class TripExpenseTest(TestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(username='manager', password='password')
        
        # Add permission
        try:
            perm = Permission.objects.get(codename='change_trip')
            self.user.user_permissions.add(perm)
        except Permission.DoesNotExist:
            print("Warning: Permission 'change_trip' not found. Tests might fail.")
        
        # Create Vehicle
        self.vehicle = Vehicle.objects.create(
            registration_plate='TEST-002',
            make_model='Test Truck 2',
            purchase_date=timezone.now().date(),
            status=Vehicle.STATUS_ACTIVE
        )
        
        # Create Party
        self.party = Party.objects.create(name='Test Party')

        # Create Trip
        self.trip = Trip.objects.create(
            driver=self.user,
            vehicle=self.vehicle,
            party=self.party,
            weight=10,
            rate_per_ton=100,
            date=timezone.now(),
            status=Trip.STATUS_IN_PROGRESS, 
            created_by=self.user
        )
        
        self.client = Client()
        self.client.login(username='manager', password='password')

    def test_fixed_expenses(self):
        url = reverse('trip-expense-update', args=[self.trip.pk])
        data = {
            'diesel_expense': 500,
            'toll_expense': 100
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        self.trip.refresh_from_db()
        self.assertEqual(self.trip.diesel_expense, 500)
        self.assertEqual(self.trip.toll_expense, 100)
        self.assertEqual(self.trip.total_cost, 600)

    def test_custom_expenses(self):
        # Add fixed expenses first
        self.trip.diesel_expense = 100
        self.trip.save()

        url = reverse('trip-custom-expense-create', args=[self.trip.pk])
        data = {
            'name': 'Lunch',
            'amount': 50,
            'notes': 'Driver lunch'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        self.assertEqual(self.trip.custom_expenses.count(), 1)
        self.assertEqual(self.trip.total_cost, 150) # 100 diesel + 50 lunch

    def test_delete_custom_expense(self):
        expense = TripExpense.objects.create(
            trip=self.trip,
            name='Snack',
            amount=20
        )
        
        url = reverse('trip-custom-expense-delete', args=[expense.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        
        self.assertFalse(TripExpense.objects.filter(pk=expense.pk).exists())