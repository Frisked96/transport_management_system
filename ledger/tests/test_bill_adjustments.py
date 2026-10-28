from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal
from ledger.models import Bill, Party, CompanyAccount, TransactionCategory
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
