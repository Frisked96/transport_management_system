"""
Management command to backfill FinancialVoucher instances for existing historical FinancialRecord rows.
Marks all historical records as Verified to establish a clean starting baseline.
"""
from decimal import Decimal
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from ledger.models import FinancialRecord, FinancialVoucher, TransactionCategory


class Command(BaseCommand):
    help = 'Backfill FinancialVoucher records for existing financial entries and mark them verified'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting historical financial voucher backfill..."))

        # Find unlinked records (including any soft-deleted)
        unlinked_records = FinancialRecord.all_objects.filter(voucher__isnull=True).order_by('created_at', 'id')
        total_unlinked = unlinked_records.count()

        if total_unlinked == 0:
            self.stdout.write(self.style.SUCCESS("All financial records are already linked to vouchers."))
            return

        self.stdout.write(f"Found {total_unlinked} unlinked financial records. Grouping into vouchers...")

        vouchers_created = 0
        records_linked = 0

        # We will iterate through records and group those created within 15 seconds by the same user with the same party/trip
        processed_ids = set()

        with transaction.atomic():
            for record in unlinked_records.iterator():
                if record.id in processed_ids:
                    continue

                # Look for co-submitted records (e.g. Payment + TDS + Shortage in one form submission)
                window_start = record.created_at - timedelta(seconds=15)
                window_end = record.created_at + timedelta(seconds=15)

                group_qs = FinancialRecord.all_objects.filter(
                    voucher__isnull=True,
                    recorded_by=record.recorded_by,
                    date=record.date,
                    created_at__range=(window_start, window_end)
                )

                if record.associated_trip_id:
                    group_qs = group_qs.filter(associated_trip_id=record.associated_trip_id)
                elif record.party_id:
                    group_qs = group_qs.filter(party_id=record.party_id)

                group_records = list(group_qs)
                if not group_records:
                    group_records = [record]

                # Determine voucher type
                v_type = FinancialVoucher.VOUCHER_TYPE_DIRECT
                for r in group_records:
                    if r.associated_bill_id and r.record_type == 'Invoice':
                        v_type = FinancialVoucher.VOUCHER_TYPE_BILL_INVOICE
                        break
                    elif r.associated_trip_id and r.record_type == 'Invoice':
                        v_type = FinancialVoucher.VOUCHER_TYPE_TRIP_ACCRUAL
                        break
                    elif r.associated_trip_id or r.allocations.exists():
                        v_type = FinancialVoucher.VOUCHER_TYPE_TRIP_PAYMENT
                        break
                    elif r.associated_bill_id or r.bill_allocations.exists():
                        v_type = FinancialVoucher.VOUCHER_TYPE_BILL_PAYMENT
                        break
                    elif r.associated_document_renewal_id:
                        v_type = FinancialVoucher.VOUCHER_TYPE_DOCUMENT_RENEWAL
                        break
                    elif r.associated_toll_recharge_id:
                        v_type = FinancialVoucher.VOUCHER_TYPE_TOLL_RECHARGE
                        break
                    elif r.associated_tyre_id:
                        v_type = FinancialVoucher.VOUCHER_TYPE_TYRE_PURCHASE
                        break
                    elif r.category and r.category.name == 'Payment Out':
                        v_type = FinancialVoucher.VOUCHER_TYPE_PAYMENT_OUT
                        break
                    elif r.category and r.category.type == TransactionCategory.TYPE_EXPENSE:
                        v_type = FinancialVoucher.VOUCHER_TYPE_EXPENSE
                        break

                # Create historical voucher marked as verified
                v_num = FinancialVoucher.generate_voucher_number(record.date)
                voucher = FinancialVoucher.objects.create(
                    voucher_number=v_num,
                    date=record.date,
                    voucher_type=v_type,
                    party=record.party,
                    account=record.account,
                    driver=record.driver,
                    created_by=record.recorded_by,
                    created_at=record.created_at,
                    verification_status=FinancialVoucher.STATUS_VERIFIED,
                    verified_by=record.recorded_by,
                    verified_at=record.created_at,
                    verification_notes="Auto-verified baseline during voucher system initialization.",
                    description=record.description or ''
                )

                # Link all group records
                for r in group_records:
                    r.voucher = voucher
                    r.verification_status = FinancialVoucher.STATUS_VERIFIED
                    r.verified_by = record.recorded_by
                    r.verified_at = record.created_at
                    r.save(update_fields=['voucher', 'verification_status', 'verified_by', 'verified_at'])
                    processed_ids.add(r.id)
                    records_linked += 1

                voucher.update_totals()
                vouchers_created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Successfully created {vouchers_created} vouchers and linked {records_linked} historical records as Verified."
        ))
