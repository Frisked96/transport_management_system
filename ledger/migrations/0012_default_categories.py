from django.db import migrations

def create_default_categories(apps, schema_editor):
    TransactionCategory = apps.get_model('ledger', 'TransactionCategory')

    default_categories = [
        # Income
        {'name': 'Trip Payment', 'type': 'Income', 'description': 'Payment received for trips'},
        {'name': 'Invoice Payment', 'type': 'Income', 'description': 'Payment received against an invoice'},
        {'name': 'General Income', 'type': 'Income', 'description': 'Other general income'},

        # Expense
        {'name': 'Fuel Expense', 'type': 'Expense', 'description': 'Cost of fuel'},
        {'name': 'Maintenance Expense', 'type': 'Expense', 'description': 'Vehicle maintenance costs'},
        {'name': 'Driver Salary', 'type': 'Expense', 'description': 'Payments to drivers'},
        {'name': 'Toll Expense', 'type': 'Expense', 'description': 'Toll charges'},
        {'name': 'Deductions', 'type': 'Expense', 'description': 'Deductions from payments'},
        {'name': 'General Expense', 'type': 'Expense', 'description': 'Other general expenses'},
    ]

    for cat_data in default_categories:
        TransactionCategory.objects.get_or_create(
            name=cat_data['name'],
            defaults={'type': cat_data['type'], 'description': cat_data['description']}
        )

def remove_default_categories(apps, schema_editor):
    TransactionCategory = apps.get_model('ledger', 'TransactionCategory')
    # We might not want to delete these automatically on reverse migration if they are in use,
    # but for completeness, we can delete the exact names we created.
    category_names = [
        'Trip Payment', 'Invoice Payment', 'General Income',
        'Fuel Expense', 'Maintenance Expense', 'Driver Salary',
        'Toll Expense', 'Deductions', 'General Expense'
    ]
    TransactionCategory.objects.filter(name__in=category_names).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0011_remove_companyaccount_invoice_template_and_more'),
    ]

    operations = [
        migrations.RunPython(create_default_categories, remove_default_categories),
    ]
