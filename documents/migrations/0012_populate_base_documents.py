from django.db import migrations

def populate_base_documents(apps, schema_editor):
    Vehicle = apps.get_model('fleet', 'Vehicle')
    Document = apps.get_model('documents', 'Document')

    base_docs = [
        'Fitness',
        '1 yr permit',
        '5yr permit',
        'Insurance',
        'Tax',
        'RC',
        'Vltd cirtificate',
    ]

    synonyms = {
        '1 yr permit': ['1 yr permit', '1 year permit', '1-year permit', '1yr permit'],
        '5yr permit': ['5yr permit', '5 yr permit', '5 year permit', '5-year permit', '5yr permit'],
        'Vltd cirtificate': ['vltd cirtificate', 'vltd certificate', 'vltd', 'vltd cert'],
        'Fitness': ['fitness', 'fitness certificate'],
        'Insurance': ['insurance', 'insurance policy'],
        'Tax': ['tax', 'road tax', 'vehicle tax'],
        'RC': ['rc', 'registration certificate', 'rc book'],
    }

    for vehicle in Vehicle.objects.all():
        existing_docs = list(Document.objects.filter(vehicle=vehicle))
        for base_name in base_docs:
            match_names = [s.lower() for s in synonyms.get(base_name, [base_name])]
            matched_doc = None
            for doc in existing_docs:
                if doc.document_name.strip().lower() in match_names:
                    matched_doc = doc
                    break
            if matched_doc:
                if not matched_doc.is_base_document:
                    matched_doc.is_base_document = True
                    matched_doc.save(update_fields=['is_base_document'])
            else:
                new_doc = Document.objects.create(
                    vehicle=vehicle,
                    document_name=base_name,
                    is_base_document=True,
                    cost=0
                )
                existing_docs.append(new_doc)


def reverse_populate(apps, schema_editor):
    Document = apps.get_model('documents', 'Document')
    Document.objects.filter(is_base_document=True, expiry_date__isnull=True, cost=0, document_number__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0011_document_cost_document_is_base_document_and_more'),
        ('fleet', '0018_vehicle_chassis_number_vehicle_engine_number_and_more'),
    ]

    operations = [
        migrations.RunPython(populate_base_documents, reverse_populate),
    ]
