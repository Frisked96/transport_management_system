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

class DocumentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Standard tailwind classes for most inputs
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        
        for field_name, field in self.fields.items():
            if field_name == 'scanned_copy':
                # Special styling for file input
                field.widget.attrs.update({
                    'class': 'block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-emerald-50 file:text-emerald-700 hover:file:bg-emerald-100'
                })
            elif field_name == 'never_expires':
                field.widget.attrs.update({
                    'class': 'h-4 w-4 text-emerald-600 focus:ring-emerald-500 border-slate-300 rounded'
                })
            else:
                field.widget.attrs.update({'class': tailwind_classes})

    class Meta:
        model = Document
        fields = ['document_type', 'document_number', 'expiry_date', 'never_expires', 'scanned_copy', 'notes']
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

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
            # Should not happen given URL patterns, but safe fallback
            return redirect('home')

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        if self.vehicle_pk:
            form.instance.vehicle = self.parent_obj
        elif self.driver_pk:
            form.instance.driver = self.parent_obj

        messages.success(self.request, 'Document added successfully!')
        return super().form_valid(form)

    def get_success_url(self):
        if self.vehicle_pk:
            return reverse_lazy('vehicle-detail', kwargs={'pk': self.vehicle_pk})
        elif self.driver_pk:
            return reverse_lazy('driver-detail', kwargs={'pk': self.driver_pk})
        return reverse_lazy('home')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context[self.context_name] = self.parent_obj
        return context

class DocumentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Document
    form_class = DocumentForm
    template_name = 'documents/document_form.html'
    permission_required = 'documents.change_document'
    object: Document

    def get_success_url(self):
        messages.success(self.request, 'Document updated successfully!')
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

@login_required
def document_download_proxy(request, pk):
    """
    Proxy view to handle document URL generation on demand.
    This avoids slow page loads in the document list view where 
    generating Google Drive URLs for many files takes a long time.
    """
    document = get_object_or_404(Document, pk=pk)
    
    # Check if field has a file assigned
    if not document.scanned_copy or not document.scanned_copy.name:
        messages.error(request, "No scanned copy available for this document.")
        return redirect('document-list')
    
    try:
        # Fetch the URL from storage (this is the slow network call)
        url = document.scanned_copy.url
        
        # Debugging to terminal to see what GDrive returns
        print(f"--- Document {pk} Proxy ---")
        print(f"File Name: {document.scanned_copy.name}")
        print(f"Generated URL Type: {type(url)}")
        print(f"Generated URL Value: {url}")
        
        if url:
            # Use HttpResponseRedirect directly to avoid resolve_url magic
            return HttpResponseRedirect(str(url))
        else:
            messages.error(request, "Google Drive storage returned an empty URL.")
    except Exception as e:
        import traceback
        traceback.print_exc() # Print full error to console
        messages.error(request, f"Error accessing document storage: {str(e)}")
    
    return redirect('document-list')
