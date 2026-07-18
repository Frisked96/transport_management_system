"""
Management command to backfill vendor hire amounts for existing trips
on attached (market) vehicles.

Usage:
    1. First, create Vendor parties (Creditor type) in the UI.
    2. Mark vehicles as 'Attached' and assign vendors via the vehicle edit page.
    3. Run: python manage.py backfill_vendor_hire --dry-run   (preview)
    4. Run: python manage.py backfill_vendor_hire              (apply)
"""
from django.core.management.base import BaseCommand
from fleet.models import Vehicle
from trips.models import Trip
from ledger.models import Bill
from ledger.services import TripFinancialService, BillingService
from decimal import Decimal
from django.db import transaction
from django.db.models import F, Sum


class Command(BaseCommand):
    help = 'Backfill vendor_hire_amount for trips on attached vehicles, then sync ledger entries.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without applying them.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('=== DRY RUN MODE ==='))
        
        # 1. Find all attached vehicles with a vendor assigned
        attached_vehicles = Vehicle.objects.filter(
            ownership=Vehicle.OWNERSHIP_ATTACHED,
            vendor__isnull=False
        ).select_related('vendor')
        
        if not attached_vehicles.exists():
            self.stdout.write(self.style.WARNING(
                'No attached vehicles found. Mark vehicles as "Attached" and assign vendors first.'
            ))
            return
        
        self.stdout.write(f'Found {attached_vehicles.count()} attached vehicle(s):')
        for v in attached_vehicles:
            self.stdout.write(f'  • {v.registration_plate} → Vendor: {v.vendor.name}')
        
        total_trips_updated = 0
        total_hire_amount = Decimal('0')
        
        # 2. Update vendor_hire_amount for all trips on attached vehicles
        for vehicle in attached_vehicles:
            trips = Trip.objects.filter(vehicle=vehicle)
            trip_count = trips.count()
            
            if trip_count == 0:
                self.stdout.write(f'  {vehicle.registration_plate}: No trips found. Skipping.')
                continue
            
            vehicle_total = trips.aggregate(total=Sum('total_revenue_cached'))['total'] or Decimal('0')
            
            if not dry_run:
                trips.update(vendor_hire_amount=F('total_revenue_cached'))
            
            total_trips_updated += trip_count
            total_hire_amount += vehicle_total
            self.stdout.write(
                f'  {vehicle.registration_plate}: {trip_count} trips, '
                f'total hire: ₹{vehicle_total:,.2f}'
            )
        
        self.stdout.write('')
        self.stdout.write(f'Total: {total_trips_updated} trips, ₹{total_hire_amount:,.2f} in vendor hire')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDry run complete. No changes made.'))
            return
        
        # 3. Re-sync ledger entries
        self.stdout.write('\nSyncing ledger entries...')
        
        with transaction.atomic():
            # 3a. Re-sync unbilled trips (creates trip-level vendor accruals)
            unbilled_trips = Trip.objects.filter(
                vehicle__in=attached_vehicles,
                bills__isnull=True
            ).select_related('vehicle__vendor').distinct()
            
            unbilled_count = unbilled_trips.count()
            for trip in unbilled_trips.iterator(chunk_size=1000):
                TripFinancialService.sync_trip_accrual(trip)
            self.stdout.write(f'  Synced {unbilled_count} unbilled trip accruals.')
            
            # 3b. Re-sync bills (creates consolidated vendor accruals)
            affected_bills = Bill.objects.filter(
                trips__vehicle__in=attached_vehicles
            ).distinct()
            
            affected_bills_count = affected_bills.count()
            for bill in affected_bills.iterator(chunk_size=500):
                BillingService.sync_bill_to_ledger(bill)
            self.stdout.write(f'  Synced {affected_bills_count} bill ledger entries.')
            
            # 3c. Refresh vendor balances
            vendor_ids = set(v.vendor_id for v in attached_vehicles)
            from ledger.models import Party
            for vendor in Party.objects.filter(pk__in=vendor_ids):
                vendor.refresh_balance()
                self.stdout.write(f'  Refreshed balance for vendor: {vendor.name} → ₹{vendor.current_balance_cached:,.2f}')
        
        self.stdout.write(self.style.SUCCESS('\n✓ Backfill complete!'))
