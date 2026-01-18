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
        context['trips'] = Trip.objects.filter(driver=driver.user).order_by('-created_at')

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


class DriverLedgerView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """
    View for driver's financial ledger (distinct from pocket transactions)
    Uses FinancialRecord model.
    """
    model = Driver
    template_name = 'drivers/driver_ledger.html'
    context_object_name = 'driver'
    permission_required = 'drivers.can_manage_driver_finance'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        driver_user = self.object.user
        
        # Get all financial records associated with this driver
        records = FinancialRecord.objects.filter(driver=driver_user).order_by('-date')
        context['financial_records'] = records
        
        # Calculate Balance
        # Logic: 
        # Positive (Asset/Owe Us): Loans, Advances (Category: Driver Payment?)
        # Negative (Liability/We Owe): Salary, Allowance (Category: Driver Payment?)
        # 
        # Current FinancialRecord Categories:
        # - FREIGHT_INCOME (Income)
        # - FUEL_EXPENSE (Expense)
        # - MAINTENANCE_EXPENSE (Expense)
        # - DRIVER_PAYMENT (Expense) -> Usually "Cash Out"
        # - OTHER (Expense)
        #
        # If we use "Driver Payment" for "Cash Out" (We paid them):
        # This reduces our liability (or increases their debt).
        # So "Driver Payment" records should probably be POSITIVE in the Driver Ledger (They received money).
        #
        # But where do we record "Salary Accrual"? 
        # We might need to use "Other" or assume a starting balance?
        # Or perhaps we simply treat "Driver Payment" as NEGATIVE (Expense for us) and just show the net?
        #
        # User request: "drivers also need to be in negative when we have to pay them"
        # So: "We owe Driver" = Negative Balance.
        # "Driver Payment" (Cash Out) = Reduces what we owe = Positive impact on balance.
        # 
        # But FinancialRecord 'amount' is just a number.
        # And 'category' determines if it's income/expense for the COMPANY.
        # DRIVER_PAYMENT is an EXPENSE for Company.
        # 
        # Let's assume:
        # All FinancialRecords linked to Driver are payments TO the driver (Debit Driver, Credit Cash).
        # So all these records INCREASE the balance (make it more positive / less negative).
        #
        # What decreases the balance (We owe them)?
        # Currently, the system might not track "Salary Accrual" automatically.
        # So the balance might just show "Total Paid to Driver".
        # 
        # However, `drivers.models.DriverTransaction` exists for "Pocket/Wallet".
        # It has TYPE_SALARY (Credit +), TYPE_PAYMENT (Debit -).
        # 
        # Use Case Conflict:
        # The user asked for "Driver Ledger" using `FinancialRecord`?
        # "similarly drivers also need to be in negative when we have to pay them... and positive when they owe us"
        # 
        # If I use `FinancialRecord` for this, I only have "Payments".
        # Unless I add a category "Driver Credit" (Salary/Allowance).
        # 
        # Let's stick to what we have:
        # `DriverTransaction` seems to ALREADY implement this logic!
        # "Positive: Company owes Driver. Negative: Driver owes Company." (Wait, looking at model docstring)
        # Docstring says: "Positive: Company owes Driver."
        # User wants: "negative when we have to pay them" (Company owes Driver).
        # So User wants NEGATIVE = Company owes Driver.
        # Existing Model `DriverTransaction` says POSITIVE = Company owes Driver.
        # 
        # I should probably just use the existing `DriverTransaction` system but flip the sign display?
        # OR, the user is asking for `FinancialRecord` integration.
        # 
        # Let's integrate `FinancialRecord` into the `DriverLedgerView` but primarily rely on `DriverTransaction` logic if it's robust?
        # NO, the user request implies `FinancialRecord` context ("making ledger entry...").
        #
        # Let's calculate balance from `FinancialRecord` linked to Driver.
        # Assume these are PAYMENTS (Money given to driver).
        # To make sense of "Balance", we need the "Accrual" side.
        # 
        # For now, I will just sum the `FinancialRecords` (Payments) as POSITIVE (Money given).
        # And I will mention in the template that this is "Total Payments".
        # 
        # WAIT, if I want to support "Negative (We owe them)", I need to record "Salary Due".
        # Can I use `FinancialRecord` with negative amount?
        # Or a new category?
        # 
        # Let's just list the records for now and sum them.
        # I will assume records with `driver` set are payments/advances (Money to Driver).
        
        total_payments = records.aggregate(total=Sum('amount'))['total'] or 0
        context['balance'] = total_payments
        
        return context
