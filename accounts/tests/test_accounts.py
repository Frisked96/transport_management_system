from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User, Group, Permission
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE
from accounts.models import UserProfile
from accounts.middleware import ActiveUserMiddleware, _thread_locals
from ledger.models import Sequence, CompanyAccount, Party
from fleet.models import Vehicle
from trips.models import Trip, Route

class UserProfileAndActivityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='test_operator', password='password123')

    def tearDown(self):
        cache.clear()
        if hasattr(_thread_locals, 'user'):
            del _thread_locals.user

    def test_user_profile_auto_created(self):
        """Test that UserProfile is automatically created when a User is created."""
        self.assertIsNotNone(self.user.profile)
        self.assertEqual(self.user.profile.user, self.user)
        self.assertIsNone(self.user.profile.last_seen)

    def test_active_user_middleware_throttled_db_write(self):
        """Test that ActiveUserMiddleware updates last_seen and throttles DB writes."""
        middleware = ActiveUserMiddleware(lambda _: None)
        
        request = self.factory.get('/trips/')
        request.user = self.user
        
        # 1. First request sets cache and updates DB
        middleware(request)
        self.user.profile.refresh_from_db()
        first_seen = self.user.profile.last_seen
        self.assertIsNotNone(first_seen)
        self.assertIsNotNone(cache.get(f'last-seen-{self.user.id}'))
        
        # 2. Second immediate request uses cache and skips DB write (throttled)
        middleware(request)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.last_seen, first_seen)

    def test_sequence_model_not_logged_in_activity(self):
        """Test that internal Sequence counter changes are NEVER logged in LogEntry."""
        _thread_locals.user = self.user
        
        # Increment sequence
        Sequence.next_value('test_counter')
        
        # Verify no LogEntry was created for sequence
        sequence_logs = LogEntry.objects.filter(content_type__model='sequence')
        self.assertEqual(sequence_logs.count(), 0)

    def test_cached_balance_updates_do_not_create_change_log(self):
        """
        Test that automated background updates to cached balance fields
        do not trigger 'Changed Company Account' or 'Changed Party' logs.
        """
        _thread_locals.user = self.user
        
        account = CompanyAccount.objects.create(name='Alpha Logistics Account')
        initial_log_count = LogEntry.objects.filter(object_id=str(account.pk)).count()
        self.assertEqual(initial_log_count, 1) # Addition log
        
        # Simulate background balance cache refresh
        account.current_balance_cached = 50000
        account.save(update_fields=['current_balance_cached'])
        
        # Should NOT have created a CHANGE log
        after_cache_count = LogEntry.objects.filter(object_id=str(account.pk)).count()
        self.assertEqual(after_cache_count, initial_log_count)

    def test_real_field_change_logs_formatted_diff(self):
        """
        Test that when a user actually changes fields on a model,
        a CHANGE log is created with exact field diffs.
        """
        _thread_locals.user = self.user
        
        account = CompanyAccount.objects.create(name='Original Name', bank_name='SBI')
        
        # Change bank_name
        account.bank_name = 'HDFC Bank'
        account.save()
        
        change_log = LogEntry.objects.filter(
            object_id=str(account.pk),
            action_flag=CHANGE
        ).order_by('-action_time').first()
        
        self.assertIsNotNone(change_log)
        self.assertIn('Bank Name: SBI → HDFC Bank', change_log.change_message)

    def test_user_profile_view_frontend_url(self):
        """
        Test that UserProfileView generates frontend_url pointing to dedicated
        front-end detail views instead of Django admin HTML forms.
        """
        _thread_locals.user = self.user
        
        vehicle = Vehicle.objects.create(
            registration_plate='RJ14-TEST-01',
            make_model='Tata Signa 4825.TK',
            purchase_date=timezone.now().date(),
            ownership='Owned'
        )
        
        self.client.login(username='test_operator', password='password123')
        response = self.client.get('/accounts/profile/')
        self.assertEqual(response.status_code, 200)
        
        activities = response.context['activities']
        vehicle_activity = next(
            (a for a in activities if a.object_id == str(vehicle.pk) and a.content_type.model == 'vehicle'),
            None
        )
        
        self.assertIsNotNone(vehicle_activity)
        # Should link to /fleet/vehicle/<pk>/
        self.assertEqual(vehicle_activity.frontend_url, f'/fleet/vehicle/{vehicle.pk}/')
        # Ensure it does not contain /admin/
        self.assertNotIn('/admin/', vehicle_activity.frontend_url)


class RoleManagementTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username='superadmin', password='password123')
        self.regular_user = User.objects.create_user(username='regular', password='password123')
        self.p_view_trip = Permission.objects.get(codename='view_trip')
        self.p_add_trip = Permission.objects.get(codename='add_trip')

    def test_regular_user_cannot_access_roles(self):
        """Regular users without superuser flag must receive 403 on role views."""
        self.client.login(username='regular', password='password123')
        for url in [reverse('role-list'), reverse('role-create')]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)

    def test_superuser_can_create_role(self):
        """Superusers can create a new role with specific permissions via front-end."""
        self.client.login(username='superadmin', password='password123')
        response = self.client.post(reverse('role-create'), {
            'name': 'Dispatcher',
            'permissions': [self.p_view_trip.pk, self.p_add_trip.pk]
        })
        self.assertRedirects(response, reverse('role-list'))
        
        role = Group.objects.filter(name='Dispatcher').first()
        self.assertIsNotNone(role)
        self.assertEqual(role.permissions.count(), 2)
        self.assertTrue(role.permissions.filter(codename='view_trip').exists())
        self.assertTrue(role.permissions.filter(codename='add_trip').exists())

    def test_superuser_can_update_role(self):
        """Superusers can update an existing role and modify permissions."""
        role = Group.objects.create(name='Accountant')
        role.permissions.add(self.p_view_trip)
        
        self.client.login(username='superadmin', password='password123')
        response = self.client.post(reverse('role-update', kwargs={'pk': role.pk}), {
            'name': 'Senior Accountant',
            'permissions': [self.p_add_trip.pk]
        })
        self.assertRedirects(response, reverse('role-list'))
        
        role.refresh_from_db()
        self.assertEqual(role.name, 'Senior Accountant')
        self.assertFalse(role.permissions.filter(codename='view_trip').exists())
        self.assertTrue(role.permissions.filter(codename='add_trip').exists())

    def test_superuser_can_delete_role(self):
        """Superusers can delete a role."""
        role = Group.objects.create(name='Temp Role')
        self.client.login(username='superadmin', password='password123')
        response = self.client.post(reverse('role-delete', kwargs={'pk': role.pk}))
        self.assertRedirects(response, reverse('role-list'))
        self.assertFalse(Group.objects.filter(pk=role.pk).exists())


class DirectURLPermissionEnforcementTests(TestCase):
    def setUp(self):
        self.unprivileged_user = User.objects.create_user(username='guest_staff', password='password123')
        self.client.login(username='guest_staff', password='password123')

    def test_route_create_blocked_without_permission(self):
        """Visiting /routes/create/ directly without trips.add_route redirects or returns 403."""
        response = self.client.get(reverse('route-create'))
        self.assertIn(response.status_code, [302, 403])
        if response.status_code == 302:
            self.assertIn('/accounts/login/', response.url)

    def test_party_views_blocked_without_permission(self):
        """Visiting /ledger/parties/ and /ledger/parties/create/ directly without permissions is blocked."""
        response_list = self.client.get(reverse('party-list'))
        self.assertIn(response_list.status_code, [302, 403])

        response_create = self.client.get(reverse('party-create'))
        self.assertIn(response_create.status_code, [302, 403])

    def test_document_list_blocked_without_permission(self):
        """Visiting /documents/ directly without documents.view_document is blocked."""
        response = self.client.get(reverse('document-list'))
        self.assertIn(response.status_code, [302, 403])

    def test_vehicle_list_blocked_without_permission(self):
        """Visiting /fleet/vehicles/ directly without fleet.view_vehicle is blocked."""
        response = self.client.get(reverse('vehicle-list'))
        self.assertIn(response.status_code, [302, 403])

    def test_financial_records_blocked_without_permission(self):
        """Visiting /ledger/records/ directly without ledger.can_view_financial_records is blocked."""
        response = self.client.get(reverse('financialrecord-list'))
        self.assertIn(response.status_code, [302, 403])

    def test_user_without_route_permissions_can_create_trips_but_cannot_access_routes(self):
        """User with trips.add_trip can create trips using existing routes, but cannot view/modify routes."""
        add_trip_perm = Permission.objects.get(codename='add_trip')
        view_trip_perm = Permission.objects.get(codename='view_trip')
        self.unprivileged_user.user_permissions.add(add_trip_perm, view_trip_perm)

        # 1. Routes endpoints must be blocked
        self.assertIn(self.client.get(reverse('route-list')).status_code, [302, 403])
        self.assertIn(self.client.get(reverse('route-create')).status_code, [302, 403])

        # 2. Navigation must not contain routes link
        resp_home = self.client.get(reverse('trip-list'))
        self.assertNotIn(f'href="{reverse("route-list")}"', resp_home.content.decode('utf-8'))

        # 3. Trip create form is accessible
        vehicle = Vehicle.objects.create(registration_plate='RJ 14 TC 0001', status=Vehicle.STATUS_ACTIVE)
        party = Party.objects.create(name='Test Logistics Party')
        route = Route.objects.create(pickup_location='Kolkata', delivery_location='Ranchi', default_rate=1200)

        resp_create = self.client.get(reverse('trip-create'))
        self.assertEqual(resp_create.status_code, 200)

        # 4. Trip can be successfully created with pre-existing route
        post_data = {
            'date': timezone.now().date().strftime('%Y-%m-%d'),
            'lr_no': 'TEST-LR-NO-ROUTE-PERM',
            'vehicle': vehicle.pk,
            'party': party.pk,
            'route': route.pk,
            'revenue_type': 'fixed',
            'weight': '20.00',
            'rate_per_ton': '1200.00',
            'vendor_hire_amount': '0.00',
        }
        post_resp = self.client.post(reverse('trip-create'), post_data)
        self.assertRedirects(post_resp, reverse('trip-list'))
        self.assertTrue(Trip.objects.filter(lr_no='TEST-LR-NO-ROUTE-PERM').exists())
