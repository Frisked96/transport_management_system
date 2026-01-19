from django.db import migrations

def migrate_expenses(apps, schema_editor):
    Trip = apps.get_model('trips', 'Trip')
    TripExpense = apps.get_model('trips', 'TripExpense')

    for trip in Trip.objects.all():
        if trip.diesel_expense > 0:
            TripExpense.objects.create(
                trip=trip,
                name='Diesel',
                amount=trip.diesel_expense,
                notes='Migrated from legacy field'
            )
        if trip.toll_expense > 0:
            TripExpense.objects.create(
                trip=trip,
                name='Toll',
                amount=trip.toll_expense,
                notes='Migrated from legacy field'
            )

def reverse_migrate_expenses(apps, schema_editor):
    Trip = apps.get_model('trips', 'Trip')
    TripExpense = apps.get_model('trips', 'TripExpense')

    for trip in Trip.objects.all():
        # Restore Diesel
        diesel = TripExpense.objects.filter(trip=trip, name='Diesel').first()
        if diesel:
            trip.diesel_expense = diesel.amount

        # Restore Toll
        toll = TripExpense.objects.filter(trip=trip, name='Toll').first()
        if toll:
            trip.toll_expense = toll.amount

        trip.save()

class Migration(migrations.Migration):

    dependencies = [
        ('trips', '0011_remove_trip_driver_new_alter_trip_driver'),
    ]

    operations = [
        migrations.RunPython(migrate_expenses, reverse_migrate_expenses),
    ]
