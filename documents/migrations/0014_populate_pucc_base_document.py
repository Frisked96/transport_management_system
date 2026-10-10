from django.db import migrations


def populate_pucc_document(apps, schema_editor):
    Vehicle = apps.get_model('fleet', 'Vehicle')
    Document = apps.get_model('documents', 'Document')

    synonyms = ['pucc', 'puc', 'pollution', 'pollution under control', 'pollution certificate', 'pollution under control certificate']

    for vehicle in Vehicle.objects.all():
        existing_docs = Document.objects.filter(vehicle=vehicle)
        matched_doc = None
        for doc in existing_docs:
            if doc.document_name.strip().lower() in synonyms:
                matched_doc = doc
                break
        if matched_doc:
            if not matched_doc.is_base_document:
                matched_doc.is_base_document = True
                matched_doc.save(update_fields=['is_base_document'])
        else:
            Document.objects.create(
                vehicle=vehicle,
                document_name='PUCC',
                is_base_document=True,
                cost=0
            )


def reverse_populate(apps, schema_editor):
    Document = apps.get_model('documents', 'Document')
    Document.objects.filter(
        document_name='PUCC',
        is_base_document=True,
        expiry_date__isnull=True,
        cost=0,
        document_number__isnull=True
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0013_documentfile_renewal'),
    ]

    operations = [
        migrations.RunPython(populate_pucc_document, reverse_populate),
    ]
