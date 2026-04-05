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
from django.http import JsonResponse, HttpResponse

from .models import Vehicle, MaintenanceLog, MaintenanceTask, Tyre, TyreLog
from .forms import VehicleForm, MaintenanceLogForm, MaintenanceTaskForm, TyreForm, TyreLogForm


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


class MaintenanceTaskListView(LoginRequiredMixin, BaseFleetPermissionMixin, ListView):
    """
    List view for maintenance tasks
    """
    model = MaintenanceTask
    template_name = 'fleet/maintenance_task_list.html'
    context_object_name = 'tasks'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = MaintenanceTask.objects.all().select_related('vehicle')
        
        # Vehicle filter
        vehicle_id = self.request.GET.get('vehicle')
        if vehicle_id:
            queryset = queryset.filter(vehicle_id=vehicle_id)
        
        # Search functionality
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(vehicle__registration_plate__icontains=search)
            )
        
        return queryset.order_by('vehicle__registration_plate', 'name')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # For grouped display like documents
        context['vehicles'] = Vehicle.objects.all().prefetch_related('maintenance_tasks')
        context['search_term'] = self.request.GET.get('search', '')
        return context


class MaintenanceTaskCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for maintenance tasks
    """
    model = MaintenanceTask
    form_class = MaintenanceTaskForm
    template_name = 'fleet/maintenance_task_form.html'
    permission_required = 'fleet.add_maintenancetask'
    
    def form_valid(self, form):
        messages.success(self.request, 'Maintenance task created successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('maintenance-task-list')


class MaintenanceTaskUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for maintenance tasks
    """
    model = MaintenanceTask
    form_class = MaintenanceTaskForm
    template_name = 'fleet/maintenance_task_form.html'
    permission_required = 'fleet.change_maintenancetask'
    
    def form_valid(self, form):
        messages.success(self.request, 'Maintenance task updated successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('maintenance-task-list')


class MaintenanceTaskDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for maintenance tasks
    """
    model = MaintenanceTask
    template_name = 'fleet/maintenance_task_confirm_delete.html'
    permission_required = 'fleet.delete_maintenancetask'
    success_url = reverse_lazy('maintenance-task-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Maintenance task deleted successfully!')
        return super().delete(request, *args, **kwargs)


class TyreListView(LoginRequiredMixin, ListView):
    model = Tyre
    template_name = 'fleet/tyre_list.html'
    context_object_name = 'tyres'
    paginate_by = 20

    def get_queryset(self):
        queryset = Tyre.objects.all().select_related('current_vehicle')
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(serial_number__icontains=search) |
                Q(brand__icontains=search) |
                Q(size__icontains=search)
            )
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        return queryset.order_by('brand', 'serial_number')


class TyreDetailView(LoginRequiredMixin, DetailView):
    model = Tyre
    template_name = 'fleet/tyre_detail.html'
    context_object_name = 'tyre'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['logs'] = self.object.logs.all().order_by('-date', '-id')
        return context


class TyreCreateView(LoginRequiredMixin, CreateView):
    model = Tyre
    form_class = TyreForm
    template_name = 'fleet/tyre_form.html'
    success_url = reverse_lazy('tyre-list')

    def form_valid(self, form):
        messages.success(self.request, 'Tyre added to inventory.')
        return super().form_valid(form)


class TyreUpdateView(LoginRequiredMixin, UpdateView):
    model = Tyre
    form_class = TyreForm
    template_name = 'fleet/tyre_form.html'
    
    def get_success_url(self):
        return reverse_lazy('tyre-detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Tyre updated.')
        return super().form_valid(form)


class TyreLogCreateView(LoginRequiredMixin, CreateView):
    model = TyreLog
    form_class = TyreLogForm
    template_name = 'fleet/tyre_log_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        action = self.request.GET.get('action')
        if action:
            context['action_title'] = f"{action} Tyre"
        else:
            context['action_title'] = "Log Tyre Action"
        return context

    def get_initial(self):
        initial = super().get_initial()
        tyre_id = self.request.GET.get('tyre')
        action = self.request.GET.get('action')
        if tyre_id:
            tyre = get_object_or_404(Tyre, pk=tyre_id)
            initial['tyre'] = tyre
            if action == 'Dismount' and tyre.current_vehicle:
                initial['vehicle'] = tyre.current_vehicle
                initial['position'] = tyre.current_position
        if action:
            initial['action'] = action
        return initial

    def form_valid(self, form):
        tyre = form.cleaned_data['tyre']
        action = form.cleaned_data['action']
        
        # We'll just let Tyre.save handle the auto-logging for Mount/Dismount/Rotation
        # unless we want to keep the specific notes from the form.
        # Actually, let's keep the notes but avoid double logs.
        
        # Synchronize Tyre model status
        if action == TyreLog.ACTION_MOUNT:
            tyre.current_vehicle = form.instance.vehicle
            tyre.current_position = form.instance.position
            tyre.status = Tyre.STATUS_MOUNTED
        elif action == TyreLog.ACTION_DISMOUNT:
            tyre.current_vehicle = None
            tyre.current_position = ''
            tyre.status = Tyre.STATUS_IN_STOCK
        elif action == TyreLog.ACTION_REPAIR or action == TyreLog.ACTION_REMOLD:
            tyre.current_vehicle = None
            tyre.current_position = ''
            tyre.status = Tyre.STATUS_REPAIR
        elif action == TyreLog.ACTION_SCRAP:
            tyre.current_vehicle = None
            tyre.current_position = ''
            tyre.status = Tyre.STATUS_SCRAP
        elif action == TyreLog.ACTION_ROTATION:
            tyre.current_position = form.instance.position

        # Using _skip_auto_log to avoid double logs, 
        # then manually saving the TyreLog from the form 
        # which has user notes/date/etc.
        
        # Calculate ODO and Distance for the form's log
        form.instance.tyre_odo = tyre.total_km
        if action in [TyreLog.ACTION_DISMOUNT, TyreLog.ACTION_ROTATION]:
            v = tyre.current_vehicle or form.instance.vehicle
            last_mount = tyre.logs.filter(action=TyreLog.ACTION_MOUNT, vehicle=v).first()
            if last_mount:
                form.instance.distance_covered = tyre.total_km - last_mount.tyre_odo
            else:
                form.instance.distance_covered = 0
        else:
            form.instance.distance_covered = 0

        tyre._skip_auto_log = True
        tyre.save()
        
        messages.success(self.request, f'Tyre action {action} processed.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('tyre-detail', kwargs={'pk': self.object.tyre.pk})


@login_required
def tyre_quick_action(request, pk, action):
    """
    Handles simple status changes without a form.
    """
    tyre = get_object_or_404(Tyre, pk=pk)
    
    if action == 'Dismount':
        tyre.current_vehicle = None
        tyre.current_position = ''
        tyre.status = Tyre.STATUS_IN_STOCK
    elif action == 'Repair':
        tyre.current_vehicle = None
        tyre.current_position = ''
        tyre.status = Tyre.STATUS_REPAIR
    elif action == 'Scrap':
        tyre.current_vehicle = None
        tyre.current_position = ''
        tyre.status = Tyre.STATUS_SCRAP
    elif action == 'Stock':
        tyre.status = Tyre.STATUS_IN_STOCK
        
    tyre.save()
    messages.success(request, f'Tyre action {action} processed successfully.')
    
    # Try to redirect back to referer (e.g., Vehicle Detail) if available
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect('tyre-detail', pk=pk)


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

    def get_initial(self):
        initial = super().get_initial()
        vehicle_id = self.request.GET.get('vehicle')
        task_id = self.request.GET.get('task')
        if vehicle_id:
            initial['vehicle'] = vehicle_id
        if task_id:
            initial['task'] = task_id
        return initial
    
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


@login_required
def tyre_photo_serve(request, pk):
    """
    Serves the tyre photo directly from storage with browser caching.
    Ensures fast loading for repeated views on the same device.
    """
    tyre = get_object_or_404(Tyre, pk=pk)
    if not tyre.photo:
        return HttpResponse(status=404)

    try:
        # Fetch from Google Drive (the slow network part)
        with tyre.photo.open('rb') as f:
            photo_data = f.read()
    except Exception as e:
        return HttpResponse(f"Error accessing storage: {str(e)}", status=500)

    # Determine content type (simple detection)
    content_type = "image/jpeg"
    # Basic extension check based on file name if available
    if tyre.photo.name.lower().endswith('.png'):
        content_type = "image/png"
    elif tyre.photo.name.lower().endswith('.gif'):
        content_type = "image/gif"

    response = HttpResponse(photo_data, content_type=content_type)

    # Browser caching (1 day) - This is the "User Mobile Cache"
    # The phone will remember the image and won't ask the server again for 24h.
    response['Cache-Control'] = 'public, max-age=86400'
    return response