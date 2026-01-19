"""
Views for Trips application with permission checks
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, FormView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db import models
from django.db.models import Q, Min, Sum
from django.utils import timezone
from datetime import datetime, timedelta

from .models import Trip, TripExpense
from .forms import TripForm, TripStatusForm, TripExpenseUpdateForm, TripCustomExpenseForm
from fleet.models import Vehicle
from ledger.models import FinancialRecord, TransactionCategory


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
    List view for trips, organized by Date (Page).
    One date per page.
    """
    model = Trip
    template_name = 'trips/trip_list.html'
    context_object_name = 'trips'
    paginate_by = None # Disable standard pagination as we use date pagination
    
    def get_queryset(self):
        """Filter trips based on date and user permissions"""
        queryset = self.get_queryset_for_user()
        
        # Date filtering
        date_str = self.request.GET.get('date')
        if date_str:
            try:
                self.view_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                self.view_date = timezone.now().date()
        else:
            self.view_date = timezone.now().date()

        # Filter by the specific date
        queryset = queryset.filter(date__date=self.view_date)
        
        # Search functionality
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(trip_number__icontains=search) |
                Q(party__name__icontains=search) |
                Q(pickup_location__icontains=search) |
                Q(delivery_location__icontains=search) |
                Q(vehicle__registration_plate__icontains=search)
            ).distinct()
        
        # Status filter
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
            
        return queryset.order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Trip.STATUS_CHOICES
        context['current_status'] = self.request.GET.get('status', '')
        context['search_term'] = self.request.GET.get('search', '')
        
        # Date Navigation
        context['view_date'] = self.view_date
        context['previous_date'] = self.view_date - timedelta(days=1)
        context['next_date'] = self.view_date + timedelta(days=1)
        context['today'] = timezone.now().date()
        
        # Summary for the day
        trips = context['trips']
        context['total_weight'] = trips.aggregate(Sum('weight'))['weight__sum'] or 0
        
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
    Create view for new trips.
    Inherits date from the context (GET param).
    """
    model = Trip
    form_class = TripForm
    template_name = 'trips/trip_form.html'
    permission_required = 'trips.add_trip'
    
    def get_initial(self):
        initial = super().get_initial()
        # You could prepopulate date here if the form had a date field, 
        # but we are handling it in form_valid
        return initial

    def form_valid(self, form):
        """Set created_by and date fields"""
        form.instance.created_by = self.request.user
        
        # Set date from GET param if available, else today
        date_str = self.request.GET.get('date')
        if date_str:
            try:
                trip_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                # Set time to current time for ordering, but date is fixed
                current_time = timezone.now().time()
                form.instance.date = datetime.combine(trip_date, current_time)
            except ValueError:
                form.instance.date = timezone.now()
        else:
            form.instance.date = timezone.now()

        messages.success(self.request, 'Trip created successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        # Redirect back to the trip list for the same date
        return reverse_lazy('trip-list') + f"?date={self.object.date.strftime('%Y-%m-%d')}"


class TripUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for existing trips
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
    """
    model = Trip
    template_name = 'trips/trip_confirm_delete.html'
    permission_required = 'trips.delete_trip'
    
    def get_success_url(self):
        return reverse_lazy('trip-list') + f"?date={self.object.date.strftime('%Y-%m-%d')}"
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Trip deleted successfully!')
        return super().delete(request, *args, **kwargs)


class TripExpenseUpdateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    """
    View to update fixed trip expenses (diesel, toll)
    """
    template_name = 'trips/trip_expense_form.html'
    form_class = TripExpenseUpdateForm
    permission_required = 'trips.change_trip'
    
    def get_initial(self):
        trip = get_object_or_404(Trip, pk=self.kwargs['pk'])
        initial = {}
        diesel = TripExpense.objects.filter(trip=trip, name='Diesel').first()
        if diesel:
            initial['diesel_expense'] = diesel.amount
        toll = TripExpense.objects.filter(trip=trip, name='Toll').first()
        if toll:
            initial['toll_expense'] = toll.amount
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['trip'] = get_object_or_404(Trip, pk=self.kwargs['pk'])
        return context

    def form_valid(self, form):
        trip = get_object_or_404(Trip, pk=self.kwargs['pk'])
        diesel_amount = form.cleaned_data.get('diesel_expense') or 0
        toll_amount = form.cleaned_data.get('toll_expense') or 0

        # Update or create Diesel
        TripExpense.objects.update_or_create(
            trip=trip,
            name='Diesel',
            defaults={'amount': diesel_amount}
        )

        # Update or create Toll
        TripExpense.objects.update_or_create(
            trip=trip,
            name='Toll',
            defaults={'amount': toll_amount}
        )

        messages.success(self.request, 'Trip expenses updated successfully!')
        return redirect('trip-detail', pk=trip.pk)


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
    
    # Active trips
    active_trips = Trip.objects.filter(
        status=Trip.STATUS_IN_PROGRESS
    ).count()
    
    # Completed trips this month
    current_month = timezone.now().month
    current_year = timezone.now().year
    
    completed_this_month = Trip.objects.filter(
        status=Trip.STATUS_COMPLETED,
        actual_completion_datetime__month=current_month,
        actual_completion_datetime__year=current_year
    ).count()
    
    # Vehicles due for maintenance (next service due within 7 days)
    seven_days_later = timezone.now().date() + timedelta(days=7)
    
    vehicles_due_maintenance = Vehicle.objects.filter(
        maintenance_logs__next_service_due__lte=seven_days_later
    ).distinct().count()
    
    # Recent financial summary
    income_this_month = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_INCOME,
        date__month=current_month,
        date__year=current_year
    ).aggregate(total=models.Sum('amount'))['total'] or 0
    
    expenses_this_month = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_EXPENSE,
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



from django.http import JsonResponse

@login_required
def get_autocomplete_suggestions(request):
    """
    Returns unique previous values for locations and expense names
    """
    field = request.GET.get('field')
    q = request.GET.get('q', '')
    
    if field == 'pickup_location':
        results = Trip.objects.filter(pickup_location__icontains=q).values_list('pickup_location', flat=True).distinct()[:10]
    elif field == 'delivery_location':
        results = Trip.objects.filter(delivery_location__icontains=q).values_list('delivery_location', flat=True).distinct()[:10]
    elif field == 'expense_name':
        results = TripExpense.objects.filter(name__icontains=q).values_list('name', flat=True).distinct()[:10]
    else:
        results = []
        
    return JsonResponse({'results': list(results)})
