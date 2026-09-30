from decimal import Decimal
from datetime import timedelta
from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from trips.models import Route, Trip, TripQuerySet
from fleet.models import Vehicle
from ledger.models import Party, CompanyAccount, TransactionCategory, FinancialRecord, Bill, BillTrip


class RouteModelTests(TestCase):
    def test_route_str_and_choices(self):
        """Verify Route __str__ returns '{pickup} to {delivery} ({Route Type Display})'."""
        route_local = Route.objects.create(
            pickup_location='Ahmedabad',
            delivery_location='Surat',
            route_type=Route.ROUTE_TYPE_LOCAL,
            default_rate=Decimal('850.00')
        )
        self.assertEqual(str(route_local), 'Ahmedabad to Surat (Local (GST))')

        route_intra = Route.objects.create(
            pickup_location='Ahmedabad',
            delivery_location='Jaipur',
            route_type=Route.ROUTE_TYPE_INTRA,
            default_rate=Decimal('1800.00')
        )
        self.assertEqual(str(route_intra), 'Ahmedabad to Jaipur (Intra/Interstate (IGST))')

    def test_route_unique_together_constraint(self):
        """Verify Route enforces uniqueness on ['pickup_location', 'delivery_location', 'route_type']."""
        Route.objects.create(
            pickup_location='Pune',
            delivery_location='Nagpur',
            route_type=Route.ROUTE_TYPE_LOCAL
        )
        with self.assertRaises(Exception):
            Route.objects.create(
                pickup_location='Pune',
                delivery_location='Nagpur',
                route_type=Route.ROUTE_TYPE_LOCAL
            )


