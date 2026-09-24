"""
CompanyAccount views and Global Resync view.
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Sum, F, DecimalField, Value, Case, When, OuterRef, Count
from django.utils import timezone
from decimal import Decimal, InvalidOperation, DecimalException
from datetime import datetime
import json
from django.http import JsonResponse, HttpResponse

from ledger.models import FinancialRecord, Party, CompanyAccount, TripAllocation, TransactionCategory, Bill, BillTrip
from ledger.forms import CompanyAccountForm
from trips.models import Trip
from ledger.views.base import BaseLedgerPermissionMixin
from ledger.utils import format_indian_comma, format_balance

class CompanyAccountListView(LoginRequiredMixin, BaseLedgerPermissionMixin, ListView):
    """
    List view for company accounts
    """
    model = CompanyAccount
    template_name = 'ledger/account_list.html'
    context_object_name = 'accounts'
    paginate_by = 25
    
    def get_queryset(self):
        # Drivers have no access
        if self.has_driver_permission():
            return CompanyAccount.objects.none()
            
        return CompanyAccount.objects.all().order_by('name')

class CompanyAccountCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for new account
    """
    model = CompanyAccount
    form_class = CompanyAccountForm
    template_name = 'ledger/account_form.html'
    permission_required = 'ledger.add_financialrecord'
    success_url = reverse_lazy('account-list')
    
    def form_valid(self, form):
        messages.success(self.request, 'Account created successfully!')
        return super().form_valid(form)

class CompanyAccountUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for existing account
    """
    model = CompanyAccount
    form_class = CompanyAccountForm
    template_name = 'ledger/account_form.html'
    permission_required = 'ledger.change_financialrecord'
    success_url = reverse_lazy('account-list')

    def form_valid(self, form):
        messages.success(self.request, 'Account updated successfully!')
        return super().form_valid(form)

class CompanyAccountDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for account
    """
    model = CompanyAccount
    template_name = 'ledger/account_confirm_delete.html'
    permission_required = 'ledger.delete_financialrecord'
    success_url = reverse_lazy('account-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Account deleted successfully!')
        return super().delete(request, *args, **kwargs)

class CompanyAccountDetailView(LoginRequiredMixin, BaseLedgerPermissionMixin, DetailView):
    """
    Detail view for an account (showing transaction history)
    """
    model = CompanyAccount
    template_name = 'ledger/account_detail.html'
    context_object_name = 'account'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get date range from request
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        
        records = self.object.financial_records.exclude(
            Q(record_type=FinancialRecord.RECORD_TYPE_INVOICE) | 
            Q(category__name__in=['Deductions', 'TDS', 'Shortage', 'Credit Note', 'Debit Note'])
        ).select_related('category', 'party', 'driver__user', 'associated_trip', 'associated_bill', 'associated_tyre')
        
        if start_date:
            records = records.filter(date__gte=start_date)
        if end_date:
            records = records.filter(date__lte=end_date)
            
        context['financial_records'] = records.order_by('-date', '-created_at')
        return context


@login_required
def global_resync(request):
    """
    Manually triggers a full refresh of all denormalized balances.
    Only accessible by superusers or managers.
    """
    if not (request.user.is_superuser or request.user.groups.filter(name='manager').exists()):
        messages.error(request, "You do not have permission to perform this action.")
        return redirect('financialrecord-list')

    # 1. Parties
    parties = Party.objects.all()
    for party in parties:
        party.refresh_balance()
        
    # 2. Company Accounts
    accounts = CompanyAccount.objects.all()
    for account in accounts:
        account.refresh_balance()
        
    # 3. Drivers
    from drivers.models import Driver
    drivers = Driver.objects.all()
    for driver in drivers:
        driver.refresh_balance()

    # 4. Bills & Trips (Financial Caches)
    bills = Bill.objects.all()
    for bill in bills:
        bill.update_financial_caches()
    
    # Note: Trip caches are updated by bill.update_financial_caches() for billed trips,
    # but we should also handle unbilled trips.
    trips = Trip.objects.filter(bills__isnull=True)
    for trip in trips:
        trip.update_financial_caches()
        
    messages.success(request, "All balances (Parties, Accounts, Drivers, Bills, and Trips) have been successfully resynced.")
    
    # Redirect to referer if available, else financial summary
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect('financial-summary')

