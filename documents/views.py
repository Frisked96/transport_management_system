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

from concurrent.futures import ThreadPoolExecutor

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
            
            # Save formset with parallel indexing/upload assignment
            files = files_formset.save(commit=False)
            base_index = 0
            
            def save_file(i, file_instance):
                file_instance.document = self.object
                file_instance._upload_index = base_index + i + 1
                file_instance.save()

            # Execute GDrive uploads in parallel
            with ThreadPoolExecutor(max_workers=5) as executor:
                executor.map(lambda x: save_file(*x), enumerate(files))
            
            for obj in files_formset.deleted_objects:
                obj.delete()

            messages.success(self.request, 'Document added successfully!')
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
            
            # Handle indexing for new files
            existing_count = self.object.files.count()
            
            # Save formset with parallel indexing/upload assignment
            new_files = files_formset.save(commit=False)
            
            def save_file(i, file_instance):
                if not file_instance.pk: # It's a new file
                    # Continue indexing from existing
                    file_instance._upload_index = existing_count + i + 1
                file_instance.document = self.object
                file_instance.save()

            # Execute GDrive uploads in parallel
            with ThreadPoolExecutor(max_workers=5) as executor:
                executor.map(lambda x: save_file(*x), enumerate(new_files))
            
            for obj in files_formset.deleted_objects:
                obj.delete()

            messages.success(self.request, 'Document updated successfully!')
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
