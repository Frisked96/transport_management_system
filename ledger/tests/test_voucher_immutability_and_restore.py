"""
Comprehensive test suite for Universal System-Generated Immutable Vouchers,
Soft-Deletion, 1-Click Restoration, Alteration Diff Tracking, and Review Dashboard.
"""
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Permission
from django.utils import timezone
from django.core.exceptions import PermissionDenied

from ledger.models import (
    FinancialRecord, FinancialVoucher, Party, CompanyAccount,
    TransactionCategory, Bill, BillTrip, TripAllocation
)
from trips.models import Trip
from fleet.models import Vehicle


class VoucherImmutabilityAndRestoreTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Users: superuser, operator (Akash)
        self.superuser = User.objects.create_superuser('admin_user', 'admin@example.com', 'adminpass')
        self.operator = User.objects.create_user('akash', 'akash@example.com', 'akashpass')
        perms = Permission.objects.filter(codename__in=[
            'add_financialrecord', 'change_financialrecord', 'delete_financialrecord',
            'can_view_financial_records', 'can_manage_financial_records'
        ])
        self.operator.user_permissions.add(*perms)

        # Company Account & Party
        self.account = CompanyAccount.objects.create(
            name='Primary Operating Bank',
            opening_balance=Decimal('100000.00')
        )
        self.debtor = Party.objects.create(
            name='Test Logistics Debtor',
            party_type=Party.TYPE_DEBTOR,
            opening_balance=Decimal('0.00')
        )
        self.vendor = Party.objects.create(
            name='Test Fleet Vendor',
            party_type=Party.TYPE_CREDITOR,
            opening_balance=Decimal('0.00')
        )

        # Vehicle & Trip
        self.vehicle = Vehicle.objects.create(
            registration_plate='CG 04 AA 1234',
            ownership='Owned'
        )
        self.trip = Trip.objects.create(
            vehicle=self.vehicle,
            party=self.debtor,
            revenue_type=Trip.REVENUE_FIXED,
            rate_per_ton=Decimal('50000.00'),
            date=timezone.now().date(),
            created_by=self.operator
        )

        # Categories
        self.cat_payment_in, _ = TransactionCategory.objects.get_or_create(
            name='Trip Payment',
            defaults={'type': TransactionCategory.TYPE_INCOME}
        )
        self.cat_expense, _ = TransactionCategory.objects.get_or_create(
            name='Office Expense',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )
        self.cat_tds, _ = TransactionCategory.objects.get_or_create(
            name='TDS',
            defaults={'type': TransactionCategory.TYPE_EXPENSE}
        )

    def test_financial_record_auto_attaches_system_generated_voucher(self):
        """Any financial record created anywhere automatically receives an immutable voucher"""
        record = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('15000.00'),
            recorded_by=self.operator,
            description='Direct payment entry'
        )

        self.assertIsNotNone(record.voucher)
        self.assertTrue(record.voucher.voucher_number.startswith('REF-'))
        self.assertEqual(record.voucher.created_by, self.operator)
        self.assertEqual(record.voucher.verification_status, FinancialVoucher.STATUS_PENDING)
        self.assertEqual(record.reference_number, record.voucher.voucher_number)

    def test_voucher_immutability_prevents_hard_delete_and_number_alteration(self):
        """Vouchers cannot be deleted and voucher numbers cannot be altered"""
        record = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('10000.00'),
            recorded_by=self.operator
        )
        voucher = record.voucher
        orig_number = voucher.voucher_number

        # Hard delete must raise PermissionDenied
        with self.assertRaises(PermissionDenied):
            voucher.delete()

        # Attempting to change voucher_number on save must be reverted
        voucher.voucher_number = 'FAKE-REF-9999'
        voucher.save()
        voucher.refresh_from_db()
        self.assertEqual(voucher.voucher_number, orig_number)

    def test_soft_delete_retains_entry_number_and_excludes_from_active_ledgers(self):
        """Deleting a record soft-deletes it, keeps entry_number, and removes from active balances"""
        self.account.refresh_balance()
        initial_balance = self.account.current_balance_value

        record = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('12000.00'),
            recorded_by=self.operator
        )
        self.account.refresh_balance()
        self.assertEqual(self.account.current_balance_value, initial_balance + Decimal('12000.00'))

        entry_num = record.entry_number
        voucher = record.voucher

        # Soft delete the record
        record.soft_delete(user=self.operator, reason='Accidental duplicate payment entry')

        # Active queryset must exclude it
        self.assertFalse(FinancialRecord.objects.filter(pk=record.pk).exists())

        # All-objects manager must still find it
        reloaded = FinancialRecord.all_objects.get(pk=record.pk)
        self.assertTrue(reloaded.is_deleted)
        self.assertEqual(reloaded.entry_number, entry_num)  # Entry number NOT cleared
        self.assertEqual(reloaded.deleted_by, self.operator)
        self.assertEqual(reloaded.deletion_reason, 'Accidental duplicate payment entry')

        # Account balance must revert
        self.account.refresh_balance()
        self.assertEqual(self.account.current_balance_value, initial_balance)

        # Voucher must reflect deleted status and audit event
        voucher.refresh_from_db()
        self.assertEqual(voucher.verification_status, FinancialVoucher.STATUS_DELETED)
        self.assertEqual(voucher.deleted_by, self.operator)
        self.assertTrue(any(ev.get('action') == 'record_deleted' for ev in voucher.audit_log))

    def test_one_click_restore_reactivates_record_and_updates_balances(self):
        """Restoring a deleted voucher restores constituent records and updates balances without resequencing"""
        record = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('8000.00'),
            recorded_by=self.operator
        )
        entry_num = record.entry_number
        voucher = record.voucher

        # Soft delete
        voucher.soft_delete(user=self.operator, reason='Client cancelled order')
        self.assertFalse(FinancialRecord.objects.filter(pk=record.pk).exists())
        self.assertEqual(voucher.verification_status, FinancialVoucher.STATUS_DELETED)

        # Restore via VoucherService or endpoint
        self.client.login(username='admin_user', password='adminpass')
        restore_url = reverse('restore-financial-entry', kwargs={'pk': voucher.pk})
        response = self.client.post(restore_url, {'restore_reason': 'Order reinstated by client'}, follow=True)
        self.assertEqual(response.status_code, 200)

        # Verify record is active again
        self.assertTrue(FinancialRecord.objects.filter(pk=record.pk).exists())
        record.refresh_from_db()
        self.assertFalse(record.is_deleted)
        self.assertEqual(record.entry_number, entry_num)

        # Voucher is now queued for re-verification
        voucher.refresh_from_db()
        self.assertEqual(voucher.verification_status, FinancialVoucher.STATUS_PENDING)
        self.assertTrue(any(ev.get('action') == 'voucher_restored' for ev in voucher.audit_log))

    def test_record_alteration_logs_diff_and_reopens_verification(self):
        """Editing an entry captures before-and-after diffs and resets voucher to pending"""
        record = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('5000.00'),
            recorded_by=self.operator
        )
        voucher = record.voucher
        voucher.verification_status = FinancialVoucher.STATUS_VERIFIED
        voucher.save()

        # Update record via FinancialRecordUpdateView
        self.client.login(username='akash', password='akashpass')
        update_url = reverse('financialrecord-update', kwargs={'pk': record.pk})
        form_data = {
            'date': str(record.date),
            'account': str(self.account.pk),
            'party': str(self.debtor.pk),
            'category': str(self.cat_payment_in.pk),
            'record_type': 'Transaction',
            'amount': '7500.00',  # Changed from 5000 to 7500
            'description': 'Updated payment amount after verification'
        }
        resp = self.client.post(update_url, data=form_data, follow=True)
        self.assertEqual(resp.status_code, 200)

        # Verify voucher status reset to pending
        voucher.refresh_from_db()
        self.assertEqual(voucher.verification_status, FinancialVoucher.STATUS_PENDING)
        self.assertEqual(voucher.total_settled_amount, Decimal('7500.00'))

        # Verify audit log captured the amount diff
        altered_events = [ev for ev in voucher.audit_log if ev.get('action') == 'altered']
        self.assertTrue(len(altered_events) >= 1)
        self.assertIn('amount', altered_events[0]['details'])
        self.assertEqual(altered_events[0]['details']['amount']['old'], '5000.00')
        self.assertEqual(altered_events[0]['details']['amount']['new'], '7500.00')

    def test_review_view_status_tabs_include_deleted(self):
        """Review dashboard properly lists pending, verified, flagged, and deleted vouchers"""
        # Create 1 active and 1 deleted voucher
        rec_active = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('4000.00'),
            recorded_by=self.operator
        )
        rec_deleted = FinancialRecord.objects.create(
            date=timezone.now().date(),
            account=self.account,
            party=self.debtor,
            category=self.cat_payment_in,
            amount=Decimal('6000.00'),
            recorded_by=self.operator
        )
        v_deleted = rec_deleted.voucher
        v_deleted.soft_delete(user=self.operator, reason='Test soft delete')

        self.client.login(username='admin_user', password='adminpass')
        review_url = reverse('financial-entry-review')

        # Query pending tab
        resp_pending = self.client.get(review_url + '?status=pending')
        self.assertEqual(resp_pending.status_code, 200)
        self.assertIn(rec_active.voucher, resp_pending.context['vouchers'])
        self.assertNotIn(v_deleted, resp_pending.context['vouchers'])

        # Query deleted tab
        resp_deleted = self.client.get(review_url + '?status=deleted')
        self.assertEqual(resp_deleted.status_code, 200)
        self.assertIn(v_deleted, resp_deleted.context['vouchers'])
        self.assertNotIn(rec_active.voucher, resp_deleted.context['vouchers'])
