from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_date
from ledger.models import BillAllocation, Bill, FinancialRecord, Party
from transport_mgmt.utils import format_local_datetime
from ledger.services import BillingService


class Command(BaseCommand):
    help = 'Safely cleans up automated FIFO BillAllocations created in production and recalculates affected bill balances.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--party',
            type=str,
            help='Filter by party name or ID (e.g. "Lloyds"). If omitted, all parties matching criteria are shown.'
        )
        parser.add_argument(
            '--since',
            type=str,
            help='Filter allocations created on or after date (YYYY-MM-DD), e.g. "2026-10-10".'
        )
        parser.add_argument(
            '--bill',
            type=str,
            help='Filter by specific Bill Number or ID.'
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Execute deletion and recalculation. If not set, runs as a dry-run preview.'
        )
        parser.add_argument(
            '--clean-descriptions',
            action='store_true',
            help='Clean "Paid across invoices: ..." prefix from payment record descriptions.'
        )

    def handle(self, *args, **options):
        party_query = options.get('party')
        since_str = options.get('since')
        bill_query = options.get('bill')
        apply = options.get('apply')
        clean_desc = options.get('clean_descriptions')

        allocations = BillAllocation.objects.select_related('bill', 'financial_record', 'financial_record__party')

        if party_query:
            if party_query.isdigit():
                allocations = allocations.filter(financial_record__party_id=int(party_query))
            else:
                allocations = allocations.filter(financial_record__party__name__icontains=party_query)

        if since_str:
            d = parse_date(since_str)
            if d:
                allocations = allocations.filter(created_at__date__gte=d)
            else:
                self.stderr.write(self.style.ERROR(f"Invalid date format for --since: {since_str}. Use YYYY-MM-DD."))
                return

        if bill_query:
            if bill_query.isdigit():
                allocations = allocations.filter(bill_id=int(bill_query))
            else:
                allocations = allocations.filter(bill__bill_number__icontains=bill_query)

        count = allocations.count()
        if count == 0:
            self.stdout.write(self.style.WARNING("No BillAllocation records found matching the given filters."))
            return

        self.stdout.write(self.style.NOTICE(f"Found {count} BillAllocation record(s):"))
        affected_bills = set()
        affected_records = set()

        for alloc in allocations:
            party_name = alloc.financial_record.party.name if (alloc.financial_record and alloc.financial_record.party) else "Unknown"
            bill_num = alloc.bill.bill_number or f"Draft-{alloc.bill.pk}"
            rec_id = alloc.financial_record.pk if alloc.financial_record else "None"
            created = format_local_datetime(alloc.created_at, '%Y-%m-%d %H:%M', default="Unknown")
            self.stdout.write(
                f"  - Alloc #{alloc.pk} | Party: {party_name} | Amount: ₹{alloc.amount} | "
                f"Payment Record: #{rec_id} -> Bill: {bill_num} | Created: {created}"
            )
            if alloc.bill:
                affected_bills.add(alloc.bill)
            if alloc.financial_record:
                affected_records.add(alloc.financial_record)

        if not apply:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "[DRY-RUN] No changes were made. Rerun with --apply to delete these allocations "
                "and restore bill balances."
            ))
            return

        # Perform actual deletion and balance update
        with transaction.atomic():
            deleted_count, _ = allocations.delete()

            # Refresh affected bills
            for bill in affected_bills:
                bill.refresh_from_db()
                BillingService.update_bill_financial_caches(bill)
                self.stdout.write(
                    f"Recalculated bill {bill.bill_number or bill.pk}: "
                    f"received=₹{bill.amount_received}, balance=₹{bill.outstanding_balance}, status={bill.payment_status}"
                )

            # Clean payment descriptions if requested
            if clean_desc:
                for rec in affected_records:
                    rec.refresh_from_db()
                    if rec.description and "Paid across invoices:" in rec.description:
                        # If description was only the invoice string, empty it or trim it
                        parts = rec.description.split("Paid across invoices:")
                        cleaned = parts[0].strip()
                        rec.description = cleaned
                        rec.save(update_fields=['description'])
                        self.stdout.write(f"Cleaned description on Payment Record #{rec.pk}")

        self.stdout.write(self.style.SUCCESS(f"Successfully removed {deleted_count} allocation(s) and restored bill balances."))
