from django.conf import settings
from storages.backends.s3 import S3Storage
from boto3.s3.transfer import TransferConfig


class CloudflareR2Storage(S3Storage):
    """
    S3-compatible storage backend specifically tuned for Cloudflare R2.
    - Zero egress fees.
    - Fast presigned HMAC URLs for private documents.
    - file_overwrite=True skips redundant pre-upload HEAD/exists checks against R2.
    - 50MB multipart threshold ensures all standard documents upload via a single atomic PutObject.
    - Supports optional custom domain or pub-xxx.r2.dev.
    """
    default_acl = None
    signature_version = 's3v4'
    file_overwrite = True
    region_name = 'auto'
    addressing_style = 'path'

    def __init__(self, **kwargs):
        if 'transfer_config' not in kwargs:
            kwargs['transfer_config'] = TransferConfig(
                multipart_threshold=50 * 1024 * 1024,
                multipart_chunksize=25 * 1024 * 1024,
            )
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
