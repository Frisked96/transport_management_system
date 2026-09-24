from django.test import TestCase, Client
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth.models import User, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.models import LogEntry, DELETION
from trips.models import Trip
from fleet.models import Vehicle
from ledger.models import FinancialRecord, Party, TransactionCategory, CompanyAccount, Bill

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


class TripBusinessLogicTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='ops_user', email='ops@example.com', password='password123')
        self.account = CompanyAccount.objects.create(name='Main Firm Account', invoice_prefix='INV-{YYYY}/')
        self.debtor = Party.objects.create(name='Debtor Client', party_type=Party.TYPE_DEBTOR)
        self.vendor = Party.objects.create(name='Vendor Fleet Owner', party_type=Party.TYPE_CREDITOR)
        
        self.owned_vehicle = Vehicle.objects.create(
            registration_plate='MH 12 OW 0001',
            ownership=Vehicle.OWNERSHIP_OWNED,
            status=Vehicle.STATUS_ACTIVE
        )
        self.attached_vehicle = Vehicle.objects.create(
            registration_plate='MH 12 AT 9999',
            ownership=Vehicle.OWNERSHIP_ATTACHED,
            vendor=self.vendor,
            status=Vehicle.STATUS_ACTIVE
        )

        from trips.models import Route
        self.route_local = Route.objects.create(
            pickup_location='Pune',
            delivery_location='Mumbai',
            route_type=Route.ROUTE_TYPE_LOCAL,
            default_rate=Decimal('1000.00')
        )
        self.route_intra = Route.objects.create(
            pickup_location='Pune',
            delivery_location='Bangalore',
            route_type=Route.ROUTE_TYPE_INTRA,
            default_rate=Decimal('2500.00')
        )
        self.route_none = Route.objects.create(
            pickup_location='Yard A',
            delivery_location='Yard B',
            route_type=Route.ROUTE_TYPE_NONE,
            default_rate=Decimal('500.00')
        )

    def test_revenue_calculation_per_ton_and_fixed(self):
        """Test per-ton and fixed revenue math and fallback for missing values"""
        # Per Ton
        trip_per_ton = Trip.objects.create(
            vehicle=self.owned_vehicle,
            party=self.debtor,
            revenue_type=Trip.REVENUE_PER_TON,
            weight=Decimal('25.50'),
            rate_per_ton=Decimal('1200.00')
        )
        self.assertEqual(trip_per_ton.revenue, Decimal('30600.00'))

        # Fixed Revenue
        trip_fixed = Trip.objects.create(
            vehicle=self.owned_vehicle,
            party=self.debtor,
            revenue_type=Trip.REVENUE_FIXED,
            weight=Decimal('25.50'),
            rate_per_ton=Decimal('15000.00')
        )
        self.assertEqual(trip_fixed.revenue, Decimal('15000.00'))

        # Missing values (should evaluate to 0, not raise error)
        trip_zero = Trip.objects.create(
            vehicle=self.owned_vehicle,
            party=self.debtor,
            revenue_type=Trip.REVENUE_PER_TON,
            weight=None,
            rate_per_ton=Decimal('1000.00')
        )
        self.assertEqual(trip_zero.revenue, Decimal('0.00'))

    def test_gst_type_snapshotting_and_tax_calculation(self):
        """Test GST type snapshot from route and verify snapshot immutability"""
        trip_local = Trip.objects.create(
            vehicle=self.owned_vehicle,
            party=self.debtor,
            route=self.route_local,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.assertEqual(trip_local.gst_type_snapshot, Bill.GST_TYPE_GST)
        self.assertEqual(trip_local.gst_amount, Decimal('1800.00')) # 18% of 10000
        self.assertEqual(trip_local.total_revenue, Decimal('11800.00'))

        trip_intra = Trip.objects.create(
            vehicle=self.owned_vehicle,
            party=self.debtor,
            route=self.route_intra,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.assertEqual(trip_intra.gst_type_snapshot, Bill.GST_TYPE_IGST)
        self.assertEqual(trip_intra.gst_amount, Decimal('1800.00'))

        trip_none = Trip.objects.create(
            vehicle=self.owned_vehicle,
            party=self.debtor,
            route=self.route_none,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.assertEqual(trip_none.gst_type_snapshot, Bill.GST_TYPE_NONE)
        self.assertEqual(trip_none.gst_amount, Decimal('0.00'))
        self.assertEqual(trip_none.total_revenue, Decimal('10000.00'))

        # Snapshot principle: changing route route_type later does NOT change existing trip snapshot
        from trips.models import Route
        self.route_local.route_type = Route.ROUTE_TYPE_NONE
        self.route_local.save()

        trip_local.refresh_from_db()
        self.assertEqual(trip_local.gst_type_snapshot, Bill.GST_TYPE_GST)

    def test_payment_status_progression_and_balance(self):
        """Test payment status transitions Unpaid -> Partially Paid -> Paid as payments are allocated"""
        trip = Trip.objects.create(
            vehicle=self.owned_vehicle,
            party=self.debtor,
            route=self.route_none,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.assertEqual(trip.total_revenue, Decimal('10000.00'))
        self.assertEqual(trip.amount_received, Decimal('0.00'))
        self.assertEqual(trip.outstanding_balance, Decimal('10000.00'))
        self.assertEqual(trip.payment_status, Trip.PAYMENT_STATUS_UNPAID)

        # 1. Partial payment (4000)
        pay_cat, _ = TransactionCategory.objects.get_or_create(name='Trip Payment', defaults={'type': 'Income'})
        rec1 = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=pay_cat,
            amount=Decimal('4000.00'),
            associated_trip=trip
        )
        trip.refresh_from_db()
        self.assertEqual(trip.amount_received, Decimal('4000.00'))
        self.assertEqual(trip.outstanding_balance, Decimal('6000.00'))
        self.assertEqual(trip.payment_status, Trip.PAYMENT_STATUS_PARTIAL)

        # 2. Complete remaining payment (6000)
        rec2 = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=pay_cat,
            amount=Decimal('6000.00'),
            associated_trip=trip
        )
        trip.refresh_from_db()
        self.assertEqual(trip.amount_received, Decimal('10000.00'))
        self.assertEqual(trip.outstanding_balance, Decimal('0.00'))
        self.assertEqual(trip.payment_status, Trip.PAYMENT_STATUS_PAID)

    def test_attached_vehicle_vendor_hire_accrual(self):
        """Test that attached vehicle trips automatically create vendor hire accrual"""
        trip = Trip.objects.create(
            vehicle=self.attached_vehicle,
            party=self.debtor,
            route=self.route_none,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('15000.00'),
            vendor_hire_amount=Decimal('11000.00')
        )

        vendor_rec = FinancialRecord.objects.filter(
            associated_trip=trip,
            category__name='Lorry Hire',
            party=self.vendor,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).first()

        self.assertIsNotNone(vendor_rec)
        self.assertEqual(vendor_rec.amount, Decimal('11000.00'))

    def test_billed_trip_cannot_change_financial_fields_or_party(self):
        """Test that modifying financial fields or party on a billed trip raises ValidationError"""
        from django.core.exceptions import ValidationError
        trip = Trip.objects.create(
            vehicle=self.owned_vehicle,
            party=self.debtor,
            route=self.route_none,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )

        # Create Bill and attach Trip
        bill = Bill.objects.create(
            issuer=self.account,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP
        )
        bill.trips.add(trip)
        trip.refresh_from_db()
        self.assertTrue(trip.is_billed)

        # Attempt to change rate_per_ton
        trip.rate_per_ton = Decimal('12000.00')
        with self.assertRaises(ValidationError):
            trip.clean()
        with self.assertRaises(ValidationError):
            trip.save()

        # Attempt to change party
        trip.refresh_from_db()
        other_party = Party.objects.create(name='Other Party')
        trip.party = other_party
        with self.assertRaises(ValidationError):
            trip.clean()
        with self.assertRaises(ValidationError):
            trip.save()

