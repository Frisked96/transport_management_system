from django.test import TestCase, Client
from django.contrib.auth.models import User, Permission
from django.urls import reverse
from django.utils import timezone
from fleet.models import Vehicle
from trips.models import Trip
from ledger.models import Party
from drivers.models import Driver

class TripSimplificationTest(TestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(username='manager', password='password')
        
        # Add permissions
        permissions = ['add_trip', 'change_trip', 'view_trip']
        for codename in permissions:
            try:
                perm = Permission.objects.get(codename=codename)
                self.user.user_permissions.add(perm)
            except Permission.DoesNotExist:
                pass

        self.driver_profile = Driver.objects.create(
            user=self.user,
            employee_id='D001',
            license_number='LIC123',
            phone_number='1234567890'
        )
        
        # Create Vehicle
        self.vehicle = Vehicle.objects.create(
            registration_plate='TEST-002',
            make_model='Test Truck 2',
            purchase_date=timezone.now().date(),
            status=Vehicle.STATUS_ACTIVE
        )
        
        # Create Party
        self.party = Party.objects.create(name='Test Party')

        self.client = Client()
        self.client.login(username='manager', password='password')

    def test_trip_creation_simplified(self):
        """Test creating a trip with the new simplified form (no fuel/odo/expenses)"""
        url = reverse('trip-create')
        data = {
            'vehicle': self.vehicle.pk,
            'driver': self.driver_profile.pk,
            'party': self.party.pk,
            'revenue_type': 'per_ton',
            'pickup_location': 'City A',
            'delivery_location': 'City B',
            'weight': 15.5,
            'rate_per_ton': 1200,
            'notes': 'Simplified trip test'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        # Verify trip exists and has correct data
        trip = Trip.objects.get(pickup_location='City A')
        self.assertEqual(trip.weight, 15.5)
        self.assertEqual(trip.revenue, 15.5 * 1200)
        
        # Verify no automatic expenses were created
        self.assertEqual(trip.custom_expenses.count(), 0)
        self.assertEqual(trip.total_cost, 0)

    def test_revenue_types(self):
        """Test calculation logic for Per Ton vs Fixed revenue types still works"""
        trip = Trip.objects.create(
            driver=self.driver_profile,
            vehicle=self.vehicle,
            party=self.party,
            weight=10,
            rate_per_ton=100,
            date=timezone.now(),
            created_by=self.user
        )
        
        # 1. Default: Per Ton (weight=10, rate=100)
        self.assertEqual(trip.revenue_type, Trip.REVENUE_PER_TON)
        self.assertEqual(trip.revenue, 1000)

        # 2. Change to Fixed
        trip.revenue_type = Trip.REVENUE_FIXED
        trip.rate_per_ton = 1500
        trip.save()
        self.assertEqual(trip.revenue, 1500)

    def test_trip_detail_view_no_status_fuel_sections(self):
        """Verify the detail view works without the removed sections"""
        trip = Trip.objects.create(
            driver=self.driver_profile,
            vehicle=self.vehicle,
            party=self.party,
            weight=10,
            rate_per_ton=100,
            date=timezone.now(),
            created_by=self.user
        )
        url = reverse('trip-detail', args=[trip.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        # Check that removed sections are not in HTML (at least their labels)
        self.assertNotContains(response, 'Fuel & Mileage')
        self.assertNotContains(response, 'Total Cost')
        self.assertNotContains(response, 'Profit (Incl. GST)')
