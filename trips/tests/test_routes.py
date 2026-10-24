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


class RouteAnalyticsOwnershipAndTollTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='route_analyst',
            email='analyst@example.com',
            password='password123'
        )
        self.client.force_login(self.user)

        self.party = Party.objects.create(name='Steel Corp', party_type=Party.TYPE_DEBTOR)

        self.route = Route.objects.create(
            pickup_location='Ghugus',
            delivery_location='Jalna',
            route_type=Route.ROUTE_TYPE_LOCAL,
            default_rate=Decimal('2000.00')
        )

        # Company-owned vehicle
        self.veh_owned = Vehicle.objects.create(
            registration_plate='MH 40 DC 9982',
            ownership=Vehicle.OWNERSHIP_OWNED,
            status=Vehicle.STATUS_ACTIVE
        )

        # Market/Attached vehicle
        self.veh_attached = Vehicle.objects.create(
            registration_plate='CG 04 AT 1234',
            ownership=Vehicle.OWNERSHIP_ATTACHED,
            status=Vehicle.STATUS_ACTIVE
        )

        from ledger.models import TollAccount, TollEntry

        self.toll_account = TollAccount.objects.create(name='FASTag ICICI', provider='ICICI')

        # Trips on Route:
        # Trip 1: Owned, Date Day 1
        self.trip_owned_1 = Trip.objects.create(
            vehicle=self.veh_owned,
            party=self.party,
            route=self.route,
            date=timezone.now().date() - timedelta(days=10),
            trip_number='TRIP-OWN-01',
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('50000.00')
        )

        # Toll for Trip 1
        self.toll_1 = TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.veh_owned,
            date=timezone.now().date() - timedelta(days=9),
            amount=Decimal('6000.00'),
            toll_plaza='Nagewadi Plaza'
        )

        # Trip 2: Owned, Date Day 5
        self.trip_owned_2 = Trip.objects.create(
            vehicle=self.veh_owned,
            party=self.party,
            route=self.route,
            date=timezone.now().date() - timedelta(days=5),
            trip_number='TRIP-OWN-02',
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('52000.00')
        )

        # Toll for Trip 2
        self.toll_2 = TollEntry.objects.create(
            toll_account=self.toll_account,
            vehicle=self.veh_owned,
            date=timezone.now().date() - timedelta(days=4),
            amount=Decimal('4000.00'),
            toll_plaza='Khed Plaza'
        )

        # Trip 3: Attached (Market), Date Day 3
        self.trip_attached = Trip.objects.create(
            vehicle=self.veh_attached,
            party=self.party,
            route=self.route,
            date=timezone.now().date() - timedelta(days=7),
            trip_number='TRIP-ATT-01',
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('48000.00')
        )

    def test_route_analytics_filter_company_owned(self):
        """Route analytics with ownership=owned shows only company fleet trips and tolls."""
        url = reverse('route-dashboard', kwargs={'pk': self.route.pk})
        resp = self.client.get(url, {'ownership': 'owned'})
        self.assertEqual(resp.status_code, 200)

        # Total trips should be 2 (owned only)
        self.assertEqual(resp.context['total_trips'], 2)
        self.assertEqual(resp.context['selected_ownership'], 'owned')

        # Toll metrics
        # Total toll: 6000 + 4000 = 10000
        # Avg toll: 10000 / 2 = 5000
        self.assertEqual(resp.context['total_toll_expense'], Decimal('10000.00'))
        self.assertEqual(resp.context['avg_toll_per_trip'], Decimal('5000.00'))
        self.assertEqual(resp.context['owned_trips_count'], 2)
        self.assertEqual(resp.context['trips_with_tolls_count'], 2)

        # Recent trips should only contain owned trips
        trips = list(resp.context['recent_trips'])
        self.assertEqual(len(trips), 2)
        self.assertIn(self.trip_owned_1, trips)
        self.assertIn(self.trip_owned_2, trips)
        self.assertNotIn(self.trip_attached, trips)

        # HTML assertions
        self.assertContains(resp, 'Avg Toll Expense')
        self.assertContains(resp, '₹5,000')

    def test_route_analytics_filter_attached(self):
        """Route analytics with ownership=attached shows only attached trips."""
        url = reverse('route-dashboard', kwargs={'pk': self.route.pk})
        resp = self.client.get(url, {'ownership': 'attached'})
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(resp.context['total_trips'], 1)
        self.assertEqual(resp.context['selected_ownership'], 'attached')
        trips = list(resp.context['recent_trips'])
        self.assertEqual(len(trips), 1)
        self.assertIn(self.trip_attached, trips)
        self.assertNotIn(self.trip_owned_1, trips)

    def test_route_analytics_all_vehicles(self):
        """Route analytics with ownership=all shows all 3 trips, but toll metrics only average owned trips."""
        url = reverse('route-dashboard', kwargs={'pk': self.route.pk})
        resp = self.client.get(url, {'ownership': 'all'})
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(resp.context['total_trips'], 3)
        # Toll stats still reflect owned fleet trips on that route
        self.assertEqual(resp.context['total_toll_expense'], Decimal('10000.00'))
        self.assertEqual(resp.context['avg_toll_per_trip'], Decimal('5000.00'))
        self.assertEqual(resp.context['owned_trips_count'], 2)

        # Check trip-level annotated toll
        trips_by_id = {t.id: t for t in resp.context['recent_trips']}
        self.assertEqual(trips_by_id[self.trip_owned_1.id].annotated_toll_expense, Decimal('6000.00'))
        self.assertEqual(trips_by_id[self.trip_owned_2.id].annotated_toll_expense, Decimal('4000.00'))
        self.assertEqual(trips_by_id[self.trip_attached.id].annotated_toll_expense, Decimal('0.00'))

    def test_routes_network_dashboard_ownership_filter(self):
        """Network dashboard respects ownership=owned filter."""
        url = reverse('routes-dashboard')
        resp = self.client.get(url, {'ownership': 'owned'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['selected_ownership'], 'owned')
        self.assertEqual(resp.context['total_trips'], 2)





