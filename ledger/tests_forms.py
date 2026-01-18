from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from .forms import FinancialRecordForm
from .models import Party
from trips.models import Trip, TripLeg
from fleet.models import Vehicle

class FinancialRecordFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.party1 = Party.objects.create(name='Party 1')
        self.party2 = Party.objects.create(name='Party 2')
        
        self.vehicle = Vehicle.objects.create(
            registration_plate='V1', 
            make_model='M1', 
            status='Active',
            purchase_date=timezone.now().date()
        )
        self.trip = Trip.objects.create(driver=self.user, vehicle=self.vehicle, created_by=self.user)
        
        self.leg1 = TripLeg.objects.create(trip=self.trip, party=self.party1, pickup_location='A', delivery_location='B', date=timezone.now())
        self.leg2 = TripLeg.objects.create(trip=self.trip, party=self.party2, pickup_location='C', delivery_location='D', date=timezone.now())

    def test_form_filters_legs_by_party_initial(self):
        # Initialize form with party1 in initial data
        form = FinancialRecordForm(initial={'party': self.party1})
        
        # Check that queryset only contains leg1
        queryset = form.fields['associated_legs'].queryset
        self.assertIn(self.leg1, queryset)
        self.assertNotIn(self.leg2, queryset)

    def test_form_filters_legs_by_party_data(self):
        # Initialize form with bound data containing party1
        form = FinancialRecordForm(data={'party': self.party1.pk})
        
        # Check that queryset only contains leg1
        queryset = form.fields['associated_legs'].queryset
        self.assertIn(self.leg1, queryset)
        self.assertNotIn(self.leg2, queryset)
