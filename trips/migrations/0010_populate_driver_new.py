from django.db import migrations

def populate_driver(apps, schema_editor):
    Trip = apps.get_model('trips', 'Trip')
    Driver = apps.get_model('drivers', 'Driver')

    for trip in Trip.objects.all():
        if trip.driver:
            try:
                driver_profile = Driver.objects.get(user=trip.driver)
                trip.driver_new = driver_profile
                trip.save()
            except Driver.DoesNotExist:
                raise Exception(f"Data Integrity Error: Trip {trip.pk} refers to User {trip.driver.pk} which has no Driver profile.")

def reverse_populate(apps, schema_editor):
    Trip = apps.get_model('trips', 'Trip')
    for trip in Trip.objects.all():
        trip.driver_new = None
        trip.save()

class Migration(migrations.Migration):

    dependencies = [
        ('trips', '0009_trip_driver_new'),
        ('drivers', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(populate_driver, reverse_populate),
    ]
