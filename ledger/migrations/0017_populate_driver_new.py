from django.db import migrations

def populate_driver(apps, schema_editor):
    FinancialRecord = apps.get_model('ledger', 'FinancialRecord')
    Driver = apps.get_model('drivers', 'Driver')

    for record in FinancialRecord.objects.all():
        if record.driver:
            try:
                driver_profile = Driver.objects.get(user=record.driver)
                record.driver_new = driver_profile
                record.save()
            except Driver.DoesNotExist:
                raise Exception(f"Data Integrity Error: FinancialRecord {record.pk} refers to User {record.driver.pk} which has no Driver profile.")

def reverse_populate(apps, schema_editor):
    FinancialRecord = apps.get_model('ledger', 'FinancialRecord')
    for record in FinancialRecord.objects.all():
        record.driver_new = None
        record.save()

class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0016_financialrecord_driver_new'),
        ('drivers', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(populate_driver, reverse_populate),
    ]
