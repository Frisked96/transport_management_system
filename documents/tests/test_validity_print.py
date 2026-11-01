from datetime import timedelta
from django.contrib.auth.models import User, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from fleet.models import Vehicle
from documents.models import Document
from ledger.models import CompanyAccount


class DocumentValidityPrintTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='doc_viewer', password='password123')
        view_perm = Permission.objects.get(codename='view_document', content_type__app_label='documents')
        self.user.user_permissions.add(view_perm)

        self.firm1 = CompanyAccount.objects.create(
            name='Alpha Transporters',
            gstin='07AAAAA0000A1Z5',
            address='New Delhi, India'
        )
        self.firm2 = CompanyAccount.objects.create(
            name='Beta Logistics',
            gstin='27BBBBB1111B2Z6',
            address='Mumbai, India'
        )

        self.v1 = Vehicle.objects.create(
            registration_plate='DL 01 AB 1001',
            make_model='Tata Signa 4825',
            status=Vehicle.STATUS_ACTIVE,
            ownership=Vehicle.OWNERSHIP_OWNED,
            company_account=self.firm1
        )
        self.v2 = Vehicle.objects.create(
            registration_plate='MH 02 CD 2002',
            make_model='Ashok Leyland 4220',
            status=Vehicle.STATUS_MAINTENANCE,
            ownership=Vehicle.OWNERSHIP_OWNED,
            company_account=self.firm2
        )
        self.v3 = Vehicle.objects.create(
            registration_plate='HR 55 EF 3003',
            make_model='BharatBenz 3523',
            status=Vehicle.STATUS_ACTIVE,
            ownership=Vehicle.OWNERSHIP_ATTACHED,
            company_account=None
        )

        today = timezone.now().date()
        # Documents for v1
        Document.objects.create(
            vehicle=self.v1,
            document_name='Fitness',
            expiry_date=today + timedelta(days=60)
        )
        Document.objects.create(
            vehicle=self.v1,
            document_name='Insurance',
            expiry_date=today + timedelta(days=15)  # expiring soon
        )
        Document.objects.create(
            vehicle=self.v1,
            document_name='1 yr permit',
            expiry_date=today - timedelta(days=5)  # expired
        )
        Document.objects.create(
            vehicle=self.v1,
            document_name='Tax',
            expiry_date=today + timedelta(days=120)
        )
        Document.objects.create(
            vehicle=self.v1,
            document_name='5yr permit',
            expiry_date=today + timedelta(days=800)
        )

        self.client.login(username='doc_viewer', password='password123')

    def test_unauthenticated_user_redirected(self):
        """Anonymous requests must redirect to login."""
        self.client.logout()
        url = reverse('document-validity-print')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.url)

    def test_user_without_permission_forbidden(self):
        """Users without documents.view_document permission are denied with 403."""
        user_no_perm = User.objects.create_user(username='noperm', password='password123')
        self.client.login(username='noperm', password='password123')
        url = reverse('document-validity-print')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_default_validity_print_view(self):
        """Default request loads company-owned active vehicles with the 5 core documents."""
        url = reverse('document-validity-print')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'documents/validity_print.html')

        rows = resp.context['vehicle_rows']
        # By default, ownership='owned', so v1 (DL 01 AB 1001) and v2 (MH 02 CD 2002) are owned
        plates = [r['vehicle'].registration_plate for r in rows]
        self.assertIn('DL 01 AB 1001', plates)
        self.assertIn('MH 02 CD 2002', plates)
        self.assertNotIn('HR 55 EF 3003', plates)  # attached vehicle excluded by default

        # Check documents for v1
        v1_row = next(r for r in rows if r['vehicle'].registration_plate == 'DL 01 AB 1001')
        doc_labels = [d['label'] for d in v1_row['documents']]
        self.assertEqual(doc_labels, ['Fitness', 'Insurance', '1 Year Permit', 'Road Tax', '5 Year Permit'])

        # Check statuses
        doc_statuses = {d['label']: d['status'] for d in v1_row['documents']}
        self.assertEqual(doc_statuses['Fitness'], 'Valid')
        self.assertEqual(doc_statuses['Insurance'], 'Expiring Soon')
        self.assertEqual(doc_statuses['1 Year Permit'], 'Expired')
        self.assertEqual(doc_statuses['Road Tax'], 'Valid')
        self.assertEqual(doc_statuses['5 Year Permit'], 'Valid')

    def test_firm_filtering(self):
        """Filtering by firm ID returns only vehicles linked to that CompanyAccount."""
        url = reverse('document-validity-print') + f'?firm={self.firm1.id}&ownership=all'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['selected_firm'], self.firm1)

        rows = resp.context['vehicle_rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['vehicle'].registration_plate, 'DL 01 AB 1001')
        self.assertContains(resp, 'Alpha Transporters')

    def test_vehicle_status_filtering(self):
        """Filtering by vehicle status returns only vehicles matching that status."""
        url = reverse('document-validity-print') + '?vehicle_status=Active&ownership=all'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        rows = resp.context['vehicle_rows']
        plates = [r['vehicle'].registration_plate for r in rows]
        self.assertIn('DL 01 AB 1001', plates)
        self.assertIn('HR 55 EF 3003', plates)
        self.assertNotIn('MH 02 CD 2002', plates)  # Maintenance vehicle excluded

    def test_ownership_filtering(self):
        """Filtering by ownership returns only matching vehicles."""
        # Attached only
        url = reverse('document-validity-print') + '?ownership=attached'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        rows = resp.context['vehicle_rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['vehicle'].registration_plate, 'HR 55 EF 3003')

    def test_custom_document_selection(self):
        """Selecting specific document types displays only those documents."""
        url = reverse('document-validity-print') + '?documents=Fitness&documents=Tax'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        rows = resp.context['vehicle_rows']
        v1_row = next(r for r in rows if r['vehicle'].registration_plate == 'DL 01 AB 1001')
        doc_labels = [d['label'] for d in v1_row['documents']]
        self.assertEqual(doc_labels, ['Fitness', 'Road Tax'])

    def test_permanent_and_missing_document_handling(self):
        """Permanent documents show Permanent, and missing documents show Not Available."""
        # Create a permanent document (never_expires=True) for v2
        Document.objects.create(
            vehicle=self.v2,
            document_name='Fitness',
            never_expires=True
        )
        url = reverse('document-validity-print') + f'?firm={self.firm2.id}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        v2_row = resp.context['vehicle_rows'][0]
        fitness_doc = next(d for d in v2_row['documents'] if d['label'] == 'Fitness')
        self.assertEqual(fitness_doc['status'], 'Permanent')
        self.assertTrue(fitness_doc['never_expires'])

        # Insurance exists as base document without an expiry date -> status is 'Not Set'
        insurance_doc = next(d for d in v2_row['documents'] if d['label'] == 'Insurance')
        self.assertEqual(insurance_doc['status'], 'Not Set')
        self.assertIsNone(insurance_doc['expiry_date'])

        # If a document does not exist at all (e.g. PUCC deleted) -> status is 'Not Available'
        Document.objects.filter(vehicle=self.v2, document_name='PUCC').delete()
        resp_custom = self.client.get(reverse('document-validity-print') + f'?firm={self.firm2.id}&documents=PUCC')
        pucc_doc = resp_custom.context['vehicle_rows'][0]['documents'][0]
        self.assertEqual(pucc_doc['status'], 'Not Available')

    def test_document_list_view_contains_print_button_and_modal_context(self):
        """Document list view contains the 'Print Validity' button and modal context."""
        url = reverse('document-list')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Print Validity')
        self.assertContains(resp, 'id="validityPrintModal"')
        self.assertIn('firms', resp.context)
        self.assertIn('vehicle_status_choices', resp.context)
        self.assertIn('available_document_types', resp.context)
