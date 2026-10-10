"""
Management command to migrate historical billed invoices containing attached vehicle trips
into linked Creditor Bills for the respective vendors.

Features:
- Backfills trip.vendor_hire_amount from trip.revenue_cached for attached trips where hire is 0 or null.
- Generates linked Creditor Bills (CR-<CustomerBillNo>) for attached vehicle vendors.
- Passes customer bill's GST rate and GST type to the creditor bill so vendor liability includes GST.
- Reconciles vendor ledgers with consolidated 'Lorry Hire' invoice entries.
- Refreshes vendor cached balances.
- Safe by default: runs in dry-run mode unless --commit is explicitly passed.

Usage:
    python manage.py migrate_attached_creditor_bills              # Preview (Dry Run)
    python manage.py migrate_attached_creditor_bills --dry-run    # Explicit Dry Run
    python manage.py migrate_attached_creditor_bills --commit     # Apply Changes to Database
    python manage.py migrate_attached_creditor_bills --commit --vendor-id=57
    python manage.py migrate_attached_creditor_bills --commit --bill-number=GS-KRL-26-27/29
"""
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F, Sum, Q
from fleet.models import Vehicle
from trips.models import Trip
from ledger.models import Bill, Party, FinancialRecord
from ledger.services import BillingService, TripFinancialService


