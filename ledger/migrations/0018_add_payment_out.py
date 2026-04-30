from django.db import migrations

def add_payment_out(apps, schema_editor):
    TransactionCategory = apps.get_model('ledger', 'TransactionCategory')
    TransactionCategory.objects.get_or_create(
        name='Payment Out',
        defaults={'type': 'Expense'}
    )

class Migration(migrations.Migration):
    dependencies = [
        ('ledger', '0017_initialize_invoice_categories'),
    ]

    operations = [
        migrations.RunPython(add_payment_out),
    ]
