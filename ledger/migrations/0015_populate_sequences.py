from django.db import migrations
from django.db.models import Max
import re

def populate_sequences(apps, schema_editor):
    Sequence = apps.get_model('ledger', 'Sequence')
    FinancialRecord = apps.get_model('ledger', 'FinancialRecord')
    Trip = apps.get_model('trips', 'Trip')

    # 1. Financial Record Entry Number
    # Check if FinancialRecord table exists and has entries
    if FinancialRecord.objects.exists():
        max_entry = FinancialRecord.objects.aggregate(Max('entry_number'))['entry_number__max']
        if max_entry:
            Sequence.objects.create(key='financial_record_entry_number', value=max_entry)

    # 2. Trip Sequences
    pattern = re.compile(r'^.*-(\d+)/(\d+)/(\d+)$')

    sequences = {}

    # We iterate all trips. If there are many, this might be slow, but for internal app it's fine.
    for trip in Trip.objects.all():
        if not trip.trip_number:
            continue

        match = pattern.match(trip.trip_number)
        if match:
            try:
                total = int(match.group(1))
                month = int(match.group(2))
                year = int(match.group(3))

                # Determine keys
                v_id = trip.vehicle_id

                # Use created_at or date for the time-based keys
                # We need to ensure we map to the correct bucket.
                # Assuming the count was generated based on created_at (or date if created_at was null/equal)
                ref_date = trip.created_at or trip.date
                if not ref_date:
                    continue # Should not happen

                key_total = f"trip_total_{v_id}"
                key_month = f"trip_month_{v_id}_{ref_date.year}_{ref_date.month}"
                key_year = f"trip_year_{v_id}_{ref_date.year}"

                sequences[key_total] = max(sequences.get(key_total, 0), total)
                sequences[key_month] = max(sequences.get(key_month, 0), month)
                sequences[key_year] = max(sequences.get(key_year, 0), year)

            except ValueError:
                pass

    # Batch create/update
    # Since this is a new table, we can just create.
    # But to be safe against re-runs (if we didn't use unique constraints properly in logic), check existence.
    # Actually, we can just iterate sequences dict.
    for key, value in sequences.items():
        # logic to update if exists (unlikely in fresh migration) or create
        # Use update_or_create to be safe
        Sequence.objects.update_or_create(key=key, defaults={'value': value})

def reverse_populate(apps, schema_editor):
    Sequence = apps.get_model('ledger', 'Sequence')
    Sequence.objects.all().delete()

class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0014_sequence'),
        ('trips', '0008_remove_trip_amount_received_and_more'),
    ]

    operations = [
        migrations.RunPython(populate_sequences, reverse_populate),
    ]
