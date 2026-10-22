"""
Vehicle CRUD and management views for Fleet app.
"""
from decimal import Decimal
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Count, Sum
from django.core.paginator import Paginator

from ledger.models import Party, CompanyAccount
from fleet.models import Vehicle
from fleet.forms import VehicleForm
from .mixins import BaseFleetPermissionMixin


class VehicleListView(LoginRequiredMixin, PermissionRequiredMixin, BaseFleetPermissionMixin, ListView):
    """
    List view for vehicles with permission-based filtering
    """
    model = Vehicle
    template_name = 'fleet/vehicle_list.html'
    context_object_name = 'vehicles'
    permission_required = 'fleet.view_vehicle'
    paginate_by = 15
    
    def get_queryset(self):
        """Filter vehicles based on user permissions"""
        queryset = Vehicle.objects.all().select_related('vendor', 'company_account', 'created_by')
        
        # Drivers can only view active vehicles
        if self.has_driver_permission():
            queryset = queryset.filter(status=Vehicle.STATUS_ACTIVE)
        
        # Search functionality
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(registration_plate__icontains=search) |
                Q(make_model__icontains=search) |
                Q(chassis_number__icontains=search) |
                Q(engine_number__icontains=search)
            )
        
        # Ownership filter (default: Company Owned)
        self.ownership = self.request.GET.get('ownership', 'owned').strip().lower()
        if not self.ownership:
            self.ownership = 'owned'
            
        if self.ownership == 'owned':
            queryset = queryset.filter(ownership=Vehicle.OWNERSHIP_OWNED)
        elif self.ownership == 'attached':
            queryset = queryset.filter(ownership=Vehicle.OWNERSHIP_ATTACHED)
        # if 'all', do not filter by ownership

        # Status filter
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        # Add maintenance log count
        queryset = queryset.annotate(
            maintenance_count=Count('maintenance_records')
        )
        
        return queryset.order_by('registration_plate')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Vehicle.STATUS_CHOICES
        context['current_status'] = self.request.GET.get('status', '')
        context['search_term'] = self.request.GET.get('search', '')
        context['current_ownership'] = self.ownership
        context['owned_count'] = Vehicle.objects.filter(ownership=Vehicle.OWNERSHIP_OWNED).count()
        context['attached_count'] = Vehicle.objects.filter(ownership=Vehicle.OWNERSHIP_ATTACHED).count()
        context['total_count'] = Vehicle.objects.count()
        context['vendors'] = Party.objects.filter(party_type=Party.TYPE_CREDITOR).order_by('name')
        context['company_accounts'] = CompanyAccount.objects.all().order_by('name')
        return context


class VehicleDetailView(LoginRequiredMixin, PermissionRequiredMixin, BaseFleetPermissionMixin, DetailView):
    """
    Detail view for a single vehicle
    """
    model = Vehicle
    template_name = 'fleet/vehicle_detail.html'
    context_object_name = 'vehicle'
    permission_required = 'fleet.view_vehicle'

    def get_queryset(self):
        return super().get_queryset().select_related('vendor', 'company_account', 'created_by')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get recent maintenance records
        context['maintenance_records'] = self.object.maintenance_records.order_by('is_completed', 'expiry_date', '-completion_date')[:10]
        
        # All trips for this vehicle: sorted latest to oldest, paginated
        trips_qs = self.object.trips.select_related(
            'party', 'route', 'driver__user'
        ).with_billing_info().order_by('-date', '-created_at')
        
        total_trips_count = trips_qs.count()
        paginator = Paginator(trips_qs, 15)
        page_num = self.request.GET.get('page') or 1
        trips_page_obj = paginator.get_page(page_num)

        # Pre-compute toll expense for trips on this page
        from trips.services.toll_service import calculate_trips_toll_map
        page_tolls = calculate_trips_toll_map(trips_page_obj.object_list)
        for t in trips_page_obj.object_list:
            t.annotated_toll_expense = page_tolls.get(t.id, Decimal('0.00'))

        context['trips_page_obj'] = trips_page_obj
        context['recent_trips'] = trips_page_obj  # Backward compatibility
        context['total_trips_count'] = total_trips_count
        
        # Calculate maintenance cost
        total_maint_cost = self.object.total_maintenance_cost or Decimal('0.00')
        context['total_maintenance_cost'] = total_maint_cost
        context['maintenance_records_count'] = self.object.maintenance_records.filter(is_completed=True).count()
        
        # 18-Wheel + 1 Stepney Tyre Layout
        context['tyre_layout'] = self.object.tyre_layout

        # Toll Expenses Till Date
        toll_entries = self.object.toll_entries.select_related('toll_account').order_by('-date', '-time', '-created_at')
        context['recent_toll_entries'] = toll_entries[:8]
        toll_stats = self.object.toll_entries.aggregate(total=Sum('amount'), count=Count('id'))
        total_toll_cost = toll_stats['total'] or Decimal('0.00')
        context['total_toll_cost'] = total_toll_cost
        context['toll_entries_count'] = toll_stats['count'] or 0

        # Document / Compliance Expenses Till Date
        total_doc_cost = Decimal(str(self.object.total_compliance_expenses or '0.00'))
        context['total_doc_cost'] = total_doc_cost
        context['doc_count'] = self.object.documents.count()

        # Total Tracked Vehicle Expenses Till Date (Documents + Tolls)
        context['total_vehicle_expenses'] = Decimal(str(total_toll_cost)) + total_doc_cost

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
        form.instance.created_by = self.request.user
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


@login_required
@permission_required('fleet.change_vehicle')
def vehicle_bulk_update(request):
    """
    Bulk update ownership and vendor for multiple vehicles.
    """
    if request.method == 'POST':
        vehicle_ids = list(set(request.POST.getlist('vehicle_ids')))
        ownership = request.POST.get('ownership')
        vendor_id = request.POST.get('vendor')

        if not vehicle_ids:
            messages.error(request, 'No vehicles selected for update.')
            return redirect('vehicle-list')

        if ownership not in [Vehicle.OWNERSHIP_OWNED, Vehicle.OWNERSHIP_ATTACHED]:
            messages.error(request, 'Invalid ownership type selected.')
            return redirect('vehicle-list')

        vendor = None
        company_account = None
        if ownership == Vehicle.OWNERSHIP_ATTACHED:
            if not vendor_id:
                messages.error(request, 'Vendor must be selected for attached vehicles.')
                return redirect('vehicle-list')
            vendor = get_object_or_404(Party, pk=vendor_id)
        elif ownership == Vehicle.OWNERSHIP_OWNED:
            company_account_id = request.POST.get('company_account')
            if company_account_id:
                company_account = get_object_or_404(CompanyAccount, pk=company_account_id)

        # Update the selected vehicles using save() to keep financial sync consistent
        for vehicle in Vehicle.objects.filter(id__in=vehicle_ids):
            vehicle.ownership = ownership
            vehicle.vendor = vendor if ownership == Vehicle.OWNERSHIP_ATTACHED else None
            vehicle.company_account = company_account if ownership == Vehicle.OWNERSHIP_OWNED else None
            vehicle.save()
        
        messages.success(request, f'Successfully updated {len(vehicle_ids)} vehicle(s).')
    
    return redirect('vehicle-list')


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
