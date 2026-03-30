
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

    def _save(self, name, content):
        """
        Optimized save method with proper Mimetype detection to prevent AttributeError.
        """
        folder_id = getattr(settings, 'GOOGLE_DRIVE_STORAGE_MEDIA_ROOT', None)
        
        # Detect mimetype from the filename
        mimetype, _ = mimetypes.guess_type(name)
        if not mimetype:
            mimetype = 'application/octet-stream'

        file_metadata = {'name': name}
        if folder_id:
            file_metadata['parents'] = [folder_id]

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