class TripNumberingModelTests(TestCase):
    def setUp(self):
        self.party = Party.objects.create(name='Trip Logistics Client', party_type=Party.TYPE_DEBTOR)
        self.veh1 = Vehicle.objects.create(registration_plate='MH 12 AA 1001')
        self.veh2 = Vehicle.objects.create(registration_plate='MH 12 BB 2002')

    def test_trip_number_auto_generation_sequence(self):
        """Verify Trip generates sequence-based trip_number format '{reg_plate}-{count}'."""
        trip1 = Trip.objects.create(
            vehicle=self.veh1,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        trip2 = Trip.objects.create(
            vehicle=self.veh1,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.assertTrue(trip1.trip_number.startswith('MH 12 AA 1001-'))
        self.assertTrue(trip2.trip_number.startswith('MH 12 AA 1001-'))
        
        # Verify sequential suffixes
        suffix1 = int(trip1.trip_number.split('-')[-1])
        suffix2 = int(trip2.trip_number.split('-')[-1])
        self.assertEqual(suffix2, suffix1 + 1)

    def test_trip_number_update_on_vehicle_change(self):
        """Verify changing a trip's vehicle regenerates the trip_number with the new vehicle plate."""
        trip = Trip.objects.create(
            vehicle=self.veh1,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('15000.00')
        )
        self.assertIn('MH 12 AA 1001', trip.trip_number)

        # Reassign to veh2
        trip.vehicle = self.veh2
        trip.save()
        trip.refresh_from_db()
        self.assertIn('MH 12 BB 2002', trip.trip_number)

    def test_trip_number_manual_prefix_correction(self):
        """Verify trip_number prefix is corrected if vehicle plate changed without resetting suffix."""
        trip = Trip.objects.create(
            vehicle=self.veh1,
            party=self.party,
            trip_number='OLD-PLATE-99',
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        trip.save()
        self.assertEqual(trip.trip_number, 'MH 12 AA 1001-99')


class TripFinancialModelTests(TestCase):
    def setUp(self):
        self.account = CompanyAccount.objects.create(name='Operating Firm AC')
        self.party = Party.objects.create(name='Apex Enterprises', party_type=Party.TYPE_DEBTOR)
        self.vendor = Party.objects.create(name='Fleet Owner Vendor', party_type=Party.TYPE_CREDITOR)
        self.vehicle = Vehicle.objects.create(registration_plate='MH 14 TR 7001')
        self.attached_veh = Vehicle.objects.create(
            registration_plate='MH 14 AT 8002',
            ownership=Vehicle.OWNERSHIP_ATTACHED,
            vendor=self.vendor
        )
        self.route_gst = Route.objects.create(
            pickup_location='Pune',
            delivery_location='Mumbai',
            route_type=Route.ROUTE_TYPE_LOCAL
        )
        self.route_igst = Route.objects.create(
            pickup_location='Pune',
            delivery_location='Bengaluru',
            route_type=Route.ROUTE_TYPE_INTRA
        )
        self.route_none = Route.objects.create(
            pickup_location='Yard A',
            delivery_location='Yard B',
            route_type=Route.ROUTE_TYPE_NONE
        )

    def test_revenue_calculation_modes(self):
        """Verify revenue calculation for REVENUE_PER_TON and REVENUE_FIXED modes."""
        # Per ton mode
        trip_ton = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_PER_TON,
            weight=Decimal('30.00'),
            rate_per_ton=Decimal('1250.00')
        )
        self.assertEqual(trip_ton.revenue, Decimal('37500.00'))

        # Fixed mode
        trip_fixed = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            weight=Decimal('30.00'),
            rate_per_ton=Decimal('28000.00')
        )
        self.assertEqual(trip_fixed.revenue, Decimal('28000.00'))

        # Missing values fallback
        trip_zero = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_PER_TON,
            weight=None,
            rate_per_ton=Decimal('1000.00')
        )
        self.assertEqual(trip_zero.revenue, Decimal('0.00'))

    def test_route_location_and_gst_snapshot(self):
        """Verify route locations and GST type are snapshotted at creation and remain immutable."""
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            route=self.route_igst,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('20000.00')
        )
        self.assertEqual(trip.pickup_location, 'Pune')
        self.assertEqual(trip.delivery_location, 'Bengaluru')
        self.assertEqual(trip.gst_type_snapshot, Bill.GST_TYPE_IGST)
        self.assertEqual(trip.gst_type, Bill.GST_TYPE_IGST)

        # Snapshot principle: changing route doesn't alter existing trip snapshot
        self.route_igst.route_type = Route.ROUTE_TYPE_NONE
        self.route_igst.save()
        trip.refresh_from_db()
        self.assertEqual(trip.gst_type_snapshot, Bill.GST_TYPE_IGST)

    def test_gst_amount_calculation_and_total_revenue(self):
        """Verify 18% default on taxable unbilled trips and 0% on non-GST routes."""
        trip_gst = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            route=self.route_gst,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.assertEqual(trip_gst.gst_amount, Decimal('1800.00')) # 18% of 10,000
        self.assertEqual(trip_gst.total_revenue, Decimal('11800.00'))

        trip_none = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            route=self.route_none,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.assertEqual(trip_none.gst_amount, Decimal('0.00'))
        self.assertEqual(trip_none.total_revenue, Decimal('10000.00'))

    def test_payment_status_cached_lifecycle(self):
        """Verify payment_status progresses from Unpaid -> Partially Paid -> Paid as payments are recorded."""
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            route=self.route_none,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.assertEqual(trip.payment_status, Trip.PAYMENT_STATUS_UNPAID)
        self.assertEqual(trip.outstanding_balance, Decimal('10000.00'))

        cat, _ = TransactionCategory.objects.get_or_create(name='Trip Payment', defaults={'type': 'Income'})
        
        # 1. Partial payment (3000)
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=cat,
            amount=Decimal('3000.00'),
            associated_trip=trip
        )
        trip.refresh_from_db()
        self.assertEqual(trip.amount_received, Decimal('3000.00'))
        self.assertEqual(trip.outstanding_balance, Decimal('7000.00'))
        self.assertEqual(trip.payment_status, Trip.PAYMENT_STATUS_PARTIAL)

        # 2. Remaining payment (7000)
        FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.party,
            category=cat,
            amount=Decimal('7000.00'),
            associated_trip=trip
        )
        trip.refresh_from_db()
        self.assertEqual(trip.amount_received, Decimal('10000.00'))
        self.assertEqual(trip.outstanding_balance, Decimal('0.00'))
        self.assertEqual(trip.payment_status, Trip.PAYMENT_STATUS_PAID)

    def test_attached_vehicle_vendor_hire_accrual(self):
        """Verify trips on attached vehicles auto-create Lorry Hire invoice record."""
        trip = Trip.objects.create(
            vehicle=self.attached_veh,
            party=self.party,
            route=self.route_none,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('25000.00'),
            vendor_hire_amount=Decimal('18000.00')
        )
        hire_rec = FinancialRecord.objects.filter(
            associated_trip=trip,
            category__name='Lorry Hire',
            party=self.vendor,
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).first()

        self.assertIsNotNone(hire_rec)
        self.assertEqual(hire_rec.amount, Decimal('18000.00'))


