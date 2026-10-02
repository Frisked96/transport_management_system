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

        # Request as Regular User: should receive empty dict
        req_regular = self.factory.get('/')
        req_regular.user = self.regular_user
        self.assertEqual(document_alerts(req_regular), {})

        # Request as Unauthenticated: should receive empty dict
        from django.contrib.auth.models import AnonymousUser
        req_anon = self.factory.get('/')
        req_anon.user = AnonymousUser()
        self.assertEqual(document_alerts(req_anon), {})


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

    def test_storage_bridge_fallback_when_gdrive_unreachable(self):
        """Test GoogleDriveOAuth2Storage automatically falls back to local storage when network fails."""
        from transport_mgmt.storage_bridge import GoogleDriveOAuth2Storage
        from unittest.mock import patch

        with override_settings(MEDIA_ROOT=self.temp_dir):
            storage = GoogleDriveOAuth2Storage()
            # Simulate network unreachable error on GDrive _ensure_service
            with patch.object(storage, '_ensure_service', side_effect=OSError(101, 'Network is unreachable')):
                test_file = SimpleUploadedFile('network_test.pdf', b'sample content', content_type='application/pdf')
                saved_path = storage._save('documents/test_offline.pdf', test_file)
                self.assertEqual(saved_path, 'documents/test_offline.pdf')
                self.assertTrue(storage.exists(saved_path))
                self.assertEqual(storage.url(saved_path), '/media/documents/test_offline.pdf')
                self.assertTrue(os.path.exists(storage.path(saved_path)))
                storage.delete(saved_path)
                self.assertFalse(storage.exists(saved_path))


