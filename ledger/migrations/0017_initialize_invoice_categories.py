from django.db import migrations

def initialize_categories(apps, schema_editor):
    TransactionCategory = apps.get_model('ledger', 'TransactionCategory')
    
    # Standard Income categories
    categories = [
        {'name': 'Halting', 'type': 'Income'},
        {'name': 'Debit Note', 'type': 'Income'},
        {'name': 'Credit Note', 'type': 'Income'},
        {'name': 'Standard', 'type': 'Income'},
    ]
    
    for cat in categories:
        TransactionCategory.objects.get_or_create(
            name=cat['name'],
            defaults={'type': cat['type']}
        )

class Migration(migrations.Migration):
    dependencies = [
        ('ledger', '0016_bill_category'),
    ]

    operations = [
        migrations.RunPython(initialize_categories),
    ]
