"""
Views for Trips application with permission checks
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db import models
from django.db.models import Q, Min
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Trip, TripLeg, TripExpense
from .forms import TripForm, TripStatusForm, TripLegForm, TripExpenseUpdateForm, TripCustomExpenseForm


class BaseTripPermissionMixin:
    """Base mixin for trip permissions"""
    
    def has_manager_permission(self):
        """Check if user is in manager group"""
        return self.request.user.groups.filter(name='manager').exists()
    
    def has_supervisor_permission(self):
        """Check if user is in supervisor group"""
        return self.request.user.groups.filter(name='supervisor').exists()
    
    def has_driver_permission(self):
        """Check if user is in driver group"""
        return self.request.user.groups.filter(name='driver').exists()
    
    def get_queryset_for_user(self):
        """Filter trips based on user permissions"""
        user = self.request.user
        
        # Admin can see all trips
        if user.is_superuser:
            return Trip.objects.all()
        
        # Manager and supervisor can see all trips
        if self.has_manager_permission() or self.has_supervisor_permission():
            return Trip.objects.all()
        
        # Driver can only see their own trips
        if self.has_driver_permission():
            return Trip.objects.filter(driver=user)
        
        # Default: no trips
        return Trip.objects.none()


class TripListView(LoginRequiredMixin, BaseTripPermissionMixin, ListView):
    """
    List view for trips with permission-based filtering
    """
    model = Trip
    template_name = 'trips/trip_list.html'
    context_object_name = 'trips'
    paginate_by = 20
    
    def get_queryset(self):
        """Filter trips based on user permissions"""
        queryset = self.get_queryset_for_user()
        
        # Search functionality
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(trip_number__icontains=search) |
                Q(legs__party__name__icontains=search) |
                Q(legs__pickup_location__icontains=search) |
                Q(legs__delivery_location__icontains=search)
            ).distinct()
        
        # Status filter
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        # Annotate with effective date for grouping
        # Use the first leg's date, fallback to created_at
        queryset = queryset.annotate(
            effective_date=Coalesce(Min('legs__date'), 'created_at')
        )
        
        return queryset.order_by('-effective_date', '-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Trip.STATUS_CHOICES
        context['current_status'] = self.request.GET.get('status', '')
        context['search_term'] = self.request.GET.get('search', '')
        return context


class TripDetailView(LoginRequiredMixin, BaseTripPermissionMixin, DetailView):
    """
    Detail view for a single trip
    """
    model = Trip
    template_name = 'trips/trip_detail.html'
    context_object_name = 'trip'
    
    def get_queryset(self):
        """Ensure user has permission to view this trip"""
        return self.get_queryset_for_user()


class TripCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for new trips
    Permission: Only admin and manager can create trips
    """
    model = Trip
    form_class = TripForm
    template_name = 'trips/trip_form.html'
    permission_required = 'trips.add_trip'
    
    def form_valid(self, form):
        """Set created_by field"""
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Trip created successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('trip-detail', kwargs={'pk': self.object.pk})


class TripUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for existing trips
    Permission: Admin and manager can update all fields
    Supervisor can only update status
    Driver cannot update trips (except status via separate view)
    """
    model = Trip
    form_class = TripForm
    template_name = 'trips/trip_form.html'
    permission_required = 'trips.change_trip'
    
    def form_valid(self, form):
        messages.success(self.request, 'Trip updated successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('trip-detail', kwargs={'pk': self.object.pk})


class TripDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for trips
    Permission: Only admin can delete trips
    """
    model = Trip
    template_name = 'trips/trip_confirm_delete.html'
    permission_required = 'trips.delete_trip'
    success_url = reverse_lazy('trip-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Trip deleted successfully!')
        return super().delete(request, *args, **kwargs)


class TripLegCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    View to add a leg to a trip
    """
    model = TripLeg
    form_class = TripLegForm
    template_name = 'trips/trip_leg_form.html'
    permission_required = 'trips.change_trip'

    def dispatch(self, request, *args, **kwargs):
        self.trip = get_object_or_404(Trip, pk=kwargs['trip_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.trip = self.trip
        messages.success(self.request, 'Trip Leg added successfully!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('trip-detail', kwargs={'pk': self.trip.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['trip'] = self.trip
        return context


class TripLegUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for trip legs
    """
    model = TripLeg
    form_class = TripLegForm
    template_name = 'trips/trip_leg_form.html'
    permission_required = 'trips.change_trip'

    def get_success_url(self):
        return reverse_lazy('trip-detail', kwargs={'pk': self.object.trip.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['trip'] = self.object.trip
        return context


class TripLegDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for trip legs
    """
    model = TripLeg
    template_name = 'trips/trip_leg_confirm_delete.html'
    permission_required = 'trips.change_trip'

    def get_success_url(self):
        return reverse_lazy('trip-detail', kwargs={'pk': self.object.trip.pk})


class TripExpenseUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    View to update fixed trip expenses (diesel, toll)
    """
    model = Trip
    form_class = TripExpenseUpdateForm
    template_name = 'trips/trip_expense_form.html'
    permission_required = 'trips.change_trip'
    
    def get_success_url(self):
        messages.success(self.request, 'Trip expenses updated successfully!')
        return reverse_lazy('trip-detail', kwargs={'pk': self.object.pk})


class TripCustomExpenseCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    View to add a custom expense to a trip
    """
    model = TripExpense
    form_class = TripCustomExpenseForm
    template_name = 'trips/trip_custom_expense_form.html'
    permission_required = 'trips.change_trip'
    
    def dispatch(self, request, *args, **kwargs):
        self.trip = get_object_or_404(Trip, pk=kwargs['trip_pk'])
        return super().dispatch(request, *args, **kwargs)
    
    def form_valid(self, form):
        form.instance.trip = self.trip
        messages.success(self.request, 'Expense added successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('trip-detail', kwargs={'pk': self.trip.pk})
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['trip'] = self.trip
        return context


class TripCustomExpenseDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for custom trip expenses
    """
    model = TripExpense
    template_name = 'trips/trip_custom_expense_confirm_delete.html'
    permission_required = 'trips.change_trip'
    
    def get_success_url(self):
        messages.success(self.request, 'Expense deleted successfully!')
        return reverse_lazy('trip-detail', kwargs={'pk': self.object.trip.pk})


@login_required
def update_trip_status(request, pk):
    """
    Dedicated view for updating trip status
    - Drivers can only update their own trips
    - Supervisors can update any trip status
    - Uses separate form for status-only updates
    """
    trip = get_object_or_404(Trip, pk=pk)
    
    # Permission checks
    is_driver = request.user.groups.filter(name='driver').exists()
    is_supervisor = request.user.groups.filter(name='supervisor').exists()
    is_manager = request.user.groups.filter(name='manager').exists()
    is_admin = request.user.is_superuser
    
    # Check if user can update this trip's status
    can_update = False
    
    if is_admin or is_manager or is_supervisor:
        can_update = True
    elif is_driver and trip.driver == request.user:
        can_update = True
    
    if not can_update:
        messages.error(request, 'You do not have permission to update this trip status.')
        return redirect('trip-detail', pk=pk)
    
    if request.method == 'POST':
        form = TripStatusForm(request.POST, instance=trip)
        if form.is_valid():
            old_status = trip.status
            form.save()
            
            # Log the status change
            messages.success(
                request, 
                f'Trip status updated from "{old_status}" to "{trip.status}" successfully!'
            )
            return redirect('trip-detail', pk=pk)
    else:
        form = TripStatusForm(instance=trip)
    
    return render(request, 'trips/trip_status_form.html', {
        'form': form,
        'trip': trip
    })





@login_required
def manager_dashboard(request):
    """
    Manager dashboard - shows system overview
    """
    # Check if user is manager or admin
    if not (request.user.is_superuser or 
            request.user.groups.filter(name='manager').exists()):
        messages.error(request, 'Access denied. Manager dashboard is only for managers.')
        return redirect('trip-list')
    
    from fleet.models import Vehicle, MaintenanceLog
    from ledger.models import FinancialRecord
    
    # Active trips
    active_trips = Trip.objects.filter(
        status=Trip.STATUS_IN_PROGRESS
    ).count()
    
    # Completed trips this month
    from datetime import datetime
    current_month = timezone.now().month
    current_year = timezone.now().year
    
    completed_this_month = Trip.objects.filter(
        status=Trip.STATUS_COMPLETED,
        actual_completion_datetime__month=current_month,
        actual_completion_datetime__year=current_year
    ).count()
    
    # Vehicles due for maintenance (next service due within 7 days)
    from datetime import timedelta
    seven_days_later = timezone.now().date() + timedelta(days=7)
    
    vehicles_due_maintenance = Vehicle.objects.filter(
        maintenance_logs__next_service_due__lte=seven_days_later
    ).distinct().count()
    
    # Recent financial summary
    # Income this month
    income_this_month = FinancialRecord.objects.filter(
        category=FinancialRecord.CATEGORY_FREIGHT_INCOME,
        date__month=current_month,
        date__year=current_year
    ).aggregate(total=models.Sum('amount'))['total'] or 0
    
    # Expenses this month
    expenses_this_month = FinancialRecord.objects.filter(
        category__in=[
            FinancialRecord.CATEGORY_FUEL_EXPENSE,
            FinancialRecord.CATEGORY_MAINTENANCE_EXPENSE,
            FinancialRecord.CATEGORY_DRIVER_PAYMENT,
            FinancialRecord.CATEGORY_OTHER
        ],
        date__month=current_month,
        date__year=current_year
    ).aggregate(total=models.Sum('amount'))['total'] or 0
    
    # Recent trips
    recent_trips = Trip.objects.order_by('-created_at')[:10]
    
    # Vehicles in maintenance
    vehicles_in_maintenance = Vehicle.objects.filter(
        status=Vehicle.STATUS_MAINTENANCE
    ).count()
    
    context = {
        'active_trips': active_trips,
        'completed_this_month': completed_this_month,
        'vehicles_due_maintenance': vehicles_due_maintenance,
        'income_this_month': income_this_month,
        'expenses_this_month': expenses_this_month,
        'net_profit': income_this_month - expenses_this_month,
        'recent_trips': recent_trips,
        'vehicles_in_maintenance': vehicles_in_maintenance,
    }
    
    return render(request, 'trips/manager_dashboard.html', context)