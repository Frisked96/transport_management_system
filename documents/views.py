"""
Views for Documents application
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Count
from django.utils import timezone
from datetime import timedelta
from .models import Document
from fleet.models import Vehicle
from drivers.models import Driver
from django import forms

from django.forms import inlineformset_factory

class DocumentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Standard tailwind classes for most inputs
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        
        for field_name, field in self.fields.items():
            if field_name == 'never_expires':
                field.widget.attrs.update({
                    'class': 'h-4 w-4 text-emerald-600 focus:ring-emerald-500 border-slate-300 rounded'
                })
            else:
                field.widget.attrs.update({'class': tailwind_classes})

    class Meta:
        model = Document
        # We keep scanned_copy out of the form as we'll use the formset for files
        fields = ['document_name', 'document_number', 'expiry_date', 'never_expires', 'notes']
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

from .models import DocumentFile

class DocumentFileForm(forms.ModelForm):
    class Meta:
        model = DocumentFile
        fields = ['file']
        widgets = {
            'file': forms.FileInput(attrs={
                'class': 'block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-emerald-50 file:text-emerald-700 hover:file:bg-emerald-100'
            })
        }

DocumentFileFormSet = inlineformset_factory(
    Document, 
    DocumentFile, 
    form=DocumentFileForm,
    extra=1, 
    can_delete=True
)

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
        
        # Mapping context name based on type
        if self.doc_type == 'drivers':
            context['drivers'] = context['page_obj']
            context['vehicles'] = []
        else:
            context['vehicles'] = context['page_obj']
            context['drivers'] = []
            
        return context

import os
from .services import process_uploads_background
from django.conf import settings

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
            
            # Handle files manually to save locally first
            upload_dir = os.path.join(settings.BASE_DIR, 'tmp', 'uploads')
            os.makedirs(upload_dir, exist_ok=True)
            
            new_doc_file_ids = []
            
            # Formsets use indexed names for files
            for i in range(int(self.request.POST.get('files-TOTAL_FORMS', 0))):
                file_key = f'files-{i}-file'
                uploaded_file = self.request.FILES.get(file_key)
                
                if uploaded_file:
                    # Save to local temp storage
                    local_path = os.path.join(upload_dir, f"{self.object.pk}_{i}_{uploaded_file.name}")
                    with open(local_path, 'wb+') as destination:
                        for chunk in uploaded_file.chunks():
                            destination.write(chunk)
                    
                    # Create record with local path
                    doc_file = DocumentFile.objects.create(
                        document=self.object,
                        local_tmp_path=local_path,
                        upload_status='pending'
                    )
                    new_doc_file_ids.append(doc_file.pk)
            
            if new_doc_file_ids:
                process_uploads_background(new_doc_file_ids)

            messages.success(self.request, 'Document details saved. Files are being uploaded to Google Drive in the background.')
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
            self.object = form.save()
            
            # Handle new files from formset
            upload_dir = os.path.join(settings.BASE_DIR, 'tmp', 'uploads')
            os.makedirs(upload_dir, exist_ok=True)
            
            new_doc_file_ids = []
            
            # Check for both existing updates and new additions in formset
            for i in range(int(self.request.POST.get('files-TOTAL_FORMS', 0))):
                file_key = f'files-{i}-file'
                uploaded_file = self.request.FILES.get(file_key)
                
                # Only handle NEW files here for background processing
                # Existing files being deleted are handled by files_formset.save()
                if uploaded_file:
                    local_path = os.path.join(upload_dir, f"{self.object.pk}_{i}_{uploaded_file.name}")
                    with open(local_path, 'wb+') as destination:
                        for chunk in uploaded_file.chunks():
                            destination.write(chunk)
                    
                    doc_file = DocumentFile.objects.create(
                        document=self.object,
                        local_tmp_path=local_path,
                        upload_status='pending'
                    )
                    new_doc_file_ids.append(doc_file.pk)

            if new_doc_file_ids:
                process_uploads_background(new_doc_file_ids)
            
            # Process deletions manually to avoid formset saving new files synchronously
            # The .save(commit=False) call populates .deleted_objects
            files_formset.save(commit=False)
            for obj in files_formset.deleted_objects:
                obj.delete()

            messages.success(self.request, 'Document updated. New files are being uploaded in the background.')
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

    def get_success_url(self):
        messages.success(self.request, 'Document deleted successfully!')
        if self.object.vehicle:
            return reverse_lazy('vehicle-detail', kwargs={'pk': self.object.vehicle.pk})
        elif self.object.driver:
            return reverse_lazy('driver-detail', kwargs={'pk': self.object.driver.pk})
        return reverse_lazy('home')

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from .models import DocumentFile

from django.http import JsonResponse

@login_required
def get_upload_status(request):
    """
    Returns counts of active and recently completed background uploads.
    """
    # Active = Pending or Uploading
    active_count = DocumentFile.objects.filter(upload_status__in=['pending', 'uploading']).count()
    
    # Recent completed (last 10 minutes)
    recent_time = timezone.now() - timedelta(minutes=10)
    completed_count = DocumentFile.objects.filter(upload_status='completed', created_at__gte=recent_time).count()
    
    # Any failed uploads recently (last 10 minutes)
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
