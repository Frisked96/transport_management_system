from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User, Permission
from django.utils import timezone
from datetime import timedelta
from documents.models import Document, document_upload_path
from fleet.models import Vehicle, MaintenanceRecord
from drivers.models import Driver
from documents.context_processors import document_alerts

class DocumentLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='doc_operator', password='password123')
        self.vehicle = Vehicle.objects.create(registration_plate='MH 12 CD 3333')
        self.driver = Driver.objects.create(
            user=self.user,
            employee_id='EMP-777',
            license_number='DL99999'
        )

    def test_document_expiry_and_never_expires_logic(self):
        """Test is_expired and days_until_expiry logic across past, future, and never-expires docs"""
        today = timezone.now().date()

        # 1. Expired document
        doc_expired = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Fitness Certificate',
            expiry_date=today - timedelta(days=10)
        )
        self.assertTrue(doc_expired.is_expired)
        self.assertEqual(doc_expired.days_until_expiry, -10)

        # 2. Future document
        doc_future = Document.objects.create(
            vehicle=self.vehicle,
            document_name='National Permit',
            expiry_date=today + timedelta(days=25)
        )
        self.assertFalse(doc_future.is_expired)
        self.assertEqual(doc_future.days_until_expiry, 25)

        # 3. Document marked never_expires (even if date is in past)
        doc_permanent = Document.objects.create(
            driver=self.driver,
            document_name='Aadhaar Card',
            expiry_date=today - timedelta(days=365),
            never_expires=True
        )
        self.assertFalse(doc_permanent.is_expired)
        self.assertIsNone(doc_permanent.days_until_expiry)

        # 4. Document with null expiry
        doc_no_date = Document.objects.create(
            driver=self.driver,
            document_name='Joining Agreement',
            expiry_date=None
        )
        self.assertFalse(doc_no_date.is_expired)
        self.assertIsNone(doc_no_date.days_until_expiry)

    def test_document_upload_path_sanitization(self):
        """Test document_upload_path formats and sanitizes vehicle and driver identifiers"""
        doc_veh = Document(vehicle=self.vehicle)
        path_veh = document_upload_path(doc_veh, 'fitness.pdf')
        # Spaces replaced by underscore
        self.assertIn('MH_12_CD_3333', path_veh)
        self.assertTrue(path_veh.endswith('fitness.pdf'))

        doc_drv = Document(driver=self.driver)
        path_drv = document_upload_path(doc_drv, 'license.jpg')
        self.assertIn('EMP-777', path_drv)
        self.assertTrue(path_drv.endswith('license.jpg'))


class DocumentAlertsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.manager = User.objects.create_user(username='fleet_lead', password='password123')
        perm = Permission.objects.get(codename='can_view_manager_dashboard')
        self.manager.user_permissions.add(perm)

        self.regular_user = User.objects.create_user(username='driver_john', password='password123')
        self.vehicle = Vehicle.objects.create(registration_plate='MH 12 AL 8888', current_odometer=30000)

    def test_document_alerts_context_processor(self):
        """
        Test that document_alerts calculates expiring docs (<=30 days), 
        expired docs, and overdue maintenance records efficiently.
        """
        today = timezone.now().date()

        # 1. Expiring document (in 15 days)
        doc_expiring = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Insurance Policy',
            expiry_date=today + timedelta(days=15)
        )

        # 2. Expired document (5 days ago)
        doc_expired = Document.objects.create(
            vehicle=self.vehicle,
            document_name='PUC Certificate',
            expiry_date=today - timedelta(days=5)
        )

        # 3. Active document far in future (90 days away - should NOT trigger alert)
        doc_safe = Document.objects.create(
            vehicle=self.vehicle,
            document_name='State Permit',
            expiry_date=today + timedelta(days=90)
        )

        # 4. Overdue maintenance record (odometer threshold exceeded)
        maint_due = MaintenanceRecord.objects.create(
            vehicle=self.vehicle,
            name='Brake Pad Inspection',
            expiry_km=25000,
            is_completed=False
        )

        # Request as Manager: should receive all 3 alerts
        req_manager = self.factory.get('/')
        req_manager.user = self.manager
        alerts = document_alerts(req_manager)

        self.assertEqual(alerts['total_alerts'], 3)
        self.assertEqual(alerts['expiring_docs'].count(), 1)
        self.assertEqual(alerts['expiring_docs'].first().id, doc_expiring.id)
        self.assertEqual(alerts['expired_docs'].count(), 1)
        self.assertEqual(alerts['expired_docs'].first().id, doc_expired.id)
        self.assertEqual(alerts['due_maintenance'].count(), 1)
        self.assertEqual(alerts['due_maintenance'].first().id, maint_due.id)

        # Request as Regular User: should receive empty dict
        req_regular = self.factory.get('/')
        req_regular.user = self.regular_user
        self.assertEqual(document_alerts(req_regular), {})

        # Request as Unauthenticated: should receive empty dict
        from django.contrib.auth.models import AnonymousUser
        req_anon = self.factory.get('/')
        req_anon.user = AnonymousUser()
        self.assertEqual(document_alerts(req_anon), {})
