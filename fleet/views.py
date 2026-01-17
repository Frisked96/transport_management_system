"""
Views for Fleet application with permission checks
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Count, Sum

from .models import Vehicle, MaintenanceLog
from .forms import VehicleForm, MaintenanceLogForm


class BaseFleetPermissionMixin:
    """Base mixin for fleet permissions"""
    
    def has_manager_permission(self):
        """Check if user is in manager group"""
        return self.request.user.groups.filter(name='manager').exists()
    
    def has_supervisor_permission(self):
        """Check if user is in supervisor group"""
        return self.request.user.groups.filter(name='supervisor').exists()
    
    def has_driver_permission(self):
        """Check if user is in driver group"""
        return self.request.user.groups.filter(name='driver').exists()


class VehicleListView(LoginRequiredMixin, BaseFleetPermissionMixin, ListView):
    """
    List view for vehicles with permission-based filtering
    """
    model = Vehicle
    template_name = 'fleet/vehicle_list.html'
    context_object_name = 'vehicles'
    paginate_by = 15
    
    def get_queryset(self):
        """Filter vehicles based on user permissions"""
        queryset = Vehicle.objects.all()
        
        # Drivers can only view active vehicles
        if self.has_driver_permission():
            queryset = queryset.filter(status=Vehicle.STATUS_ACTIVE)
        
        # Search functionality
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(registration_plate__icontains=search) |
                Q(make_model__icontains=search)
            )
        
        # Status filter
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        # Add maintenance log count
        queryset = queryset.annotate(
            maintenance_count=Count('maintenance_logs')
        )
        
        return queryset.order_by('registration_plate')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Vehicle.STATUS_CHOICES
        context['current_status'] = self.request.GET.get('status', '')
        context['search_term'] = self.request.GET.get('search', '')
        return context


class VehicleDetailView(LoginRequiredMixin, BaseFleetPermissionMixin, DetailView):
    """
    Detail view for a single vehicle
    """
    model = Vehicle
    template_name = 'fleet/vehicle_detail.html'
    context_object_name = 'vehicle'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get recent maintenance logs
        context['maintenance_logs'] = self.object.maintenance_logs.order_by('-date')[:10]
        
        # Get recent trips
        context['recent_trips'] = self.object.trips.order_by('-created_at')[:10]
        
        # Calculate total maintenance cost
        context['total_maintenance_cost'] = self.object.maintenance_logs.aggregate(
            total=Sum('cost')
        )['total'] or 0
        
        return context


class VehicleCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for new vehicles
    Permission: Only admin and manager can create vehicles
    """
    model = Vehicle
    form_class = VehicleForm
    template_name = 'fleet/vehicle_form.html'
    permission_required = 'fleet.add_vehicle'
    
    def form_valid(self, form):
        messages.success(self.request, 'Vehicle created successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('vehicle-detail', kwargs={'pk': self.object.pk})


class VehicleUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for existing vehicles
    Permission: Admin and manager can update vehicles
    """
    model = Vehicle
    form_class = VehicleForm
    template_name = 'fleet/vehicle_form.html'
    permission_required = 'fleet.change_vehicle'
    
    def form_valid(self, form):
        messages.success(self.request, 'Vehicle updated successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('vehicle-detail', kwargs={'pk': self.object.pk})


class VehicleDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for vehicles
    Permission: Only admin can delete vehicles
    """
    model = Vehicle
    template_name = 'fleet/vehicle_confirm_delete.html'
    permission_required = 'fleet.delete_vehicle'
    success_url = reverse_lazy('vehicle-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Vehicle deleted successfully!')
        return super().delete(request, *args, **kwargs)


class MaintenanceLogListView(LoginRequiredMixin, BaseFleetPermissionMixin, ListView):
    """
    List view for maintenance logs
    """
    model = MaintenanceLog
    template_name = 'fleet/maintenance_log_list.html'
    context_object_name = 'maintenance_logs'
    paginate_by = 20
    
    def get_queryset(self):
        """Filter maintenance logs based on user permissions"""
        queryset = MaintenanceLog.objects.all()
        
        # Vehicle filter
        vehicle_id = self.request.GET.get('vehicle')
        if vehicle_id:
            queryset = queryset.filter(vehicle_id=vehicle_id)
        
        # Type filter
        log_type = self.request.GET.get('type')
        if log_type:
            queryset = queryset.filter(type=log_type)
        
        return queryset.order_by('-date')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['type_choices'] = MaintenanceLog.TYPE_CHOICES
        context['current_type'] = self.request.GET.get('type', '')
        context['vehicles'] = Vehicle.objects.all()
        context['selected_vehicle'] = self.request.GET.get('vehicle', '')
        return context


class MaintenanceLogCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for maintenance logs
    Permission: Admin, manager, and supervisor can create maintenance logs
    """
    model = MaintenanceLog
    form_class = MaintenanceLogForm
    template_name = 'fleet/maintenance_log_form.html'
    permission_required = 'fleet.create_maintenance_log'
    
    def form_valid(self, form):
        form.instance.logged_by = self.request.user
        messages.success(self.request, 'Maintenance log created successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('maintenance-log-list')


class MaintenanceLogUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for maintenance logs
    Permission: Only admin and manager can update maintenance logs
    """
    model = MaintenanceLog
    form_class = MaintenanceLogForm
    template_name = 'fleet/maintenance_log_form.html'
    permission_required = 'fleet.change_maintenancelog'
    
    def form_valid(self, form):
        messages.success(self.request, 'Maintenance log updated successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('maintenance-log-list')


class MaintenanceLogDetailView(LoginRequiredMixin, DetailView):
    """
    Detail view for a maintenance log
    """
    model = MaintenanceLog
    template_name = 'fleet/maintenance_log_detail.html'
    context_object_name = 'log'