class TripImmutabilityModelTests(TestCase):
    def setUp(self):
        self.account = CompanyAccount.objects.create(name='Firm Account')
        self.party1 = Party.objects.create(name='Party Alpha', party_type=Party.TYPE_DEBTOR)
        self.party2 = Party.objects.create(name='Party Beta', party_type=Party.TYPE_DEBTOR)
        self.vehicle = Vehicle.objects.create(registration_plate='MH 12 IM 9001')
        self.trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party1,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('20000.00')
        )
        # Create a bill and attach the trip
        self.bill = Bill.objects.create(
            issuer=self.account,
            party=self.party1,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP
        )
        BillTrip.objects.create(bill=self.bill, trip=self.trip)

    def test_billed_trip_clean_prevents_financial_modification(self):
        """Verify modifying financial fields or party on a billed trip raises ValidationError."""
        self.trip.refresh_from_db()
        self.assertTrue(self.trip.is_billed)
        self.assertEqual(self.trip.associated_bill, self.bill)

        # Attempt to change rate_per_ton
        self.trip.rate_per_ton = Decimal('25000.00')
        with self.assertRaises(ValidationError):
            self.trip.clean()

        # Reset rate and attempt to change party
        self.trip.rate_per_ton = Decimal('20000.00')
        self.trip.party = self.party2
        with self.assertRaises(ValidationError):
            self.trip.save()

    def test_is_billed_and_associated_bill_properties(self):
        """Verify is_billed and associated_bill properties work accurately."""
        self.trip.refresh_from_db()
        self.assertTrue(self.trip.is_billed)
        self.assertEqual(self.trip.associated_bill, self.bill)

        # Unbilled trip
        unbilled_trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party1,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('5000.00')
        )
        self.assertFalse(unbilled_trip.is_billed)
        self.assertIsNone(unbilled_trip.associated_bill)


class TripQuerySetModelTests(TestCase):
    def setUp(self):
        self.party = Party.objects.create(name='QuerySet Party', party_type=Party.TYPE_DEBTOR)
        self.vehicle = Vehicle.objects.create(registration_plate='MH 12 QS 5005')
        self.trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('15000.00')
        )

    def test_with_payment_info_annotations(self):
        """Verify TripQuerySet.with_payment_info populates annotated fields."""
        annotated_trip = Trip.objects.with_payment_info().get(pk=self.trip.pk)
        self.assertEqual(annotated_trip.annotated_revenue, self.trip.revenue_cached)
        self.assertEqual(annotated_trip.annotated_total_revenue, self.trip.total_revenue_cached)
        self.assertEqual(annotated_trip.annotated_status, self.trip.payment_status_cached)

    def test_with_billing_info_annotations(self):
        """Verify TripQuerySet.with_billing_info populates annotated_is_billed and annotated_gst_type."""
        annotated_trip = Trip.objects.with_billing_info().get(pk=self.trip.pk)
        self.assertFalse(annotated_trip.annotated_is_billed)
        self.assertEqual(annotated_trip.annotated_gst_type, 'GST')


class TripDeletionModelTests(TestCase):
    def setUp(self):
        self.party = Party.objects.create(name='Protect Party', party_type=Party.TYPE_DEBTOR)
        self.vehicle = Vehicle.objects.create(registration_plate='MH 12 DL 4004')
        self.trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )

    def test_trip_delete_sets_flag(self):
        """Verify Trip.delete sets _is_being_deleted flag to prevent signal recursion."""
        trip = self.trip
        trip.delete()
        self.assertTrue(trip._is_being_deleted)
        self.assertFalse(Trip.objects.filter(pk=trip.pk).exists())

    def test_party_protected_on_trip_delete(self):
        """Verify party with existing trips cannot be deleted due to PROTECT foreign key constraint."""
        with self.assertRaises(Exception):
            self.party.delete()
