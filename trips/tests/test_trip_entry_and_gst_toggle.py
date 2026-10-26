from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from trips.models import Trip, Route
from trips.forms import TripForm
from fleet.models import Vehicle
from ledger.models import Party, Bill, FinancialRecord, FinancialVoucher, CompanyAccount


class TripEntryAndGstToggleTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='admin_operator',
            email='operator@example.com',
            password='password123'
        )
        self.client.force_login(self.user)

        self.account = CompanyAccount.objects.create(
            name='Primary Operating Bank',
            current_balance_cached=Decimal('1000000.00')
        )

        self.vehicle = Vehicle.objects.create(
            registration_plate='MH 12 CD 9999',
            status=Vehicle.STATUS_ACTIVE,
            ownership=Vehicle.OWNERSHIP_OWNED
        )

        self.debtor = Party.objects.create(
            name='Test Client Debtor',
            party_type=Party.TYPE_DEBTOR
        )

        # Create unique routes
        self.route_mumbai_pune = Route.objects.create(
            pickup_location='Mumbai Hub',
            delivery_location='Pune Depot',
            route_type=Route.ROUTE_TYPE_LOCAL,
            default_rate=Decimal('1500.00')
        )
        self.route_raipur_nagpur = Route.objects.create(
            pickup_location='Raipur Plant',
            delivery_location='Nagpur Yard',
            route_type=Route.ROUTE_TYPE_INTRA,
            default_rate=Decimal('2200.00')
        )

    def test_route_unique_together_constraint_on_locations(self):
        """Verify duplicate routes with same pickup and delivery cannot be created, regardless of route_type."""
        with self.assertRaises((IntegrityError, Exception)):
            Route.objects.create(
                pickup_location='Mumbai Hub',
                delivery_location='Pune Depot',
                route_type=Route.ROUTE_TYPE_NONE,
                default_rate=Decimal('1200.00')
            )

    def test_gst_toggle_overrides_route_default_on_trip_creation(self):
        """Verify an operator can save a trip with Non-GST (NONE) or IGST on a Local route."""
        # 1. Create trip with Local route, but explicitly toggle Non-GST
        trip_nongst = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route_mumbai_pune,
            gst_type_snapshot=Bill.GST_TYPE_NONE,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('20000.00'),
            created_by=self.user
        )
        self.assertEqual(trip_nongst.gst_type_snapshot, 'NONE')
        self.assertEqual(trip_nongst.gst_type, 'NONE')
        self.assertEqual(trip_nongst.gst_amount, Decimal('0.00'))
        self.assertEqual(trip_nongst.total_revenue, Decimal('20000.00'))

        # Accrual record should have 0 GST
        accrual = FinancialRecord.objects.get(associated_trip=trip_nongst, record_type=FinancialRecord.RECORD_TYPE_INVOICE)
        self.assertEqual(accrual.amount, Decimal('20000.00'))

        # 2. Create trip with Local route, but explicitly toggle Interstate IGST
        trip_igst = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route_mumbai_pune,
            gst_type_snapshot=Bill.GST_TYPE_IGST,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('20000.00'),
            created_by=self.user
        )
        self.assertEqual(trip_igst.gst_type_snapshot, 'IGST')
        self.assertEqual(trip_igst.gst_type, 'IGST')
        self.assertEqual(trip_igst.gst_amount, Decimal('3600.00')) # 18% of 20,000
        self.assertEqual(trip_igst.total_revenue, Decimal('23600.00'))

        # 3. Create trip without passing gst_type_snapshot - should default to route's default
        trip_default = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route_mumbai_pune,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('20000.00'),
            created_by=self.user
        )
        self.assertEqual(trip_default.gst_type_snapshot, 'GST')
        self.assertEqual(trip_default.gst_amount, Decimal('3600.00'))

    def test_single_trip_creation_via_view(self):
        """Verify submitting a single trip via the consolidated entry view works seamlessly."""
        post_data = {
            'form-TOTAL_FORMS': '1',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            'form-0-date': '2026-10-26',
            'form-0-vehicle': str(self.vehicle.pk),
            'form-0-party': str(self.debtor.pk),
            'form-0-route': str(self.route_mumbai_pune.pk),
            'form-0-gst_type_snapshot': 'NONE',
            'form-0-revenue_type': Trip.REVENUE_PER_TON,
            'form-0-weight': '25.00',
            'form-0-rate_per_ton': '1000.00',
            'form-0-vendor_hire_amount': '0.00',
        }

        response = self.client.post(reverse('trip-create'), post_data)
        self.assertEqual(response.status_code, 302)

        trip = Trip.objects.filter(vehicle=self.vehicle).first()
        self.assertIsNotNone(trip)
        self.assertEqual(trip.gst_type_snapshot, 'NONE')
        self.assertEqual(trip.gst_amount, Decimal('0.00'))
        self.assertEqual(trip.revenue, Decimal('25000.00'))
        self.assertEqual(trip.total_revenue, Decimal('25000.00'))

    def test_multi_trip_creation_with_mixed_gst(self):
        """Verify submitting multiple trips in the consolidated grid generates correct accruals and batch voucher."""
        post_data = {
            'form-TOTAL_FORMS': '2',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            # Trip 1: GST
            'form-0-date': '2026-10-26',
            'form-0-vehicle': str(self.vehicle.pk),
            'form-0-party': str(self.debtor.pk),
            'form-0-route': str(self.route_mumbai_pune.pk),
            'form-0-gst_type_snapshot': 'GST',
            'form-0-revenue_type': Trip.REVENUE_FIXED,
            'form-0-weight': '0.00',
            'form-0-rate_per_ton': '10000.00',
            'form-0-vendor_hire_amount': '0.00',
            # Trip 2: Non-GST on same route!
            'form-1-date': '2026-10-26',
            'form-1-vehicle': str(self.vehicle.pk),
            'form-1-party': str(self.debtor.pk),
            'form-1-route': str(self.route_mumbai_pune.pk),
            'form-1-gst_type_snapshot': 'NONE',
            'form-1-revenue_type': Trip.REVENUE_FIXED,
            'form-1-weight': '0.00',
            'form-1-rate_per_ton': '10000.00',
            'form-1-vendor_hire_amount': '0.00',
        }

        response = self.client.post(reverse('trip-create'), post_data)
        self.assertEqual(response.status_code, 302)

        trips = list(Trip.objects.filter(vehicle=self.vehicle).order_by('id'))
        self.assertEqual(len(trips), 2)
        
        # Trip 1: Local GST
        self.assertEqual(trips[0].gst_type_snapshot, 'GST')
        self.assertEqual(trips[0].gst_amount, Decimal('1800.00'))
        self.assertEqual(trips[0].total_revenue, Decimal('11800.00'))

        # Trip 2: Non-GST
        self.assertEqual(trips[1].gst_type_snapshot, 'NONE')
        self.assertEqual(trips[1].gst_amount, Decimal('0.00'))
        self.assertEqual(trips[1].total_revenue, Decimal('10000.00'))

        # Batch voucher should link both accruals
        accrual1 = FinancialRecord.objects.get(associated_trip=trips[0], record_type=FinancialRecord.RECORD_TYPE_INVOICE)
        accrual2 = FinancialRecord.objects.get(associated_trip=trips[1], record_type=FinancialRecord.RECORD_TYPE_INVOICE)
        self.assertIsNotNone(accrual1.voucher)
        self.assertEqual(accrual1.voucher, accrual2.voucher)
        self.assertEqual(accrual1.voucher.total_settled_amount, Decimal('21800.00')) # 11,800 + 10,000

    def test_billed_trip_cannot_modify_gst_type(self):
        """Verify that attempting to change gst_type_snapshot on a billed trip raises a ValidationError."""
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route_mumbai_pune,
            gst_type_snapshot='GST',
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00'),
            created_by=self.user
        )

        # Create a customer bill for this trip
        bill = Bill.objects.create(
            bill_number='INV-TEST-001',
            bill_no=1,
            issuer=self.account,
            party=self.debtor,
            gst_type=Bill.GST_TYPE_GST,
            date=timezone.now().date(),
            created_by=self.user
        )
        bill.trips.add(trip)
        trip.refresh_from_db()
        self.assertTrue(trip.is_billed)

        # Attempt to change GST type on the billed trip
        trip.gst_type_snapshot = 'NONE'
        with self.assertRaises(ValidationError) as ctx:
            trip.clean()
        self.assertIn('already billed', str(ctx.exception))

    def test_backward_compatible_bulk_create_url(self):
        """Verify /trips/trip/bulk-create/ renders the consolidated view seamlessly."""
        response = self.client.get(reverse('trip-bulk-create'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('route_rates', response.context)
        self.assertIn('route_gst_types', response.context)
