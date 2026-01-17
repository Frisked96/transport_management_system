"""
Views for Ledger application with permission checks
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Sum
from django.utils import timezone

from .models import FinancialRecord
from .forms import FinancialRecordForm


class BaseLedgerPermissionMixin:
    """Base mixin for ledger permissions"""
    
    def has_manager_permission(self):
        """Check if user is in manager group"""
        return self.request.user.groups.filter(name='manager').exists()
    
    def has_supervisor_permission(self):
        """Check if user is in supervisor group"""
        return self.request.user.groups.filter(name='supervisor').exists()
    
    def has_driver_permission(self):
        """Check if user is in driver group"""
        return self.request.user.groups.filter(name='driver').exists()


class FinancialRecordListView(LoginRequiredMixin, BaseLedgerPermissionMixin, ListView):
    """
    List view for financial records with permission-based filtering
    """
    model = FinancialRecord
    template_name = 'ledger/financialrecord_list.html'
    context_object_name = 'financial_records'
    paginate_by = 20
    
    def get_queryset(self):
        """Filter financial records based on user permissions"""
        # Drivers have no access to financial records
        if self.has_driver_permission():
            return FinancialRecord.objects.none()
        
        queryset = FinancialRecord.objects.all()
        
        # Category filter
        category = self.request.GET.get('category')
        if category:
            queryset = queryset.filter(category=category)
        
        # Trip filter
        trip_id = self.request.GET.get('trip')
        if trip_id:
            queryset = queryset.filter(associated_trip_id=trip_id)
        
        # Date range filter
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
        if end_date:
            queryset = queryset.filter(date__lte=end_date)
        
        return queryset.order_by('-date')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['category_choices'] = FinancialRecord.CATEGORY_CHOICES
        context['current_category'] = self.request.GET.get('category', '')
        
        # Calculate totals for filtered records
        records = self.get_queryset()
        total_income = records.filter(
            category=FinancialRecord.CATEGORY_FREIGHT_INCOME
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        total_expenses = records.filter(
            category__in=[
                FinancialRecord.CATEGORY_FUEL_EXPENSE,
                FinancialRecord.CATEGORY_MAINTENANCE_EXPENSE,
                FinancialRecord.CATEGORY_DRIVER_PAYMENT,
                FinancialRecord.CATEGORY_OTHER
            ]
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        context['total_income'] = total_income
        context['total_expenses'] = total_expenses
        context['net_total'] = total_income - total_expenses
        
        return context


class FinancialRecordDetailView(LoginRequiredMixin, BaseLedgerPermissionMixin, DetailView):
    """
    Detail view for a single financial record
    """
    model = FinancialRecord
    template_name = 'ledger/financialrecord_detail.html'
    context_object_name = 'record'
    
    def get_queryset(self):
        """Ensure user has permission to view financial records"""
        # Drivers cannot view financial records
        if self.has_driver_permission():
            return FinancialRecord.objects.none()
        
        return FinancialRecord.objects.all()


class FinancialRecordCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for new financial records
    Permission: Only admin and manager can create financial records
    """
    model = FinancialRecord
    form_class = FinancialRecordForm
    template_name = 'ledger/financialrecord_form.html'
    permission_required = 'ledger.add_financialrecord'
    
    def get_initial(self):
        initial = super().get_initial()
        trip_id = self.request.GET.get('trip')
        if trip_id:
            from trips.models import Trip
            try:
                trip = Trip.objects.get(pk=trip_id)
                initial['associated_trip'] = trip
            except Trip.DoesNotExist:
                pass
        return initial

    def form_valid(self, form):
        form.instance.recorded_by = self.request.user
        messages.success(self.request, 'Financial record created successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('financialrecord-detail', kwargs={'pk': self.object.pk})


class FinancialRecordUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for existing financial records
    Permission: Only admin and manager can update financial records
    """
    model = FinancialRecord
    form_class = FinancialRecordForm
    template_name = 'ledger/financialrecord_form.html'
    permission_required = 'ledger.change_financialrecord'
    
    def form_valid(self, form):
        messages.success(self.request, 'Financial record updated successfully!')
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('financialrecord-detail', kwargs={'pk': self.object.pk})


class FinancialRecordDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for financial records
    Permission: Only admin can delete financial records
    """
    model = FinancialRecord
    template_name = 'ledger/financialrecord_confirm_delete.html'
    permission_required = 'ledger.delete_financialrecord'
    success_url = reverse_lazy('financialrecord-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Financial record deleted successfully!')
        return super().delete(request, *args, **kwargs)


@login_required
def financial_summary(request):
    """
    Financial summary report view
    Only accessible by admin and manager
    """
    # Check permissions
    if not (request.user.is_superuser or 
            request.user.groups.filter(name='manager').exists()):
        messages.error(request, 'Access denied. Financial summary is only for managers.')
        return redirect('trip-list')
    
    from datetime import datetime
    
    # Get current month and year
    current_month = timezone.now().month
    current_year = timezone.now().year
    
    # Monthly summary
    monthly_income = FinancialRecord.objects.filter(
        category=FinancialRecord.CATEGORY_FREIGHT_INCOME,
        date__month=current_month,
        date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or 0
    
    monthly_expenses = FinancialRecord.objects.filter(
        category__in=[
            FinancialRecord.CATEGORY_FUEL_EXPENSE,
            FinancialRecord.CATEGORY_MAINTENANCE_EXPENSE,
            FinancialRecord.CATEGORY_DRIVER_PAYMENT,
            FinancialRecord.CATEGORY_OTHER
        ],
        date__month=current_month,
        date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or 0
    
    # Yearly summary
    yearly_income = FinancialRecord.objects.filter(
        category=FinancialRecord.CATEGORY_FREIGHT_INCOME,
        date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or 0
    
    yearly_expenses = FinancialRecord.objects.filter(
        category__in=[
            FinancialRecord.CATEGORY_FUEL_EXPENSE,
            FinancialRecord.CATEGORY_MAINTENANCE_EXPENSE,
            FinancialRecord.CATEGORY_DRIVER_PAYMENT,
            FinancialRecord.CATEGORY_OTHER
        ],
        date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or 0
    
    # Category breakdown for current month
    category_breakdown = {}
    for category, _ in FinancialRecord.CATEGORY_CHOICES:
        total = FinancialRecord.objects.filter(
            category=category,
            date__month=current_month,
            date__year=current_year
        ).aggregate(total=Sum('amount'))['total'] or 0
        category_breakdown[category] = total
    
    context = {
        'monthly_income': monthly_income,
        'monthly_expenses': monthly_expenses,
        'monthly_net': monthly_income - monthly_expenses,
        'yearly_income': yearly_income,
        'yearly_expenses': yearly_expenses,
        'yearly_net': yearly_income - yearly_expenses,
        'category_breakdown': category_breakdown,
        'current_month': datetime(current_year, current_month, 1).strftime('%B %Y'),
    }
    
    return render(request, 'ledger/financial_summary.html', context)