"""
Core CRUD views for Trip model.
"""
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, FormView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Sum
from django.db import transaction
from django.utils import timezone
from django.forms import modelformset_factory
from django.core.cache import cache

from trips.models import Trip, Route
from trips.forms import TripForm
from .mixins import BaseTripPermissionMixin


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
        queryset = self.get_queryset_for_user().with_payment_info().with_billing_info().select_related(
            'vehicle', 'party', 'route'
        )
        
        # Search functionality
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(trip_number__icontains=search) |
                Q(party__name__icontains=search) |
                Q(pickup_location__icontains=search) |
                Q(delivery_location__icontains=search) |
                Q(route__pickup_location__icontains=search) |
                Q(route__delivery_location__icontains=search) |
                Q(vehicle__registration_plate__icontains=search) |
                Q(lr_no__icontains=search)
            )

        # LR Number Specific Search
        lr_search = self.request.GET.get('lr_search')
        if lr_search:
            queryset = queryset.filter(lr_no__icontains=lr_search)
        
        # Party filter
        party_id = self.request.GET.get('party')
        if party_id:
            queryset = queryset.filter(party_id=party_id)

        # Status filter (payment-based)
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(annotated_status=status)

        # Billing Status filter
        billing_status = self.request.GET.get('billing_status')
        if billing_status == 'unbilled':
            queryset = queryset.filter(annotated_is_billed=False)
        elif billing_status == 'billed':
            queryset = queryset.filter(annotated_is_billed=True)

        # GST Filter
        gst_filter = self.request.GET.get('gst_filter')
        if gst_filter == 'gst':
            queryset = queryset.filter(annotated_gst_type__in=['GST', 'IGST'])
        elif gst_filter == 'non_gst':
            queryset = queryset.filter(annotated_gst_type='NONE')
            
        # Quick Date filter
        date_filter = self.request.GET.get('date_filter')
        today = timezone.now().date()
        if date_filter == 'today':
            queryset = queryset.filter(date=today)
        elif date_filter == 'yesterday':
            queryset = queryset.filter(date=today - timedelta(days=1))
        elif date_filter == 'last_7_days':
            queryset = queryset.filter(date__gte=today - timedelta(days=7))

        # Date range filtering
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        exact_date = self.request.GET.get('exact_date')
        
        if exact_date:
            try:
                queryset = queryset.filter(date=exact_date)
            except (ValueError, TypeError):
                pass
        else:
            if start_date:
                try:
                    queryset = queryset.filter(date__gte=start_date)
                except (ValueError, TypeError):
                    pass
            if end_date:
                try:
                    queryset = queryset.filter(date__lte=end_date)
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
        context['current_billing_status'] = self.request.GET.get('billing_status', '')
        context['current_gst_filter'] = self.request.GET.get('gst_filter', '')
        context['search_term'] = self.request.GET.get('search', '')
        context['current_lr_search'] = self.request.GET.get('lr_search', '')
        context['start_date'] = self.request.GET.get('start_date', '')
        context['end_date'] = self.request.GET.get('end_date', '')
        context['exact_date'] = self.request.GET.get('exact_date', '')
        context['current_sort'] = self.request.GET.get('sort', '-date')
        context['current_party'] = self.request.GET.get('party', '')
        context['date_filter'] = self.request.GET.get('date_filter', '')
        
        # Summary for the filtered queryset
        base_qs = self.object_list
        context['total_weight'] = base_qs.aggregate(total_weight=Sum('weight'))['total_weight'] or 0
        context['total_count'] = context['paginator'].count if 'paginator' in context else base_qs.count()
        
        return context


