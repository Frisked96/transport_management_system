"""
Modularized views for Documents application.
Exports all views and helpers for backward compatibility.
"""
from .documents import (
    DocumentListView,
    DocumentCreateView,
    DocumentUpdateView,
    DocumentDeleteView,
    document_validity_print,
)
from .renewals import (
    DocumentRenewView,
    DocumentHistoryView,
    DocumentRenewalUpdateView,
    DocumentRenewalDeleteView,
)
from .files import (
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
    'document_download_proxy',
    'renewal_download_proxy',
    'document_validity_print',
]
