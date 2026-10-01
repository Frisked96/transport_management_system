from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User, Permission
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile

from documents.models import Document, DocumentRenewal
from fleet.models import Vehicle


class BaseComplianceDocumentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username='admin_test', password='password123')
        self.client.login(username='admin_test', password='password123')
        self.vehicle = Vehicle.objects.create(registration_plate='MH 14 DE 5555', make_model='Tata Prima')

    def test_base_documents_auto_created_on_vehicle_creation(self):
        """Verify that exactly the 7 required base documents are automatically created on vehicle creation."""
        base_names = [
            'Fitness',
            '1 yr permit',
            '5yr permit',
            'Insurance',
            'Tax',
            'RC',
            'Vltd cirtificate',
        ]
        vehicle_docs = self.vehicle.documents.filter(is_base_document=True)
        self.assertEqual(vehicle_docs.count(), 7)

        doc_names = list(vehicle_docs.values_list('document_name', flat=True))
        for expected_name in base_names:
            self.assertIn(expected_name, doc_names)

        # All start with status Not Set
        for doc in vehicle_docs:
            self.assertEqual(doc.status, 'Not Set')
            self.assertIsNone(doc.expiry_date)
            self.assertEqual(doc.cost, Decimal('0.00'))

    def test_base_document_cannot_be_deleted_directly(self):
        """Verify that calling delete() directly on a base document raises PermissionDenied."""
        fitness_doc = self.vehicle.documents.get(document_name='Fitness')
        self.assertTrue(fitness_doc.is_base_document)
        with self.assertRaises(PermissionDenied):
            fitness_doc.delete()

        self.assertTrue(Document.objects.filter(pk=fitness_doc.pk).exists())

    def test_base_document_delete_view_blocked(self):
        """Verify that DocumentDeleteView blocks deleting a base document and redirects."""
        fitness_doc = self.vehicle.documents.get(document_name='Fitness')
        response = self.client.post(reverse('document-delete', kwargs={'pk': fitness_doc.pk}))
        # Redirects back to vehicle detail without deleting
        self.assertRedirects(response, reverse('vehicle-detail', kwargs={'pk': self.vehicle.pk}))
        self.assertTrue(Document.objects.filter(pk=fitness_doc.pk).exists())

    def test_custom_document_can_be_deleted(self):
        """Verify custom documents can still be deleted without error."""
        custom_doc = Document.objects.create(
            vehicle=self.vehicle,
            document_name='Pollution Certificate',
            is_base_document=False
        )
        custom_doc.delete()
        self.assertFalse(Document.objects.filter(pk=custom_doc.pk).exists())

    def test_vehicle_deletion_cascades_cleanly(self):
        """Verify deleting vehicle cascades and deletes all related base documents."""
        veh_id = self.vehicle.pk
        doc_count = self.vehicle.documents.count()
        self.assertEqual(doc_count, 7)

        self.vehicle.delete()
        self.assertEqual(Document.objects.filter(vehicle_id=veh_id).count(), 0)

    def test_edit_document_syncs_to_history(self):
        """Verify editing a base document creates or updates initial history record."""
        today = timezone.now().date()
        next_year = today + timedelta(days=365)
        tax_doc = self.vehicle.documents.get(document_name='Tax')

        response = self.client.post(reverse('document-update', kwargs={'pk': tax_doc.pk}), {
            'document_name': tax_doc.document_name,
            'document_number': 'TAX-2026-99',
            'valid_from': today.isoformat(),
            'expiry_date': next_year.isoformat(),
            'cost': '12500.00',
            'notes': 'Annual road tax payment',
            'files-TOTAL_FORMS': '1',
            'files-INITIAL_FORMS': '0',
            'files-MIN_NUM_FORMS': '0',
            'files-MAX_NUM_FORMS': '1000',
        })
        self.assertRedirects(response, reverse('vehicle-detail', kwargs={'pk': self.vehicle.pk}))

        tax_doc.refresh_from_db()
        self.assertEqual(tax_doc.document_number, 'TAX-2026-99')
        self.assertEqual(tax_doc.valid_from, today)
        self.assertEqual(tax_doc.expiry_date, next_year)
        self.assertEqual(tax_doc.cost, Decimal('12500.00'))
        self.assertEqual(tax_doc.status, 'Valid')

        # History record should exist
        self.assertEqual(tax_doc.renewals.count(), 1)
        renewal = tax_doc.renewals.first()
        self.assertEqual(renewal.cost, Decimal('12500.00'))
        self.assertEqual(renewal.valid_from, today)
        self.assertEqual(renewal.valid_to, next_year)

    def test_document_renew_view_creates_history_and_updates_document(self):
        """Verify DocumentRenewView logs a renewal and updates Document validity and cost."""
        ins_doc = self.vehicle.documents.get(document_name='Insurance')
        today = timezone.now().date()
        expiry_1 = today + timedelta(days=365)

        # Initial validity
        ins_doc.valid_from = today - timedelta(days=365)
        ins_doc.expiry_date = today
        ins_doc.cost = Decimal('25000.00')
        ins_doc.document_number = 'POL-2025'
        ins_doc.save()
        ins_doc.sync_to_history(user=self.user)

        # Now renew for the next year
        expiry_2 = today + timedelta(days=365)
        renew_url = reverse('document-renew', kwargs={'pk': ins_doc.pk})
        response = self.client.post(renew_url, {
            'valid_from': today.isoformat(),
            'valid_to': expiry_2.isoformat(),
            'cost': '28000.00',
            'document_number': 'POL-2026',
            'notes': 'Renewed via ICICI Lombard',
        })
        self.assertRedirects(response, reverse('document-history', kwargs={'pk': ins_doc.pk}))

        ins_doc.refresh_from_db()
        self.assertEqual(ins_doc.document_number, 'POL-2026')
        self.assertEqual(ins_doc.valid_from, today)
        self.assertEqual(ins_doc.expiry_date, expiry_2)
        self.assertEqual(ins_doc.cost, Decimal('28000.00'))

        # Both renewals in history
        self.assertEqual(ins_doc.renewals.count(), 2)
        self.assertEqual(ins_doc.total_expenses, Decimal('53000.00'))

    def test_vehicle_total_compliance_expenses(self):
        """Verify vehicle.total_compliance_expenses calculates sum of all document renewals."""
        today = timezone.now().date()
        doc1 = self.vehicle.documents.get(document_name='Fitness')
        doc2 = self.vehicle.documents.get(document_name='Tax')

        DocumentRenewal.objects.create(
            document=doc1,
            valid_from=today,
            valid_to=today + timedelta(days=365),
            cost=Decimal('3500.00')
        )
        DocumentRenewal.objects.create(
            document=doc2,
            valid_from=today,
            valid_to=today + timedelta(days=365),
            cost=Decimal('8000.00')
        )

        self.assertEqual(self.vehicle.total_compliance_expenses, Decimal('11500.00'))

    def test_document_status_transitions(self):
        """Test document status transitions across all states."""
        today = timezone.now().date()
        doc = self.vehicle.documents.get(document_name='RC')

        # 1. Not Set
        doc.expiry_date = None
        self.assertEqual(doc.status, 'Not Set')

        # 2. Expired
        doc.expiry_date = today - timedelta(days=1)
        self.assertEqual(doc.status, 'Expired')

        # 3. Expiring Soon (within 30 days)
        doc.expiry_date = today + timedelta(days=15)
        self.assertEqual(doc.status, 'Expiring Soon')

        # 4. Valid (> 30 days)
        doc.expiry_date = today + timedelta(days=60)
        self.assertEqual(doc.status, 'Valid')

        # 5. Permanent
        doc.never_expires = True
        self.assertEqual(doc.status, 'Permanent')

    def test_renewal_delete_resyncs_document(self):
        """Verify deleting the latest renewal record resyncs the document to the previous renewal."""
        today = timezone.now().date()
        doc = self.vehicle.documents.get(document_name='1 yr permit')

        # First renewal
        r1 = DocumentRenewal.objects.create(
            document=doc,
            valid_from=today - timedelta(days=365),
            valid_to=today,
            cost=Decimal('5000.00'),
            document_number='PER-001'
        )
        # Second renewal
        r2 = DocumentRenewal.objects.create(
            document=doc,
            valid_from=today,
            valid_to=today + timedelta(days=365),
            cost=Decimal('6000.00'),
            document_number='PER-002'
        )

        doc.refresh_from_db()
        self.assertEqual(doc.expiry_date, today + timedelta(days=365))
        self.assertEqual(doc.cost, Decimal('6000.00'))
        self.assertEqual(doc.document_number, 'PER-002')

        # Delete latest renewal
        response = self.client.post(reverse('document-renewal-delete', kwargs={'pk': r2.pk}))
        self.assertRedirects(response, reverse('document-history', kwargs={'pk': doc.pk}))

        doc.refresh_from_db()
        self.assertEqual(doc.expiry_date, today)
        self.assertEqual(doc.cost, Decimal('5000.00'))
        self.assertEqual(doc.document_number, 'PER-001')

    def test_history_preserves_old_pdfs_and_images_across_renewals(self):
        """Verify that renewing a document with new files preserves previous cycle's uploaded PDFs/images in history."""
        today = timezone.now().date()
        fitness_doc = self.vehicle.documents.get(document_name='Fitness')

        # 1. First renewal with an old PDF
        old_pdf = SimpleUploadedFile("fitness_2025.pdf", b"%PDF-1.4 old fitness scan content", content_type="application/pdf")
        r1 = DocumentRenewal.objects.create(
            document=fitness_doc,
            valid_from=today - timedelta(days=365),
            valid_to=today,
            cost=Decimal('2500.00'),
            document_number='FIT-2025',
            receipt_file=old_pdf
        )

        # 2. Second renewal with a new Image via DocumentRenewView
        new_image = SimpleUploadedFile("fitness_2026.png", b"\x89PNG\r\n\x1a\n new image content", content_type="image/png")
        renew_url = reverse('document-renew', kwargs={'pk': fitness_doc.pk})
        response = self.client.post(renew_url, {
            'valid_from': today.isoformat(),
            'valid_to': (today + timedelta(days=365)).isoformat(),
            'cost': '3000.00',
            'document_number': 'FIT-2026',
            'receipt_file': new_image,
        })
        self.assertRedirects(response, reverse('document-history', kwargs={'pk': fitness_doc.pk}))

        # Refresh both renewals
        r1.refresh_from_db()
        r2 = fitness_doc.renewals.order_by('-valid_to').first()

        # Both renewals must retain their respective files
        self.assertTrue(r1.receipt_file)
        self.assertTrue(r1.receipt_file.name.endswith('.pdf'))
        self.assertTrue(r2.receipt_file)
        self.assertTrue(r2.receipt_file.name.endswith('.png'))
        self.assertNotEqual(r1.receipt_file.name, r2.receipt_file.name)

        # 3. Check that the history page includes download links for both files
        history_response = self.client.get(reverse('document-history', kwargs={'pk': fitness_doc.pk}))
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, reverse('renewal-file-view', kwargs={'pk': r1.pk}))
        self.assertContains(history_response, reverse('renewal-file-view', kwargs={'pk': r2.pk}))

        # 4. Proxy view resolves each file correctly
        proxy_r1 = self.client.get(reverse('renewal-file-view', kwargs={'pk': r1.pk}))
        self.assertEqual(proxy_r1.status_code, 302)
        self.assertTrue(proxy_r1.url.endswith('.pdf'))

        proxy_r2 = self.client.get(reverse('renewal-file-view', kwargs={'pk': r2.pk}))
        self.assertEqual(proxy_r2.status_code, 302)
        self.assertTrue(proxy_r2.url.endswith('.png'))

        # Clean up files created during test
        if r1.receipt_file:
            r1.receipt_file.delete(save=False)
        if r2.receipt_file:
            r2.receipt_file.delete(save=False)
