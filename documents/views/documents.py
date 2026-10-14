"""
Compliance Document CRUD views for Documents app.
"""
from datetime import timedelta
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Count
from django.utils import timezone

from documents.models import Document
from documents.forms import DocumentForm, DocumentFileFormSet
from fleet.models import Vehicle
from drivers.models import Driver


class DocumentListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    template_name = 'documents/document_list.html'
    paginate_by = 10
    permission_required = 'documents.view_document'

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
        if not self.has_permission():
            return self.handle_no_permission()
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
