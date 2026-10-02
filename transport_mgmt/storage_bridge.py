
import os
import mimetypes
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from storages.backends.s3 import S3Storage
from gdstorage.storage import GoogleDriveStorage
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


class CloudflareR2Storage(S3Storage):
    """
    S3-compatible storage backend specifically tuned for Cloudflare R2.
    - Zero egress fees.
    - Fast presigned HMAC URLs for private documents.
    - Supports optional custom domain or pub-xxx.r2.dev.
    """
    default_acl = None
    signature_version = 's3v4'
    file_overwrite = False
    region_name = 'auto'
    addressing_style = 'path'

    def __init__(self, **kwargs):
        if 'access_key' not in kwargs and hasattr(settings, 'CLOUDFLARE_R2_ACCESS_KEY_ID'):
            kwargs['access_key'] = getattr(settings, 'CLOUDFLARE_R2_ACCESS_KEY_ID', None)
        if 'secret_key' not in kwargs and hasattr(settings, 'CLOUDFLARE_R2_SECRET_ACCESS_KEY'):
            kwargs['secret_key'] = getattr(settings, 'CLOUDFLARE_R2_SECRET_ACCESS_KEY', None)
        if 'bucket_name' not in kwargs and hasattr(settings, 'CLOUDFLARE_R2_BUCKET_NAME'):
            kwargs['bucket_name'] = getattr(settings, 'CLOUDFLARE_R2_BUCKET_NAME', None)
        if 'endpoint_url' not in kwargs:
            account_id = getattr(settings, 'CLOUDFLARE_R2_ACCOUNT_ID', None)
            if account_id:
                kwargs['endpoint_url'] = f"https://{account_id}.r2.cloudflarestorage.com"
        if 'custom_domain' not in kwargs and hasattr(settings, 'CLOUDFLARE_R2_CUSTOM_DOMAIN'):
            kwargs['custom_domain'] = getattr(settings, 'CLOUDFLARE_R2_CUSTOM_DOMAIN', None)
        if 'querystring_auth' not in kwargs:
            custom_domain = kwargs.get('custom_domain') or getattr(settings, 'CLOUDFLARE_R2_CUSTOM_DOMAIN', None)
            kwargs['querystring_auth'] = False if custom_domain else True
        if 'querystring_expire' not in kwargs and hasattr(settings, 'CLOUDFLARE_R2_EXPIRATION_SECS'):
            kwargs['querystring_expire'] = getattr(settings, 'CLOUDFLARE_R2_EXPIRATION_SECS', 3600)
            
        super().__init__(**kwargs)


class GoogleDriveOAuth2Storage(GoogleDriveStorage):
    """
    An optimized bridge to allow django-googledrive-storage to use OAuth2.
    Optimized for speed by adjusting chunk sizes and upload methods.
    Includes automatic local storage fallback when Google Drive is unreachable.
    """
    # Simple in-memory cache for folder IDs to avoid redundant API calls
    _folder_id_cache = {}

    def __init__(self, **kwargs):
        self._permissions = kwargs.get('permissions', None)
        if self._permissions is None:
            from gdstorage.storage import _ANYONE_CAN_READ_PERMISSION_
            self._permissions = (_ANYONE_CAN_READ_PERMISSION_,)

        self._local_storage = FileSystemStorage()
        self._drive_service = None

        self.credentials = Credentials(
            token=None,
            refresh_token=settings.GOOGLE_DRIVE_STORAGE_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_DRIVE_STORAGE_CLIENT_ID,
            client_secret=settings.GOOGLE_DRIVE_STORAGE_CLIENT_SECRET,
            scopes=['https://www.googleapis.com/auth/drive']
        )
        
        # Initial refresh to populate the access token
        try:
            self.credentials.refresh(Request())
            self._drive_service = build('drive', 'v3', credentials=self.credentials)
        except Exception as e:
            print(f"Notice: GDrive service initialization failed ({str(e)}). Local fallback active.")

    def _ensure_service(self):
        """Helper to ensure credentials are valid before any call"""
        if not self.credentials.valid:
            try:
                self.credentials.refresh(Request())
            except Exception as e:
                print(f"Failed to refresh GDrive token: {str(e)}")
        if not self._drive_service:
            self._drive_service = build('drive', 'v3', credentials=self.credentials)

    def url(self, name):
        """
        Custom URL generator that finds the file by traversing the folder structure
        and returns the webContentLink. If saved locally via fallback, returns local media URL.
        """
        if self._local_storage.exists(name):
            return self._local_storage.url(name)

        try:
            self._ensure_service()
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
            if self._local_storage.exists(name):
                return self._local_storage.url(name)
        
        return None

    def exists(self, name):
        if self._local_storage.exists(name):
            return True
        try:
            return bool(self.url(name))
        except Exception:
            return False

    def path(self, name):
        if self._local_storage.exists(name):
            return self._local_storage.path(name)
        return super().path(name)

    def _open(self, name, mode='rb'):
        if self._local_storage.exists(name):
            return self._local_storage._open(name, mode)
        return super()._open(name, mode)

    def delete(self, name):
        if self._local_storage.exists(name):
            try:
                self._local_storage.delete(name)
            except Exception:
                pass
        try:
            super().delete(name)
        except Exception:
            pass

    def _get_or_create_folder(self, name, parent_id=None):
        """
        Helper to find or create a folder in Google Drive.
        Uses in-memory cache to avoid redundant calls.
        """
        self._ensure_service()
        cache_key = f"{name}_{parent_id}"
        if cache_key in self._folder_id_cache:
            return self._folder_id_cache[cache_key]

        safe_name = name.replace("'", "\\'")
        query = f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        
        results = self._drive_service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        
        if files:
            folder_id = files[0].get('id')
        else:
            # Create folder if it doesn't exist
            metadata = {
                'name': name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            if parent_id:
                metadata['parents'] = [parent_id]
                
            folder = self._drive_service.files().create(body=metadata, fields='id').execute()
            folder_id = folder.get('id')
        
        self._folder_id_cache[cache_key] = folder_id
        return folder_id

    def _save(self, name, content):
        """
        Attempts to save directly to Google Drive.
        If Google Drive is unreachable (e.g. [Errno 101] Network is unreachable,
        offline server, timeout), automatically falls back to local FileSystemStorage.
        """
        try:
            self._ensure_service()
            if hasattr(content, 'seek'):
                content.seek(0)

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

            file_size = getattr(content, 'size', None)
            if file_size is None:
                try:
                    content.seek(0, os.SEEK_END)
                    file_size = content.tell()
                    content.seek(0)
                except Exception:
                    file_size = 0

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

        except Exception as e:
            print(f"Notice: Google Drive upload failed ({str(e)}). Falling back to local file storage for '{name}'.")
            if hasattr(content, 'seek'):
                content.seek(0)
            return self._local_storage._save(name, content)


