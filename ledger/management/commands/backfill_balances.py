from django.core.management.base import BaseCommand
from ledger.models import Party, CompanyAccount
from drivers.models import Driver

class Command(BaseCommand):
    help = 'Backfills denormalized balance fields for all parties, accounts, and drivers.'

    def handle(self, *args, **options):
        self.stdout.write('Starting backfill...')
        
        # 1. Parties
        parties = Party.objects.all()
        self.stdout.write(f'Refreshing {parties.count()} parties...')
        for party in parties:
            party.refresh_balance()
            
        # 2. Company Accounts
        accounts = CompanyAccount.objects.all()
        self.stdout.write(f'Refreshing {accounts.count()} accounts...')
        for account in accounts:
            account.refresh_balance()
            
        # 3. Drivers
        drivers = Driver.objects.all()
        self.stdout.write(f'Refreshing {drivers.count()} drivers...')
        for driver in drivers:
            driver.refresh_balance()
            
        self.stdout.write(self.style.SUCCESS('Successfully backfilled all balances.'))
