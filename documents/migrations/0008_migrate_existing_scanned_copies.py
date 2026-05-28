from django.db import migrations

def migrate_scanned_copies(apps, schema_editor):
    Document = apps.get_model('documents', 'Document')
    DocumentFile = apps.get_model('documents', 'DocumentFile')
    
    for doc in Document.objects.all():
        if doc.scanned_copy:
            # Create a DocumentFile record for each existing scanned copy
            # We don't need to actually move files in storage, just link them
            # The storage path won't change immediately, but new files will use the new path
            DocumentFile.objects.create(
                document=doc,
                file=doc.scanned_copy
            )

def reverse_migrate_scanned_copies(apps, schema_editor):
    DocumentFile = apps.get_model('documents', 'DocumentFile')
    # Since we didn't remove scanned_copy from Document yet, we can't easily put it back 
    # if there were multiple. But this is a forward-only important migration.
    # We'll just delete the files.
    DocumentFile.objects.all().delete()

class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0007_remove_document_document_type_document_document_name_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_scanned_copies, reverse_migrate_scanned_copies),
    ]
