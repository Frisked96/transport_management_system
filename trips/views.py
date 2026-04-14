"""
Views for Trips application with permission checks
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Sum, F
from django.utils import timezone
from django.http import JsonResponse
from datetime import datetime, timedelta

from .models import Trip
from .forms import TripForm
from fleet.models import Vehicle, MaintenanceRecord, Tyre
from ledger.models import FinancialRecord, TransactionCategory, Bill


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
    List view for trips, showing all trips in a list with filters and sorting.
    """
    model = Trip
    template_name = 'trips/trip_list.html'
    context_object_name = 'trips'
    paginate_by = 25
    
    def get_queryset(self):
        """Filter and sort trips based on user input and permissions"""
        queryset = self.get_queryset_for_user().with_payment_info().with_billing_info().select_related('vehicle', 'party', 'driver')
        
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
        
        # Status filter (payment-based)
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(annotated_status=status)
            
        # Date range filtering
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        if start_date:
            try:
                queryset = queryset.filter(date__date__gte=start_date)
            except (ValueError, TypeError):
                pass
        if end_date:
            try:
                queryset = queryset.filter(date__date__lte=end_date)
            except (ValueError, TypeError):
                pass

        # Sorting
        sort = self.request.GET.get('sort', '-date')
        sort_mapping = {
            'date': 'date',
            '-date': '-date',
            'trip_number': 'trip_number',
            '-trip_number': '-trip_number',
            'weight': 'weight',
            '-weight': '-weight',
            'revenue': 'annotated_revenue',
            '-revenue': '-annotated_revenue',
        }
        
        if sort in sort_mapping:
            queryset = queryset.order_by(sort_mapping[sort], '-created_at')
        else:
            queryset = queryset.order_by('-date', '-created_at')
            
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Trip.PAYMENT_STATUS_CHOICES
        context['current_status'] = self.request.GET.get('status', '')
        context['search_term'] = self.request.GET.get('search', '')
        context['start_date'] = self.request.GET.get('start_date', '')
        context['end_date'] = self.request.GET.get('end_date', '')
        context['current_sort'] = self.request.GET.get('sort', '-date')
        
        # Summary for the filtered queryset
        queryset = self.get_queryset()
        context['total_weight'] = queryset.aggregate(Sum('weight'))['weight__sum'] or 0
        context['total_count'] = queryset.count()
        
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
        return self.get_queryset_for_user().with_payment_info()


class TripCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for new trips.
    """
    model = Trip
    form_class = TripForm
    template_name = 'trips/trip_form.html'
    permission_required = 'trips.add_trip'
    
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        
        # Set date from GET param if available
        date_str = self.request.GET.get('date')
        if date_str:
            try:
                trip_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                current_time = timezone.now().time()
                form.instance.date = datetime.combine(trip_date, current_time)
            except ValueError:
                form.instance.date = timezone.now()
        else:
            form.instance.date = timezone.now()

        response = super().form_valid(form)
        messages.success(self.request, 'Trip created successfully!')
        return response
    
    def get_success_url(self):
        return reverse_lazy('trip-list')


class TripUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for existing trips.
    """
    model = Trip
    form_class = TripForm
    template_name = 'trips/trip_form.html'
    permission_required = 'trips.change_trip'

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Trip updated successfully!')
        return response
    
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
        return reverse_lazy('trip-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Trip deleted successfully!')
        return super().delete(request, *args, **kwargs)


@login_required
def manager_dashboard(request):
    """
    Manager dashboard - shows system overview
    """
    if not (request.user.is_superuser or 
            request.user.groups.filter(name='manager').exists()):
        messages.error(request, 'Access denied. Manager dashboard is only for managers.')
        return redirect('trip-list')
    
    # Trips info (Payment-based status)
    all_trips = Trip.objects.with_payment_info()
    active_trips = all_trips.filter(
        Q(annotated_status='Unpaid') | Q(annotated_status='Partially Paid')
    ).count()
    
    current_month = timezone.now().month
    current_year = timezone.now().year
    
    completed_this_month = all_trips.filter(
        annotated_status='Paid',
        date__month=current_month,
        date__year=current_year
    ).count()
    
    # Maintenance info
    vehicles_due_maintenance = MaintenanceRecord.objects.filter(
        is_completed=False,
        expiry_date__lte=timezone.now().date() + timedelta(days=7)
    ).values('vehicle').distinct().count()
    
    # Financial summary
    income_this_month = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_INCOME,
        date__month=current_month,
        date__year=current_year
    ).exclude(
        record_type=FinancialRecord.RECORD_TYPE_INVOICE
    ).aggregate(total=Sum('amount'))['total'] or 0
    
    expenses_this_month = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_EXPENSE,
        date__month=current_month,
        date__year=current_year
    ).exclude(
        category__name='Deductions'
    ).aggregate(total=Sum('amount'))['total'] or 0

    gst_this_month = sum(bill.gst_amount for bill in Bill.objects.filter(
        date__month=current_month,
        date__year=current_year
    ))
    
    recent_trips = all_trips.order_by('-created_at')[:10]
    vehicles_in_maintenance = Vehicle.objects.filter(status=Vehicle.STATUS_MAINTENANCE).count()
    
    context = {
        'active_trips': active_trips,
        'completed_this_month': completed_this_month,
        'vehicles_due_maintenance': vehicles_due_maintenance,
        'income_this_month': income_this_month,
        'expenses_this_month': expenses_this_month,
        'net_profit_incl_gst': income_this_month - expenses_this_month,
        'net_profit_excl_gst': (income_this_month - gst_this_month) - expenses_this_month,
        'recent_trips': recent_trips,
        'vehicles_in_maintenance': vehicles_in_maintenance,
    }
    
    return render(request, 'trips/manager_dashboard.html', context)


@login_required
def get_autocomplete_suggestions(request):
    """
    Returns suggestions for Select2.
    """
    field = request.GET.get('field')
    term = request.GET.get('term', '')
    
    results = []
    
    if field in ['pickup_location', 'delivery_location']:
        seen_names = set()
        query_filter = {f"{field}__icontains": term} if term else {}
        local_qs = Trip.objects.filter(**query_filter).values_list(field, flat=True).distinct().order_by(field)[:10]
        
        for name in local_qs:
            if name and name not in seen_names:
                results.append({
                    'id': name,
                    'text': f"🕒 {name}",
                    'source': 'history'
                })
                seen_names.add(name)

    elif field == 'tyre_brand':
        qs = Tyre.objects.filter(brand__icontains=term).values_list('brand', flat=True).distinct()[:10]
        results = [{'id': x, 'text': x} for x in qs]
    elif field == 'tyre_size':
        qs = Tyre.objects.filter(size__icontains=term).values_list('size', flat=True).distinct()[:10]
        results = [{'id': x, 'text': x} for x in qs]
        
    return JsonResponse({'results': results})
