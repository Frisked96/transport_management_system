import re
from django.core.management.base import BaseCommand
from django.db import transaction
from ledger.models import FinancialVoucher, Sequence
from transport_mgmt.utils import to_local_datetime


class Command(BaseCommand):
    help = (
        "Fixes vouchers created on or after 2026-11-01 to ensure reference numbers "
        "follow creation date (REF-2611-#####) rather than entry/transaction dates, "
        "and resets previous months' sequence counters."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate sequence repairs without writing changes to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)

        if dry_run:
            self.stdout.write(self.style.WARNING("Running in DRY-RUN mode. No changes will be saved.\n"))

        # Target all vouchers created in November 2026 (local date >= 2026-11-01)
        nov_vouchers = []
        for v in FinancialVoucher.objects.all().order_by('id'):
            local_dt = to_local_datetime(v.created_at) if v.created_at else None
            if local_dt and local_dt.date() >= timezone_date(2026, 11, 1):
                nov_vouchers.append(v)

        self.stdout.write(f"Found {len(nov_vouchers)} voucher(s) created on or after 2026-11-01.")

        if not nov_vouchers:
            self.stdout.write(self.style.SUCCESS("No November vouchers found to adjust."))
            return

        with transaction.atomic():
            # 1. Stage temporary references to avoid unique constraint collisions
            changes = []
            for idx, voucher in enumerate(nov_vouchers, start=1):
                new_ref = f"REF-2611-{idx:05d}"
                changes.append({
                    'voucher': voucher,
                    'old_ref': voucher.voucher_number,
                    'new_ref': new_ref,
                })

            if not dry_run:
                # Step 1a: Assign temporary numbers
                for item in changes:
                    v = item['voucher']
                    FinancialVoucher.objects.filter(pk=v.pk).update(voucher_number=f"TEMP-{v.pk}")

                # Step 1b: Assign final sequential November numbers and update audit logs
                for item in changes:
                    v = item['voucher']
                    new_ref = item['new_ref']
                    old_ref = item['old_ref']

                    # Update audit log references if present
                    updated_audit = []
                    for entry in (v.audit_log or []):
                        if isinstance(entry, dict):
                            entry_copy = dict(entry)
                            if 'details' in entry_copy and isinstance(entry_copy['details'], dict):
                                details_copy = dict(entry_copy['details'])
                                if details_copy.get('reference') == old_ref:
                                    details_copy['reference'] = new_ref
                                    entry_copy['details'] = details_copy
                            updated_audit.append(entry_copy)
                        else:
                            updated_audit.append(entry)

                    FinancialVoucher.objects.filter(pk=v.pk).update(
                        voucher_number=new_ref,
                        audit_log=updated_audit
                    )

            # Report changes
            self.stdout.write("\nVoucher Renumbering Summary:")
            for item in changes:
                v = item['voucher']
                status = "WOULD RENAME" if dry_run else "RENAMED"
                self.stdout.write(
                    f"  PK {v.pk:<5} | Date: {v.date} | Created: {to_local_datetime(v.created_at).strftime('%Y-%m-%d %H:%M')} | "
                    f"{item['old_ref']:<15} -> {item['new_ref']:<15} ({status})"
                )

            # 2. Recalibrate sequence counters in Sequence model
            self.stdout.write("\nSequence Counter Recalibration:")

            # Determine legitimate max for October 2026 (excluding vouchers created in Nov)
            oct_max_no = 0
            for v in FinancialVoucher.objects.exclude(pk__in=[c['voucher'].pk for c in changes]).filter(voucher_number__startswith='REF-2610'):
                m = re.match(r'REF-2610-(\d+)', v.voucher_number)
                if m:
                    oct_max_no = max(oct_max_no, int(m.group(1)))

            # Determine legitimate max for May 2026
            may_max_no = 0
            for v in FinancialVoucher.objects.exclude(pk__in=[c['voucher'].pk for c in changes]).filter(voucher_number__startswith='REF-2605'):
                m = re.match(r'REF-2605-(\d+)', v.voucher_number)
                if m:
                    may_max_no = max(may_max_no, int(m.group(1)))

            # Determine legitimate max for April 2026
            apr_max_no = 0
            for v in FinancialVoucher.objects.exclude(pk__in=[c['voucher'].pk for c in changes]).filter(voucher_number__startswith='REF-2604'):
                m = re.match(r'REF-2604-(\d+)', v.voucher_number)
                if m:
                    apr_max_no = max(apr_max_no, int(m.group(1)))

            nov_seq_val = len(changes)

            recalibrations = [
                ('voucher_ref_2610', oct_max_no),
                ('voucher_ref_2605', may_max_no),
                ('voucher_ref_2604', apr_max_no),
                ('voucher_ref_2611', nov_seq_val),
            ]

            for key, target_val in recalibrations:
                seq_obj = Sequence.objects.filter(key=key).first()
                old_val = seq_obj.value if seq_obj else 0
                if not dry_run:
                    if seq_obj:
                        seq_obj.value = target_val
                        seq_obj.save(update_fields=['value'])
                    else:
                        Sequence.objects.create(key=key, value=target_val)
                action_str = "WOULD SET" if dry_run else "UPDATED"
                self.stdout.write(f"  {key:<20}: {old_val:>5} -> {target_val:>5} ({action_str})")

            if dry_run:
                self.stdout.write(self.style.WARNING("\nDry-run completed. Rolled back all changes."))
            else:
                self.stdout.write(self.style.SUCCESS("\nVoucher sequence repair completed successfully!"))


def timezone_date(year, month, day):
    import datetime
    return datetime.date(year, month, day)
