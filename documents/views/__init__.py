"""
Modularized views for Documents application.
Exports all views and helpers for backward compatibility.
"""
from .documents import (
    DocumentListView,
    DocumentCreateView,
    DocumentUpdateView,
    DocumentDeleteView,
)
from .renewals import (
    DocumentRenewView,
    DocumentHistoryView,
    DocumentRenewalUpdateView,
    DocumentRenewalDeleteView,
)
from .files import (
    get_upload_status,
    document_download_proxy,
    renewal_download_proxy,
)

__all__ = [
    'DocumentListView',
    'DocumentCreateView',
    'DocumentUpdateView',
    'DocumentDeleteView',
    'DocumentRenewView',
    'DocumentHistoryView',
    'DocumentRenewalUpdateView',
    'DocumentRenewalDeleteView',
    'get_upload_status',
    'document_download_proxy',
    'renewal_download_proxy',
]