class Command(BaseCommand):
    help = 'Migrate already billed customer invoices with attached vehicle trips to linked Creditor Bills.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Commit changes to the database. If omitted, runs in preview / dry-run mode.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Explicitly run in preview / dry-run mode without committing.',
        )
        parser.add_argument(
            '--vendor-id',
            type=int,
            default=None,
            help='Only migrate bills for vehicles belonging to a specific vendor Party ID.',
        )
        parser.add_argument(
            '--bill-number',
            type=str,
            default=None,
            help='Only migrate a specific customer bill number (e.g. GS-KRL-26-27/29).',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Print detailed breakdown for each bill and trip.',
        )

    def handle(self, *args, **options):
        commit = options.get('commit', False)
        dry_run = options.get('dry_run', False) or not commit
        vendor_id = options.get('vendor_id')
        bill_number = options.get('bill_number')
        verbose = options.get('verbose', False)

        mode_str = "COMMIT MODE (CHANGES WILL BE COMMITTED)" if commit else "DRY-RUN MODE (PREVIEW ONLY - NO CHANGES WILL BE SAVED)"
        banner_border = "=" * 80
        self.stdout.write(self.style.WARNING(banner_border))
        self.stdout.write(self.style.WARNING(f"  ATTACHED VEHICLE CREDITOR BILL MIGRATION: {mode_str}"))
        self.stdout.write(self.style.WARNING(banner_border))

        # 1. Identify Attached Vehicles with Vendors
        vehicles_qs = Vehicle.objects.filter(
            ownership=Vehicle.OWNERSHIP_ATTACHED,
            vendor__isnull=False
        ).select_related('vendor')

        if vendor_id:
            vehicles_qs = vehicles_qs.filter(vendor_id=vendor_id)

        attached_vehicles = list(vehicles_qs)
        if not attached_vehicles:
            self.stdout.write(self.style.ERROR("No attached vehicles with assigned vendors found matching criteria."))
            return

        vendor_names = sorted(set(v.vendor.name for v in attached_vehicles))
        self.stdout.write(f"Found {len(attached_vehicles)} attached vehicle(s) across {len(vendor_names)} vendor(s):")
        for v in attached_vehicles:
            self.stdout.write(f"  • {v.registration_plate} → Vendor: {v.vendor.name} (ID: {v.vendor_id})")

        # 2. Identify Attached Trips
        trips_qs = Trip.objects.filter(vehicle__in=attached_vehicles)
        if bill_number:
            trips_qs = trips_qs.filter(bills__bill_number=bill_number)

        total_attached_trips = trips_qs.count()
        zero_hire_trips = trips_qs.filter(Q(vendor_hire_amount=0) | Q(vendor_hire_amount__isnull=True))
        zero_hire_count = zero_hire_trips.count()

        self.stdout.write(f"\nTotal attached trips found: {total_attached_trips}")
        self.stdout.write(f"Trips with zero/null vendor hire amount: {zero_hire_count}")

        # 3. Identify Customer Bills
        customer_bills_qs = Bill.objects.filter(
            trips__in=trips_qs,
            customer_bill__isnull=True
        ).distinct().order_by('date', 'id')

        if bill_number:
            customer_bills_qs = customer_bills_qs.filter(bill_number=bill_number)

        customer_bills = list(customer_bills_qs)
        self.stdout.write(f"Customer bills to process: {len(customer_bills)}")

        if not customer_bills:
            self.stdout.write(self.style.WARNING("No customer bills found with attached vehicle trips."))
            return

        # 4. Process within an atomic transaction
        with transaction.atomic():
            # Step A: Backfill vendor_hire_amount where 0 or null
            if zero_hire_count > 0:
                self.stdout.write("\nBackfilling vendor_hire_amount from trip.revenue_cached...")
                for trip in zero_hire_trips.select_related('route'):
                    trip.vendor_hire_amount = trip.revenue_cached or Decimal('0.00')
                    trip.save(update_fields=['vendor_hire_amount'])
                self.stdout.write(self.style.SUCCESS(f"✓ Backfilled {zero_hire_count} trips with base freight revenue."))

            # Step B: Sync each customer bill
            self.stdout.write(f"\nProcessing {len(customer_bills)} customer bills...")
            total_creditor_bills_created = 0
            total_subtotal = Decimal('0.00')
            total_gst = Decimal('0.00')
            total_creditor_amount = Decimal('0.00')

            processed_summary = []

            for idx, bill in enumerate(customer_bills, start=1):
                # Sync bill to ledger (creates/updates creditor bills & financial records)
                BillingService.sync_bill_to_ledger(bill)

                linked_cr_bills = list(bill.creditor_bills.select_related('party').all())
                total_creditor_bills_created += len(linked_cr_bills)

                for cr in linked_cr_bills:
                    total_subtotal += cr.subtotal_cached
                    total_gst += cr.gst_amount_cached
                    total_creditor_amount += cr.total_amount_cached

                    processed_summary.append({
                        'cust_bill': bill.bill_number or f"DRAFT-{bill.pk}",
                        'cust_date': bill.date,
                        'cr_bill': cr.bill_number,
                        'vendor': cr.party.name,
                        'trips': cr.trips.count(),
                        'subtotal': cr.subtotal_cached,
                        'gst_rate': cr.gst_rate,
                        'gst_amount': cr.gst_amount_cached,
                        'total_amount': cr.total_amount_cached,
                        'status': cr.payment_status_cached,
                    })

                if verbose:
                    for cr in linked_cr_bills:
                        self.stdout.write(
                            f"  [{idx}/{len(customer_bills)}] {bill.bill_number} → {cr.bill_number} "
                            f"({cr.party.name}): Subtotal=₹{cr.subtotal_cached:,.2f}, GST({cr.gst_rate}%)=₹{cr.gst_amount_cached:,.2f}, "
                            f"Total=₹{cr.total_amount_cached:,.2f}"
                        )

            # Step C: Query Mirrored Debit Notes and Refresh Vendor Balances
            mirrored_dns = list(Bill.objects.filter(
                category__name='Debit Note',
                customer_bill__isnull=False
            ).select_related('original_bill', 'customer_bill', 'party'))
            total_dn_amount = sum(dn.total_amount_cached for dn in mirrored_dns)

            vendor_ids = set(v.vendor_id for v in attached_vehicles)
            vendors = Party.objects.filter(id__in=vendor_ids)
            vendor_balance_info = []
            for vendor in vendors:
                vendor.refresh_balance()
                vendor_balance_info.append({
                    'name': vendor.name,
                    'balance': vendor.current_balance,
                    'debit': vendor.total_debit,
                    'credit': vendor.total_credit,
                })

            # Step D: Print Results Table
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write("MIGRATION SUMMARY")
            self.stdout.write("=" * 80)
            self.stdout.write(f"Customer Bills Processed:      {len(customer_bills)}")
            self.stdout.write(f"Creditor Bills Generated:      {total_creditor_bills_created}")
            self.stdout.write(f"Total Freight Subtotal:        ₹{total_subtotal:,.2f}")
            self.stdout.write(f"Total GST Amount (to Vendor):  ₹{total_gst:,.2f}")
            self.stdout.write(f"Gross Creditor Bill Amount:    ₹{total_creditor_amount:,.2f}")
            self.stdout.write(f"Mirrored Shortage Debit Notes: {len(mirrored_dns)} (Total: ₹{total_dn_amount:,.2f})")
            self.stdout.write("-" * 80)

            self.stdout.write("VENDOR BALANCES POST-MIGRATION:")
            for v_info in vendor_balance_info:
                self.stdout.write(
                    f"  • {v_info['name']}: Total Credit (Billed)=₹{v_info['credit']:,.2f}, "
                    f"Total Debit (Shortage/Paid)=₹{v_info['debit']:,.2f}, Current Balance={v_info['balance']}"
                )

            # Print first 5 bills sample if not verbose
            if not verbose and processed_summary:
                self.stdout.write("\nSample Migrated Creditor Bills (First 5):")
                for s in processed_summary[:5]:
                    self.stdout.write(
                        f"  Customer Bill: {s['cust_bill']:<22} | Creditor Bill: {s['cr_bill']:<25} | "
                        f"Vendor: {s['vendor']:<12} | Trips: {s['trips']:>2} | "
                        f"Sub: ₹{s['subtotal']:>10,.2f} | GST: ₹{s['gst_amount']:>9,.2f} | "
                        f"Total: ₹{s['total_amount']:>10,.2f}"
                    )
                if len(processed_summary) > 5:
                    self.stdout.write(f"  ... and {len(processed_summary) - 5} more bills.")

            # Print mirrored Debit Notes sample
            if mirrored_dns:
                self.stdout.write(f"\nMirrored Shortage Debit Notes ({len(mirrored_dns)} total):")
                for dn in mirrored_dns[:5]:
                    orig_no = dn.original_bill.bill_number if dn.original_bill else '-'
                    cust_cn = dn.customer_bill.bill_number if dn.customer_bill else '-'
                    self.stdout.write(
                        f"  Debit Note: {dn.bill_number:<18} | Against: {orig_no:<25} | "
                        f"From Customer CN: {cust_cn:<15} | Amount: ₹{dn.total_amount_cached:>9,.2f} | Item: {dn.item_type or ''}"
                    )
                if len(mirrored_dns) > 5:
                    self.stdout.write(f"  ... and {len(mirrored_dns) - 5} more mirrored debit notes.")

            self.stdout.write("=" * 80)

            if not commit:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING(
                    "\n*** DRY-RUN COMPLETE (No changes were saved to the database) ***\n"
                    "To apply these changes permanently to production, run:\n"
                    "  python manage.py migrate_attached_creditor_bills --commit\n"
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    "\n✓ MIGRATION COMPLETED SUCCESSFULLY! All creditor bills and ledger entries committed.\n"
                ))
