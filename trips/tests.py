from django.test import TestCase, Client
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth.models import User, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.models import LogEntry, DELETION
from trips.models import Trip
from fleet.models import Vehicle
from ledger.models import FinancialRecord, Party, TransactionCategory, CompanyAccount

class TripDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='adminuser',
            email='admin@example.com',
            password='password123'
        )
        self.vehicle = Vehicle.objects.create(
            registration_plate='MH 12 AB 1234',
            status='Active'
        )
        self.party = Party.objects.create(name='Test Logistics Party')
        self.account = CompanyAccount.objects.create(name='Main Operating A/C')
        self.category, _ = TransactionCategory.objects.get_or_create(name='Trip Payment', defaults={'type': 'Income'})
        
        self.trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            date=timezone.now().date(),
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1000.00')
        )

        self.financial_record = FinancialRecord.objects.create(
            date=timezone.now().date(),
            amount=Decimal('50000.00'),
            record_type='Payment Received',
            category=self.category,
            party=self.party,
            account=self.account,
            associated_trip=self.trip
        )

    def test_trip_delete_view_with_associated_financial_record(self):
        """
        Ensure deleting a trip that has an associated FinancialRecord does not raise
        Trip.DoesNotExist during cascade deletion or signal/audit logging.
        """
        client = Client()
        client.force_login(self.user)

        response = client.post(f'/trip/{self.trip.pk}/delete/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Trip.objects.filter(pk=self.trip.pk).exists())
        self.assertFalse(FinancialRecord.objects.filter(pk=self.financial_record.pk).exists())

        # Verify LogEntry recorded the deletion
        trip_content_type = ContentType.objects.get_for_model(Trip)
        log = LogEntry.objects.filter(content_type=trip_content_type, object_id=str(self.trip.pk), action_flag=DELETION).first()
        self.assertIsNotNone(log)

    def test_direct_trip_orm_delete_with_financial_record(self):
        """
        Ensure calling .delete() directly on Trip with associated FinancialRecord succeeds cleanly.
        """
        trip_id = self.trip.pk
        self.trip.delete()
        self.assertFalse(Trip.objects.filter(pk=trip_id).exists())
        self.assertFalse(FinancialRecord.objects.filter(associated_trip_id=trip_id).exists())
