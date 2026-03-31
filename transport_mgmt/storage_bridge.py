
import os
import mimetypes
from django.conf import settings
from gdstorage.storage import GoogleDriveStorage
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

class GoogleDriveOAuth2Storage(GoogleDriveStorage):
    """
    An optimized bridge to allow django-googledrive-storage to use OAuth2.
    Optimized for speed by adjusting chunk sizes and upload methods.
    """
    def __init__(self, **kwargs):
        self._permissions = kwargs.get('permissions', None)
        if self._permissions is None:
            from gdstorage.storage import _ANYONE_CAN_READ_PERMISSION_
            self._permissions = (_ANYONE_CAN_READ_PERMISSION_,)

        credentials = Credentials(
            token=None,
            refresh_token=settings.GOOGLE_DRIVE_STORAGE_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_DRIVE_STORAGE_CLIENT_ID,
            client_secret=settings.GOOGLE_DRIVE_STORAGE_CLIENT_SECRET,
            scopes=['https://www.googleapis.com/auth/drive']
        )

        # We use a static build to avoid overhead
        self._drive_service = build('drive', 'v3', credentials=credentials)

    def url(self, name):
        """
        Custom URL generator that finds the file by traversing the folder structure
        and returns the webContentLink.
        """
        try:
            # Split path: e.g. documents/ABC_123/license.pdf
            parts = name.split(os.sep if os.sep in name else '/')
            filename = parts[-1]
            folders = parts[:-1]

            parent_id = getattr(settings, 'GOOGLE_DRIVE_STORAGE_MEDIA_ROOT', None)
            
            # Find the correct parent folder ID by traversing the path
            for folder_name in folders:
                # Escape single quotes for GDrive query
                safe_folder_name = folder_name.replace("'", "\\'")
                query = f"name = '{safe_folder_name}' and mimeType = 'application/vnd.google-apps.folder'"
                if parent_id:
                    query += f" and '{parent_id}' in parents"
                
                results = self._drive_service.files().list(q=query, fields="files(id)").execute()
                files = results.get('files', [])
                if not files:
                    return None # Folder not found
                parent_id = files[0].get('id')

            # Now find the actual file inside that specific parent folder
            # Use same escaping logic
            safe_filename = filename.replace("'", "\\'")
            file_query = f"name = '{safe_filename}' and trashed = false"
            if parent_id:
                file_query += f" and '{parent_id}' in parents"
            
            results = self._drive_service.files().list(
                q=file_query,
                fields="files(id, webContentLink, webViewLink)",
                pageSize=1
            ).execute()
            
            files = results.get('files', [])
            if files:
                return files[0].get('webContentLink') or files[0].get('webViewLink')
        except Exception as e:
            print(f"Error generating GDrive URL for {name}: {str(e)}")
        
        return None

    def _get_or_create_folder(self, name, parent_id=None):
        """
        Helper to find or create a folder in Google Drive.
        """
        query = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder'"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        
        results = self._drive_service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        
        if files:
            return files[0].get('id')
        
        # Create folder if it doesn't exist
        metadata = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            metadata['parents'] = [parent_id]
            
        folder = self._drive_service.files().create(body=metadata, fields='id').execute()
        return folder.get('id')

    def _save(self, name, content):
        """
        Optimized save method that handles subfolders in Google Drive.
        It parses the Django path and ensures actual GDrive folders exist.
        """
        # Split path: e.g. documents/ABC_123/license.pdf
        parts = name.split(os.sep if os.sep in name else '/')
        filename = parts[-1]
        folders = parts[:-1]

        # Start with the root folder configured in settings
        parent_id = getattr(settings, 'GOOGLE_DRIVE_STORAGE_MEDIA_ROOT', None)
        
        # Traverse/Create all intermediate folders
        for folder_name in folders:
            parent_id = self._get_or_create_folder(folder_name, parent_id)

        # Detect mimetype from the filename
        mimetype, _ = mimetypes.guess_type(filename)
        if not mimetype:
            mimetype = 'application/octet-stream'

        file_metadata = {'name': filename}
        if parent_id:
            file_metadata['parents'] = [parent_id]

        file_size = content.size
        # Use multipart for small files (< 5MB), resumable for larger
        is_resumable = file_size > (5 * 1024 * 1024)
        
        media_body = MediaIoBaseUpload(
            content, 
            mimetype=mimetype, 
            resumable=is_resumable,
            chunksize=1024 * 1024 # 1MB chunks
        )
        
        file_res = self._drive_service.files().create(
            body=file_metadata,
            media_body=media_body,
            fields='id'
        ).execute()
        
        file_id = file_res.get('id')
        
        # Apply permissions
        for permission in self._permissions:
            try:
                self._drive_service.permissions().create(
                    fileId=file_id,
                    body=permission.raw
                ).execute()
            except Exception:
                pass
                
        return name
