import json
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal
from ledger.models import Bill, BillItem, Party, CompanyAccount, TransactionCategory
from fleet.models import Vehicle
from django.contrib.auth.models import User, Permission


class BillAdjustmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser')
        self.party = Party.objects.create(name="Test Party")
        self.issuer = CompanyAccount.objects.create(
            name="Test Issuer",
            invoice_prefix="INV-{YYYY}/",
            cn_prefix="CN-{YYYY}/",
            dn_prefix="DN-{YYYY}/"
        )
        self.category_invoice = TransactionCategory.objects.get_or_create(name='Standard', type='Income')[0]
        self.category_cn = TransactionCategory.objects.get_or_create(name='Credit Note', type='Income')[0]
        self.category_halting = TransactionCategory.objects.get_or_create(name='Halting', type='Income')[0]

        # Give permissions to testuser
        for codename in ['add_bill', 'change_bill', 'view_bill']:
            perm = Permission.objects.get(codename=codename)
            self.user.user_permissions.add(perm)

    def test_credit_note_reassignment_updates_caches(self):
        """
        Test that moving a Credit Note from one bill to another updates the 
        outstanding balance of BOTH bills correctly.
        """
        # 1. Create two Bills
        bill_a = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('1000.00'),
            category=self.category_invoice
        )
        
        bill_b = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('2000.00'),
            category=self.category_invoice
        )

        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('1000.00'))
        self.assertEqual(bill_b.outstanding_balance_cached, Decimal('2000.00'))

        # 2. Create a Credit Note for Bill A
        cn = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('100.00'),
            category=self.category_cn,
            original_bill=bill_a
        )

        bill_a.refresh_from_db()
        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('900.00'))
        
        # 3. Reassign CN to Bill B
        cn.original_bill = bill_b
        cn.save()

        bill_a.refresh_from_db()
        bill_b.refresh_from_db()
        
        # Bill A should be restored to 1000.00
        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('1000.00'))
        # Bill B should be reduced to 1900.00
        self.assertEqual(bill_b.outstanding_balance_cached, Decimal('1900.00'))

    def test_credit_note_deletion_updates_cache(self):
        """
        Test that deleting a Credit Note updates the original bill's cache.
        """
        bill_a = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('1000.00'),
            category=self.category_invoice
        )

        cn = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('100.00'),
            category=self.category_cn,
            original_bill=bill_a
        )

        bill_a.refresh_from_db()
        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('900.00'))

        # Delete the CN
        cn.delete()
        
        bill_a.refresh_from_db()
        self.assertEqual(bill_a.outstanding_balance_cached, Decimal('1000.00'))

    def test_credit_note_excluded_from_pending_bill_filter(self):
        """
        Test that credit notes and debit notes are treated as settlements ('Applied'),
        have zero outstanding balance, and are excluded from the pending/paid payment status filters in bill-list.
        """
        # User needs permission to view bills
        view_perm = Permission.objects.get(codename='view_bill')
        self.user.user_permissions.add(view_perm)
        self.client.force_login(self.user)

        # 1. Create a Standard Invoice that is unpaid/pending
        invoice = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('5000.00'),
            category=self.category_invoice
        )

        # 2. Create a Credit Note (settlement adjustment)
        cn = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('500.00'),
            category=self.category_cn,
            original_bill=invoice
        )

        # Verify Credit Note attributes
        cn.refresh_from_db()
        self.assertTrue(cn.is_adjustment)
        self.assertEqual(cn.payment_status, 'Applied')
        self.assertEqual(cn.outstanding_balance_cached, Decimal('0.00'))
        self.assertEqual(cn.outstanding_balance, Decimal('0.00'))

        # Verify invoice balance reduced by CN
        invoice.refresh_from_db()
        self.assertEqual(invoice.outstanding_balance, Decimal('4500.00'))

        # 3. Query bill-list with payment_status=pending
        url = reverse('bill-list') + '?payment_status=pending'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        bills_in_context = list(response.context['bills'])
        self.assertIn(invoice, bills_in_context)
        self.assertNotIn(cn, bills_in_context)

        # 4. Query bill-list with payment_status=paid
        url_paid = reverse('bill-list') + '?payment_status=paid'
        response_paid = self.client.get(url_paid)
        self.assertEqual(response_paid.status_code, 200)

        bills_paid = list(response_paid.context['bills'])
        self.assertNotIn(cn, bills_paid)
        self.assertNotIn(invoice, bills_paid)

    def test_create_halting_bill_with_multiple_vehicles(self):
        """
        Test creating a Halting Bill with multiple vehicle rows via the bill creation view.
        Verifies BillItem persistence, multi-vehicle rows, subtotal, GST, and total calculation.
        """
        self.client.force_login(self.user)
        v1 = Vehicle.objects.create(registration_plate="RJ 14 GA 1111", make_model="Tata 1613")
        v2 = Vehicle.objects.create(registration_plate="RJ 14 GB 2222", make_model="Ashok Leyland")

        items_payload = [
            {
                'vehicle_id': v1.id,
                'vehicle_number': v1.registration_plate,
                'description': 'Halting charges at warehouse A',
                'quantity': 2,
                'rate': 1500,
                'amount': 3000,
                'lr_no': 'LR-101'
            },
            {
                'vehicle_id': v2.id,
                'vehicle_number': v2.registration_plate,
                'description': 'Halting charges at factory B',
                'quantity': 3,
                'rate': 1500,
                'amount': 4500,
                'lr_no': 'LR-102'
            },
            {
                'vehicle_id': '',
                'vehicle_number': 'RJ 14 MT 9999',
                'description': 'Halting charges (Market truck)',
                'quantity': 1,
                'rate': 1000,
                'amount': 1000,
                'lr_no': 'LR-103'
            }
        ]

        post_data = {
            'bill_type': Bill.TYPE_STANDARD,
            'category': self.category_halting.id,
            'issuer': self.issuer.id,
            'party': self.party.id,
            'date': timezone.now().date().isoformat(),
            'item_type': 'Halting Charges',
            'gst_rate': 18,
            'gst_type': 'GST',
            'use_roundoff': True,
            'items_data': json.dumps(items_payload),
        }

        url = reverse('bill-create')
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        # Retrieve created bill
        bill = Bill.objects.filter(party=self.party, category=self.category_halting).first()
        self.assertIsNotNone(bill)
        self.assertEqual(bill.items.count(), 3)

        # Verify amounts
        # 3000 + 4500 + 1000 = 8500 subtotal
        self.assertEqual(bill.subtotal, Decimal('8500.00'))
        # 18% of 8500 = 1530.00
        self.assertEqual(bill.gst_amount, Decimal('1530.00'))
        # Total = 8500 + 1530 = 10030.00
        self.assertEqual(bill.rounded_total, Decimal('10030.00'))
        self.assertEqual(bill.total_amount_cached, Decimal('10030.00'))

        # Verify individual items
        items = list(bill.items.order_by('id'))
        self.assertEqual(items[0].vehicle, v1)
        self.assertEqual(items[0].vehicle_number, 'RJ 14 GA 1111')
        self.assertEqual(items[0].amount, Decimal('3000.00'))
        self.assertEqual(items[0].lr_no, 'LR-101')

        self.assertEqual(items[1].vehicle, v2)
        self.assertEqual(items[1].amount, Decimal('4500.00'))

        self.assertIsNone(items[2].vehicle)
        self.assertEqual(items[2].vehicle_number, 'RJ 14 MT 9999')
        self.assertEqual(items[2].amount, Decimal('1000.00'))

        # Verify detail view and print view render all vehicle rows
        detail_url = reverse('bill-detail', kwargs={'pk': bill.pk})
        detail_resp = self.client.get(detail_url)
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, 'RJ 14 GA 1111')
        self.assertContains(detail_resp, 'RJ 14 GB 2222')
        self.assertContains(detail_resp, 'RJ 14 MT 9999')
        self.assertContains(detail_resp, '3,000.00')
        self.assertContains(detail_resp, '4,500.00')
        self.assertContains(detail_resp, '1,000.00')

        # Test combined bill print view
        print_url = reverse('print-combined-bill', kwargs={'pk': bill.pk})
        print_resp = self.client.get(print_url)
        self.assertEqual(print_resp.status_code, 200)
        self.assertContains(print_resp, 'RJ 14 GA 1111')
        self.assertContains(print_resp, 'RJ 14 GB 2222')
        self.assertContains(print_resp, 'RJ 14 MT 9999')

    def test_create_credit_note_with_multiple_vehicles_against_invoice(self):
        """
        Test creating a Credit Note with multiple vehicle rows against an existing invoice.
        Verifies that each vehicle line item is saved and original bill's outstanding balance is adjusted.
        """
        self.client.force_login(self.user)
        v1 = Vehicle.objects.create(registration_plate="RJ 14 CN 0001")
        v2 = Vehicle.objects.create(registration_plate="RJ 14 CN 0002")

        # Create original bill for 50000
        orig_bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            amount_override=Decimal('50000.00'),
            category=self.category_invoice
        )
        self.assertEqual(orig_bill.outstanding_balance_cached, Decimal('50000.00'))

        items_payload = [
            {
                'vehicle_id': v1.id,
                'vehicle_number': v1.registration_plate,
                'description': 'Shortage deduction for vehicle 1',
                'quantity': 1,
                'rate': 2000,
                'amount': 2000,
                'lr_no': 'LR-CN-01'
            },
            {
                'vehicle_id': v2.id,
                'vehicle_number': v2.registration_plate,
                'description': 'Damage deduction for vehicle 2',
                'quantity': 1,
                'rate': 3000,
                'amount': 3000,
                'lr_no': 'LR-CN-02'
            }
        ]

        post_data = {
            'bill_type': Bill.TYPE_STANDARD,
            'category': self.category_cn.id,
            'original_bill': orig_bill.id,
            'issuer': self.issuer.id,
            'party': self.party.id,
            'date': timezone.now().date().isoformat(),
            'item_type': 'Credit Note',
            'gst_rate': 0,
            'gst_type': 'GST',
            'items_data': json.dumps(items_payload),
        }

        url = reverse('bill-create')
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        cn = Bill.objects.filter(party=self.party, category=self.category_cn, original_bill=orig_bill).first()
        self.assertIsNotNone(cn)
        self.assertEqual(cn.items.count(), 2)
        self.assertEqual(cn.subtotal, Decimal('5000.00'))
        self.assertEqual(cn.total_amount_cached, Decimal('5000.00'))
        self.assertEqual(cn.payment_status_cached, 'Applied')

        # Verify original bill outstanding is reduced from 50000 to 45000
        orig_bill.refresh_from_db()
        self.assertEqual(orig_bill.outstanding_balance_cached, Decimal('45000.00'))

    def test_update_halting_bill_multiple_vehicles(self):
        """
        Test editing a Halting Bill to modify vehicle items (updating amounts, adding a row, removing a row).
        """
        self.client.force_login(self.user)
        v1 = Vehicle.objects.create(registration_plate="RJ 14 UP 0001")
        v2 = Vehicle.objects.create(registration_plate="RJ 14 UP 0002")
        v3 = Vehicle.objects.create(registration_plate="RJ 14 UP 0003")

        # Create halting bill with 2 initial items
        bill = Bill.objects.create(
            issuer=self.issuer,
            party=self.party,
            date=timezone.now().date(),
            bill_type=Bill.TYPE_STANDARD,
            category=self.category_halting,
            amount_override=Decimal('4000.00'),
            gst_rate=0
        )
        BillItem.objects.create(bill=bill, vehicle=v1, description="Halt 1", quantity=1, rate=2000, amount=2000)
        BillItem.objects.create(bill=bill, vehicle=v2, description="Halt 2", quantity=1, rate=2000, amount=2000)
        bill.update_financial_caches()
        self.assertEqual(bill.subtotal, Decimal('4000.00'))

        # Edit view GET test - form should serialize items_data in initial
        update_url = reverse('bill-update', kwargs={'pk': bill.pk})
        get_resp = self.client.get(update_url)
        self.assertEqual(get_resp.status_code, 200)
        form = get_resp.context['form']
        initial_items = json.loads(form.fields['items_data'].initial)
        self.assertEqual(len(initial_items), 2)

        # Update bill: change v1 amount to 3500, remove v2, and add v3 with amount 2500
        updated_items = [
            {
                'vehicle_id': v1.id,
                'vehicle_number': v1.registration_plate,
                'description': 'Updated Halt 1',
                'quantity': 1,
                'rate': 3500,
                'amount': 3500,
            },
            {
                'vehicle_id': v3.id,
                'vehicle_number': v3.registration_plate,
                'description': 'New Halt 3',
                'quantity': 1,
                'rate': 2500,
                'amount': 2500,
            }
        ]

        post_data = {
            'bill_type': Bill.TYPE_STANDARD,
            'category': self.category_halting.id,
            'issuer': self.issuer.id,
            'party': self.party.id,
            'date': timezone.now().date().isoformat(),
            'item_type': 'Halting Charges',
            'gst_rate': 0,
            'gst_type': 'GST',
            'items_data': json.dumps(updated_items),
        }

        post_resp = self.client.post(update_url, post_data)
        self.assertEqual(post_resp.status_code, 302)

        bill.refresh_from_db()
        self.assertEqual(bill.items.count(), 2)
        # 3500 + 2500 = 6000
        self.assertEqual(bill.subtotal, Decimal('6000.00'))
        self.assertEqual(bill.total_amount_cached, Decimal('6000.00'))

        plates = list(bill.items.values_list('vehicle_number', flat=True))
        self.assertIn('RJ 14 UP 0001', plates)
        self.assertIn('RJ 14 UP 0003', plates)
        self.assertNotIn('RJ 14 UP 0002', plates)
