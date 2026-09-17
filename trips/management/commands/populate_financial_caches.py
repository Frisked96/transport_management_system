from django.core.management.base import BaseCommand
from trips.models import Trip
from ledger.models import Bill
from django.db import transaction

class Command(BaseCommand):
    help = 'Populate cached financial fields for all existing Trips and Bills'

    def handle(self, *args, **options):
        self.stdout.write('Populating Bill caches...')
        bills = Bill.objects.all()
        count_bills = bills.count()
        with transaction.atomic():
            for i, bill in enumerate(bills, 1):
                bill.update_financial_caches()
                if i % 100 == 0:
                    self.stdout.write(f'Processed {i}/{count_bills} bills...')

        self.stdout.write('Populating Trip caches...')
        trips = Trip.objects.all()
        count_trips = trips.count()
        with transaction.atomic():
            for i, trip in enumerate(trips, 1):
                trip.update_financial_caches()
                if i % 100 == 0:
                    self.stdout.write(f'Processed {i}/{count_trips} trips...')
        
        self.stdout.write(self.style.SUCCESS('Successfully populated all financial caches'))
