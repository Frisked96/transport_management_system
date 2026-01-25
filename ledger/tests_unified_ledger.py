from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import User

from trips.models import Trip
from fleet.models import Vehicle
from ledger.models import Party, FinancialRecord, TransactionCategory, Account
from drivers.models import Driver

class UnifiedLedgerTest(TestCase):
    def setUp(self):
        # Setup basic data
        self.user = User.objects.create_user('admin', 'admin@example.com', 'pass')
        self.driver_user = User.objects.create_user('driver', 'driver@example.com', 'pass')
        self.driver = Driver.objects.create(user=self.driver_user, employee_id='D001', license_number='L123', phone_number='123')

        self.vehicle = Vehicle.objects.create(
            registration_plate='KA01', make_model='Truck', purchase_date=timezone.now().date()
        )

        self.party = Party.objects.create(name='Test Party')
        self.account = Account.objects.create(name='Cash')

        # Ensure categories exist
        self.cat_income = TransactionCategory.objects.create(name='General Income', type=TransactionCategory.TYPE_INCOME)

    def test_trip_sync_invoice(self):
        """Test that creating a trip creates a FinancialRecord Invoice"""
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            driver=self.driver,
            party=self.party,
            weight=10,
            rate_per_ton=100, # Revenue = 1000
            created_by=self.user,
            date=timezone.now()
        )

        # Check Invoice
        invoices = FinancialRecord.objects.filter(associated_trip=trip, record_type=FinancialRecord.RECORD_TYPE_INVOICE)
        self.assertEqual(invoices.count(), 1)
        invoice = invoices.first()
        self.assertEqual(invoice.amount, 1000)
        self.assertEqual(invoice.party, self.party)
        self.assertEqual(invoice.category.name, 'Trip Revenue')

        # Update Trip
        trip.weight = 20 # Revenue = 2000
        trip.save()

        invoice.refresh_from_db()
        self.assertEqual(invoice.amount, 2000)

    def test_party_balance_logic(self):
        """Test that party balance is calculated from Invoices - Payments"""
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            driver=self.driver,
            party=self.party,
            weight=10,
            rate_per_ton=100, # Revenue = 1000
            created_by=self.user
        )

        # Verify Invoice created
        self.assertTrue(FinancialRecord.objects.filter(record_type='Invoice', party=self.party).exists())

        # Since Balance logic is in the View, we can manually check the calculation logic
        # Total Billed = Sum(Invoices)
        total_billed = FinancialRecord.objects.filter(
            party=self.party,
            record_type='Invoice'
        ).aggregate(total=models.Sum('amount'))['total'] or 0
        self.assertEqual(total_billed, 1000)

        # Add Payment
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.party,
            category=self.cat_income,
            amount=500,
            record_type='Transaction', # Explicitly
            associated_trip=trip,
            recorded_by=self.user
        )

        total_received = FinancialRecord.objects.filter(
            party=self.party,
            record_type='Transaction',
            category__type='Income'
        ).aggregate(total=models.Sum('amount'))['total'] or 0
        self.assertEqual(total_received, 500)

        balance = total_billed - total_received
        self.assertEqual(balance, 500)

    def test_auto_closure(self):
        """Test automatic trip closure"""
        # Trip in past
        past_date = timezone.now() - timedelta(days=1)
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            driver=self.driver,
            party=self.party,
            weight=10,
            rate_per_ton=100, # Revenue = 1000
            date=past_date,
            created_by=self.user,
            status=Trip.STATUS_IN_PROGRESS
        )

        # 1. Not paid yet -> In Progress
        self.assertEqual(trip.status, Trip.STATUS_IN_PROGRESS)

        # 2. Add Full Payment
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.party,
            category=self.cat_income,
            amount=1000,
            record_type='Transaction',
            associated_trip=trip,
            recorded_by=self.user
        )

        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.STATUS_COMPLETED)
        self.assertIsNotNone(trip.actual_completion_datetime)

    def test_amount_received_excludes_invoice(self):
        """Test that Trip.amount_received does not include the Invoice record"""
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            driver=self.driver,
            party=self.party,
            weight=10,
            rate_per_ton=100,
            created_by=self.user
        )

        # Invoice exists (1000)
        self.assertEqual(FinancialRecord.objects.filter(associated_trip=trip).count(), 1)

        # Amount Received should be 0 (Cash only)
        self.assertEqual(trip.amount_received, 0)

        # Add Payment (500)
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            party=self.party,
            category=self.cat_income,
            amount=500,
            record_type='Transaction',
            associated_trip=trip,
            recorded_by=self.user
        )

        self.assertEqual(trip.amount_received, 500)

from django.db import models
