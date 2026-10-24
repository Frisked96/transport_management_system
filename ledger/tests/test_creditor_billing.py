from django.test import TestCase, Client
from django.utils import timezone
from django.db import models
from django.urls import reverse
import datetime
from decimal import Decimal
from ledger.models import (
    Bill, Party, CompanyAccount, TransactionCategory, FinancialRecord,
    TripAllocation, BillAllocation
)
from ledger.services import BalanceService, BillingService, TripFinancialService
from django.contrib.auth.models import User, Permission
from trips.models import Trip, Route
from fleet.models import Vehicle

class CreditorAttachedVehicleBillingTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_superuser(username='adminuser', password='password123')
        self.client.force_login(self.user)

        self.issuer = CompanyAccount.objects.create(
            name="Alpha Logistics",
            invoice_prefix="INV-{YYYY}/",
            cn_prefix="CN-{YYYY}/",
            dn_prefix="DN-{YYYY}/"
        )
        self.debtor = Party.objects.create(name="Customer Corp", party_type='Debtor')
        self.creditor = Party.objects.create(
            name="Vendor Trucking Services",
            party_type='Creditor',
            bank_name="HDFC Bank",
            account_number="9876543210",
            ifsc_code="HDFC0001234",
            bank_branch="Kolkata"
        )

        self.cat_trip_payment, _ = TransactionCategory.objects.get_or_create(name='Trip Payment', type='Income')
        self.cat_lorry_hire, _ = TransactionCategory.objects.get_or_create(name='Lorry Hire', type='Expense')
        self.cat_payment_out, _ = TransactionCategory.objects.get_or_create(name='Payment Out', type='Expense')
        self.cat_tds, _ = TransactionCategory.objects.get_or_create(name='TDS', type='Expense')

        self.vehicle = Vehicle.objects.create(
            registration_plate="WB01AB9999",
            ownership='Attached',
            vendor=self.creditor
        )
        self.route = Route.objects.create(pickup_location="Kolkata", delivery_location="Patna")

    def test_attached_vehicle_trip_accrual_and_creditor_bill_creation(self):
        """
        Verify that:
        1. Trip creation on attached vehicle creates trip accruals in both debtor and creditor ledgers.
        2. Customer bill creation consolidates both and generates a linked Creditor Bill.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1500.00'),
            vendor_hire_amount=Decimal('22000.00'),
            date=timezone.now().date()
        )

        # Accrual records before customer bill
        debtor_record = FinancialRecord.objects.filter(associated_trip=trip, party=self.debtor).first()
        creditor_record = FinancialRecord.objects.filter(associated_trip=trip, party=self.creditor).first()
        self.assertIsNotNone(debtor_record)
        self.assertIsNotNone(creditor_record)
        self.assertEqual(creditor_record.amount, Decimal('22000.00'))
        self.assertEqual(trip.is_billed, False)
        self.assertEqual(trip.is_creditor_billed, False)

        # Create customer bill
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            creditor_invoice_number="VENDOR-INV-101",
            creditor_invoice_date=timezone.now().date()
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        # Check linked Creditor Bill was generated
        self.assertEqual(customer_bill.creditor_bills.count(), 1)
        creditor_bill = customer_bill.creditor_bills.first()
        self.assertEqual(creditor_bill.customer_bill, customer_bill)
        self.assertEqual(creditor_bill.party, self.creditor)
        self.assertEqual(creditor_bill.creditor_invoice_number, "VENDOR-INV-101")
        self.assertEqual(creditor_bill.display_invoice_number, "VENDOR-INV-101")
        self.assertEqual(creditor_bill.subtotal, Decimal('22000.00'))
        self.assertEqual(creditor_bill.rounded_total, Decimal('22000.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('22000.00'))
        self.assertEqual(creditor_bill.payment_status, 'Unpaid')

        # Check Trip status
        trip.refresh_from_db()
        self.assertTrue(trip.is_billed)
        self.assertTrue(trip.is_creditor_billed)
        self.assertEqual(trip.associated_bill, customer_bill)
        self.assertEqual(trip.creditor_bill, creditor_bill)

        # Check trip accruals removed and replaced with consolidated entries
        self.assertFalse(FinancialRecord.objects.filter(associated_trip=trip).exists())
        debtor_bill_record = FinancialRecord.objects.filter(associated_bill=customer_bill, party=self.debtor).first()
        self.assertIsNotNone(debtor_bill_record)
        creditor_bill_record = FinancialRecord.objects.filter(associated_bill=creditor_bill, party=self.creditor).first()
        self.assertIsNotNone(creditor_bill_record)
        self.assertEqual(creditor_bill_record.amount, Decimal('22000.00'))
        self.assertEqual(creditor_bill_record.category, self.cat_lorry_hire)

    def test_creditor_bill_payments_and_tds_settlement(self):
        """
        Verify that recording Payment Out and TDS reduces creditor bill outstanding
        and transitions payment status appropriately.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1500.00'),
            vendor_hire_amount=Decimal('25000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            creditor_invoice_number="VEND-202"
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('25000.00'))

        # Record partial payment out: ₹15,000
        p1 = FinancialRecord.objects.create(
            party=self.creditor,
            account=self.issuer,
            record_type='Expense',
            category=self.cat_payment_out,
            amount=Decimal('15000.00'),
            associated_bill=creditor_bill,
            date=timezone.now().date()
        )
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('15000.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('10000.00'))
        self.assertEqual(creditor_bill.payment_status, 'Partially Paid')

        # Record TDS: ₹2,500
        p2 = FinancialRecord.objects.create(
            party=self.creditor,
            account=self.issuer,
            record_type='Expense',
            category=self.cat_tds,
            amount=Decimal('2500.00'),
            associated_bill=creditor_bill,
            date=timezone.now().date()
        )
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('17500.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('7500.00'))
        self.assertEqual(creditor_bill.payment_status, 'Partially Paid')

        # Record final payment out: ₹7,500
        p3 = FinancialRecord.objects.create(
            party=self.creditor,
            account=self.issuer,
            record_type='Expense',
            category=self.cat_payment_out,
            amount=Decimal('7500.00'),
            associated_bill=creditor_bill,
            date=timezone.now().date()
        )
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('25000.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('0.00'))
        self.assertEqual(creditor_bill.payment_status, 'Paid')

    def test_customer_bill_deletion_cascades_and_restores_accruals(self):
        """
        Verify that deleting the customer bill deletes the linked Creditor Bill
        and automatically restores unbilled trip accruals for both Debtor and Creditor.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('1000.00'),
            vendor_hire_amount=Decimal('8000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            creditor_invoice_number="VEND-303"
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()
        creditor_bill_id = creditor_bill.pk

        # Delete customer bill
        customer_bill.delete()

        # Creditor bill must be deleted
        self.assertFalse(Bill.objects.filter(pk=creditor_bill_id).exists())

        # Trip must be restored to unbilled
        trip.refresh_from_db()
        self.assertFalse(trip.is_billed)
        self.assertFalse(trip.is_creditor_billed)
        self.assertIsNone(trip.associated_bill)
        self.assertIsNone(trip.creditor_bill)

        # Accruals restored
        self.assertTrue(FinancialRecord.objects.filter(associated_trip=trip, party=self.debtor).exists())
        self.assertTrue(FinancialRecord.objects.filter(associated_trip=trip, party=self.creditor).exists())

    def test_creditor_bill_views_and_filters(self):
        """
        Test bill-list filtering, bill-detail rendering, and party-detail tabs for creditor billing.
        """
        from django.urls import reverse
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('15.00'),
            rate_per_ton=Decimal('2000.00'),
            vendor_hire_amount=Decimal('24000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            creditor_invoice_number="VEND-VIEW-999"
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()

        # 1. bill-list with bill_kind=customer
        resp_cust = self.client.get(reverse('bill-list') + '?bill_kind=customer')
        self.assertEqual(resp_cust.status_code, 200)
        self.assertContains(resp_cust, customer_bill.bill_number)
        self.assertNotContains(resp_cust, "VEND-VIEW-999")

        # 2. bill-list with bill_kind=vendor
        resp_vend = self.client.get(reverse('bill-list') + '?bill_kind=vendor')
        self.assertEqual(resp_vend.status_code, 200)
        self.assertContains(resp_vend, "VEND-VIEW-999")
        self.assertContains(resp_vend, "Vendor Bill")

        # 3. Search by creditor invoice number
        resp_search = self.client.get(reverse('bill-list') + '?search=VEND-VIEW-999')
        self.assertEqual(resp_search.status_code, 200)
        self.assertContains(resp_search, "VEND-VIEW-999")

        # 4. Customer bill detail renders linked creditor bill
        resp_cust_detail = self.client.get(reverse('bill-detail', kwargs={'pk': customer_bill.pk}))
        self.assertEqual(resp_cust_detail.status_code, 200)
        self.assertContains(resp_cust_detail, "Linked Vendor Bills (Attached)")
        self.assertContains(resp_cust_detail, "VEND-VIEW-999")

        # 5. Creditor bill detail renders vendor bill format
        resp_vend_detail = self.client.get(reverse('bill-detail', kwargs={'pk': creditor_bill.pk}))
        self.assertEqual(resp_vend_detail.status_code, 200)
        self.assertContains(resp_vend_detail, "Vendor Bill / Lorry Hire Invoice")
        self.assertContains(resp_vend_detail, "VEND-VIEW-999")
        self.assertContains(resp_vend_detail, customer_bill.bill_number)
        self.assertContains(resp_vend_detail, "1,600.00")

        # 6. Creditor party detail renders operations and invoices
        resp_party = self.client.get(reverse('party-detail', kwargs={'pk': self.creditor.pk}))
        self.assertEqual(resp_party.status_code, 200)
        self.assertContains(resp_party, "Operations")
        self.assertContains(resp_party, "Invoices")
        self.assertContains(resp_party, "VEND-VIEW-999")

    def test_migrate_attached_creditor_bills_dry_run(self):
        """
        Verify that migrate_attached_creditor_bills with --dry-run previews
        without persisting any creditor bills or hire amounts.
        """
        from django.core.management import call_command
        import io

        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('2000.00'),
            vendor_hire_amount=Decimal('0.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            gst_rate=18,
            gst_type=Bill.GST_TYPE_GST
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        # Initially 0 creditor bills
        self.assertEqual(Bill.objects.filter(customer_bill__isnull=False).count(), 0)

        out = io.StringIO()
        call_command('migrate_attached_creditor_bills', '--dry-run', stdout=out)

        # Still 0 creditor bills after dry-run
        self.assertEqual(Bill.objects.filter(customer_bill__isnull=False).count(), 0)
        trip.refresh_from_db()
        self.assertEqual(trip.vendor_hire_amount, Decimal('0.00'))
        self.assertIn("DRY-RUN COMPLETE", out.getvalue())

    def test_migrate_attached_creditor_bills_commit(self):
        """
        Verify that migrate_attached_creditor_bills with --commit generates
        creditor bills including GST, backfills hire amounts, and updates vendor balances.
        Also verifies idempotency on repeated execution.
        """
        from django.core.management import call_command
        import io

        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1500.00'),
            vendor_hire_amount=Decimal('0.00'),
            date=timezone.now().date()
        )
        # Revenue = 20 * 1500 = 30,000
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            gst_rate=18,
            gst_type=Bill.GST_TYPE_GST
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        out = io.StringIO()
        call_command('migrate_attached_creditor_bills', '--commit', stdout=out)

        # 1. Trip vendor_hire_amount backfilled to base revenue (30,000)
        trip.refresh_from_db()
        self.assertEqual(trip.vendor_hire_amount, Decimal('30000.00'))

        # 2. Creditor bill created with 18% GST
        creditor_bills = Bill.objects.filter(customer_bill=customer_bill)
        self.assertEqual(creditor_bills.count(), 1)
        cr_bill = creditor_bills.first()
        self.assertEqual(cr_bill.party, self.creditor)
        self.assertEqual(cr_bill.subtotal_cached, Decimal('30000.00'))
        self.assertEqual(cr_bill.gst_rate, 18)
        self.assertEqual(cr_bill.gst_type, Bill.GST_TYPE_GST)
        # 30000 * 18% = 5400 GST, Total = 35400
        self.assertEqual(cr_bill.gst_amount_cached, Decimal('5400.00'))
        self.assertEqual(cr_bill.total_amount_cached, Decimal('35400.00'))

        # 3. FinancialRecord has full amount with GST
        fr = FinancialRecord.objects.filter(associated_bill=cr_bill, party=self.creditor).first()
        self.assertIsNotNone(fr)
        self.assertEqual(fr.amount, Decimal('35400.00'))

        # 4. Vendor balance reflects full credit including GST
        self.creditor.refresh_from_db()
        self.assertEqual(self.creditor.total_credit_amount, Decimal('35400.00'))
        self.assertEqual(self.creditor.current_balance_cached, Decimal('-35400.00'))

        # 5. Idempotent re-run
        call_command('migrate_attached_creditor_bills', '--commit', stdout=out)
        self.assertEqual(Bill.objects.filter(customer_bill=customer_bill).count(), 1)
        self.assertEqual(FinancialRecord.objects.filter(associated_bill=cr_bill, party=self.creditor).count(), 1)
        self.creditor.refresh_from_db()
        self.assertEqual(self.creditor.current_balance_cached, Decimal('-35400.00'))

    def test_auto_mirror_customer_credit_note_as_creditor_debit_note(self):
        """
        Verify that creating a Customer Credit Note against a customer bill with attached trips
        automatically generates a mirrored Debit Note for the vendor, properly reducing
        the vendor bill's outstanding balance and vendor ledger balance.
        Also verifies deletion of the Customer CN cascades and restores vendor balance.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('20.00'),
            rate_per_ton=Decimal('1000.00'),
            vendor_hire_amount=Decimal('20000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
            gst_rate=18,
            gst_type=Bill.GST_TYPE_GST
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()
        self.assertEqual(creditor_bill.total_amount_cached, Decimal('23600.00')) # 20000 + 3600 GST
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('23600.00'))

        # Create Customer Credit Note for Shortage: ₹2,000 subtotal + 18% GST = ₹2,360
        cat_cn = TransactionCategory.objects.get(name='Credit Note')
        cust_cn = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            original_bill=customer_bill,
            bill_type=Bill.TYPE_STANDARD,
            category=cat_cn,
            date=timezone.now().date(),
            item_type="SHORTAGE",
            amount_override=Decimal('2000.00'),
            gst_rate=18,
            gst_type=Bill.GST_TYPE_GST
        )
        cust_cn.sync_to_ledger()

        # 1. Mirrored Debit Note created for Creditor
        mirrored_dn = Bill.objects.filter(customer_bill=cust_cn, party=self.creditor).first()
        self.assertIsNotNone(mirrored_dn)
        self.assertEqual(mirrored_dn.original_bill, creditor_bill)
        self.assertEqual(mirrored_dn.category.name, 'Debit Note')
        self.assertEqual(mirrored_dn.total_amount_cached, Decimal('23600.00') if False else Decimal('2360.00'))
        self.assertEqual(mirrored_dn.item_type, 'SHORTAGE')

        # 2. Creditor Bill outstanding reduced
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('2360.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('21240.00')) # 23600 - 2360

        # 3. Vendor balance updated (Net Liability: 23600 - 2360 = 21240 Cr)
        self.creditor.refresh_from_db()
        self.assertEqual(self.creditor.total_debit_amount, Decimal('2360.00'))
        self.assertEqual(self.creditor.total_credit_amount, Decimal('23600.00'))
        self.assertEqual(self.creditor.current_balance_cached, Decimal('-21240.00'))

        # 4. Deleting Customer Credit Note cascades and deletes mirrored Debit Note
        cust_cn_pk = cust_cn.pk
        cust_cn.delete()
        self.assertFalse(Bill.objects.filter(customer_bill_id=cust_cn_pk).exists())

        # Parent creditor bill restored
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.amount_received_cached, Decimal('0.00'))
        self.assertEqual(creditor_bill.outstanding_balance_cached, Decimal('23600.00'))

        # Vendor balance restored
        self.creditor.refresh_from_db()
        self.assertEqual(self.creditor.current_balance_cached, Decimal('-23600.00'))

    def test_vendor_bill_direct_update_view_and_form(self):
        """
        Verify that a vendor bill can be directly updated with vendor bill no and date,
        updating the creditor bill, ledger entry, and synced customer bill.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('2000.00'),
            vendor_hire_amount=Decimal('15000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()
        self.assertIsNotNone(creditor_bill)
        self.assertFalse(creditor_bill.creditor_invoice_number)

        # 1. GET vendor-bill-update
        get_url = reverse('vendor-bill-update', kwargs={'pk': creditor_bill.pk})
        resp_get = self.client.get(get_url)
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, "Update Vendor Bill")
        self.assertContains(resp_get, self.vehicle.registration_plate)
        self.assertContains(resp_get, "id_creditor_invoice_number")
        self.assertContains(resp_get, "id_creditor_invoice_date")

        # 2. POST updates
        new_date = datetime.date(2026, 10, 15)
        resp_post = self.client.post(get_url, {
            'creditor_invoice_number': 'PHYSICAL-VB-777',
            'creditor_invoice_date': new_date.strftime('%Y-%m-%d'),
        })
        self.assertRedirects(resp_post, reverse('bill-detail', kwargs={'pk': creditor_bill.pk}))

        # 3. Verify creditor_bill updated
        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.creditor_invoice_number, 'PHYSICAL-VB-777')
        self.assertEqual(creditor_bill.creditor_invoice_date, new_date)
        self.assertEqual(creditor_bill.date, new_date)
        self.assertEqual(creditor_bill.display_invoice_number, 'PHYSICAL-VB-777')

        # 4. Verify associated ledger entry updated
        ledger_entry = FinancialRecord.objects.filter(associated_bill=creditor_bill, record_type=FinancialRecord.RECORD_TYPE_INVOICE).first()
        self.assertIsNotNone(ledger_entry)
        self.assertEqual(ledger_entry.date, new_date)
        self.assertIn('PHYSICAL-VB-777', ledger_entry.description)

        # 5. Verify customer bill synced
        customer_bill.refresh_from_db()
        self.assertEqual(customer_bill.creditor_invoice_number, 'PHYSICAL-VB-777')
        self.assertEqual(customer_bill.creditor_invoice_date, new_date)

    def test_bill_update_view_redirects_creditor_bill_to_vendor_update(self):
        """
        Verify that accessing bill-update on a creditor bill redirects to vendor-bill-update.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('5.00'),
            rate_per_ton=Decimal('2000.00'),
            vendor_hire_amount=Decimal('8000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()
        resp = self.client.get(reverse('bill-update', kwargs={'pk': creditor_bill.pk}))
        self.assertRedirects(resp, reverse('vendor-bill-update', kwargs={'pk': creditor_bill.pk}))

    def test_customer_bill_resave_preserves_vendor_bill_details(self):
        """
        Verify that saving a customer bill with empty creditor_invoice_number does not
        erase a previously set vendor bill number on creditor bill.
        """
        trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            route=self.route,
            weight=Decimal('10.00'),
            rate_per_ton=Decimal('2000.00'),
            vendor_hire_amount=Decimal('12000.00'),
            date=timezone.now().date()
        )
        customer_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.debtor,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_TRIP,
            category=self.cat_trip_payment,
        )
        customer_bill.trips.add(trip)
        customer_bill.sync_to_ledger()

        creditor_bill = customer_bill.creditor_bills.first()
        creditor_bill.creditor_invoice_number = "DIRECT-VEND-123"
        creditor_bill.creditor_invoice_date = datetime.date(2026, 10, 10)
        creditor_bill.save()

        # Re-sync/save customer bill
        customer_bill.sync_to_ledger()

        creditor_bill.refresh_from_db()
        self.assertEqual(creditor_bill.creditor_invoice_number, "DIRECT-VEND-123")
        self.assertEqual(creditor_bill.creditor_invoice_date, datetime.date(2026, 10, 10))



