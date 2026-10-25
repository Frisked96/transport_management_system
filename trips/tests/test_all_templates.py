import os
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.paginator import Paginator
from django.template.loader import get_template, render_to_string
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from documents.models import Document, DocumentRenewal
from drivers.models import Driver, DriverTransaction
from fleet.models import MaintenanceRecord, Tyre, TyreBrand, TyreLog, Vehicle
from ledger.models import (
    Bill,
    CompanyAccount,
    FinancialRecord,
    Party,
    TollAccount,
    TollEntry,
    TollRecharge,
    TransactionCategory,
)
from trips.models import Route, Trip


class AllTemplatesTest(TestCase):
    """
    Automated test suite verifying that all .html templates in the project
    compile, load, and render with real views and domain models without errors.
    """

    @classmethod
    def setUpTestData(cls):
        # 1. Superuser for authenticated access
        cls.superuser = User.objects.create_superuser(
            username='test_admin_user',
            email='testadmin@example.com',
            password='testpassword123',
        )

        # 2. Driver user & driver profile
        cls.driver_user = User.objects.create_user(
            username='test_driver_user',
            first_name='John',
            last_name='Driver',
            password='testpassword123',
        )
        cls.driver = Driver.objects.create(
            user=cls.driver_user,
            employee_id='EMP-001',
            license_number='DL-01-TEST',
            phone_number='9876543210',
            address='123 Transport Hub',
            joined_date=date.today() - timedelta(days=100),
        )
        cls.driver_tx = DriverTransaction.objects.create(
            driver=cls.driver,
            transaction_type='Salary',
            amount=Decimal('5000.00'),
            date=date.today(),
            description='Monthly salary',
            created_by=cls.superuser,
        )

        # 3. Company Accounts (Firm)
        cls.company_account = CompanyAccount.objects.create(
            name='Test Logistics Ltd',
            account_holder_name='Test Logistics Ltd',
            account_number='123456789012',
            bank_name='HDFC Bank',
            ifsc_code='HDFC0001234',
            gstin='27ABCDE1234F1Z5',
            opening_balance=Decimal('100000.00'),
        )

        # 4. Parties (Client / Creditor)
        cls.debtor_party = Party.objects.create(
            name='Test Debtor Client',
            party_type=Party.TYPE_DEBTOR,
            phone_number='9876543211',
            opening_balance=Decimal('0.00'),
        )
        cls.creditor_party = Party.objects.create(
            name='Test Creditor Vendor',
            party_type=Party.TYPE_CREDITOR,
            phone_number='9876543212',
            opening_balance=Decimal('0.00'),
        )

        # 5. Fleet: Vehicle, Maintenance, Tyres
        cls.vehicle = Vehicle.objects.create(
            registration_plate='NL01AB1234',
            make_model='Tata Signa 4825.TK',
            ownership='owned',
            status='Active',
            company_account=cls.company_account,
        )
        cls.maintenance = MaintenanceRecord.objects.create(
            vehicle=cls.vehicle,
            name='Engine Oil & Filter Replacement',
            expiry_date=date.today() + timedelta(days=30),
            is_completed=False,
            cost=Decimal('4500.00'),
            logged_by=cls.superuser,
        )
        cls.tyre_brand = TyreBrand.objects.create(name='MRF')
        cls.tyre = Tyre.objects.create(
            serial_number='MRF-RADIAL-001',
            brand=cls.tyre_brand,
            size='295/80 R22.5',
            current_vehicle=cls.vehicle,
            current_position='Axle 1 Left Outer',
            status='Mounted',
        )
        cls.tyre_log = TyreLog.objects.create(
            tyre=cls.tyre,
            action='Fitted',
            vehicle=cls.vehicle,
            position='Axle 1 Left Outer',
            date=date.today(),
            notes='Initial fitting',
            logged_by=cls.superuser,
        )

        # 6. Operations: Route & Trip
        cls.route = Route.objects.create(
            pickup_location='Mumbai Port',
            delivery_location='Pune Warehouse',
            default_rate=Decimal('1650.00'),
        )
        cls.trip = Trip.objects.create(
            lr_no='LR-TEST-1001',
            revenue_type='Fixed Rate',
            driver=cls.driver,
            vehicle=cls.vehicle,
            date=date.today(),
            party=cls.debtor_party,
            route=cls.route,
            weight=Decimal('25.50'),
            rate_per_ton=Decimal('1000.00'),
            created_by=cls.superuser,
        )

        # 7. Financial & Invoicing: Bills & Records
        cls.bill = Bill.objects.create(
            bill_no=70001,
            issuer=cls.company_account,
            party=cls.debtor_party,
            date=date.today(),
            bill_type='Standard',
            item_type='Standard',
            amount_override=Decimal('25000.00'),
            created_by=cls.superuser,
        )
        cls.vendor_bill = Bill.objects.create(
            bill_no=70002,
            issuer=cls.company_account,
            party=cls.creditor_party,
            date=date.today(),
            bill_type='Standard',
            item_type='Standard',
            amount_override=Decimal('15000.00'),
            created_by=cls.superuser,
        )
        cls.category = TransactionCategory.objects.create(
            name='Freight Revenue',
            type='Income',
        )
        cls.financial_record = FinancialRecord.objects.create(
            record_type='Income',
            account=cls.company_account,
            party=cls.debtor_party,
            date=date.today(),
            amount=Decimal('25000.00'),
            category=cls.category,
            recorded_by=cls.superuser,
        )

        # 8. Toll FASTag Account, Entries & Recharges
        cls.toll_account = TollAccount.objects.create(
            name='ICICI Commercial FASTag',
            account_number='ICICI-FASTAG-9876',
            provider='ICICI',
            opening_balance=Decimal('5000.00'),
        )
        cls.toll_entry = TollEntry.objects.create(
            toll_account=cls.toll_account,
            vehicle=cls.vehicle,
            date=date.today(),
            time='14:30:00',
            amount=Decimal('185.00'),
            toll_plaza='Khed Shivapur Plaza',
            associated_trip=cls.trip,
            created_by=cls.superuser,
        )
        cls.toll_recharge = TollRecharge.objects.create(
            toll_account=cls.toll_account,
            company_account=cls.company_account,
            date=date.today(),
            time='10:15:00',
            amount=Decimal('10000.00'),
            payment_mode='Net Banking',
            reference_number='UPI12345678',
            created_by=cls.superuser,
        )

        # 9. Compliance Documents & Renewals
        cls.document = Document.objects.create(
            document_name='National Goods Permit',
            vehicle=cls.vehicle,
            document_number='NP-2026-999',
            expiry_date=date.today() + timedelta(days=90),
            added_by=cls.superuser,
        )
        cls.document_renewal = DocumentRenewal.objects.create(
            document=cls.document,
            valid_from=date.today() - timedelta(days=365),
            valid_to=date.today() + timedelta(days=90),
            cost=Decimal('3500.00'),
            renewed_by=cls.superuser,
        )

        # 10. Accounts Role (Group)
        cls.group = Group.objects.create(name='Operations Supervisor')

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.superuser)
        self.factory = RequestFactory()

    def _get_all_templates_on_disk(self):
        """Discovers all .html files inside templates/ directory."""
        templates_dir = os.path.join(settings.BASE_DIR, 'templates')
        template_files = set()
        for root, _, files in os.walk(templates_dir):
            for f in files:
                if f.endswith('.html'):
                    rel_path = os.path.relpath(os.path.join(root, f), templates_dir)
                    template_files.add(rel_path.replace(os.sep, '/'))
        return template_files

    def test_all_templates_compile_and_load(self):
        """
        Tier 1: Dynamic discovery & template compilation test.
        Verifies that every single .html template parses and compiles cleanly
        without TemplateSyntaxError or tag/filter resolution failures.
        """
        all_templates = self._get_all_templates_on_disk()
        self.assertGreater(len(all_templates), 90, "Expected at least 90 templates in the project.")

        load_errors = []
        for tpl in sorted(all_templates):
            try:
                get_template(tpl)
            except Exception as e:
                load_errors.append((tpl, str(e)))

        self.assertEqual(
            len(load_errors),
            0,
            f"Failed to load {len(load_errors)} template(s): {load_errors}",
        )

    def test_all_views_render_templates(self):
        """
        Tier 2: View execution smoke test across all non-admin view routes.
        Verifies that views execute with real data, return HTTP 200/302,
        render without 500 template exceptions, and generate non-empty HTML.
        """
        # Complete registry of all user-facing view routes
        view_routes = [
            # Accounts
            ('user-list', {}),
            ('user-create', {}),
            ('user-update', {'pk': self.superuser.pk}),
            ('user-delete', {'pk': self.driver_user.pk}),
            ('user-password-reset', {'pk': self.driver_user.pk}),
            ('user-profile', {}),
            ('user-profile-detail', {'pk': self.superuser.pk}),
            ('role-list', {}),
            ('role-create', {}),
            ('role-update', {'pk': self.group.pk}),
            ('role-delete', {'pk': self.group.pk}),
            ('login', {}),

            # Trips
            ('trip-list', {}),
            ('trip-detail', {'pk': self.trip.pk}),
            ('trip-create', {}),
            ('trip-bulk-create', {}),
            ('trip-update', {'pk': self.trip.pk}),
            ('trip-delete', {'pk': self.trip.pk}),
            ('trip-export-excel', {}),
            ('route-list', {}),
            ('route-create', {}),
            ('routes-dashboard', {}),
            ('route-detail', {'pk': self.route.pk}),
            ('route-dashboard', {'pk': self.route.pk}),
            ('route-update', {'pk': self.route.pk}),
            ('route-delete', {'pk': self.route.pk}),
            ('manager-dashboard', {}),

            # Fleet
            ('vehicle-list', {}),
            ('vehicle-detail', {'pk': self.vehicle.pk}),
            ('vehicle-create', {}),
            ('vehicle-update', {'pk': self.vehicle.pk}),
            ('vehicle-delete', {'pk': self.vehicle.pk}),
            ('maintenance-list', {}),
            ('maintenance-detail', {'pk': self.maintenance.pk}),
            ('maintenance-create', {}),
            ('maintenance-update', {'pk': self.maintenance.pk}),
            ('maintenance-complete', {'pk': self.maintenance.pk}),
            ('maintenance-delete', {'pk': self.maintenance.pk}),
            ('tyre-list', {}),
            ('tyre-detail', {'pk': self.tyre.pk}),
            ('tyre-create', {}),
            ('tyre-update', {'pk': self.tyre.pk}),
            ('tyre-delete', {'pk': self.tyre.pk}),
            ('tyre-log-create', {}),
            ('tyre-brand-list', {}),
            ('tyre-brand-create', {}),
            ('tyre-brand-update', {'pk': self.tyre_brand.pk}),
            ('tyre-brand-delete', {'pk': self.tyre_brand.pk}),

            # Ledger
            ('financialrecord-list', {}),
            ('financial-entry-review', {}),
            ('financialrecord-detail', {'pk': self.financial_record.pk}),
            ('financialrecord-create', {}),
            ('financialrecord-update', {'pk': self.financial_record.pk}),
            ('financialrecord-delete', {'pk': self.financial_record.pk}),
            ('financial-summary', {}),
            ('party-list', {}),
            ('party-create', {}),
            ('party-detail', {'pk': self.debtor_party.pk}),
            ('party-update', {'pk': self.debtor_party.pk}),
            ('party-delete', {'pk': self.debtor_party.pk}),
            ('party-statement-pdf', {'pk': self.debtor_party.pk}),
            ('account-list', {}),
            ('account-create', {}),
            ('account-detail', {'pk': self.company_account.pk}),
            ('account-update', {'pk': self.company_account.pk}),
            ('account-delete', {'pk': self.company_account.pk}),
            ('account-statement-pdf', {'pk': self.company_account.pk}),
            ('unified-ledger-pdf', {}),
            ('bill-list', {}),
            ('bill-create', {}),
            ('bill-detail', {'pk': self.bill.pk}),
            ('bill-update', {'pk': self.bill.pk}),
            ('vendor-bill-update', {'pk': self.vendor_bill.pk}),
            ('bill-delete', {'pk': self.bill.pk}),
            ('print-invoice', {'pk': self.bill.pk}),
            ('print-annexure', {'pk': self.bill.pk}),
            ('print-combined-bill', {'pk': self.bill.pk}),
            ('shared-bill', {'token': self.bill.share_token}),
            ('toll-ledger', {}),
            ('toll-account-list', {}),
            ('toll-account-create', {}),
            ('toll-account-update', {'pk': self.toll_account.pk}),
            ('toll-account-delete', {'pk': self.toll_account.pk}),
            ('toll-recharge-create', {}),
            ('toll-recharge-update', {'pk': self.toll_recharge.pk}),
            ('toll-recharge-delete', {'pk': self.toll_recharge.pk}),
            ('toll-entry-create', {}),
            ('toll-entry-bulk-create', {}),
            ('toll-entry-update', {'pk': self.toll_entry.pk}),
            ('toll-entry-delete', {'pk': self.toll_entry.pk}),
            ('toll-statement-print', {}),
            ('toll-statement-import', {}),
            ('toll-reconcile', {}),

            # Drivers
            ('driver-list', {}),
            ('driver-create', {}),
            ('driver-detail', {'pk': self.driver.pk}),
            ('driver-update', {'pk': self.driver.pk}),
            ('driver-ledger', {'pk': self.driver.pk}),
            ('driver-transaction-create', {'driver_pk': self.driver.pk}),

            # Documents
            ('document-list', {}),
            ('document-create-vehicle', {'vehicle_pk': self.vehicle.pk}),
            ('document-create-driver', {'driver_pk': self.driver.pk}),
            ('document-update', {'pk': self.document.pk}),
            ('document-delete', {'pk': self.document.pk}),
            ('document-renew', {'pk': self.document.pk}),
            ('document-history', {'pk': self.document.pk}),
            ('document-renewal-update', {'pk': self.document_renewal.pk}),
            ('document-renewal-delete', {'pk': self.document_renewal.pk}),
        ]

        failed_views = []
        for route_name, kwargs in view_routes:
            url = reverse(route_name, kwargs=kwargs)
            try:
                response = self.client.get(url)
                if response.status_code not in (200, 302):
                    failed_views.append((route_name, url, response.status_code, "Unexpected HTTP status"))
                elif response.status_code == 200 and len(response.content) == 0:
                    failed_views.append((route_name, url, response.status_code, "Empty content returned"))
            except Exception as e:
                failed_views.append((route_name, url, 500, str(e)))

        # Also test bulk-print-invoices with required issuer query parameter
        bulk_url = reverse('bulk-print-invoices') + f'?issuer={self.company_account.pk}'
        try:
            response = self.client.get(bulk_url)
            if response.status_code != 200:
                failed_views.append(('bulk-print-invoices', bulk_url, response.status_code, "Unexpected status"))
        except Exception as e:
            failed_views.append(('bulk-print-invoices', bulk_url, 500, str(e)))

        self.assertEqual(
            len(failed_views),
            0,
            f"Failed rendering {len(failed_views)} view(s): {failed_views}",
        )

    def test_includes_and_partials_render(self):
        """
        Tier 3: Standalone rendering test for shared includes & partial templates.
        Ensures reusable components render without variable lookup errors.
        """
        request = self.factory.get('/')
        request.user = self.superuser
        paginator = Paginator(['item1', 'item2'], 1)
        page_obj = paginator.get_page(1)

        partials_to_test = [
            (
                'includes/confirm_delete_card.html',
                {
                    'cancel_url': '/trips/',
                    'object_display': 'Test Item',
                    'title': 'Delete Confirmation',
                    'warning_text': 'Permanent action.',
                },
            ),
            (
                'includes/empty_state.html',
                {
                    'title': 'No records found',
                    'message': 'Create a record to get started.',
                },
            ),
            (
                'includes/pagination.html',
                {
                    'page_obj': page_obj,
                    'is_paginated': True,
                    'paginator': paginator,
                },
            ),
            (
                'includes/form_actions.html',
                {
                    'cancel_url': '/trips/',
                    'submit_text': 'Save Record',
                },
            ),
            (
                'includes/form_errors.html',
                {
                    'errors': ['Sample error message'],
                },
            ),
            (
                'ledger/partials/invoice_styles.html',
                {},
            ),
            (
                'ledger/partials/bill_content.html',
                {
                    'bill': self.bill,
                    'invoice_items': [],
                    'bill_trips': [],
                    'has_discount': False,
                    'has_lr': False,
                },
            ),
            (
                'fleet/includes/tyre_diagram.html',
                {
                    'vehicle': self.vehicle,
                    'axle_data': [],
                    'tyres_by_pos': {},
                },
            ),
            (
                'fleet/includes/tyre_axle_row.html',
                {
                    'axle_num': 1,
                    'left_slots': ['A1_LO'],
                    'right_slots': ['A1_RO'],
                    'tyres_by_pos': {},
                },
            ),
            (
                'fleet/includes/tyre_slot.html',
                {
                    'pos_code': 'A1_LO',
                    'pos_label': 'Left Outer',
                    'tyre': self.tyre,
                },
            ),
        ]

        errors = []
        for tpl_path, context in partials_to_test:
            try:
                rendered = render_to_string(tpl_path, context, request=request)
                self.assertGreater(len(rendered), 0)
            except Exception as e:
                errors.append((tpl_path, str(e)))

        self.assertEqual(
            len(errors),
            0,
            f"Failed rendering partials: {errors}",
        )

    def test_all_templates_covered(self):
        """
        Tier 4: Completeness assertion.
        Verifies that every single .html file on disk has been exercised by either
        the view smoke tests, layout extensions, or component render tests.
        """
        all_disk_templates = self._get_all_templates_on_disk()

        # Collect all templates rendered during a representative run
        rendered_templates = set()

        # Run client GETs and collect rendered templates
        sample_urls = [
            reverse('trip-list'),
            reverse('trip-detail', kwargs={'pk': self.trip.pk}),
            reverse('trip-create'),
            reverse('trip-bulk-create'),
            reverse('trip-update', kwargs={'pk': self.trip.pk}),
            reverse('trip-delete', kwargs={'pk': self.trip.pk}),
            reverse('trip-export-excel'),
            reverse('route-list'),
            reverse('route-create'),
            reverse('routes-dashboard'),
            reverse('route-detail', kwargs={'pk': self.route.pk}),
            reverse('route-dashboard', kwargs={'pk': self.route.pk}),
            reverse('route-update', kwargs={'pk': self.route.pk}),
            reverse('route-delete', kwargs={'pk': self.route.pk}),
            reverse('manager-dashboard'),
            reverse('vehicle-list'),
            reverse('vehicle-detail', kwargs={'pk': self.vehicle.pk}),
            reverse('vehicle-create'),
            reverse('vehicle-update', kwargs={'pk': self.vehicle.pk}),
            reverse('vehicle-delete', kwargs={'pk': self.vehicle.pk}),
            reverse('maintenance-list'),
            reverse('maintenance-detail', kwargs={'pk': self.maintenance.pk}),
            reverse('maintenance-create'),
            reverse('maintenance-update', kwargs={'pk': self.maintenance.pk}),
            reverse('maintenance-complete', kwargs={'pk': self.maintenance.pk}),
            reverse('maintenance-delete', kwargs={'pk': self.maintenance.pk}),
            reverse('tyre-list'),
            reverse('tyre-detail', kwargs={'pk': self.tyre.pk}),
            reverse('tyre-create'),
            reverse('tyre-update', kwargs={'pk': self.tyre.pk}),
            reverse('tyre-delete', kwargs={'pk': self.tyre.pk}),
            reverse('tyre-log-create'),
            reverse('tyre-brand-list'),
            reverse('tyre-brand-create'),
            reverse('tyre-brand-update', kwargs={'pk': self.tyre_brand.pk}),
            reverse('tyre-brand-delete', kwargs={'pk': self.tyre_brand.pk}),
            reverse('financialrecord-list'),
            reverse('financial-entry-review'),
            reverse('financialrecord-detail', kwargs={'pk': self.financial_record.pk}),
            reverse('financialrecord-create'),
            reverse('financialrecord-update', kwargs={'pk': self.financial_record.pk}),
            reverse('financialrecord-delete', kwargs={'pk': self.financial_record.pk}),
            reverse('financial-summary'),
            reverse('party-list'),
            reverse('party-create'),
            reverse('party-detail', kwargs={'pk': self.debtor_party.pk}),
            reverse('party-update', kwargs={'pk': self.debtor_party.pk}),
            reverse('party-delete', kwargs={'pk': self.debtor_party.pk}),
            reverse('party-statement-pdf', kwargs={'pk': self.debtor_party.pk}),
            reverse('account-list'),
            reverse('account-create'),
            reverse('account-detail', kwargs={'pk': self.company_account.pk}),
            reverse('account-update', kwargs={'pk': self.company_account.pk}),
            reverse('account-delete', kwargs={'pk': self.company_account.pk}),
            reverse('account-statement-pdf', kwargs={'pk': self.company_account.pk}),
            reverse('unified-ledger-pdf'),
            reverse('bill-list'),
            reverse('bill-create'),
            reverse('bill-detail', kwargs={'pk': self.bill.pk}),
            reverse('bill-update', kwargs={'pk': self.bill.pk}),
            reverse('vendor-bill-update', kwargs={'pk': self.vendor_bill.pk}),
            reverse('bill-delete', kwargs={'pk': self.bill.pk}),
            reverse('print-invoice', kwargs={'pk': self.bill.pk}),
            reverse('print-annexure', kwargs={'pk': self.bill.pk}),
            reverse('print-combined-bill', kwargs={'pk': self.bill.pk}),
            reverse('shared-bill', kwargs={'token': self.bill.share_token}),
            reverse('bulk-print-invoices') + f'?issuer={self.company_account.pk}',
            reverse('toll-ledger'),
            reverse('toll-account-list'),
            reverse('toll-account-create'),
            reverse('toll-account-update', kwargs={'pk': self.toll_account.pk}),
            reverse('toll-account-delete', kwargs={'pk': self.toll_account.pk}),
            reverse('toll-recharge-create'),
            reverse('toll-recharge-update', kwargs={'pk': self.toll_recharge.pk}),
            reverse('toll-recharge-delete', kwargs={'pk': self.toll_recharge.pk}),
            reverse('toll-entry-create'),
            reverse('toll-entry-bulk-create'),
            reverse('toll-entry-update', kwargs={'pk': self.toll_entry.pk}),
            reverse('toll-entry-delete', kwargs={'pk': self.toll_entry.pk}),
            reverse('toll-statement-print'),
            reverse('toll-statement-import'),
            reverse('toll-reconcile'),
            reverse('driver-list'),
            reverse('driver-create'),
            reverse('driver-detail', kwargs={'pk': self.driver.pk}),
            reverse('driver-update', kwargs={'pk': self.driver.pk}),
            reverse('driver-ledger', kwargs={'pk': self.driver.pk}),
            reverse('driver-transaction-create', kwargs={'driver_pk': self.driver.pk}),
            reverse('document-list'),
            reverse('document-create-vehicle', kwargs={'vehicle_pk': self.vehicle.pk}),
            reverse('document-create-driver', kwargs={'driver_pk': self.driver.pk}),
            reverse('document-update', kwargs={'pk': self.document.pk}),
            reverse('document-delete', kwargs={'pk': self.document.pk}),
            reverse('document-renew', kwargs={'pk': self.document.pk}),
            reverse('document-history', kwargs={'pk': self.document.pk}),
            reverse('document-renewal-update', kwargs={'pk': self.document_renewal.pk}),
            reverse('document-renewal-delete', kwargs={'pk': self.document_renewal.pk}),
            reverse('user-list'),
            reverse('user-create'),
            reverse('user-update', kwargs={'pk': self.superuser.pk}),
            reverse('user-delete', kwargs={'pk': self.driver_user.pk}),
            reverse('user-password-reset', kwargs={'pk': self.driver_user.pk}),
            reverse('user-profile'),
            reverse('user-profile-detail', kwargs={'pk': self.superuser.pk}),
            reverse('role-list'),
            reverse('role-create'),
            reverse('role-update', kwargs={'pk': self.group.pk}),
            reverse('role-delete', kwargs={'pk': self.group.pk}),
            reverse('login'),
        ]

        for url in sample_urls:
            resp = self.client.get(url)
            for t in getattr(resp, 'templates', []):
                if t.name:
                    rendered_templates.add(t.name.replace(os.sep, '/'))

        # Also add directly tested includes
        request = self.factory.get('/')
        rendered_templates.add('includes/confirm_delete_card.html')
        rendered_templates.add('includes/empty_state.html')

        uncovered = all_disk_templates - rendered_templates
        self.assertEqual(
            len(uncovered),
            0,
            f"The following {len(uncovered)} template(s) were not exercised: {sorted(uncovered)}",
        )
