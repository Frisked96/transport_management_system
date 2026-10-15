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
    FinancialRecord, Party, TransactionCategory, CompanyAccount, Bill, BillTrip
)

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




class RouteNetworkDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='dashboard_admin',
            email='dashadmin@example.com',
            password='password123'
        )
        self.unauthorized_user = User.objects.create_user(
            username='plain_user',
            password='password123'
        )
        self.vehicle = Vehicle.objects.create(
            registration_plate='MH 12 CD 5678',
            status='Active'
        )
        self.party1 = Party.objects.create(name='Alpha Cement')
        self.party2 = Party.objects.create(name='Beta Steel')

        # Create routes
        self.route_local = Route.objects.create(
            pickup_location='Pune',
            delivery_location='Mumbai',
            route_type=Route.ROUTE_TYPE_LOCAL,
            default_rate=Decimal('1200.00')
        )
        self.route_intra = Route.objects.create(
            pickup_location='Nagpur',
            delivery_location='Hyderabad',
            route_type=Route.ROUTE_TYPE_INTRA,
            default_rate=Decimal('2500.00')
        )
        self.route_dormant = Route.objects.create(
            pickup_location='Nashik',
            delivery_location='Surat',
            route_type=Route.ROUTE_TYPE_LOCAL,
            default_rate=Decimal('1500.00')
        )

        today = timezone.now().date()

        # Trips on Route 1
        Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party1,
            route=self.route_local,
            date=today,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1250.00'),
            revenue_type=Trip.REVENUE_PER_TON
        )
        Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party1,
            route=self.route_local,
            date=today - timedelta(days=10),
            weight=Decimal('30.00'),
            rate_per_ton=Decimal('1300.00'),
            revenue_type=Trip.REVENUE_PER_TON
        )

        # Trip on Route 2
        Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party2,
            route=self.route_intra,
            date=today - timedelta(days=40),
            weight=Decimal('25.00'),
            rate_per_ton=Decimal('2400.00'),
            revenue_type=Trip.REVENUE_PER_TON
        )

    def test_anonymous_redirected(self):
        resp = self.client.get('/routes/dashboard/')
        self.assertEqual(resp.status_code, 302)

    def test_unauthorized_user_forbidden(self):
        self.client.force_login(self.unauthorized_user)
        resp = self.client.get('/routes/dashboard/')
        self.assertEqual(resp.status_code, 403)

    def test_authorized_user_success(self):
        self.client.force_login(self.user)
        resp = self.client.get('/routes/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'trips/routes_dashboard.html')

        # Check KPI context
        self.assertEqual(resp.context['all_routes_count'], 3)
        self.assertEqual(resp.context['active_routes_count'], 2)
        self.assertEqual(resp.context['inactive_routes_count'], 1)
        self.assertEqual(resp.context['total_trips'], 3)
        self.assertEqual(resp.context['total_weight'], Decimal('75.00'))

        # Check leaderboards
        self.assertEqual(len(resp.context['top_by_revenue']), 2)
        self.assertEqual(len(resp.context['inactive_routes_list']), 1)
        self.assertEqual(resp.context['inactive_routes_list'][0]['route'], self.route_dormant)

    def test_period_filter_30d(self):
        self.client.force_login(self.user)
        resp = self.client.get('/routes/dashboard/?period=30d')
        self.assertEqual(resp.status_code, 200)
        # In last 30 days, only route_local had trips (2 trips, 50 MT)
        self.assertEqual(resp.context['total_trips'], 2)
        self.assertEqual(resp.context['total_weight'], Decimal('50.00'))
        self.assertEqual(resp.context['active_routes_count'], 1)

    def test_route_type_filter(self):
        self.client.force_login(self.user)
        resp = self.client.get(f'/routes/dashboard/?route_type={Route.ROUTE_TYPE_INTRA}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_trips'], 1)
        self.assertEqual(resp.context['total_weight'], Decimal('25.00'))

    def test_party_filter(self):
        self.client.force_login(self.user)
        resp = self.client.get(f'/routes/dashboard/?party={self.party1.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_trips'], 2)


class RouteUpdateViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='admin_routes',
            email='admin_routes@example.com',
            password='password123'
        )
        self.vehicle = Vehicle.objects.create(
            registration_plate='MH 12 UV 3344',
            status='Active'
        )
        self.party = Party.objects.create(name='Test Party', party_type=Party.TYPE_DEBTOR)
        self.route = Route.objects.create(
            pickup_location='Nasik',
            delivery_location='Pune',
            route_type=Route.ROUTE_TYPE_LOCAL,
            default_rate=Decimal('1000.00')
        )
        self.trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.party,
            route=self.route,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('10000.00')
        )

    def test_route_update_view_syncs_unbilled_trips(self):
        """Updating route_type in RouteUpdateView updates unbilled trips using that route."""
        self.assertEqual(self.trip.gst_type_snapshot, Bill.GST_TYPE_GST)
        self.assertEqual(self.trip.gst_amount, Decimal('1800.00'))
        self.assertEqual(self.trip.total_revenue, Decimal('11800.00'))

        self.client.force_login(self.user)
        resp = self.client.post(reverse('route-update', kwargs={'pk': self.route.pk}), {
            'pickup_location': 'Nasik',
            'delivery_location': 'Pune',
            'route_type': Route.ROUTE_TYPE_NONE,
            'default_rate': '1000.00'
        })
        self.assertEqual(resp.status_code, 302)

        self.trip.refresh_from_db()
        self.assertEqual(self.trip.gst_type_snapshot, Bill.GST_TYPE_NONE)
        self.assertEqual(self.trip.gst_type, Bill.GST_TYPE_NONE)
        self.assertEqual(self.trip.gst_amount, Decimal('0.00'))
        self.assertEqual(self.trip.gst_amount_cached, Decimal('0.00'))
        self.assertEqual(self.trip.total_revenue, Decimal('10000.00'))
        self.assertEqual(self.trip.total_revenue_cached, Decimal('10000.00'))

        # Now update back to Local (GST)
        resp = self.client.post(reverse('route-update', kwargs={'pk': self.route.pk}), {
            'pickup_location': 'Nasik',
            'delivery_location': 'Pune',
            'route_type': Route.ROUTE_TYPE_LOCAL,
            'default_rate': '1000.00'
        })
        self.assertEqual(resp.status_code, 302)

        self.trip.refresh_from_db()
        self.assertEqual(self.trip.gst_type_snapshot, Bill.GST_TYPE_GST)
        self.assertEqual(self.trip.gst_type, Bill.GST_TYPE_GST)
        self.assertEqual(self.trip.gst_amount, Decimal('1800.00'))
        self.assertEqual(self.trip.gst_amount_cached, Decimal('1800.00'))
        self.assertEqual(self.trip.total_revenue, Decimal('11800.00'))
        self.assertEqual(self.trip.total_revenue_cached, Decimal('11800.00'))




