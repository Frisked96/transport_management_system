from django.core.management.base import BaseCommand
from trips.models import Trip
from ledger.models import Bill
from django.db import transaction

class Command(BaseCommand):
    help = 'Populate cached financial fields for all existing Trips and Bills'

    def handle(self, *args, **options):
        self.stdout.write('Populating Trip caches...')
        trips = Trip.objects.all()
        count = trips.count()
        
        with transaction.atomic():
            for i, trip in enumerate(trips, 1):
                # Use properties to get current values (which fall back to dynamic if cached is missing)
                # But since we just added the fields, they will be 0/default.
                # So we must call the calculation logic.
                
                # Note: properties are already updated to check _cached fields. 
                # We need to bypass them or ensure they calculate.
                
                # To be safe, we'll re-trigger the logic that populates them.
                trip.revenue_cached = trip.revenue
                trip.gst_amount_cached = trip.gst_amount
                trip.total_revenue_cached = trip.total_revenue
                trip.amount_received_cached = trip.calculate_amount_received()
                trip.outstanding_balance_cached = trip.total_revenue_cached - trip.amount_received_cached
                
                # Determine Status
                received = trip.amount_received_cached
                total_rev = trip.total_revenue_cached
                if total_rev <= 0:
                    trip.payment_status_cached = 'Unpaid'
                elif received >= total_rev:
                    trip.payment_status_cached = 'Paid'
                elif received > 0:
                    trip.payment_status_cached = 'Partially Paid'
                else:
                    trip.payment_status_cached = 'Unpaid'

                trip.save(update_fields=[
                    'revenue_cached', 'gst_amount_cached', 'total_revenue_cached',
                    'amount_received_cached', 'outstanding_balance_cached', 'payment_status_cached'
                ])
                if i % 100 == 0:
                    self.stdout.write(f'Processed {i}/{count} trips...')

        self.stdout.write('Populating Bill caches...')
        bills = Bill.objects.all()
        count = bills.count()
        
        with transaction.atomic():
            for i, bill in enumerate(bills, 1):
                bill.subtotal_cached = bill.subtotal
                bill.gst_amount_cached = bill.gst_amount
                bill.total_amount_cached = bill.rounded_total
                bill.amount_received_cached = bill.calculate_amount_received()
                bill.outstanding_balance_cached = bill.total_amount_cached - bill.amount_received_cached
                
                # Determine Status
                received = bill.amount_received_cached
                total = bill.total_amount_cached
                if total <= 0:
                    bill.payment_status_cached = 'Unpaid'
                elif received >= total:
                    bill.payment_status_cached = 'Paid'
                elif received > 0:
                    bill.payment_status_cached = 'Partially Paid'
                else:
                    bill.payment_status_cached = 'Unpaid'

                bill.save(update_fields=[
                    'subtotal_cached', 'gst_amount_cached', 'total_amount_cached',
                    'amount_received_cached', 'outstanding_balance_cached', 'payment_status_cached'
                ])
                if i % 100 == 0:
                    self.stdout.write(f'Processed {i}/{count} bills...')
        
        self.stdout.write(self.style.SUCCESS('Successfully populated all financial caches'))
