"""
Tests for timezone and timestamp formatting utilities across the application.
Ensures Asia/Kolkata (IST) consistency across audit trails, JSON logs, and verification views.
"""
from datetime import datetime, date
import zoneinfo
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from django.template import Template, Context
from django.urls import reverse
from django.contrib.auth.models import User, Permission

from transport_mgmt.utils import (
    to_local_datetime,
    format_local_datetime,
    get_local_now,
    get_local_iso_timestamp,
    get_local_time_str,
    get_local_date,
)
from ledger.models import FinancialVoucher, FinancialRecord, CompanyAccount, Party, TransactionCategory
from ledger.services import VoucherService


class TimezoneUtilsTestCase(TestCase):
    def test_to_local_datetime_from_utc_string(self):
        """UTC ISO timestamp string is properly converted to Asia/Kolkata"""
        utc_str = '2026-10-23T09:12:55.503468+00:00'
        local_dt = to_local_datetime(utc_str)
        self.assertIsNotNone(local_dt)
        self.assertEqual(local_dt.tzinfo.key, 'Asia/Kolkata')
        self.assertEqual(local_dt.hour, 14)  # 09:12 UTC + 5:30 = 14:42 IST
        self.assertEqual(local_dt.minute, 42)
        self.assertEqual(local_dt.day, 23)

    def test_to_local_datetime_from_naive_string(self):
        """Naive ISO string is assumed UTC and converted to Asia/Kolkata"""
        naive_str = '2026-10-23T09:12:55'
        local_dt = to_local_datetime(naive_str)
        self.assertIsNotNone(local_dt)
        self.assertEqual(local_dt.hour, 14)
        self.assertEqual(local_dt.minute, 42)

    def test_to_local_datetime_from_aware_datetime(self):
        """Aware datetime in UTC is converted to Asia/Kolkata"""
        dt_utc = datetime(2026, 10, 24, 6, 30, tzinfo=zoneinfo.ZoneInfo('UTC'))
        local_dt = to_local_datetime(dt_utc)
        self.assertEqual(local_dt.hour, 12)  # 06:30 + 5:30 = 12:00
        self.assertEqual(local_dt.minute, 0)

    def test_to_local_datetime_from_date(self):
        """Date object is converted to midnight in Asia/Kolkata"""
        d = date(2026, 10, 24)
        local_dt = to_local_datetime(d)
        self.assertEqual(local_dt.year, 2026)
        self.assertEqual(local_dt.month, 10)
        self.assertEqual(local_dt.day, 24)
        self.assertEqual(local_dt.hour, 0)
        self.assertEqual(local_dt.minute, 0)

    def test_to_local_datetime_invalid_and_empty(self):
        """Invalid and empty values return None"""
        self.assertIsNone(to_local_datetime(''))
        self.assertIsNone(to_local_datetime(None))
        self.assertIsNone(to_local_datetime('invalid-string'))

    def test_format_local_datetime(self):
        """format_local_datetime properly formats timestamps in Asia/Kolkata"""
        utc_str = '2026-10-23T09:12:55+00:00'
        formatted = format_local_datetime(utc_str)
        self.assertEqual(formatted, '23 Oct 2026, 14:42')

        formatted_custom = format_local_datetime(utc_str, fmt='%Y-%m-%d %H:%M')
        self.assertEqual(formatted_custom, '2026-10-23 14:42')

    def test_get_local_iso_timestamp_has_kolkata_offset(self):
        """get_local_iso_timestamp produces ISO string with +05:30 offset"""
        iso_str = get_local_iso_timestamp()
        self.assertTrue('+05:30' in iso_str)

    def test_get_local_date_matches_kolkata(self):
        """get_local_date returns date in Asia/Kolkata"""
        expected_date = timezone.localtime().date()
        self.assertEqual(get_local_date(), expected_date)

    def test_template_filters_audit_timestamp_and_kolkata_time(self):
        """Template filters audit_timestamp and kolkata_time correctly format ISO timestamps"""
        t = Template('{% load ledger_extras %}{{ ts|audit_timestamp }}')
        rendered = t.render(Context({'ts': '2026-10-23T09:12:55+00:00'}))
        self.assertEqual(rendered, '23 Oct 2026, 14:42')

        t_alias = Template('{% load ledger_extras %}{{ ts|kolkata_time }}')
        rendered_alias = t_alias.render(Context({'ts': '2026-10-23T09:12:55+00:00'}))
        self.assertEqual(rendered_alias, '23 Oct 2026, 14:42')

    def test_voucher_audit_log_records_kolkata_time(self):
        """Voucher creation and alteration write audit log entries in local time"""
        user = User.objects.create_user(username='tester', password='pass')
        voucher = VoucherService.create_voucher(
            date=date.today(),
            voucher_type=FinancialVoucher.VOUCHER_TYPE_DIRECT,
            created_by=user,
            description='Test voucher'
        )
        self.assertEqual(len(voucher.audit_log), 1)
        created_event = voucher.audit_log[0]
        self.assertIn('+05:30', created_event['timestamp'])

    def test_verification_view_displays_audit_log_in_kolkata_time(self):
        """Review dashboard renders audit log timestamps in Asia/Kolkata (not UTC)"""
        manager = User.objects.create_user(username='mgr', password='pass')
        manager.user_permissions.add(
            Permission.objects.get(codename='can_view_financial_records'),
            Permission.objects.get(codename='can_verify_financial_records')
        )
        # Create voucher with UTC timestamp in audit log
        v_num = FinancialVoucher.generate_voucher_number()
        voucher = FinancialVoucher.objects.create(
            voucher_number=v_num,
            date=date.today(),
            created_by=manager,
            audit_log=[{
                'timestamp': '2026-10-23T09:12:55.503468+00:00',
                'action': 'created',
                'user': 'mgr',
                'details': {}
            }]
        )

        self.client.login(username='mgr', password='pass')
        resp = self.client.get(reverse('financial-entry-review'))
        self.assertEqual(resp.status_code, 200)
        # Should render 14:42 (Asia/Kolkata), NOT 09:12 (UTC)
        content = resp.content.decode('utf-8')
        self.assertIn('23 Oct 2026, 14:42', content)
        self.assertNotIn('2026-10-23T09:12', content)

    def test_ajax_verify_returns_kolkata_time(self):
        """AJAX verify endpoint returns verified_at in Asia/Kolkata timezone"""
        manager = User.objects.create_user(username='mgr2', password='pass')
        manager.user_permissions.add(
            Permission.objects.get(codename='can_view_financial_records'),
            Permission.objects.get(codename='can_verify_financial_records')
        )
        v_num = FinancialVoucher.generate_voucher_number()
        voucher = FinancialVoucher.objects.create(
            voucher_number=v_num,
            date=date.today(),
            created_by=manager,
        )

        self.client.login(username='mgr2', password='pass')
        resp = self.client.post(
            reverse('verify-financial-entry', kwargs={'pk': voucher.pk}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        voucher.refresh_from_db()
        expected_verified_at = timezone.localtime(voucher.verified_at).strftime('%d %b %Y, %H:%M')
        self.assertEqual(data['verified_at'], expected_verified_at)
