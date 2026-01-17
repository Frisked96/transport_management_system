"""
Views for Drivers application
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.urls import reverse_lazy, reverse
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.db.models import Sum, F, DecimalField
from django.contrib.auth.models import User

from .models import Driver, DriverTransaction
from .forms import DriverForm, DriverTransactionForm
from fleet.models import Vehicle
from trips.models import Trip, TripLeg
from ledger.models import FinancialRecord


class DriverListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    List view for drivers
    """
    model = Driver
    template_name = 'drivers/driver_list.html'
    context_object_name = 'drivers'
    permission_required = 'drivers.can_view_all_drivers'

    def get_queryset(self):
        return Driver.objects.all().select_related('user')


class DriverDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """
    Detail view for a driver with profit and history
    """
    model = Driver
    template_name = 'drivers/driver_detail.html'
    context_object_name = 'driver'
    permission_required = 'drivers.can_view_all_drivers'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        driver = self.object

        # Vehicles driven (History)
        # Distinct vehicles from trips assigned to this driver
        context['vehicles_driven'] = Vehicle.objects.filter(
            trips__driver=driver.user
        ).distinct()

        # Trips history
        context['trips'] = Trip.objects.filter(driver=driver.user).order_by('-scheduled_datetime')

        # Profit Calculation
        # 1. Total Revenue: Sum of (Weight * Price) for all legs of trips by this driver
        total_revenue = TripLeg.objects.filter(
            trip__driver=driver.user
        ).aggregate(
            total=Sum(F('weight') * F('price_per_ton'), output_field=DecimalField())
        )['total'] or 0

        # 2. Total Expenses
        # FinancialRecords associated with trips by this driver, that are expenses
        expense_categories = [
            FinancialRecord.CATEGORY_FUEL_EXPENSE,
            FinancialRecord.CATEGORY_MAINTENANCE_EXPENSE,
            FinancialRecord.CATEGORY_DRIVER_PAYMENT,
            FinancialRecord.CATEGORY_OTHER
        ]

        # We filter FinancialRecords by the trips assigned to this driver
        total_expenses = FinancialRecord.objects.filter(
            associated_trip__driver=driver.user,
            category__in=expense_categories
        ).aggregate(total=Sum('amount'))['total'] or 0

        context['total_revenue'] = total_revenue
        context['total_expenses'] = total_expenses
        context['profit'] = total_revenue - total_expenses

        # Expenses List (for the panel)
        context['expenses_list'] = FinancialRecord.objects.filter(
            associated_trip__driver=driver.user,
            category__in=expense_categories
        ).order_by('-date')

        # Pocket Transactions
        context['transactions'] = driver.transactions.all().order_by('-date', '-created_at')

        return context


class DriverCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for new drivers
    """
    model = Driver
    form_class = DriverForm
    template_name = 'drivers/driver_form.html'
    permission_required = 'drivers.add_driver' # Standard django permission
    success_url = reverse_lazy('driver-list')

    def form_valid(self, form):
        messages.success(self.request, 'Driver created successfully!')
        return super().form_valid(form)


class DriverUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for existing drivers
    """
    model = Driver
    form_class = DriverForm
    template_name = 'drivers/driver_form.html'
    permission_required = 'drivers.change_driver'

    def form_valid(self, form):
        messages.success(self.request, 'Driver updated successfully!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('driver-detail', kwargs={'pk': self.object.pk})


class DriverTransactionCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    View to add a financial transaction to the driver's pocket
    """
    model = DriverTransaction
    form_class = DriverTransactionForm
    template_name = 'drivers/driver_transaction_form.html'
    permission_required = 'drivers.can_manage_driver_finance'

    def dispatch(self, request, *args, **kwargs):
        self.driver = get_object_or_404(Driver, pk=kwargs['driver_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.driver = self.driver
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Transaction recorded successfully!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('driver-detail', kwargs={'pk': self.driver.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['driver'] = self.driver
        return context
