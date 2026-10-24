from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.contrib.auth.models import User, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.models import LogEntry, DELETION
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.urls import reverse
from trips.models import Route, Trip, TripQuerySet
from fleet.models import Vehicle
from ledger.models import (
    FinancialRecord, Party, TransactionCategory, CompanyAccount, Bill, BillTrip,
    TollAccount, TollEntry
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

        # Snapshot principle: changing route directly doesn't alter existing trip snapshot without save
        self.route_igst.route_type = Route.ROUTE_TYPE_NONE
        self.route_igst.save()
        trip.refresh_from_db()
        self.assertEqual(trip.gst_type_snapshot, Bill.GST_TYPE_IGST)

    def test_trip_route_change_updates_gst_components_and_tags(self):
        """Verify changing route on unbilled trip updates GST snapshot, GST amount, and total revenue."""
        # 1. Create trip with GST route
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            route=self.route_gst,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )
        self.assertEqual(trip.gst_type_snapshot, Bill.GST_TYPE_GST)
        self.assertEqual(trip.gst_type, Bill.GST_TYPE_GST)
        self.assertEqual(trip.gst_amount, Decimal('1800.00'))
        self.assertEqual(trip.gst_amount_cached, Decimal('1800.00'))
        self.assertEqual(trip.total_revenue, Decimal('11800.00'))
        self.assertEqual(trip.total_revenue_cached, Decimal('11800.00'))
        
        accrual = FinancialRecord.objects.get(associated_trip=trip, record_type=FinancialRecord.RECORD_TYPE_INVOICE)
        self.assertEqual(accrual.amount, Decimal('11800.00'))
        
        annotated = Trip.objects.with_billing_info().with_payment_info().get(pk=trip.pk)
        self.assertEqual(annotated.annotated_gst_type, 'GST')
        self.assertEqual(annotated.annotated_gst_amount, Decimal('1800.00'))

        # 2. Change route to Non-GST route
        trip.route = self.route_none
        trip.save()
        trip.refresh_from_db()

        self.assertEqual(trip.gst_type_snapshot, Bill.GST_TYPE_NONE)
        self.assertEqual(trip.gst_type, Bill.GST_TYPE_NONE)
        self.assertEqual(trip.gst_amount, Decimal('0.00'))
        self.assertEqual(trip.gst_amount_cached, Decimal('0.00'))
        self.assertEqual(trip.total_revenue, Decimal('10000.00'))
        self.assertEqual(trip.total_revenue_cached, Decimal('10000.00'))

        accrual.refresh_from_db()
        self.assertEqual(accrual.amount, Decimal('10000.00'))

        annotated = Trip.objects.with_billing_info().with_payment_info().get(pk=trip.pk)
        self.assertEqual(annotated.annotated_gst_type, 'NONE')
        self.assertEqual(annotated.annotated_gst_amount, Decimal('0.00'))

        # 3. Change route back to GST route
        trip.route = self.route_gst
        trip.save()
        trip.refresh_from_db()

        self.assertEqual(trip.gst_type_snapshot, Bill.GST_TYPE_GST)
        self.assertEqual(trip.gst_type, Bill.GST_TYPE_GST)
        self.assertEqual(trip.gst_amount, Decimal('1800.00'))
        self.assertEqual(trip.gst_amount_cached, Decimal('1800.00'))
        self.assertEqual(trip.total_revenue, Decimal('11800.00'))

        accrual.refresh_from_db()
        self.assertEqual(accrual.amount, Decimal('11800.00'))

        annotated = Trip.objects.with_billing_info().with_payment_info().get(pk=trip.pk)
        self.assertEqual(annotated.annotated_gst_type, 'GST')
        self.assertEqual(annotated.annotated_gst_amount, Decimal('1800.00'))

        # 4. Change route to IGST route
        trip.route = self.route_igst
        trip.save()
        trip.refresh_from_db()

        self.assertEqual(trip.gst_type_snapshot, Bill.GST_TYPE_IGST)
        self.assertEqual(trip.gst_type, Bill.GST_TYPE_IGST)
        self.assertEqual(trip.gst_amount, Decimal('1800.00'))
        self.assertEqual(trip.total_revenue, Decimal('11800.00'))

        annotated = Trip.objects.with_billing_info().with_payment_info().get(pk=trip.pk)
        self.assertEqual(annotated.annotated_gst_type, 'IGST')
        self.assertEqual(annotated.annotated_gst_amount, Decimal('1800.00'))

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


class TripTollIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='trip_admin', password='password123')
        self.client = Client()
        self.client.login(username='trip_admin', password='password123')

        self.party = Party.objects.create(name='Toll Test Party', party_type=Party.TYPE_DEBTOR)
        self.vehicle = Vehicle.objects.create(registration_plate='CG 04 PJ 8276')

        self.toll_account = TollAccount.objects.create(
            name='BlackBuck',
            provider='BlackBuck',
            opening_balance=Decimal('50000.00'),
            is_default=True
        )

        # Trip 1: 2026-10-10
        self.trip1 = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            date=timezone.datetime(2026, 10, 10).date(),
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('25000.00')
        )

        # Trip 2: 2026-10-15
        self.trip2 = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            date=timezone.datetime(2026, 10, 15).date(),
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('30000.00')
        )

        # Toll 1: During Trip 1 (2026-10-11 08:30)
        self.toll1 = TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.vehicle,
            date=timezone.datetime(2026, 10, 11).date(),
            time=timezone.datetime(2026, 10, 11, 8, 30).time(),
            amount=Decimal('550.00'),
            toll_plaza='Mathni - Maharashtra',
            transaction_reference='TXN-TOLL-01',
            created_by=self.user
        )

        # Toll 2: During Trip 1 (2026-10-14 16:45)
        self.toll2 = TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.vehicle,
            date=timezone.datetime(2026, 10, 14).date(),
            time=timezone.datetime(2026, 10, 14, 16, 45).time(),
            amount=Decimal('450.00'),
            toll_plaza='Arambha Toll Plaza',
            transaction_reference='TXN-TOLL-02',
            created_by=self.user
        )

        # Toll 3: During Trip 2 (2026-10-15)
        self.toll3 = TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.vehicle,
            date=timezone.datetime(2026, 10, 15).date(),
            amount=Decimal('600.00'),
            toll_plaza='Khed Toll Plaza',
            transaction_reference='TXN-TOLL-03',
            created_by=self.user
        )

    def test_get_next_trip(self):
        """Trip 1 should identify Trip 2 as its next trip; Trip 2 should have None."""
        self.assertEqual(self.trip1.get_next_trip(), self.trip2)
        self.assertIsNone(self.trip2.get_next_trip())

    def test_get_toll_entries_during_trip_period(self):
        """Trip 1 should have tolls from 10-10 to 10-15 (tolls 1 & 2); Trip 2 has toll 3."""
        trip1_tolls = list(self.trip1.get_toll_entries())
        self.assertEqual(len(trip1_tolls), 2)
        self.assertIn(self.toll1, trip1_tolls)
        self.assertIn(self.toll2, trip1_tolls)
        self.assertNotIn(self.toll3, trip1_tolls)
        self.assertEqual(self.trip1.total_toll_expense, Decimal('1000.00'))

        trip2_tolls = list(self.trip2.get_toll_entries())
        self.assertEqual(len(trip2_tolls), 1)
        self.assertIn(self.toll3, trip2_tolls)
        self.assertEqual(self.trip2.total_toll_expense, Decimal('600.00'))

    def test_trip_detail_view_renders_toll_dropdown(self):
        """Trip detail view should include toll entries, total tolls, and toll table in context and HTML."""
        response = self.client.get(reverse('trip-detail', kwargs={'pk': self.trip1.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['toll_count'], 2)
        self.assertEqual(response.context['total_tolls'], Decimal('1000.00'))
        self.assertEqual(response.context['next_trip'], self.trip2)

        # Assert HTML contains toll details and collapsed by default
        self.assertContains(response, 'Expenses')
        self.assertContains(response, 'Toll Expense')
        self.assertContains(response, 'Mathni - Maharashtra')
        self.assertContains(response, 'Arambha Toll Plaza')
        self.assertContains(response, '08:30 AM')
        self.assertContains(response, '04:45 PM')
        self.assertContains(response, '₹1,000')
        self.assertNotContains(response, '<details class="group" open>')


