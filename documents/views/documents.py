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

    def get_paginate_by(self, queryset):
        alert_filter = self.request.GET.get('alert', '').strip().lower()
        if alert_filter in ['alerts', 'all_alerts', 'active', 'expired', 'expiring', 'expiring_soon']:
            return 25
        return self.paginate_by

    def get_queryset(self):
        self.doc_type = self.request.GET.get('type', 'vehicles')
        search_term = self.request.GET.get('search', '').strip()
        self.alert_filter = self.request.GET.get('alert', '').strip().lower()
        self.ownership_filter = self.request.GET.get('ownership', 'owned').strip().lower()
        if not self.ownership_filter:
            self.ownership_filter = 'owned'
        
        today = timezone.now().date()
        warning_date = today + timedelta(days=30)
        is_alert_mode = self.alert_filter in ['alerts', 'all_alerts', 'active', 'expired', 'expiring', 'expiring_soon']

        # When listing alerts, return the alerting Document instances directly
        if is_alert_mode:
            if self.doc_type == 'drivers':
                queryset = Document.objects.filter(
                    driver__isnull=False,
                    never_expires=False,
                    expiry_date__isnull=False
                )
                if self.alert_filter in ['alerts', 'all_alerts', 'active']:
                    queryset = queryset.filter(expiry_date__lte=warning_date)
                elif self.alert_filter == 'expired':
                    queryset = queryset.filter(expiry_date__lt=today)
                elif self.alert_filter in ['expiring', 'expiring_soon']:
                    queryset = queryset.filter(expiry_date__gte=today, expiry_date__lte=warning_date)

                if search_term:
                    queryset = queryset.filter(
                        Q(document_name__icontains=search_term) |
                        Q(document_number__icontains=search_term) |
                        Q(driver__user__first_name__icontains=search_term) |
                        Q(driver__user__last_name__icontains=search_term) |
                        Q(driver__user__username__icontains=search_term) |
                        Q(driver__employee_id__icontains=search_term) |
                        Q(driver__license_number__icontains=search_term)
                    )

                return queryset.select_related('driver', 'driver__user', 'added_by').prefetch_related('files', 'renewals').order_by('expiry_date', 'document_name')
            else:
                queryset = Document.objects.filter(
                    vehicle__isnull=False,
                    never_expires=False,
                    expiry_date__isnull=False
                )

                # Ownership filter (default: Company Owned)
                if self.ownership_filter == 'owned':
                    queryset = queryset.filter(vehicle__ownership=Vehicle.OWNERSHIP_OWNED)
                elif self.ownership_filter == 'attached':
                    queryset = queryset.filter(vehicle__ownership=Vehicle.OWNERSHIP_ATTACHED)

                if self.alert_filter in ['alerts', 'all_alerts', 'active']:
                    queryset = queryset.filter(expiry_date__lte=warning_date)
                elif self.alert_filter == 'expired':
                    queryset = queryset.filter(expiry_date__lt=today)
                elif self.alert_filter in ['expiring', 'expiring_soon']:
                    queryset = queryset.filter(expiry_date__gte=today, expiry_date__lte=warning_date)

                if search_term:
                    queryset = queryset.filter(
                        Q(document_name__icontains=search_term) |
                        Q(document_number__icontains=search_term) |
                        Q(vehicle__registration_plate__icontains=search_term) |
                        Q(vehicle__make_model__icontains=search_term)
                    )

                return queryset.select_related('vehicle', 'added_by').prefetch_related('files', 'renewals').order_by('expiry_date', 'document_name')

        # Normal mode (not alert filter): list vehicles or drivers
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
            
            # Ownership filter (default: Company Owned)
            if self.ownership_filter == 'owned':
                queryset = queryset.filter(ownership=Vehicle.OWNERSHIP_OWNED)
            elif self.ownership_filter == 'attached':
                queryset = queryset.filter(ownership=Vehicle.OWNERSHIP_ATTACHED)
            # if 'all', do not filter by ownership
            
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
        context['alert_filter'] = self.alert_filter
        context['ownership_filter'] = self.ownership_filter
        
        today = timezone.now().date()
        warning_date = today + timedelta(days=30)
        is_alert_mode = self.alert_filter in ['alerts', 'all_alerts', 'active', 'expired', 'expiring', 'expiring_soon']
        context['is_alert_mode'] = is_alert_mode

        if self.doc_type == 'drivers':
            context['summary_counts'] = Driver.objects.aggregate(
                total=Count('id', distinct=True),
                expired=Count('id', filter=Q(documents__never_expires=False, documents__expiry_date__lt=today), distinct=True),
                expiring=Count('id', filter=Q(documents__never_expires=False, documents__expiry_date__lte=warning_date, documents__expiry_date__gte=today), distinct=True),
                alerts=Count('id', filter=Q(documents__never_expires=False, documents__expiry_date__lte=warning_date), distinct=True),
            )
            context['alert_doc_counts'] = Document.objects.filter(
                driver__isnull=False, never_expires=False, expiry_date__isnull=False
            ).aggregate(
                total=Count('id'),
                expired=Count('id', filter=Q(expiry_date__lt=today)),
                expiring=Count('id', filter=Q(expiry_date__lte=warning_date, expiry_date__gte=today)),
                alerts=Count('id', filter=Q(expiry_date__lte=warning_date)),
            )

            if is_alert_mode:
                context['alerts'] = context['page_obj']
                context['documents'] = context['page_obj']
                driver_ids = [doc.driver_id for doc in context['page_obj'] if doc.driver_id]
                context['drivers'] = Driver.objects.filter(id__in=driver_ids)
                context['vehicles'] = []
            else:
                context['drivers'] = context['page_obj']
                context['vehicles'] = []
                context['alerts'] = []
                context['documents'] = []
        else:
            # Base queryset for summary counts reflecting current ownership filter
            base_veh = Vehicle.objects.all()
            if self.ownership_filter == 'owned':
                base_veh = base_veh.filter(ownership=Vehicle.OWNERSHIP_OWNED)
            elif self.ownership_filter == 'attached':
                base_veh = base_veh.filter(ownership=Vehicle.OWNERSHIP_ATTACHED)

            context['summary_counts'] = base_veh.aggregate(
                total=Count('id', distinct=True),
                expired=Count('id', filter=Q(documents__never_expires=False, documents__expiry_date__lt=today), distinct=True),
                expiring=Count('id', filter=Q(documents__never_expires=False, documents__expiry_date__lte=warning_date, documents__expiry_date__gte=today), distinct=True),
                alerts=Count('id', filter=Q(documents__never_expires=False, documents__expiry_date__lte=warning_date), distinct=True),
            )

            base_doc_qs = Document.objects.filter(vehicle__isnull=False, never_expires=False, expiry_date__isnull=False)
            if self.ownership_filter == 'owned':
                base_doc_qs = base_doc_qs.filter(vehicle__ownership=Vehicle.OWNERSHIP_OWNED)
            elif self.ownership_filter == 'attached':
                base_doc_qs = base_doc_qs.filter(vehicle__ownership=Vehicle.OWNERSHIP_ATTACHED)

            context['alert_doc_counts'] = base_doc_qs.aggregate(
                total=Count('id'),
                expired=Count('id', filter=Q(expiry_date__lt=today)),
                expiring=Count('id', filter=Q(expiry_date__lte=warning_date, expiry_date__gte=today)),
                alerts=Count('id', filter=Q(expiry_date__lte=warning_date)),
            )

            context['owned_vehicles_count'] = Vehicle.objects.filter(ownership=Vehicle.OWNERSHIP_OWNED).count()
            context['attached_vehicles_count'] = Vehicle.objects.filter(ownership=Vehicle.OWNERSHIP_ATTACHED).count()
            context['total_vehicles_count'] = Vehicle.objects.count()

            if is_alert_mode:
                context['alerts'] = context['page_obj']
                context['documents'] = context['page_obj']
                vehicle_ids = [doc.vehicle_id for doc in context['page_obj'] if doc.vehicle_id]
                context['vehicles'] = Vehicle.objects.filter(id__in=vehicle_ids)
                context['drivers'] = []
            else:
                context['vehicles'] = context['page_obj']
                context['drivers'] = []
                context['alerts'] = []
                context['documents'] = []
            
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
