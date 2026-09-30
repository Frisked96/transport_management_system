import os
import shutil
import tempfile
from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from documents.models import (
    Document,
    DocumentFile,
    document_upload_path,
    document_file_upload_path,
)
from fleet.models import Vehicle
from drivers.models import Driver


class DocumentPathHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='doc_user', password='password123')
        self.vehicle = Vehicle.objects.create(registration_plate='MH 12/AB 1234')
        self.driver_with_emp = Driver.objects.create(
            user=self.user,
            employee_id='EMP/007 1',
            license_number='DL123'
        )
        self.user_no_emp = User.objects.create_user(
            username='rajesh_driver',
            first_name='Rajesh',
            last_name='Sharma',
            password='password123'
        )
        self.driver_no_emp = Driver.objects.create(
            user=self.user_no_emp,
            employee_id='',
            license_number='DL456'
        )

    def test_document_upload_path_vehicle(self):
        """Verify document_upload_path replaces spaces with _ and / with - for vehicles."""
        doc = Document(vehicle=self.vehicle)
        path = document_upload_path(doc, 'permit.pdf')
        self.assertEqual(path, os.path.join('documents', 'MH_12-AB_1234', 'permit.pdf'))

    def test_document_upload_path_driver_employee_id(self):
        """Verify document_upload_path uses employee_id with sanitized characters."""
        doc = Document(driver=self.driver_with_emp)
        path = document_upload_path(doc, 'license.pdf')
        self.assertEqual(path, os.path.join('documents', 'EMP-007_1', 'license.pdf'))

    def test_document_upload_path_driver_name_fallback(self):
        """Verify document_upload_path falls back to driver name when employee_id is blank."""
        doc = Document(driver=self.driver_no_emp)
        path = document_upload_path(doc, 'license.pdf')
        self.assertEqual(path, os.path.join('documents', 'Rajesh_Sharma', 'license.pdf'))

    def test_document_upload_path_miscellaneous(self):
        """Verify document_upload_path defaults to miscellaneous when no vehicle or driver."""
        doc = Document()
        path = document_upload_path(doc, 'general.pdf')
        self.assertEqual(path, os.path.join('documents', 'miscellaneous', 'general.pdf'))

    def test_document_file_upload_path_with_number_and_index(self):
        """Verify document_file_upload_path format documents/<identifier>/<docname>_<docnum>_<index>.<ext>."""
        doc = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Tax Receipt & Permit',
            document_number='TAX/2026/01'
        )
        doc_file = DocumentFile(document=doc)
        doc_file._upload_index = 2
        path = document_file_upload_path(doc_file, 'scan.jpeg')

        # safe_doc_name: 'Tax_Receipt__Permit' (special chars stripped, spaces to _)
        # safe_doc_num: 'TAX-2026-01'
        expected_dir = os.path.join('documents', 'MH_12-AB_1234')
        self.assertTrue(path.startswith(expected_dir))
        self.assertTrue(path.endswith('_2.jpeg'))
        self.assertIn('Tax_Receipt__Permit_TAX202601_2.jpeg', path)

    def test_document_file_upload_path_index_fallback(self):
        """Verify document_file_upload_path falls back to files.count() + 1 if _upload_index not set."""
        doc = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Fitness',
            document_number=None
        )
        doc_file = DocumentFile(document=doc)
        path = document_file_upload_path(doc_file, 'test.png')
        self.assertTrue(path.endswith('Fitness_1.png'))


class DocumentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='doc_admin', password='password123')
        self.vehicle = Vehicle.objects.create(registration_plate='DL 01 AB 9999')
        self.driver = Driver.objects.create(user=self.user, employee_id='EMP-101')

    def test_document_str_formatting(self):
        """Verify __str__ includes document_number if present, otherwise just document_name."""
        doc_with_num = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Insurance Policy',
            document_number='POL-8888'
        )
        self.assertEqual(str(doc_with_num), 'Insurance Policy - POL-8888')

        doc_without_num = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Pollution Certificate',
            document_number=''
        )
        self.assertEqual(str(doc_without_num), 'Pollution Certificate')

    def test_document_expiry_properties(self):
        """Test is_expired and days_until_expiry across different expiry conditions."""
        today = timezone.now().date()

        # Expired doc
        doc_expired = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Old Permit',
            expiry_date=today - timedelta(days=7)
        )
        self.assertTrue(doc_expired.is_expired)
        self.assertEqual(doc_expired.days_until_expiry, -7)

        # Future doc
        doc_active = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Current Insurance',
            expiry_date=today + timedelta(days=30)
        )
        self.assertFalse(doc_active.is_expired)
        self.assertEqual(doc_active.days_until_expiry, 30)

        # Never expires
        doc_permanent = Document.objects.create(
            driver=self.driver,
            document_name='PAN Card',
            expiry_date=today - timedelta(days=100),
            never_expires=True
        )
        self.assertFalse(doc_permanent.is_expired)
        self.assertIsNone(doc_permanent.days_until_expiry)

        # No expiry date
        doc_no_date = Document.objects.create(
            driver=self.driver,
            document_name='Registration Form',
            expiry_date=None
        )
        self.assertFalse(doc_no_date.is_expired)
        self.assertIsNone(doc_no_date.days_until_expiry)

    def test_document_ordering(self):
        """Verify default ordering is by expiry_date ascending, then -created_at."""
        today = timezone.now().date()
        doc_far = Document.objects.create(document_name='Far Future', expiry_date=today + timedelta(days=100))
        doc_soon = Document.objects.create(document_name='Soon', expiry_date=today + timedelta(days=5))
        doc_past = Document.objects.create(document_name='Past', expiry_date=today - timedelta(days=10))

        docs = list(Document.objects.all())
        self.assertEqual(docs[0], doc_past)
        self.assertEqual(docs[1], doc_soon)
        self.assertEqual(docs[2], doc_far)

    def test_document_foreign_key_cascades(self):
        """Verify deleting vehicle/driver cascades, while deleting user sets added_by to NULL."""
        doc1 = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Veh Doc',
            added_by=self.user
        )
        doc2 = Document.objects.create(
            driver=self.driver,
            document_name='Drv Doc',
            added_by=self.user
        )

        # Delete added_by user -> doc should not be deleted, added_by set to None
        self.user.delete()
        doc1.refresh_from_db()
        self.assertIsNone(doc1.added_by)

        # Delete vehicle -> doc1 deleted
        self.vehicle.delete()
        self.assertFalse(Document.objects.filter(pk=doc1.pk).exists())

        # Delete driver -> doc2 deleted
        self.driver.delete()
        self.assertFalse(Document.objects.filter(pk=doc2.pk).exists())


class DocumentFileModelTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.vehicle = Vehicle.objects.create(registration_plate='KA 01 CD 5678')
        self.document = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Fitness Certificate'
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_document_file_creation_defaults_and_str(self):
        """Verify DocumentFile creation defaults to pending and __str__ contains status."""
        doc_file = DocumentFile.objects.create(document=self.document)
        self.assertEqual(doc_file.upload_status, 'pending')
        self.assertEqual(
            str(doc_file),
            f"File for {self.document.document_name} (Pending)"
        )

    def test_document_file_status_choices(self):
        """Verify DocumentFile supports all status choices."""
        choices = dict(DocumentFile.UPLOAD_STATUS_CHOICES)
        self.assertIn('pending', choices)
        self.assertIn('uploading', choices)
        self.assertIn('completed', choices)
        self.assertIn('failed', choices)

    def test_document_file_cascade_on_document_delete(self):
        """Verify deleting Document cascades and deletes child DocumentFiles."""
        file1 = DocumentFile.objects.create(document=self.document)
        file2 = DocumentFile.objects.create(document=self.document)
        self.assertEqual(self.document.files.count(), 2)

        self.document.delete()
        self.assertFalse(DocumentFile.objects.filter(pk=file1.pk).exists())
        self.assertFalse(DocumentFile.objects.filter(pk=file2.pk).exists())

    def test_document_file_delete_signal_removes_physical_file(self):
        """Verify post_delete signal deletes file from storage when DocumentFile is deleted."""
        with override_settings(MEDIA_ROOT=self.temp_dir):
            upload = SimpleUploadedFile('test_file.txt', b'file-content-to-delete')
            doc_file = DocumentFile.objects.create(document=self.document, file=upload)
            file_path = doc_file.file.path
            self.assertTrue(os.path.exists(file_path))

            doc_file.delete()
            self.assertFalse(os.path.exists(file_path))

    def test_document_file_change_signal_removes_old_physical_file(self):
        """Verify pre_save signal deletes old file from storage when file field is updated."""
        with override_settings(MEDIA_ROOT=self.temp_dir):
            upload1 = SimpleUploadedFile('first.txt', b'first-content')
            doc_file = DocumentFile.objects.create(document=self.document, file=upload1)
            first_path = doc_file.file.path
            self.assertTrue(os.path.exists(first_path))

            upload2 = SimpleUploadedFile('second.txt', b'second-content')
            doc_file.file = upload2
            doc_file.save()

            # Old file should be deleted, new file should exist
            self.assertFalse(os.path.exists(first_path))
            self.assertTrue(os.path.exists(doc_file.file.path))
