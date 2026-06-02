import os
import threading
from django.core.files import File
from .models import DocumentFile

def process_uploads_background(doc_file_ids):
    """
    Spawns a background thread to upload files from local storage to Google Drive.
    """
    def run():
        # Using a new thread. In a production environment with high traffic, 
        # a task queue like Celery or Huey would be preferred.
        for pk in doc_file_ids:
            try:
                doc_file = DocumentFile.objects.get(pk=pk)
                if doc_file.upload_status != 'pending':
                    continue

                doc_file.upload_status = 'uploading'
                doc_file.save()

                if not doc_file.local_tmp_path or not os.path.exists(doc_file.local_tmp_path):
                    doc_file.upload_status = 'failed'
                    doc_file.error_message = "Local temporary file not found."
                    doc_file.save()
                    continue

                # Open the local file and save it to the FileField
                # This triggers the GoogleDriveOAuth2Storage backend
                with open(doc_file.local_tmp_path, 'rb') as f:
                    django_file = File(f)
                    filename = os.path.basename(doc_file.local_tmp_path)
                    # The .save() method on the FileField handles the storage interaction
                    doc_file.file.save(filename, django_file, save=True)

                # Clean up local file
                try:
                    os.remove(doc_file.local_tmp_path)
                except OSError:
                    pass

                doc_file.upload_status = 'completed'
                doc_file.local_tmp_path = None # Clear path after successful sync
                doc_file.save()

            except Exception as e:
                try:
                    df = DocumentFile.objects.get(pk=pk)
                    df.upload_status = 'failed'
                    df.error_message = str(e)
                    df.save()
                except Exception:
                    pass
                print(f"Background upload error for file {pk}: {str(e)}")

    thread = threading.Thread(target=run)
    thread.daemon = True
    thread.start()
