"""
File handling and download proxy views for Documents app.
"""
import os
import mimetypes
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponseRedirect, FileResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.clickjacking import xframe_options_sameorigin

from documents.models import DocumentFile, DocumentRenewal


@login_required
@permission_required('documents.view_document', raise_exception=True)
@xframe_options_sameorigin
def document_download_proxy(request, pk):
    """
    Proxy view to handle document URL generation for a specific DocumentFile.
    Supports ?download=1 to force attachment download.
    """
    doc_file = get_object_or_404(DocumentFile, pk=pk)
    
    if not doc_file.file or not doc_file.file.name:
        messages.error(request, "File not found.")
        return redirect('document-list')
    
    filename = os.path.basename(doc_file.file.name)
    if request.GET.get('download'):
        storage = doc_file.file.storage
        if hasattr(storage, 'bucket_name') and hasattr(storage, 'connection'):
            try:
                client = storage.connection.meta.client
                url = client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': storage.bucket_name,
                        'Key': doc_file.file.name,
                        'ResponseContentDisposition': f'attachment; filename="{filename}"'
                    },
                    ExpiresIn=getattr(settings, 'CLOUDFLARE_R2_EXPIRATION_SECS', 3600)
                )
                return HttpResponseRedirect(str(url))
            except Exception:
                pass
        try:
            content_type, _ = mimetypes.guess_type(filename)
            return FileResponse(doc_file.file.open('rb'), as_attachment=True, filename=filename, content_type=content_type)
        except Exception as e:
            messages.error(request, f"Error downloading document: {str(e)}")
            return redirect('document-list')
    
    try:
        url = doc_file.file.url
        if url:
            return HttpResponseRedirect(str(url))
        else:
            messages.error(request, "Cloud storage returned an empty URL.")
    except Exception as e:
        messages.error(request, f"Error accessing document storage: {str(e)}")
    
    return redirect('document-list')


@login_required
@permission_required('documents.view_document', raise_exception=True)
@xframe_options_sameorigin
def renewal_download_proxy(request, pk):
    """
    Proxy view to handle document URL generation for a DocumentRenewal's receipt_file.
    Supports ?download=1 to force attachment download.
    """
    renewal = get_object_or_404(DocumentRenewal, pk=pk)
    
    if not renewal.receipt_file or not renewal.receipt_file.name:
        messages.error(request, "File not found.")
        return redirect('document-history', pk=renewal.document.pk)
    
    filename = os.path.basename(renewal.receipt_file.name)
    if request.GET.get('download'):
        storage = renewal.receipt_file.storage
        if hasattr(storage, 'bucket_name') and hasattr(storage, 'connection'):
            try:
                client = storage.connection.meta.client
                url = client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': storage.bucket_name,
                        'Key': renewal.receipt_file.name,
                        'ResponseContentDisposition': f'attachment; filename="{filename}"'
                    },
                    ExpiresIn=getattr(settings, 'CLOUDFLARE_R2_EXPIRATION_SECS', 3600)
                )
                return HttpResponseRedirect(str(url))
            except Exception:
                pass
        try:
            content_type, _ = mimetypes.guess_type(filename)
            return FileResponse(renewal.receipt_file.open('rb'), as_attachment=True, filename=filename, content_type=content_type)
        except Exception as e:
            messages.error(request, f"Error downloading document: {str(e)}")
            return redirect('document-history', pk=renewal.document.pk)
    
    try:
        url = renewal.receipt_file.url
        if url:
            return HttpResponseRedirect(str(url))
        else:
            messages.error(request, "Storage returned an empty URL.")
    except Exception as e:
        messages.error(request, f"Error accessing document storage: {str(e)}")
    
    return redirect('document-history', pk=renewal.document.pk)
