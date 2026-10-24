"""
VoucherService: Centralized umbrella service for managing system-generated immutable vouchers,
source traceability, audit tracking, soft-deletion, and restoration.
"""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import PermissionDenied
from transport_mgmt.utils import get_local_iso_timestamp, get_local_time_str


class VoucherService:
    """
    Umbrella service managing FinancialVouchers and audit traceability across the TMS.
    Vouchers cannot be created manually by users; they are system-generated and immutable.
    """

    @staticmethod
    def generate_reference(date=None):
        """Generates a gapless REF-YYMM-##### sequence number atomically"""
        from ledger.models import FinancialVoucher
        return FinancialVoucher.generate_voucher_number(date)

    @staticmethod
    def create_voucher(date, voucher_type, created_by=None, party=None, account=None, driver=None, description='', document_ref=None):
        """
        System-creates an immutable FinancialVoucher with atomic sequence number.
        """
        from ledger.models import FinancialVoucher
        v_num = FinancialVoucher.generate_voucher_number(date)
        voucher = FinancialVoucher.objects.create(
            voucher_number=v_num,
            date=date,
            voucher_type=voucher_type,
            party=party,
            account=account,
            driver=driver,
            created_by=created_by,
            verification_status=FinancialVoucher.STATUS_PENDING,
            description=description or '',
            document_ref=document_ref,
            audit_log=[{
                'timestamp': get_local_iso_timestamp(),
                'action': 'created',
                'user': created_by.username if created_by else 'System',
                'details': {
                    'voucher_type': voucher_type,
                    'reference': v_num,
                }
            }]
        )
        return voucher

    @staticmethod
    def resolve_voucher_type(record):
        """
        Determines the appropriate voucher type from a FinancialRecord.
        """
        from ledger.models import FinancialVoucher, TransactionCategory
        if record.associated_bill:
            return FinancialVoucher.VOUCHER_TYPE_BILL_PAYMENT if record.record_type != 'Invoice' else FinancialVoucher.VOUCHER_TYPE_BILL_INVOICE
        if record.associated_trip:
            return FinancialVoucher.VOUCHER_TYPE_TRIP_PAYMENT if record.record_type != 'Invoice' else FinancialVoucher.VOUCHER_TYPE_TRIP_ACCRUAL
        if record.associated_document_renewal:
            return FinancialVoucher.VOUCHER_TYPE_DOCUMENT_RENEWAL
        if record.associated_toll_recharge:
            return FinancialVoucher.VOUCHER_TYPE_TOLL_RECHARGE
        if record.associated_tyre:
            return FinancialVoucher.VOUCHER_TYPE_TYRE_PURCHASE
        if record.driver:
            return FinancialVoucher.VOUCHER_TYPE_DRIVER_SETTLEMENT
        if record.category:
            if record.category.name == 'Payment Out':
                return FinancialVoucher.VOUCHER_TYPE_PAYMENT_OUT
            if record.category.type == TransactionCategory.TYPE_EXPENSE:
                return FinancialVoucher.VOUCHER_TYPE_EXPENSE
        return FinancialVoucher.VOUCHER_TYPE_DIRECT

    @classmethod
    def get_or_create_voucher_for_record(cls, record):
        """
        Ensures that every FinancialRecord has an immutable parent voucher.
        If missing, generates one seamlessly behind the scenes.
        """
        if record.voucher_id:
            return record.voucher

        v_type = cls.resolve_voucher_type(record)
        user = getattr(record, 'recorded_by', None) or getattr(record, '_current_user', None)

        voucher = cls.create_voucher(
            date=record.date or timezone.now().date(),
            voucher_type=v_type,
            created_by=user,
            party=record.party,
            account=record.account,
            driver=record.driver,
            description=record.description or '',
            document_ref=record.document_ref
        )
        record.voucher = voucher
        return voucher

    @staticmethod
    def record_alteration(voucher, user, field_diffs, notes=''):
        """
        Appends an alteration diff to the voucher's audit log, resets verification
        status to pending, and saves.
        """
        from ledger.models import FinancialVoucher
        if not field_diffs and not notes:
            return

        user_name = user.username if user else 'System'
        timestamp_str = get_local_time_str()

        # Human-readable diff summary
        diff_texts = []
        for field, (old_val, new_val) in field_diffs.items():
            if isinstance(old_val, Decimal) or isinstance(new_val, Decimal):
                diff_texts.append(f"{field.replace('_', ' ').title()}: ₹{old_val:,.2f} → ₹{new_val:,.2f}")
            else:
                diff_texts.append(f"{field.replace('_', ' ').title()}: '{old_val}' → '{new_val}'")

        summary = f"Altered by {user_name} on {timestamp_str}: " + "; ".join(diff_texts)
        if notes:
            summary += f" | Note: {notes}"

        audit_entry = {
            'timestamp': get_local_iso_timestamp(),
            'action': 'altered',
            'user': user_name,
            'details': {field: {'old': str(old_val), 'new': str(new_val)} for field, (old_val, new_val) in field_diffs.items()},
            'summary': summary
        }

        voucher.audit_log.append(audit_entry)
        voucher.verification_status = FinancialVoucher.STATUS_PENDING
        voucher.verified_by = None
        voucher.verified_at = None
        voucher.verification_notes = summary
        voucher.update_totals(save=False)
        voucher.save()

    @staticmethod
    def soft_delete_record(record, user=None, reason=''):
        """
        Soft-deletes a single FinancialRecord.
        Per user directive: entry_number is NEVER released, and ledgers are NOT resequenced.
        """
        from ledger.models import FinancialVoucher
        with transaction.atomic():
            # Snapshot and clear allocations so trip/bill balances are relieved
            alloc_snapshot = [
                {'trip_id': a.trip_id, 'amount': str(a.amount)}
                for a in record.allocations.all()
            ]
            bill_alloc_snapshot = [
                {'bill_id': a.bill_id, 'amount': str(a.amount)}
                for a in record.bill_allocations.all()
            ]
            if alloc_snapshot:
                record.allocations.all().delete()
            if bill_alloc_snapshot:
                record.bill_allocations.all().delete()

            record.is_deleted = True
            record.deleted_at = timezone.now()
            record.deleted_by = user
            record.deletion_reason = reason or ''
            record.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason'])

            # Refresh financial balances without this record
            if record.party:
                record.party.refresh_balance()
            if record.account:
                record.account.refresh_balance()
            if record.associated_trip:
                from ledger.services import TripFinancialService
                TripFinancialService.update_trip_financial_caches(record.associated_trip)
            if record.associated_bill:
                from ledger.services import BillingService
                BillingService.update_bill_financial_caches(record.associated_bill)

            # Update parent voucher audit log & status
            voucher = record.voucher
            if voucher:
                user_name = user.username if user else 'System'
                timestamp_str = get_local_time_str()
                entry_num = record.entry_number or record.pk
                desc = f"Record #{entry_num} (₹{record.amount:,.2f}) soft-deleted by {user_name} on {timestamp_str}"
                if reason:
                    desc += f" (Reason: {reason})"

                voucher.audit_log.append({
                    'timestamp': get_local_iso_timestamp(),
                    'action': 'record_deleted',
                    'user': user_name,
                    'record_id': record.pk,
                    'entry_number': record.entry_number,
                    'amount': str(record.amount),
                    'reason': reason or '',
                    'alloc_snapshot': alloc_snapshot,
                    'bill_alloc_snapshot': bill_alloc_snapshot,
                    'summary': desc
                })

                # Check if all records in this voucher are now soft-deleted
                active_count = voucher.financial_records.filter(is_deleted=False).count()
                if active_count == 0:
                    voucher.verification_status = FinancialVoucher.STATUS_DELETED
                    voucher.deleted_by = user
                    voucher.deleted_at = timezone.now()
                    voucher.deletion_reason = reason or ''
                    voucher.verification_notes = f"All entries soft-deleted by {user_name} on {timestamp_str}"
                else:
                    voucher.verification_status = FinancialVoucher.STATUS_PENDING
                    voucher.verification_notes = desc

                voucher.update_totals(save=False)
                voucher.save()

    @staticmethod
    def restore_record(record, user=None, reason=''):
        """
        Restores a soft-deleted FinancialRecord.
        Retains original entry_number; no resequencing needed.
        """
        from ledger.models import FinancialVoucher
        with transaction.atomic():
            record.is_deleted = False
            record.deleted_at = None
            record.deleted_by = None
            record.deletion_reason = ''
            record.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason'])

            # Restore allocations from audit snapshot if available
            voucher = record.voucher
            if voucher:
                last_del_event = None
                for ev in reversed(voucher.audit_log):
                    if ev.get('action') == 'record_deleted' and ev.get('record_id') == record.pk:
                        last_del_event = ev
                        break
                if last_del_event:
                    from ledger.models import TripAllocation, BillAllocation
                    for a_data in last_del_event.get('alloc_snapshot', []):
                        TripAllocation.objects.get_or_create(
                            financial_record=record,
                            trip_id=a_data['trip_id'],
                            defaults={'amount': Decimal(str(a_data['amount']))}
                        )
                    for b_data in last_del_event.get('bill_alloc_snapshot', []):
                        BillAllocation.objects.get_or_create(
                            financial_record=record,
                            bill_id=b_data['bill_id'],
                            defaults={'amount': Decimal(str(b_data['amount']))}
                        )

            # Refresh financial balances to re-include this record
            if record.party:
                record.party.refresh_balance()
            if record.account:
                record.account.refresh_balance()
            if record.associated_trip:
                from ledger.services import TripFinancialService
                TripFinancialService.update_trip_financial_caches(record.associated_trip)
            if record.associated_bill:
                from ledger.services import BillingService
                BillingService.update_bill_financial_caches(record.associated_bill)

            # Update parent voucher
            voucher = record.voucher
            if voucher:
                user_name = user.username if user else 'System'
                timestamp_str = get_local_time_str()
                entry_num = record.entry_number or record.pk
                desc = f"Record #{entry_num} restored by {user_name} on {timestamp_str}"
                if reason:
                    desc += f" (Reason: {reason})"

                voucher.audit_log.append({
                    'timestamp': get_local_iso_timestamp(),
                    'action': 'record_restored',
                    'user': user_name,
                    'record_id': record.pk,
                    'entry_number': record.entry_number,
                    'reason': reason or '',
                    'summary': desc
                })

                voucher.verification_status = FinancialVoucher.STATUS_PENDING
                voucher.deleted_by = None
                voucher.deleted_at = None
                voucher.deletion_reason = ''
                voucher.verification_notes = desc
                voucher.update_totals(save=False)
                voucher.save()

    @classmethod
    def soft_delete_voucher(cls, voucher, user=None, reason=''):
        """
        Soft-deletes an entire voucher and all its constituent records.
        Vouchers themselves are immutable audit records and are NEVER deleted from the DB.
        """
        from ledger.models import FinancialVoucher
        with transaction.atomic():
            records = list(voucher.financial_records.all())
            for r in records:
                if not r.is_deleted:
                    cls.soft_delete_record(r, user=user, reason=reason)

            user_name = user.username if user else 'System'
            timestamp_str = get_local_time_str()
            voucher.verification_status = FinancialVoucher.STATUS_DELETED
            voucher.deleted_by = user
            voucher.deleted_at = timezone.now()
            voucher.deletion_reason = reason or ''
            voucher.verification_notes = f"Voucher soft-deleted by {user_name} on {timestamp_str}" + (f": {reason}" if reason else "")
            voucher.audit_log.append({
                'timestamp': get_local_iso_timestamp(),
                'action': 'voucher_deleted',
                'user': user_name,
                'reason': reason or '',
                'summary': voucher.verification_notes
            })
            voucher.update_totals(save=False)
            voucher.save()

    @classmethod
    def restore_voucher(cls, voucher, user=None, reason=''):
        """
        Restores a soft-deleted voucher and all its constituent records.
        """
        from ledger.models import FinancialVoucher, FinancialRecord
        with transaction.atomic():
            # Get all child records, including deleted ones
            records = list(FinancialRecord.all_objects.filter(voucher=voucher))
            for r in records:
                if r.is_deleted:
                    cls.restore_record(r, user=user, reason=reason)

            user_name = user.username if user else 'System'
            timestamp_str = get_local_time_str()
            voucher.verification_status = FinancialVoucher.STATUS_PENDING
            voucher.deleted_by = None
            voucher.deleted_at = None
            voucher.deletion_reason = ''
            voucher.verification_notes = f"Voucher restored by {user_name} on {timestamp_str}" + (f": {reason}" if reason else "")
            voucher.audit_log.append({
                'timestamp': get_local_iso_timestamp(),
                'action': 'voucher_restored',
                'user': user_name,
                'reason': reason or '',
                'summary': voucher.verification_notes
            })
            voucher.update_totals(save=False)
            voucher.save()
