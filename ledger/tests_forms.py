from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from .forms import FinancialRecordForm
from .models import Party
from trips.models import Trip
from fleet.models import Vehicle
from drivers.models import Driver

class FinancialRecordFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')

        self.driver_profile = Driver.objects.create(
            user=self.user,
            employee_id='D001',
            license_number='LIC123',
            phone_number='1234567890'
        )

        self.party1 = Party.objects.create(name='Party 1')
        self.party2 = Party.objects.create(name='Party 2')
        
        self.vehicle = Vehicle.objects.create(
            registration_plate='V1', 
            make_model='M1', 
            status='Active',
            purchase_date=timezone.now().date()
        )
        
        # Trip for Party 1
        self.trip1 = Trip.objects.create(
            driver=self.driver_profile,
            vehicle=self.vehicle, 
            party=self.party1,
            created_by=self.user,
            date=timezone.now()
        )
        
        # Trip for Party 2
        self.trip2 = Trip.objects.create(
            driver=self.driver_profile,
            vehicle=self.vehicle, 
            party=self.party2,
            created_by=self.user,
            date=timezone.now()
        )

    def test_form_filters_trips_by_party_initial(self):
        # Initialize form with party1 in initial data
        form = FinancialRecordForm(initial={'party': self.party1})
        
        # Check that queryset only contains trip1
        queryset = form.fields['associated_trip'].queryset
        self.assertIn(self.trip1, queryset)
        self.assertNotIn(self.trip2, queryset)

    def test_form_filters_trips_by_party_data(self):
        # Initialize form with bound data containing party1
        form = FinancialRecordForm(data={'party': self.party1.pk})
        
        # Check that queryset only contains trip1
        queryset = form.fields['associated_trip'].queryset
        self.assertIn(self.trip1, queryset)
        self.assertNotIn(self.trip2, queryset)