from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from django.core.cache import cache
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
