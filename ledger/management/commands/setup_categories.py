from django.core.management.base import BaseCommand
from ledger.models import TransactionCategory
from django.db.models import ProtectedError

class Command(BaseCommand):
    help = 'Initializes current categories and REMOVES outdated ones'

    def handle(self, *args, **options):
        # 1. Define the "Source of Truth" categories
        # Format: (name, type, description)
        current_categories = [
            # Income Categories
            ('Trip Payment', TransactionCategory.TYPE_INCOME, 'Standard payment received for a trip'),
            ('Invoice Payment', TransactionCategory.TYPE_INCOME, 'Payment received against a specific bill/invoice'),
            ('Deductions', TransactionCategory.TYPE_INCOME, 'Non-cash reductions in outstanding balance (e.g. TDS, Shortage)'),
            ('Payment In', TransactionCategory.TYPE_INCOME, 'General payment received (unallocated)'),
            ('Halting', TransactionCategory.TYPE_INCOME, 'Charges for vehicle halting/detention'),
            ('Debit Note', TransactionCategory.TYPE_INCOME, 'Increase in amount owed (Adjustment)'),
            ('Credit Note', TransactionCategory.TYPE_INCOME, 'Decrease in amount owed (Adjustment)'),
            ('Standard', TransactionCategory.TYPE_INCOME, 'General service or miscellaneous income'),
            
            # Expense Categories
            ('Payment Out', TransactionCategory.TYPE_EXPENSE, 'General payment made to a vendor/party'),
            ('Expense', TransactionCategory.TYPE_EXPENSE, 'General business expense'),
            ('Maintenance', TransactionCategory.TYPE_EXPENSE, 'Vehicle maintenance or repair expense'),
        ]

        target_names = [cat[0] for cat in current_categories]
        created_count = 0
        updated_count = 0
        deleted_count = 0
        skipped_count = 0

        self.stdout.write("--- Syncing Categories ---")

        # 2. Add or Update target categories
        for name, cat_type, desc in current_categories:
            cat, created = TransactionCategory.objects.get_or_create(
                name=name,
                defaults={'type': cat_type, 'description': desc}
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'Created: {name}'))
            else:
                # Sync type and description if they changed
                changed = False
                if cat.type != cat_type:
                    cat.type = cat_type
                    changed = True
                if cat.description != desc:
                    cat.description = desc
                    changed = True
                
                if changed:
                    cat.save()
                    updated_count += 1
                    self.stdout.write(self.style.WARNING(f'Updated: {name}'))

        # 3. Identify and Remove outdated categories
        all_existing = TransactionCategory.objects.all()
        for cat in all_existing:
            if cat.name not in target_names:
                try:
                    cat_name = cat.name
                    cat.delete()
                    deleted_count += 1
                    self.stdout.write(self.style.NOTICE(f'Deleted Outdated: {cat_name}'))
                except ProtectedError:
                    skipped_count += 1
                    self.stdout.write(self.style.ERROR(
                        f'Cannot Delete: "{cat.name}" is still used by existing financial records. '
                        f'Please reassign those records manually first.'
                    ))

        self.stdout.write("\n--- Final Summary ---")
        self.stdout.write(f'Target Categories Processed: {len(current_categories)}')
        self.stdout.write(f'New Created: {created_count}')
        self.stdout.write(f'Updated/Synced: {updated_count}')
        self.stdout.write(self.style.SUCCESS(f'Successfully Deleted: {deleted_count}'))
        
        if skipped_count > 0:
            self.stdout.write(self.style.WARNING(f'Skipped (In-Use): {skipped_count}'))
            self.stdout.write(self.style.NOTICE("Note: Old categories with existing transactions were kept to prevent data loss."))