class TripDetailView(LoginRequiredMixin, BaseTripPermissionMixin, DetailView):
    """
    Detail view for a single trip.
    Uses prefetching + model properties to avoid parser stack overflow.
    """
    model = Trip
    template_name = 'trips/trip_detail.html'
    context_object_name = 'trip'
    
    def get_queryset(self):
        """
        Optimized queryset for detail view.
        We avoid complex annotations (with_payment_info) and use prefetching instead.
        """
        return self.get_queryset_for_user().select_related(
            'vehicle', 'party', 'driver', 'route'
        ).prefetch_related(
            'bills',
            'bills__category',
            'bills__financial_records',
            'bills__financial_records__category',
            'bills__trips',
            'bills__adjustment_bills',
            'bills__adjustment_bills__category',
            'financial_records',
            'financial_records__category',
            'payment_allocations',
            'payment_allocations__financial_record',
            'payment_allocations__financial_record__category'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        trip = self.object
        next_trip = trip.get_next_trip()
        toll_entries = list(trip.get_toll_entries())
        total_tolls = sum((t.amount for t in toll_entries), Decimal('0.00'))
        vendor_hire = trip.vendor_hire_amount or Decimal('0.00')
        total_expenses = total_tolls + vendor_hire
        net_profit = trip.total_revenue - total_expenses

        context['next_trip'] = next_trip
        context['toll_entries'] = toll_entries
        context['total_tolls'] = total_tolls
        context['toll_count'] = len(toll_entries)
        context['vendor_hire'] = vendor_hire
        context['total_expenses'] = total_expenses
        context['net_profit'] = net_profit
        context['net_trip_profit'] = net_profit
        return context


class TripBulkCreateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    """
    Bulk create view for multiple trips.
    """
    template_name = 'trips/trip_bulk_form.html'
    permission_required = 'trips.add_trip'
    success_url = reverse_lazy('trip-list')
    
    def get_form_class(self):
        return modelformset_factory(
            Trip,
            form=TripForm,
            extra=1,
            can_delete=True
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['queryset'] = Trip.objects.none()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        routes = Route.objects.all().values('id', 'default_rate')
        route_rates = {str(r['id']): float(r['default_rate']) for r in routes}
        context['route_rates'] = route_rates
        return context

    def form_valid(self, form):
        # Prevent rapid duplicate submissions (e.g. double click or network replay)
        post_data = self.request.POST.copy()
        post_data.pop('csrfmiddlewaretoken', None)
        fingerprint = hashlib.md5(str(sorted(post_data.items())).encode('utf-8')).hexdigest()
        cache_key = f"bulk_trip_submit_{self.request.user.id}_{fingerprint}"
        
        if not cache.add(cache_key, '1', timeout=10):
            messages.warning(self.request, "Your trips have already been submitted.")
            return redirect(self.success_url)

        with transaction.atomic():
            instances = form.save(commit=False)
            if not instances:
                return super().form_valid(form)

            batch_voucher = None
            if len(instances) > 1:
                from ledger.services import VoucherService
                from ledger.models import FinancialVoucher, CompanyAccount
                first_party = instances[0].party
                same_party = all(inst.party == first_party for inst in instances)
                batch_party = first_party if same_party else None
                first_date = instances[0].date or timezone.now().date()
                account = CompanyAccount.objects.first()

                batch_voucher = VoucherService.create_voucher(
                    date=first_date,
                    voucher_type=FinancialVoucher.VOUCHER_TYPE_TRIP_ACCRUAL,
                    created_by=self.request.user,
                    party=batch_party,
                    account=account,
                    description=f"Bulk Trip Creation ({len(instances)} Trips)"
                )

            for instance in instances:
                instance.created_by = self.request.user
                if not instance.date:
                     instance.date = timezone.now().date()
                if batch_voucher:
                    instance._batch_voucher = batch_voucher
                instance.save()

            if batch_voucher:
                batch_voucher.update_totals()

        messages.success(self.request, f'{len(instances)} Trips created successfully!')
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, "There were errors saving the trips. Please correct the highlighted fields below.")
        return super().form_invalid(form)


class TripCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for new trips.
    """
    model = Trip
    form_class = TripForm
    template_name = 'trips/trip_form.html'
    permission_required = 'trips.add_trip'
    
    def get_initial(self):
        initial = super().get_initial()
        
        # Handle date from GET
        date_str = self.request.GET.get('date')
        if date_str:
            try:
                initial['date'] = datetime.strptime(date_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                pass
                
        # Handle party from GET
        party_id = self.request.GET.get('party')
        if party_id:
            initial['party'] = party_id
            
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        
        # Set date from GET param if not provided in form (e.g. if field was somehow omitted)
        if not form.cleaned_data.get('date'):
            date_str = self.request.GET.get('date')
            if date_str:
                try:
                    form.instance.date = datetime.strptime(date_str, '%Y-%m-%d').date()
                except ValueError:
                    form.instance.date = timezone.now().date()
            else:
                form.instance.date = timezone.now().date()

        response = super().form_valid(form)
        messages.success(self.request, 'Trip created successfully!')
        
        # Handle "Save and Add New" options
        if '_save_new_date' in self.request.POST:
            date_str = self.object.date.strftime('%Y-%m-%d') if self.object.date else ''
            return redirect(f"{self.request.path}?date={date_str}")
        elif '_save_new_party' in self.request.POST:
            party_id = self.object.party.id if self.object.party else ''
            return redirect(f"{self.request.path}?party={party_id}")
            
        return response
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Provide route default rates for JS auto-population
        routes = Route.objects.all().values('id', 'default_rate')
        route_rates = {str(r['id']): float(r['default_rate']) for r in routes}
        context['route_rates'] = route_rates
        return context

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
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Provide route default rates for JS auto-population
        routes = Route.objects.all().values('id', 'default_rate')
        route_rates = {str(r['id']): float(r['default_rate']) for r in routes}
        context['route_rates'] = route_rates
        return context

    def get_success_url(self):
        return reverse_lazy('trip-detail', kwargs={'pk': self.object.pk})


class TripDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for trips
    """
    model = Trip
    template_name = 'trips/trip_confirm_delete.html'
    permission_required = 'trips.delete_trip'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Provide route default rates for JS auto-population
        routes = Route.objects.all().values('id', 'default_rate')
        route_rates = {str(r['id']): float(r['default_rate']) for r in routes}
        context['route_rates'] = route_rates
        return context

    def get_success_url(self):
        return reverse_lazy('trip-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Trip deleted successfully!')
        return super().delete(request, *args, **kwargs)
