import os
import shutil
import tempfile
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from documents.models import Document, DocumentFile, document_upload_path
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

        # Request as Regular User: should also receive all 3 alerts
        req_regular = self.factory.get('/')
        req_regular.user = self.regular_user
        regular_alerts = document_alerts(req_regular)
        self.assertEqual(regular_alerts['total_alerts'], 3)
        self.assertEqual(regular_alerts['expiring_docs'].count(), 1)
        self.assertEqual(regular_alerts['expired_docs'].count(), 1)
        self.assertEqual(regular_alerts['due_maintenance'].count(), 1)
        self.assertFalse(regular_alerts['is_manager'])

        # Request as Unauthenticated: should receive empty dict
        from django.contrib.auth.models import AnonymousUser
        req_anon = self.factory.get('/')
        req_anon.user = AnonymousUser()
        self.assertEqual(document_alerts(req_anon), {})

    def test_regular_user_sees_notifications_in_template(self):
        """Verify that regular non-manager users see the notification bell and alerts in the UI with vehicle numbers."""
        today = timezone.now().date()
        Document.objects.create(
            vehicle=self.vehicle,
            document_name='Tax Token',
            expiry_date=today - timedelta(days=2)
        )
        self.client.login(username='driver_john', password='password123')
        response = self.client.get(reverse('trip-list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'fa-bell')
        self.assertContains(response, 'Alerts')
        self.assertContains(response, 'Tax Token')
        # Check that vehicle number is also displayed with the alert in notification
        self.assertContains(response, self.vehicle.registration_plate)

    def test_notifications_include_vehicle_and_driver_identifiers(self):
        """Verify expired/expiring docs in alerts dropdown display vehicle plate or driver name."""
        today = timezone.now().date()
        doc_veh = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Road Tax',
            document_number='RT-888',
            expiry_date=today - timedelta(days=3)
        )
        u_drv = User.objects.create_user(username='sarah_d', first_name='Sarah', last_name='Connor', password='password123')
        drv = Driver.objects.create(user=u_drv, employee_id='EMP-001')
        doc_drv = Document.objects.create(
            driver=drv,
            document_name='Commercial License',
            expiry_date=today + timedelta(days=5)
        )
        self.client.login(username='driver_john', password='password123')
        response = self.client.get(reverse('trip-list'))
        self.assertEqual(response.status_code, 200)
        # Vehicle alert should have vehicle registration plate
        self.assertContains(response, self.vehicle.registration_plate)
        self.assertContains(response, 'Road Tax')
        # Driver alert should have driver name
        self.assertContains(response, drv.name)
        self.assertContains(response, 'Commercial License')

    def test_direct_alert_mode_lists_documents_without_vehicle_expansion(self):
        """Test that in alert mode, documents are listed directly without requiring vehicle expansion."""
        today = timezone.now().date()
        veh_a = Vehicle.objects.create(registration_plate='MH 12 ALERT 1')
        veh_b = Vehicle.objects.create(registration_plate='MH 12 ALERT 2')

        doc_expired = Document.objects.create(
            vehicle=veh_a,
            document_name='National Permit',
            document_number='NP-100',
            expiry_date=today - timedelta(days=4)
        )
        doc_expiring = Document.objects.create(
            vehicle=veh_b,
            document_name='Insurance Policy',
            document_number='INS-200',
            expiry_date=today + timedelta(days=7)
        )

        view_perm = Permission.objects.get(codename='view_document')
        change_perm = Permission.objects.get(codename='change_document')
        self.regular_user.user_permissions.add(view_perm, change_perm)
        self.client.login(username='driver_john', password='password123')

        url = reverse('document-list')

        # 1. Active alerts: both documents returned directly in alerts context
        res_alerts = self.client.get(url, {'type': 'vehicles', 'alert': 'alerts'})
        self.assertEqual(res_alerts.status_code, 200)
        self.assertTrue(res_alerts.context['is_alert_mode'])
        alerts_list = list(res_alerts.context['alerts'])
        self.assertIn(doc_expired, alerts_list)
        self.assertIn(doc_expiring, alerts_list)

        # Directly contains vehicle registration plate and document name in the alert table
        self.assertContains(res_alerts, 'MH 12 ALERT 1')
        self.assertContains(res_alerts, 'National Permit')
        self.assertContains(res_alerts, 'MH 12 ALERT 2')
        self.assertContains(res_alerts, 'Insurance Policy')

        # Contains direct Renew action link
        renew_url_expired = reverse('document-renew', kwargs={'pk': doc_expired.pk})
        renew_url_expiring = reverse('document-renew', kwargs={'pk': doc_expiring.pk})
        self.assertContains(res_alerts, renew_url_expired)
        self.assertContains(res_alerts, renew_url_expiring)

        # 2. Expired filter: only doc_expired in alerts list
        res_exp = self.client.get(url, {'type': 'vehicles', 'alert': 'expired'})
        self.assertEqual(res_exp.status_code, 200)
        self.assertTrue(res_exp.context['is_alert_mode'])
        exp_list = list(res_exp.context['alerts'])
        self.assertIn(doc_expired, exp_list)
        self.assertNotIn(doc_expiring, exp_list)
        self.assertContains(res_exp, 'MH 12 ALERT 1')

        # 3. Expiring filter: only doc_expiring in alerts list
        res_soon = self.client.get(url, {'type': 'vehicles', 'alert': 'expiring'})
        self.assertEqual(res_soon.status_code, 200)
        self.assertTrue(res_soon.context['is_alert_mode'])
        soon_list = list(res_soon.context['alerts'])
        self.assertIn(doc_expiring, soon_list)
        self.assertNotIn(doc_expired, soon_list)
        self.assertContains(res_soon, 'MH 12 ALERT 2')

    def test_document_list_view_alert_filters_vehicles(self):
        """Test filtering document list view by alert statuses (expired, expiring, alerts) for vehicles."""
        today = timezone.now().date()
        veh_clean = Vehicle.objects.create(registration_plate='MH 12 CLEAN')
        veh_expired = Vehicle.objects.create(registration_plate='MH 12 EXPIRED')
        veh_expiring = Vehicle.objects.create(registration_plate='MH 12 EXPIRING')

        # Clean has far future doc
        Document.objects.create(vehicle=veh_clean, document_name='Fitness', expiry_date=today + timedelta(days=90))
        # Expired doc
        Document.objects.create(vehicle=veh_expired, document_name='Insurance', expiry_date=today - timedelta(days=5))
        # Expiring soon doc
        Document.objects.create(vehicle=veh_expiring, document_name='Permit', expiry_date=today + timedelta(days=10))

        # Grant view permission to user
        view_perm = Permission.objects.get(codename='view_document')
        change_perm = Permission.objects.get(codename='change_document')
        self.regular_user.user_permissions.add(view_perm, change_perm)
        self.client.login(username='driver_john', password='password123')

        url = reverse('document-list')

        # 1. No filter: all vehicles returned
        res_all = self.client.get(url, {'type': 'vehicles'})
        self.assertEqual(res_all.status_code, 200)
        vehicles_all = list(res_all.context['vehicles'])
        self.assertIn(veh_clean, vehicles_all)
        self.assertIn(veh_expired, vehicles_all)
        self.assertIn(veh_expiring, vehicles_all)
        self.assertEqual(res_all.context['summary_counts']['expired'], 1)
        self.assertEqual(res_all.context['summary_counts']['expiring'], 1)
        self.assertEqual(res_all.context['summary_counts']['alerts'], 2)

        # 2. alert=expired: only veh_expired
        res_expired = self.client.get(url, {'type': 'vehicles', 'alert': 'expired'})
        self.assertEqual(res_expired.status_code, 200)
        vehicles_expired = list(res_expired.context['vehicles'])
        self.assertIn(veh_expired, vehicles_expired)
        self.assertNotIn(veh_clean, vehicles_expired)
        self.assertNotIn(veh_expiring, vehicles_expired)
        self.assertContains(res_expired, 'Filtered by:')
        self.assertContains(res_expired, 'Expired Documents')

        # 3. alert=expiring: only veh_expiring
        res_expiring = self.client.get(url, {'type': 'vehicles', 'alert': 'expiring'})
        self.assertEqual(res_expiring.status_code, 200)
        vehicles_expiring = list(res_expiring.context['vehicles'])
        self.assertIn(veh_expiring, vehicles_expiring)
        self.assertNotIn(veh_clean, vehicles_expiring)
        self.assertNotIn(veh_expired, vehicles_expiring)
        self.assertContains(res_expiring, 'Filtered by:')
        self.assertContains(res_expiring, 'Expiring Soon')

        # 4. alert=alerts: both veh_expired and veh_expiring
        res_alerts = self.client.get(url, {'type': 'vehicles', 'alert': 'alerts'})
        self.assertEqual(res_alerts.status_code, 200)
        vehicles_alerts = list(res_alerts.context['vehicles'])
        self.assertIn(veh_expired, vehicles_alerts)
        self.assertIn(veh_expiring, vehicles_alerts)
        self.assertNotIn(veh_clean, vehicles_alerts)

    def test_document_list_view_alert_filters_drivers(self):
        """Test filtering document list view by alert statuses for drivers."""
        today = timezone.now().date()
        u1 = User.objects.create_user(username='drv_ok', password='password123')
        d_ok = Driver.objects.create(user=u1, employee_id='D-OK')
        Document.objects.create(driver=d_ok, document_name='License', expiry_date=today + timedelta(days=120))

        u2 = User.objects.create_user(username='drv_bad', password='password123')
        d_bad = Driver.objects.create(user=u2, employee_id='D-BAD')
        Document.objects.create(driver=d_bad, document_name='License', expiry_date=today - timedelta(days=10))

        view_perm = Permission.objects.get(codename='view_document')
        change_perm = Permission.objects.get(codename='change_document')
        self.regular_user.user_permissions.add(view_perm, change_perm)
        self.client.login(username='driver_john', password='password123')

        url = reverse('document-list')
        res = self.client.get(url, {'type': 'drivers', 'alert': 'expired'})
        self.assertEqual(res.status_code, 200)
        drivers_list = list(res.context['drivers'])
        self.assertIn(d_bad, drivers_list)
        self.assertNotIn(d_ok, drivers_list)

    def test_document_list_view_ownership_filters_default_company_owned(self):
        """Test that by default only company owned vehicles are shown, with option to filter by ownership."""
        veh_owned = Vehicle.objects.create(registration_plate='MH 12 OW 555', ownership=Vehicle.OWNERSHIP_OWNED)
        veh_attached = Vehicle.objects.create(registration_plate='MH 12 AT 777', ownership=Vehicle.OWNERSHIP_ATTACHED)

        view_perm = Permission.objects.get(codename='view_document')
        change_perm = Permission.objects.get(codename='change_document')
        self.regular_user.user_permissions.add(view_perm, change_perm)
        self.client.login(username='driver_john', password='password123')

        url = reverse('document-list')

        # 1. Default request (no ownership param): only company owned vehicles shown
        res_default = self.client.get(url, {'type': 'vehicles'})
        self.assertEqual(res_default.status_code, 200)
        default_vehicles = list(res_default.context['vehicles'])
        self.assertIn(veh_owned, default_vehicles)
        self.assertNotIn(veh_attached, default_vehicles)
        self.assertEqual(res_default.context['ownership_filter'], 'owned')

        # 2. Explicit ownership=attached: only attached vehicles shown
        res_attached = self.client.get(url, {'type': 'vehicles', 'ownership': 'attached'})
        self.assertEqual(res_attached.status_code, 200)
        attached_vehicles = list(res_attached.context['vehicles'])
        self.assertIn(veh_attached, attached_vehicles)
        self.assertNotIn(veh_owned, attached_vehicles)
        self.assertEqual(res_attached.context['ownership_filter'], 'attached')

        # 3. Explicit ownership=all: both owned and attached vehicles shown
        res_all = self.client.get(url, {'type': 'vehicles', 'ownership': 'all'})
        self.assertEqual(res_all.status_code, 200)
        all_vehicles = list(res_all.context['vehicles'])
        self.assertIn(veh_owned, all_vehicles)
        self.assertIn(veh_attached, all_vehicles)
        self.assertEqual(res_all.context['ownership_filter'], 'all')


class DirectDocumentUploadTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.user = User.objects.create_user(username='doc_admin', password='password123')
        add_perm = Permission.objects.get(codename='add_document')
        change_perm = Permission.objects.get(codename='change_document')
        view_veh_perm = Permission.objects.get(codename='view_vehicle')
        self.user.user_permissions.add(add_perm, change_perm, view_veh_perm)
        self.client.login(username='doc_admin', password='password123')
        self.vehicle = Vehicle.objects.create(registration_plate='MH 04 AB 1234')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_document_create_direct_file_upload(self):
        """Test creating a document with file uploads saves directly to storage without temp directory."""
        with override_settings(MEDIA_ROOT=self.temp_dir):
            url = reverse('document-create-vehicle', kwargs={'vehicle_pk': self.vehicle.pk})
            uploaded_file = SimpleUploadedFile('rc_book.pdf', b'%PDF-1.4 test rc book content', content_type='application/pdf')
            
            data = {
                'document_name': 'Registration Certificate',
                'document_number': 'RC-12345',
                'valid_from': '2026-01-01',
                'expiry_date': '2030-01-01',
                'cost': '0.00',
                'files-TOTAL_FORMS': '1',
                'files-INITIAL_FORMS': '0',
                'files-MIN_NUM_FORMS': '0',
                'files-MAX_NUM_FORMS': '1000',
                'files-0-file': uploaded_file,
            }

            response = self.client.post(url, data, follow=True)
            self.assertEqual(response.status_code, 200)

            # Check document was created
            doc = Document.objects.filter(vehicle=self.vehicle, document_name='Registration Certificate').first()
            self.assertIsNotNone(doc)

            # Check DocumentFile was created with completed status and direct storage path
            self.assertEqual(doc.files.count(), 1)
            doc_file = doc.files.first()
            self.assertEqual(doc_file.upload_status, 'completed')
            self.assertIsNone(doc_file.local_tmp_path)
            self.assertTrue(doc_file.file)
            self.assertTrue(os.path.exists(doc_file.file.path))

            # Verify no temp files exist in tmp/uploads
            tmp_uploads = os.path.join(settings.BASE_DIR, 'tmp', 'uploads')
            if os.path.exists(tmp_uploads):
                self.assertEqual(len(os.listdir(tmp_uploads)), 0)

    def test_document_update_direct_file_upload_and_delete(self):
        """Test updating a document to add new files and delete existing files directly in storage."""
        with override_settings(MEDIA_ROOT=self.temp_dir):
            doc = Document.objects.create(
                vehicle=self.vehicle,
                document_name='Pollution Under Control',
                document_number='PUC-9999',
                cost=0
            )
            initial_file = SimpleUploadedFile('old_puc.pdf', b'old content', content_type='application/pdf')
            existing_doc_file = DocumentFile.objects.create(
                document=doc,
                file=initial_file,
                upload_status='completed'
            )
            old_file_path = existing_doc_file.file.path
            self.assertTrue(os.path.exists(old_file_path))

            url = reverse('document-update', kwargs={'pk': doc.pk})
            new_uploaded_file = SimpleUploadedFile('new_puc.pdf', b'new content', content_type='application/pdf')

            data = {
                'document_name': 'Pollution Under Control',
                'document_number': 'PUC-9999',
                'valid_from': '2026-01-01',
                'expiry_date': '2027-01-01',
                'cost': '0.00',
                'files-TOTAL_FORMS': '2',
                'files-INITIAL_FORMS': '1',
                'files-MIN_NUM_FORMS': '0',
                'files-MAX_NUM_FORMS': '1000',
                'files-0-id': str(existing_doc_file.pk),
                'files-0-DELETE': 'on', # Delete old file
                'files-1-file': new_uploaded_file, # Add new file
            }

            response = self.client.post(url, data, follow=True)
            self.assertEqual(response.status_code, 200)

            # Old doc file should be deleted from DB and disk
            self.assertFalse(DocumentFile.objects.filter(pk=existing_doc_file.pk).exists())
            self.assertFalse(os.path.exists(old_file_path))

            # New doc file should exist and be completed
            self.assertEqual(doc.files.count(), 1)
            new_file_record = doc.files.first()
            self.assertEqual(new_file_record.upload_status, 'completed')
            self.assertIsNone(new_file_record.local_tmp_path)
            self.assertTrue(new_file_record.file)
            self.assertTrue(os.path.exists(new_file_record.file.path))

