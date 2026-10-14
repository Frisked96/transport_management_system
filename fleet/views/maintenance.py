"""
Maintenance views for Fleet app.
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.utils import timezone

from fleet.models import Vehicle, MaintenanceRecord
from fleet.forms import MaintenanceRecordForm, MaintenanceCompleteForm
from .mixins import BaseFleetPermissionMixin


class MaintenanceListView(LoginRequiredMixin, PermissionRequiredMixin, BaseFleetPermissionMixin, ListView):
    """
    Unified list view for maintenance records (both pending and completed)
    """
    model = MaintenanceRecord
    template_name = 'fleet/maintenance_list.html'
    context_object_name = 'maintenance_records'
    permission_required = 'fleet.view_maintenancerecord'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = MaintenanceRecord.objects.all().select_related('vehicle')
        
        # Vehicle filter
        vehicle_id = self.request.GET.get('vehicle')
        if vehicle_id:
            queryset = queryset.filter(vehicle_id=vehicle_id)
        
        # Status filter
        status = self.request.GET.get('status')
        if status == 'completed':
            queryset = queryset.filter(is_completed=True)
        elif status == 'pending':
            queryset = queryset.filter(is_completed=False)
            
        # Search
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(name__icontains=search)
            
        return queryset.order_by('is_completed', 'expiry_date', '-completion_date')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['vehicles'] = Vehicle.objects.all().order_by('registration_plate')
        context['selected_vehicle'] = self.request.GET.get('vehicle', '')
        context['current_status'] = self.request.GET.get('status', '')
        context['search_term'] = self.request.GET.get('search', '')
        return context


class MaintenanceRecordCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = MaintenanceRecord
    form_class = MaintenanceRecordForm
    template_name = 'fleet/maintenance_form.html'
    permission_required = 'fleet.add_maintenancerecord'

    def get_initial(self):
        initial = super().get_initial()
        vehicle_id = self.request.GET.get('vehicle')
        if vehicle_id:
            initial['vehicle'] = vehicle_id
        return initial
    
    def form_valid(self, form):
        form.instance.logged_by = self.request.user
        messages.success(self.request, 'Maintenance record created successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('maintenance-list')


class MaintenanceRecordUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = MaintenanceRecord
    form_class = MaintenanceRecordForm
    template_name = 'fleet/maintenance_form.html'
    permission_required = 'fleet.change_maintenancerecord'
    
    def form_valid(self, form):
        messages.success(self.request, 'Maintenance record updated successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('maintenance-list')


class MaintenanceRecordDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = MaintenanceRecord
    template_name = 'fleet/maintenance_detail.html'
    context_object_name = 'record'
    permission_required = 'fleet.view_maintenancerecord'


class MaintenanceRecordDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = MaintenanceRecord
    template_name = 'fleet/maintenance_confirm_delete.html'
    permission_required = 'fleet.delete_maintenancerecord'
    success_url = reverse_lazy('maintenance-list')


@login_required
@permission_required('fleet.change_maintenancerecord')
def maintenance_record_complete(request, pk):
    """
    View to mark a pending maintenance record as completed.
    """
    record = get_object_or_404(MaintenanceRecord, pk=pk)
    
    if record.is_completed:
        messages.warning(request, 'This record is already completed.')
        return redirect('maintenance-list')
        
    if request.method == 'POST':
        form = MaintenanceCompleteForm(request.POST)
        if form.is_valid():
            record.mark_as_completed(
                date=form.cleaned_data['completion_date'],
                km=form.cleaned_data['completion_km'],
                cost=form.cleaned_data['cost'],
                provider=form.cleaned_data['service_provider'],
                notes=form.cleaned_data['notes'],
                user=request.user
            )
            messages.success(request, f'Maintenance "{record.name}" marked as completed.')
            return redirect('maintenance-list')
    else:
        # Pre-fill with current odometer
        form = MaintenanceCompleteForm(initial={
            'completion_km': record.vehicle.current_odometer,
            'completion_date': timezone.now().date()
        })
        
    return render(request, 'fleet/maintenance_complete_form.html', {
        'form': form,
        'record': record
    })
