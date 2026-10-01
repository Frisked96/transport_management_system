"""
Views for Documents application
"""
import os
from datetime import timedelta
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Q, Count
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView

from .models import Document, DocumentFile, DocumentRenewal
from .forms import DocumentForm, DocumentFileForm, DocumentRenewalForm, DocumentFileFormSet
from fleet.models import Vehicle
from drivers.models import Driver


class DocumentListView(LoginRequiredMixin, ListView):
    template_name = 'documents/document_list.html'
    paginate_by = 10

    def get_queryset(self):
        self.doc_type = self.request.GET.get('type', 'vehicles')
        search_term = self.request.GET.get('search')
        
        today = timezone.now().date()
        warning_date = today + timedelta(days=30)

        if self.doc_type == 'drivers':
            queryset = Driver.objects.select_related('user').prefetch_related('documents').annotate(
                total_docs=Count('documents'),
                expiring_count=Count(
                    'documents', 
                    filter=Q(documents__never_expires=False, documents__expiry_date__lte=warning_date, documents__expiry_date__gte=today)
                ),
                expired_count=Count(
                    'documents', 
                    filter=Q(documents__never_expires=False, documents__expiry_date__lt=today)
                )
            ).all().order_by('user__first_name')
            if search_term:
                queryset = queryset.filter(
                    Q(user__first_name__icontains=search_term) |
                    Q(user__last_name__icontains=search_term) |
                    Q(user__username__icontains=search_term) |
                    Q(employee_id__icontains=search_term) |
                    Q(license_number__icontains=search_term)
                )
        else:
            queryset = Vehicle.objects.prefetch_related('documents').annotate(
                total_docs=Count('documents'),
                expiring_count=Count(
                    'documents', 
                    filter=Q(documents__never_expires=False, documents__expiry_date__lte=warning_date, documents__expiry_date__gte=today)
                ),
                expired_count=Count(
                    'documents', 
                    filter=Q(documents__never_expires=False, documents__expiry_date__lt=today)
                )
            ).all().order_by('registration_plate')
            if search_term:
                queryset = queryset.filter(
                    Q(registration_plate__icontains=search_term) |
                    Q(make_model__icontains=search_term)
                )
        
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['doc_type'] = self.doc_type
        context['search_term'] = self.request.GET.get('search')
        
        if self.doc_type == 'drivers':
            context['drivers'] = context['page_obj']
            context['vehicles'] = []
        else:
            context['vehicles'] = context['page_obj']
            context['drivers'] = []
            
        return context


class DocumentCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Document
    form_class = DocumentForm
    template_name = 'documents/document_form.html'
    permission_required = 'documents.add_document'

    def dispatch(self, request, *args, **kwargs):
        self.vehicle_pk = kwargs.get('vehicle_pk')
        self.driver_pk = kwargs.get('driver_pk')

        if self.vehicle_pk:
            self.parent_obj = get_object_or_404(Vehicle, pk=self.vehicle_pk)
            self.context_name = 'vehicle'
        elif self.driver_pk:
            self.parent_obj = get_object_or_404(Driver, pk=self.driver_pk)
            self.context_name = 'driver'
        else:
            return redirect('home')

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context[self.context_name] = self.parent_obj
        if self.request.POST:
            context['files_formset'] = DocumentFileFormSet(self.request.POST, self.request.FILES)
        else:
            context['files_formset'] = DocumentFileFormSet()
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        files_formset = context['files_formset']
        
        if files_formset.is_valid():
            if self.vehicle_pk:
                form.instance.vehicle = self.parent_obj
            elif self.driver_pk:
                form.instance.driver = self.parent_obj
            
            form.instance.added_by = self.request.user
            self.object = form.save()
            self.object.sync_to_history(user=self.request.user)
            
            # Save files directly to storage (Google Drive / default storage)
            files_formset.instance = self.object
            instances = files_formset.save(commit=False)
            try:
                for i, doc_file in enumerate(instances):
                    doc_file.document = self.object
                    doc_file._upload_index = i + 1
                    doc_file.upload_status = 'completed'
                    doc_file.local_tmp_path = None
                    doc_file.save()
                
                for obj in files_formset.deleted_objects:
                    obj.delete()

                messages.success(self.request, 'Document saved successfully.')
            except Exception as e:
                messages.error(self.request, f"Document saved, but file upload failed: {str(e)}")

            return redirect(self.get_success_url())
        else:
            return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        if self.vehicle_pk:
            return reverse_lazy('vehicle-detail', kwargs={'pk': self.vehicle_pk})
        elif self.driver_pk:
            return reverse_lazy('driver-detail', kwargs={'pk': self.driver_pk})
        return reverse_lazy('home')


class DocumentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Document
    form_class = DocumentForm
    template_name = 'documents/document_form.html'
    permission_required = 'documents.change_document'
    object: Document

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['files_formset'] = DocumentFileFormSet(self.request.POST, self.request.FILES, instance=self.object)
        else:
            context['files_formset'] = DocumentFileFormSet(instance=self.object)
        
        if self.object.vehicle:
            context['vehicle'] = self.object.vehicle
        elif self.object.driver:
            context['driver'] = self.object.driver
            
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        files_formset = context['files_formset']
        
        if files_formset.is_valid():
            # If base doc, make sure name isn't lost if disabled in form
            if self.object.is_base_document:
                form.instance.document_name = self.object.document_name

            self.object = form.save()
            self.object.sync_to_history(user=self.request.user)
            
            # Save files directly to storage (Google Drive / default storage)
            files_formset.instance = self.object
            instances = files_formset.save(commit=False)
            existing_count = self.object.files.exclude(pk__in=[f.pk for f in instances if f.pk]).count()
            new_file_idx = 0
            try:
                for doc_file in instances:
                    if not doc_file.pk:
                        new_file_idx += 1
                        doc_file._upload_index = existing_count + new_file_idx
                    doc_file.document = self.object
                    doc_file.upload_status = 'completed'
                    doc_file.local_tmp_path = None
                    doc_file.save()
                
                for obj in files_formset.deleted_objects:
                    obj.delete()

                messages.success(self.request, 'Document updated successfully.')
            except Exception as e:
                messages.error(self.request, f"Document updated, but file upload failed: {str(e)}")

            return redirect(self.get_success_url())
        else:
            return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        if self.object.vehicle:
            return reverse_lazy('vehicle-detail', kwargs={'pk': self.object.vehicle.pk})
        elif self.object.driver:
            return reverse_lazy('driver-detail', kwargs={'pk': self.object.driver.pk})
        return reverse_lazy('home')


class DocumentDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Document
    template_name = 'documents/document_confirm_delete.html'
    permission_required = 'documents.delete_document'
    object: Document

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.is_base_document:
            messages.error(request, f"Base compliance document '{self.object.document_name}' cannot be deleted.")
            if self.object.vehicle:
                return redirect('vehicle-detail', pk=self.object.vehicle.pk)
            return redirect('document-list')
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        messages.success(self.request, 'Document deleted successfully!')
        if self.object.vehicle:
            return reverse_lazy('vehicle-detail', kwargs={'pk': self.object.vehicle.pk})
        elif self.object.driver:
            return reverse_lazy('driver-detail', kwargs={'pk': self.object.driver.pk})
        return reverse_lazy('home')


class DocumentRenewView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    View to record a renewal for a document with valid_from, valid_to, and renewal cost.
    """
    model = DocumentRenewal
    form_class = DocumentRenewalForm
    template_name = 'documents/document_renew_form.html'
    permission_required = 'documents.change_document'

    def dispatch(self, request, *args, **kwargs):
        self.document = get_object_or_404(Document, pk=kwargs.get('pk'))
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        # Pre-fill valid_from with current expiry_date or today
        if self.document.expiry_date:
            initial['valid_from'] = self.document.expiry_date
        else:
            initial['valid_from'] = timezone.now().date()
        if self.document.document_number:
            initial['document_number'] = self.document.document_number
        if self.document.cost:
            initial['cost'] = self.document.cost
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['document'] = self.document
        context['vehicle'] = self.document.vehicle
        context['driver'] = self.document.driver
        return context

    def form_valid(self, form):
        form.instance.document = self.document
        form.instance.renewed_by = self.request.user
        self.object = form.save()
        messages.success(self.request, f"Renewal for '{self.document.document_name}' recorded successfully.")
        return redirect('document-history', pk=self.document.pk)


class DocumentHistoryView(LoginRequiredMixin, DetailView):
    """
    Detail view showing full renewal history, validity periods, and expenses for a document.
    """
    model = Document
    template_name = 'documents/document_history.html'
    context_object_name = 'document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['renewals'] = self.object.renewals.all().order_by('-valid_to', '-created_at')
        context['total_cost'] = self.object.total_expenses
        context['vehicle'] = self.object.vehicle
        context['driver'] = self.object.driver
        return context


class DocumentRenewalUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View to edit a historical renewal entry.
    """
    model = DocumentRenewal
    form_class = DocumentRenewalForm
    template_name = 'documents/document_renewal_form.html'
    permission_required = 'documents.change_document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['document'] = self.object.document
        context['vehicle'] = self.object.document.vehicle
        return context

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, 'Renewal entry updated successfully.')
        return redirect('document-history', pk=self.object.document.pk)


class DocumentRenewalDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    View to delete an accidental historical renewal entry.
    """
    model = DocumentRenewal
    template_name = 'documents/document_renewal_confirm_delete.html'
    permission_required = 'documents.change_document'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['document'] = self.object.document
        context['vehicle'] = self.object.document.vehicle
        return context

    def get_success_url(self):
        return reverse_lazy('document-history', kwargs={'pk': self.object.document.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Renewal history entry deleted.')
        return super().form_valid(form)


@login_required
def get_upload_status(request):
    """
    Returns counts of active and recently completed background uploads.
    """
    active_count = DocumentFile.objects.filter(upload_status__in=['pending', 'uploading']).count()
    recent_time = timezone.now() - timedelta(minutes=10)
    completed_count = DocumentFile.objects.filter(upload_status='completed', created_at__gte=recent_time).count()
    failed_count = DocumentFile.objects.filter(upload_status='failed', created_at__gte=recent_time).count()
    
    return JsonResponse({
        'active': active_count,
        'completed': completed_count,
        'failed': failed_count
    })


@login_required
def document_download_proxy(request, pk):
    """
    Proxy view to handle document URL generation for a specific DocumentFile.
    """
    doc_file = get_object_or_404(DocumentFile, pk=pk)
    
    if not doc_file.file or not doc_file.file.name:
        messages.error(request, "File not found.")
        return redirect('document-list')
    
    try:
        url = doc_file.file.url
        if url:
            return HttpResponseRedirect(str(url))
        else:
            messages.error(request, "Google Drive storage returned an empty URL.")
    except Exception as e:
        messages.error(request, f"Error accessing document storage: {str(e)}")
    
    return redirect('document-list')


@login_required
def renewal_download_proxy(request, pk):
    """
    Proxy view to handle document URL generation for a DocumentRenewal's receipt_file.
    Prevents slow page loads when using cloud storage.
    """
    renewal = get_object_or_404(DocumentRenewal, pk=pk)
    
    if not renewal.receipt_file or not renewal.receipt_file.name:
        messages.error(request, "File not found.")
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
