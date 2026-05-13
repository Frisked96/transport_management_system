"""
Views for Ledger application with permission checks
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Sum, F, DecimalField, Value, Case, When, OuterRef, Subquery, Count
from django.db.models.functions import Coalesce
from django.utils import timezone
from decimal import Decimal, InvalidOperation, DecimalException
from datetime import datetime
import json
from django.http import JsonResponse, HttpResponse
from django.template.loader import get_template, render_to_string
from xhtml2pdf import pisa
from itertools import groupby
from operator import attrgetter

from .models import FinancialRecord, Party, CompanyAccount, TripAllocation, TransactionCategory, Bill, BillTrip
from .forms import FinancialRecordForm, PartyForm, CompanyAccountForm, BillForm
from trips.models import Trip


def format_indian_comma(amount):
    """Formats a number into Indian style commas (e.g., 1,45,140.00)."""
    try:
        val = Decimal(str(amount))
    except (ValueError, TypeError, Exception):
        return "0.00"
    
    parts = f"{val:.2f}".split(".")
    whole, decimal = parts[0], parts[1]
    is_negative = whole.startswith("-")
    if is_negative: whole = whole[1:]
    
    if len(whole) <= 3:
        res = whole
    else:
        last_three = whole[-3:]
        remaining = whole[:-3]
        res = ""
        while len(remaining) > 2:
            res = "," + remaining[-2:] + res
            remaining = remaining[:-2]
        res = remaining + res + "," + last_three
    
    if is_negative: res = "-" + res
    return res + "." + decimal

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
    List view for financial records with permission-based filtering.
    Acts as a Financial Dashboard.
    """
    model = FinancialRecord
    template_name = 'ledger/financialrecord_list.html'
    context_object_name = 'financial_records'
    paginate_by = 25
    
    def get_queryset(self):
        """Filter financial records based on user permissions"""
        # Drivers have no access to financial records
        if self.has_driver_permission():
            return FinancialRecord.objects.none()
        
        queryset = FinancialRecord.objects.all().select_related('category', 'party', 'associated_trip')
        
        # Category filter
        category_id = self.request.GET.get('category')
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        
        # Trip filter
        trip_id = self.request.GET.get('trip')
        if trip_id:
            queryset = queryset.filter(associated_trip_id=trip_id)
        
        # Party filter
        party_id = self.request.GET.get('party')
        if party_id:
            queryset = queryset.filter(party_id=party_id)
        
        # Date range filter
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
        if end_date:
            queryset = queryset.filter(date__lte=end_date)
        
        return queryset.order_by('-date', '-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['category_choices'] = TransactionCategory.objects.all()
        context['current_category'] = self.request.GET.get('category', '')

        # 1. Financial Records Totals (Filtered)
        records = self.get_queryset()
        total_income = records.filter(
            category__type=TransactionCategory.TYPE_INCOME
        ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or 0
        
        total_expenses = records.filter(
            category__type=TransactionCategory.TYPE_EXPENSE
        ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or 0

        context['total_income'] = total_income
        context['total_expenses'] = total_expenses
        context['net_total'] = total_income - total_expenses

        # 2. Party Outstanding Dashboard (Unfiltered by date/category)
        # We want to see who owes money overall
        parties = Party.objects.all()
        party_dashboard = []
        total_outstanding = Decimal('0')

        # Define payment categories to include for "Last Payment"
        # We exclude TDS and Adjustment Notes (Credit/Debit Notes)
        # We only care about actual money coming in (Income)
        payment_categories = TransactionCategory.objects.filter(
            type=TransactionCategory.TYPE_INCOME
        ).exclude(
            name__in=['Credit Note', 'Debit Note', 'TDS', 'TDS Receivable', 'Opening Balance']
        )

        for p in parties:
            bal = p.current_balance_cached
            if p.party_type == Party.TYPE_DEBTOR:
                total_outstanding += max(Decimal('0'), bal)
            
            # Find last actual payment received
            last_payment = FinancialRecord.objects.filter(
                party=p,
                category__in=payment_categories
            ).exclude(
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).order_by('-date', '-created_at').first()
            
            party_dashboard.append({
                'id': p.id,
                'name': p.name,
                'balance': bal,
                'party_type': p.party_type,
                'last_payment_date': last_payment.date if last_payment else None
            })

        # Sort by absolute balance descending (most critical accounts first)
        party_dashboard.sort(key=lambda x: abs(x['balance']), reverse=True)

        context['total_outstanding'] = total_outstanding
        context['party_dashboard'] = party_dashboard[:10] # Top 10 for dashboard
        context['all_parties_dashboard'] = party_dashboard # Full list if needed
        
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
        
        party_id = self.request.GET.get('party')
        if party_id:
            try:
                party = Party.objects.get(pk=party_id)
                initial['party'] = party
            except Party.DoesNotExist:
                pass
                
        driver_id = self.request.GET.get('driver')
        if driver_id:
            try:
                from django.contrib.auth.models import User
                driver_user = User.objects.get(pk=driver_id)
                initial['driver'] = driver_user
            except User.DoesNotExist:
                pass
                
        bill_id = self.request.GET.get('associated_bill')
        if bill_id:
            try:
                bill = Bill.objects.get(pk=bill_id)
                initial['associated_bill'] = bill
            except Bill.DoesNotExist:
                pass
        
        if 'amount' in self.request.GET:
            initial['amount'] = self.request.GET.get('amount')
            
        if 'description' in self.request.GET:
            initial['description'] = self.request.GET.get('description')
                
        return initial

    def form_valid(self, form):
        distribution_json = form.cleaned_data.get('payment_distribution')
        
        if distribution_json:
            try:
                distribution_data = json.loads(distribution_json)
                
                # 1. Create the single parent FinancialRecord
                self.object = form.save(commit=False)
                self.object.recorded_by = self.request.user
                self.object.save()
                
                total_input_amount = self.object.amount
                total_distributed = Decimal('0')
                
                # 2. Iterate and create allocations for trips
                for item in distribution_data:
                    trip_id = item.get('trip_id')
                    try:
                        amount = Decimal(str(item.get('amount')))
                    except (ValueError, InvalidOperation):
                        raise ValueError(f"Invalid amount format for trip {trip_id}")
                    
                    if amount > 0:
                        trip = Trip.objects.get(pk=trip_id)
                        
                        TripAllocation.objects.create(
                            financial_record=self.object,
                            trip=trip,
                            amount=amount
                        )
                        
                        total_distributed += amount
                
                messages.success(self.request, f'Financial record created and distributed across {len(distribution_data)} trips!')
                
                # Redirect logic - Use redirect() not reverse_lazy()
                if self.object.party:
                    return redirect('party-detail', pk=self.object.party.pk)
                return redirect('financialrecord-list')

            except Exception as e:
                form.add_error(None, f"Error processing payment distribution: {str(e)}")
                return self.form_invalid(form)
        
        # Fallback to standard single record creation
        form.instance.recorded_by = self.request.user
        response = super().form_valid(form)
        
        messages.success(self.request, 'Financial record created successfully!')
        return response
    
    def get_success_url(self):
        # Redirect back to party detail if created from there
        if self.object.party:
            return reverse_lazy('party-detail', kwargs={'pk': self.object.party.pk})
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
        response = super().form_valid(form)
        messages.success(self.request, 'Financial record updated successfully!')
        return response
    
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        record = self.object
        impact = []
        if record.record_type == FinancialRecord.RECORD_TYPE_INVOICE and record.associated_bill:
            bill = record.associated_bill
            impact.append(f"The associated Bill/Invoice ({bill.bill_number or 'Draft'}) will be DELETED.")
            impact.append(f"{bill.trips.count()} trips will become UNBILLED.")
            payments = bill.amount_received
            if payments > 0:
                impact.append(f"₹{payments:,.2f} in payments made against this bill/trips will REMAIN in the system as unallocated Payments In/Deductions.")
        elif record.allocations.exists():
            impact.append(f"This record is allocated to {record.allocations.count()} trips. Deleting it will increase their outstanding balances.")
        else:
            if record.associated_trip:
                impact.append(f"Deleting this will remove the payment/expense from Trip {record.associated_trip.trip_number}.")
            else:
                impact.append("Deleting this will directly adjust the party and account balances.")
        
        impact.append("Ledger entry numbers will be automatically re-sequenced to prevent gaps.")
        context['impact_statements'] = impact
        return context
    
    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        response = super().delete(request, *args, **kwargs)
        messages.success(self.request, 'Financial record deleted successfully!')
        return response


@login_required
def financial_summary(request):
    """
    Financial summary report view
    """
    now = timezone.now()
    current_month = now.month
    current_year = now.year
    
    # Month calculations
    monthly_income = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_INCOME,
        date__month=current_month,
        date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or 0
    
    monthly_expenses = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_EXPENSE,
        date__month=current_month,
        date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or 0
    
    # Year calculations
    yearly_income = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_INCOME,
        date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or 0
    
    yearly_expenses = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_EXPENSE,
        date__year=current_year
    ).aggregate(total=Sum('amount'))['total'] or 0

    # Calculate GST portion from all Bills
    from .models import Bill
    monthly_gst = sum(bill.gst_amount for bill in Bill.objects.filter(
        date__month=current_month,
        date__year=current_year
    ))
    yearly_gst = sum(bill.gst_amount for bill in Bill.objects.filter(
        date__year=current_year
    ))
    
    # Category breakdown for current month
    category_breakdown = []
    for cat in TransactionCategory.objects.all():
        total = FinancialRecord.objects.filter(
            category=cat,
            date__month=current_month,
            date__year=current_year
        ).aggregate(total=Sum('amount'))['total'] or 0
        if total > 0:
            category_breakdown.append({
                'name': cat.name,
                'amount': total,
                'type': cat.type
            })
    
    context = {
        'monthly_income': monthly_income,
        'monthly_expenses': monthly_expenses,
        'monthly_net_incl_gst': monthly_income - monthly_expenses,
        'monthly_net_excl_gst': (monthly_income - monthly_gst) - monthly_expenses,
        'yearly_income': yearly_income,
        'yearly_expenses': yearly_expenses,
        'yearly_net_incl_gst': yearly_income - yearly_expenses,
        'yearly_net_excl_gst': (yearly_income - yearly_gst) - yearly_expenses,
        'category_breakdown': category_breakdown,
        'current_month': datetime(current_year, current_month, 1).strftime('%B %Y'),
    }
    
    return render(request, 'ledger/financial_summary.html', context)


# --- Party Views ---

class PartyListView(LoginRequiredMixin, BaseLedgerPermissionMixin, ListView):
    """
    List view for parties
    """
    model = Party
    template_name = 'ledger/party_list.html'
    context_object_name = 'parties'
    paginate_by = 25
    
    def get_queryset(self):
        # Drivers have no access
        if self.has_driver_permission():
            return Party.objects.none()
            
        queryset = Party.objects.all()
        
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(phone_number__icontains=search) |
                Q(state__icontains=search)
            )
            
        sort = self.request.GET.get('sort', 'name')
        if sort == 'most_outstanding':
            queryset = queryset.order_by('-current_balance_cached')
        elif sort == 'most_payable':
            queryset = queryset.order_by('current_balance_cached')
        elif sort == 'name':
            queryset = queryset.order_by('name')
        else:
            queryset = queryset.order_by('name')

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_sort'] = self.request.GET.get('sort', 'name')
        return context

from django.core.paginator import Paginator

class PartyDetailView(LoginRequiredMixin, BaseLedgerPermissionMixin, DetailView):
    """
    Detail view for a party
    """
    model = Party
    template_name = 'ledger/party_detail.html'
    context_object_name = 'party'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # 1. Trips Pagination (Operations Tab)
        # Optimized with prefetching for bills and allocations to support accurate Python-side balance calculations
        trips_qs = Trip.objects.filter(party=self.object).select_related(
            'vehicle', 'route'
        ).prefetch_related(
            'bills',
            'bills__category',
            'bills__adjustment_bills',
            'bills__adjustment_bills__category',
            'bills__financial_records',
            'bills__financial_records__category',
            'payment_allocations',
            'financial_records',
            'financial_records__category'
        ).with_payment_info().with_billing_info().order_by('-date', '-created_at')

        billed_filter = self.request.GET.get('billed')
        if billed_filter == 'unbilled':
            trips_qs = trips_qs.filter(annotated_is_billed=False)
        elif billed_filter == 'billed':
            trips_qs = trips_qs.filter(annotated_is_billed=True)

        trips_paginator = Paginator(trips_qs, 25)
        trips_page_num = self.request.GET.get('page')
        context['trips'] = trips_paginator.get_page(trips_page_num)
        context['billed_filter'] = billed_filter

        # 2. Financial Records Pagination (Ledger Tab)
        # We need a separate page parameter for ledger
        ledger_page_num = self.request.GET.get('ledger_page', 1)
        ledger_page_size = 50

        # Get all records in CHRONOLOGICAL order to calculate running balance accurately
        records_qs = self.object.financial_records.select_related(
            'category', 'associated_trip', 'associated_bill', 'associated_bill__category'
        ).order_by('date', 'created_at')

        # We still need to calculate the running balance for ALL records up to the current page.
        # For performance with very large ledgers, this might need optimization, 
        # but for now, we calculate it in-memory for the current set.
        all_records = list(records_qs)
        running_bal = self.object.opening_balance
        for rec in all_records:
            debit = rec.debit_amount or Decimal('0')
            credit = rec.credit_amount or Decimal('0')
            running_bal += (debit - credit)
            rec.running_balance = running_bal

        # Sort back to newest-first
        all_records.reverse()

        ledger_paginator = Paginator(all_records, ledger_page_size)
        ledger_page = ledger_paginator.get_page(ledger_page_num)

        context['financial_records'] = ledger_page
        context['ledger_page_obj'] = ledger_page

        # Get Bills with prefetching
        bills_qs = self.object.bills.select_related('issuer', 'category').prefetch_related(
            'trips',
            'trips__payment_allocations',
            'financial_records',
            'financial_records__category',
            'bill_trips',
            'bill_trips__trip',
            'adjustment_bills',
            'adjustment_bills__category'
        ).order_by('-date', '-created_at')
        
        bills_page_num = self.request.GET.get('bills_page', 1)
        bills_paginator = Paginator(bills_qs, 25)
        context['bills'] = bills_paginator.get_page(bills_page_num)
        # Use model properties for accurate totals (They are more inclusive of manual entries)
        context['total_revenue'] = self.object.total_billed
        context['total_received'] = self.object.total_received
        context['balance'] = self.object.current_balance
        
        return context

class PartyCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for new party
    """
    model = Party
    form_class = PartyForm
    template_name = 'ledger/party_form.html'
    permission_required = 'ledger.add_financialrecord'
    
    def get_success_url(self):
        return reverse_lazy('party-detail', kwargs={'pk': self.object.pk})
        
    def form_valid(self, form):
        messages.success(self.request, 'Party created successfully!')
        return super().form_valid(form)

class PartyUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for existing party
    """
    model = Party
    form_class = PartyForm
    template_name = 'ledger/party_form.html'
    permission_required = 'ledger.change_financialrecord'
    
    def get_success_url(self):
        return reverse_lazy('party-detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Party updated successfully!')
        return super().form_valid(form)

class PartyDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for party
    """
    model = Party
    template_name = 'ledger/party_confirm_delete.html'
    permission_required = 'ledger.delete_financialrecord'
    success_url = reverse_lazy('party-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Party deleted successfully!')
        return super().delete(request, *args, **kwargs)


# --- Account Views ---

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
        
        records = self.object.financial_records.all().select_related('category', 'party')
        
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

@login_required
def get_party_unpaid_trips(request):
    """
    AJAX endpoint to get unpaid/partial trips for a party
    """
    party_id = request.GET.get('party_id')
    if not party_id:
        return JsonResponse({'trips': []})
    
    try:
        trips = Trip.objects.with_payment_info().filter(
            party_id=party_id
        ).exclude(
            annotated_status=Trip.PAYMENT_STATUS_PAID
        ).order_by('date')
        
        data = [{
            'id': trip.id,
            'label': f"{trip.date.strftime('%d/%m/%Y')} - {trip.vehicle.registration_plate} (Pending: ₹{trip.outstanding_balance:,.2f})",
            'balance': float(trip.outstanding_balance)
        } for trip in trips]
        
        return JsonResponse({'trips': data})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e), 'detail': 'Check server logs for traceback'}, status=400)

@login_required
def get_bill_balance(request):
# ... rest of get_bill_balance ...
    """
    AJAX endpoint to get outstanding balance for a bill
    """
    bill_id = request.GET.get('bill_id')
    if not bill_id:
        return JsonResponse({'balance': 0})

    try:
        bill = get_object_or_404(Bill, pk=bill_id)
        return JsonResponse({
            'balance': float(bill.outstanding_balance),
            'total': float(bill.rounded_total),
            'received': float(bill.amount_received),
            'subtotal': float(bill.subtotal),
        })
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e), 'detail': 'Check server logs for traceback'}, status=400)

@login_required
def get_party_unbilled_trips(request):
    """
    AJAX endpoint to get unbilled/available trips for a party
    """
    party_id = request.GET.get('party_id')
    bill_id = request.GET.get('bill_id')
    
    if not party_id:
        return JsonResponse({'trips': []})
    
    try:
        # Show trips for this party
        qs = Trip.objects.filter(party_id=party_id)
        
        if bill_id:
            # Include currently selected trips for this bill + unbilled ones
            qs = qs.filter(Q(bills__isnull=True) | Q(bills__id=bill_id))
        else:
            qs = qs.filter(bills__isnull=True)
            
        trips = qs.distinct().order_by('-date', '-created_at')
        
        data = []
        for trip in trips:
            lr_no = trip.lr_no or ''
            discount = 0.0

            # If editing a bill, get the specific LR No or Discount saved for this bill
            if bill_id:
                bill_trip = BillTrip.objects.filter(bill_id=bill_id, trip=trip).first()
                if bill_trip:
                    lr_no = bill_trip.lr_no or ''
                    discount = float(bill_trip.discount or 0)

            data.append({
                'id': trip.id,
                'date': trip.date.strftime('%d %b %Y'),
                'vehicle': trip.vehicle.registration_plate,
                'pickup': trip.pickup_location,
                'delivery': trip.delivery_location,
                'weight': float(trip.weight or 0),
                'rate': float(trip.rate_per_ton or 0),
                'revenue': float(trip.revenue or 0),
                'gst_type': trip.gst_type, # IGST or GST
                'lr_no': lr_no,
                'discount': discount,
            })        
        return JsonResponse({'trips': data})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e), 'detail': 'Check server logs for traceback'}, status=400)


# --- Bill Views ---

class BillListView(LoginRequiredMixin, BaseLedgerPermissionMixin, ListView):
    model = Bill
    template_name = 'ledger/bill_list.html'
    context_object_name = 'bills'
    paginate_by = 25
    
    def get_queryset(self):
        if self.has_driver_permission():
            return Bill.objects.none()
            
        # We use prefetch_related to solve the N+1 problem without complex SQL annotations 
        # that cause 'parser stack overflow' on some SQLite configurations.
        queryset = Bill.objects.all().select_related(
            'party', 'issuer', 'category'
        ).prefetch_related(
            'trips',
            'trips__payment_allocations',
            'financial_records',
            'financial_records__category',
            'bill_trips',
            'bill_trips__trip',
            'adjustment_bills',
            'adjustment_bills__category'
        ).order_by('-date', '-created_at')
        
        # Filter by Issuer (Company Account)
        issuer_id = self.request.GET.get('issuer')
        if issuer_id:
            queryset = queryset.filter(issuer_id=issuer_id)
            
        # Filter by Party
        party_id = self.request.GET.get('party')
        if party_id:
            queryset = queryset.filter(party_id=party_id)

        # Search filter
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(bill_number__icontains=search) |
                Q(party__name__icontains=search) |
                Q(issuer__name__icontains=search)
            )
            
        # Filter by Category (Invoice Type)
        cat_filter = self.request.GET.get('category')
        if cat_filter == 'invoice':
            queryset = queryset.exclude(category__name__in=['Credit Note', 'Debit Note'])
        elif cat_filter == 'credit':
            queryset = queryset.filter(category__name='Credit Note')
        elif cat_filter == 'debit':
            queryset = queryset.filter(category__name='Debit Note')

        # Filter by Payment Status
        status_filter = self.request.GET.get('payment_status')
        if status_filter == 'pending':
            queryset = queryset.filter(outstanding_balance_cached__gt=0)
        elif status_filter == 'paid':
            queryset = queryset.filter(outstanding_balance_cached__lte=0)

        # Filter by Date Range
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
        if end_date:
            queryset = queryset.filter(date__lte=end_date)
            
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['issuers'] = CompanyAccount.objects.all().order_by('name')
        context['parties'] = Party.objects.all().order_by('name')
        context['current_issuer'] = self.request.GET.get('issuer', '')
        context['current_party'] = self.request.GET.get('party', '')
        context['current_status'] = self.request.GET.get('payment_status', '')
        context['search'] = self.request.GET.get('search', '')
        context['start_date'] = self.request.GET.get('start_date', '')
        context['end_date'] = self.request.GET.get('end_date', '')
        return context

class BillCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Bill
    form_class = BillForm
    template_name = 'ledger/bill_form.html'
    permission_required = 'ledger.add_financialrecord'
    success_url = reverse_lazy('bill-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.method == 'GET':
            if 'initial' not in kwargs:
                kwargs['initial'] = {}

            if 'party' in self.request.GET:
                kwargs['initial']['party'] = self.request.GET.get('party')

            if 'trip_ids' in self.request.GET:
                # Handle multiple values for checkboxes
                kwargs['initial']['trips'] = self.request.GET.getlist('trip_ids')
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        # sync_to_ledger is already called in BillForm.save(), 
        # but views might also trigger it for safety/clarity.
        self.object.sync_to_ledger()

        messages.success(self.request, 'Bill created successfully!')

        if 'save_print' in self.request.POST:
            return redirect('bill-detail', pk=self.object.pk)

        return response
class BillUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Bill
    form_class = BillForm
    template_name = 'ledger/bill_form.html'
    permission_required = 'ledger.change_financialrecord'

    def get_success_url(self):
        return reverse_lazy('bill-detail', kwargs={'pk': self.object.pk})
    def form_valid(self, form):
        response = super().form_valid(form)
        # sync_to_ledger is already called in BillForm.save()
        self.object.sync_to_ledger()
        messages.success(self.request, 'Bill updated successfully!')
        
        if 'save_print' in self.request.POST:
            return redirect('bill-detail', pk=self.object.pk)
            
        return response

class BillDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Bill
    template_name = 'ledger/bill_confirm_delete.html'
    permission_required = 'ledger.delete_financialrecord'
    success_url = reverse_lazy('bill-list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bill = self.object
        impact = []
        impact.append(f"The consolidated Ledger Entry (Invoice) for ₹{bill.rounded_total:,.2f} will be DELETED.")
        impact.append(f"{bill.trips.count()} trips will become UNBILLED.")
        
        payments = bill.amount_received
        if payments > 0:
            impact.append(f"₹{payments:,.2f} in payments made against these trips will REMAIN in the system as Payments In/Deductions, protecting your cash balance.")
            
        impact.append("Ledger entry numbers will be automatically re-sequenced to prevent gaps.")
        context['impact_statements'] = impact
        return context

class BillDetailView(LoginRequiredMixin, BaseLedgerPermissionMixin, DetailView):
    model = Bill
    template_name = 'ledger/bill_detail.html'
    context_object_name = 'bill'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bill = self.object
        # Add the same summarized items used in the print view
        invoice_items = group_trips_for_bill(bill)
        context['invoice_items'] = invoice_items
        
        bill_trips = bill.bill_trips.select_related('trip', 'trip__vehicle').order_by('trip__date')
        context['bill_trips'] = bill_trips

        # Detect if we should show Discount or LR columns
        has_discount = False
        if bill.bill_type == 'Standard':
            has_discount = (bill.discount or 0) > 0
        else:
            has_discount = any((bt.discount or 0) > 0 for bt in bill_trips)

        has_lr = False
        if bill.bill_type != 'Standard':
            has_lr = any(bt.lr_no or (bt.trip and bt.trip.lr_no) for bt in bill_trips)

        context['has_discount'] = has_discount
        context['has_lr'] = has_lr
        
        return context

def group_trips_for_bill(bill):
# ... rest of group_trips_for_bill ...
    """
    Groups bill_trips by (Pickup, Delivery, Rate) and returns a list of dictionaries.
    """
    bill_trips = list(bill.bill_trips.select_related('trip', 'trip__vehicle').all())

    # Pre-calculate sort key values
    def get_sort_key(bt):
        trip = bt.trip
        return (
            trip.pickup_location or '',
            trip.delivery_location or '',
            trip.rate_per_ton or 0
        )

    # Sort bill_trips
    bill_trips.sort(key=get_sort_key)

    grouped_items = []

    for key, group in groupby(bill_trips, key=get_sort_key):
        items = list(group)
        pickup, delivery, rate = key

        # Build Description
        if pickup and delivery:
            desc = f"Freight charges from {pickup} to {delivery}"
        elif pickup:
            desc = f"Freight charges from {pickup}"
        elif delivery:
            desc = f"Freight charges to {delivery}"
        else:
            desc = "Transportation Charges"

        total_weight = sum((bt.trip.weight or 0) for bt in items)
        total_discount = sum((bt.discount or 0) for bt in items)
        total_amount = sum((bt.trip.revenue or 0) for bt in items) - total_discount

        grouped_items.append({
            'description': desc,
            'rate': rate,
            'weight': total_weight,
            'discount': total_discount,
            'amount': total_amount,
            'count': len(items),
            'bill_trips': items, # Keep track of actual bill_trips in this group
        })

    return grouped_items

def print_invoice(request, pk):
    """Render print‑optimized invoice using the combined format."""
    return print_combined_bill(request, pk)

def print_annexure(request, pk):
    """Render annexure using the combined format (legacy link support)."""
    return print_combined_bill(request, pk)

def print_combined_bill(request, pk):
    """Render a combined invoice and annexure for printing."""
    bill = get_object_or_404(Bill, pk=pk)

    # For invoice section
    invoice_items = group_trips_for_bill(bill)

    # For annexure
    bill_trips = bill.bill_trips.select_related('trip', 'trip__vehicle').order_by('trip__date')
    date_groups = []
    for date, group in groupby(bill_trips, key=lambda bt: bt.trip.date.date()):
        bt_list = list(group)
        date_groups.append({
            'date': date,
            'bill_trips': bt_list,
            'total_weight': sum(bt.trip.weight or 0 for bt in bt_list),
            'total_amount': sum(bt.trip.revenue or 0 for bt in bt_list),
        })

    # Detect if we should show Discount or LR columns
    has_discount = False
    if bill.bill_type == 'Standard':
        has_discount = (bill.discount or 0) > 0
    else:
        has_discount = any((bt.discount or 0) > 0 for bt in bill_trips)

    has_lr = False
    if bill.bill_type != 'Standard':
        has_lr = any(bt.lr_no or (bt.trip and bt.trip.lr_no) for bt in bill_trips)

    context = {
        'bill': bill,
        'invoice_items': invoice_items,
        'date_groups': date_groups,
        'bill_trips': bill_trips,
        'has_discount': has_discount,
        'has_lr': has_lr,
    }
    return render(request, 'ledger/combined_bill_print.html', context)


@login_required
def party_statement_pdf(request, pk):
    """
    Generates a PDF statement for a party within a date range.
    """
    party = get_object_or_404(Party, pk=pk)
    
    # Get date range from request
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    
    # Defaults
    if not start_date_str:
        # Default to start of current month
        start_date = timezone.now().replace(day=1).date()
    else:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = timezone.now().replace(day=1).date()
            
    if not end_date_str:
        end_date = timezone.now().date()
    else:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = timezone.now().date()

    def format_indian_comma(amount):
        """Formats a number into Indian style commas (e.g., 1,45,140.00)."""
        try:
            val = Decimal(str(amount))
        except (ValueError, TypeError, DecimalException):
            return "0.00"
        
        parts = f"{val:.2f}".split(".")
        whole, decimal = parts[0], parts[1]
        is_negative = whole.startswith("-")
        if is_negative: whole = whole[1:]
        
        if len(whole) <= 3:
            res = whole
        else:
            last_three = whole[-3:]
            remaining = whole[:-3]
            res = ""
            while len(remaining) > 2:
                res = "," + remaining[-2:] + res
                remaining = remaining[:-2]
            res = remaining + res + "," + last_three
        
        if is_negative: res = "-" + res
        return res + "." + decimal

    def format_balance(val):
        formatted_val = format_indian_comma(abs(val))
        if party.party_type == Party.TYPE_DEBTOR:
            if val > 0: return f"{formatted_val}\u00A0Dr"
            elif val < 0: return f"{formatted_val}\u00A0Cr"
        else:
            if val > 0: return f"{formatted_val}\u00A0Cr"
            elif val < 0: return f"{formatted_val}\u00A0Dr"
        return "0.00"

    # 1. Calculate Opening Balance (before start_date)
    opening_bal = party.opening_balance
    
    # Efficiently aggregate totals before the start date
    pre_totals = FinancialRecord.objects.filter(
        party=party,
        date__lt=start_date
    ).select_related('category')
    
    for rec in pre_totals:
        debit = rec.debit_amount or Decimal('0')
        credit = rec.credit_amount or Decimal('0')
        opening_bal += (debit - credit)

    # 2. Get records in range
    records = FinancialRecord.objects.filter(
        party=party,
        date__range=[start_date, end_date]
    ).select_related('category', 'associated_trip', 'associated_bill', 'party').order_by('date', 'created_at')

    # 3. Build statement rows with running balance
    statement_rows = []
    current_running_bal = opening_bal
    total_period_debit = Decimal('0')
    total_period_credit = Decimal('0')
    
    for rec in records:
        # Use model properties for debit/credit from the perspective of the record's primary entity
        debit = rec.debit_amount or Decimal('0')
        credit = rec.credit_amount or Decimal('0')
        
        # Skip records that have neither debit nor credit for the primary party
        if debit == 0 and credit == 0:
            continue

        current_running_bal += (debit - credit)
        total_period_debit += debit
        total_period_credit += credit
            
        # Get reference string
        ref = "-"
        if rec.associated_bill:
            ref = f"INV: {rec.associated_bill.bill_number or 'Draft'}"
        elif rec.associated_trip:
            ref = f"TRP: {rec.associated_trip.trip_number}"
        elif rec.linked_bill:
            ref = f"INV: {rec.linked_bill.bill_number or 'Draft'}"
        elif rec.linked_trip:
            ref = f"TRP: {rec.linked_trip.trip_number}"

        statement_rows.append({
            'date': rec.date,
            'description': rec.description or rec.category.name,
            'reference': ref,
            'debit': debit,
            'credit': credit,
            'balance': current_running_bal,
            'balance_formatted': format_balance(current_running_bal)
        })

    # 4. Render to PDF
    context = {
        'party': party,
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_bal,
        'opening_balance_formatted': format_balance(opening_bal),
        'statement_rows': statement_rows,
        'total_debit': total_period_debit,
        'total_credit': total_period_credit,
        'closing_balance': current_running_bal,
        'closing_balance_formatted': format_balance(current_running_bal),
        'generated_at': timezone.now(),
        'company': CompanyAccount.objects.first(),
        'title': f"Statement of Account - {party.name}",
    }
    
    html = render_to_string('ledger/party_statement_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Statement_{party.name.replace(" ", "_")}_{start_date}.pdf"'
    
    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error generating PDF', status=500)
    return response


@login_required
def account_statement_pdf(request, pk):
    """
    Generates a PDF statement for a Company Account within a date range.
    """
    account = get_object_or_404(CompanyAccount, pk=pk)
    
    # Get date range from request
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    
    # Defaults
    if not start_date_str:
        start_date = timezone.now().replace(day=1).date()
    else:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = timezone.now().replace(day=1).date()
            
    if not end_date_str:
        end_date = timezone.now().date()
    else:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = timezone.now().date()

    def format_balance(val):
        formatted_val = format_indian_comma(abs(val))
        if val > 0: return f"{formatted_val} Dr"
        elif val < 0: return f"{formatted_val} Cr"
        return "0.00"

    # 1. Calculate Opening Balance (before start_date)
    opening_bal = account.opening_balance
    
    pre_records = FinancialRecord.objects.filter(
        account=account,
        date__lt=start_date
    ).select_related('category')
    
    for rec in pre_records:
        if rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            continue # Skip accruals for cash statements
        
        # For Company Account (Asset): Income=Debit (+), Expense=Credit (-)
        if rec.is_income:
            opening_bal += rec.amount
        elif rec.is_expense:
            opening_bal -= rec.amount

    # 2. Get records in range
    records = FinancialRecord.objects.filter(
        account=account,
        date__range=[start_date, end_date]
    ).select_related('category', 'associated_trip', 'associated_bill', 'party').order_by('date', 'created_at')

    # 3. Build statement rows
    statement_rows = []
    current_running_bal = opening_bal
    total_period_debit = Decimal('0')
    total_period_credit = Decimal('0')
    
    for rec in records:
        if rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            continue # Skip accruals
            
        debit = rec.debit_amount or Decimal('0')
        credit = rec.credit_amount or Decimal('0')
            
        current_running_bal += (debit - credit)
        total_period_debit += debit
        total_period_credit += credit
            
        # Get reference string
        ref = "-"
        if rec.associated_bill:
            ref = f"INV: {rec.associated_bill.bill_number or 'Draft'}"
        elif rec.associated_trip:
            ref = f"TRP: {rec.associated_trip.trip_number}"
        
        # Build description
        desc = rec.description or rec.category.name
        if rec.party:
            desc = f"{desc} (Party: {rec.party.name})"

        statement_rows.append({
            'date': rec.date,
            'description': desc,
            'reference': ref,
            'debit': debit,
            'credit': credit,
            'balance': current_running_bal,
            'balance_formatted': format_balance(current_running_bal)
        })

    # 4. Render to PDF
    context = {
        'party': account, # Reusing template variable name for simplicity
        'recipient_name': account.name,
        'recipient_label': 'ACCOUNT',
        'recipient_address': account.address,
        'recipient_gstin': account.gstin,
        'recipient_phone': account.phone_number,
        'recipient_extra': f"Bank: {account.bank_name} - {account.account_number}",
        'title': f"Account Statement - {account.name}",
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_bal,
        'opening_balance_formatted': format_balance(opening_bal),
        'statement_rows': statement_rows,
        'total_debit': total_period_debit,
        'total_credit': total_period_credit,
        'closing_balance': current_running_bal,
        'closing_balance_formatted': format_balance(current_running_bal),
        'generated_at': timezone.now(),
        'company': account, 
    }
    
    html = render_to_string('ledger/party_statement_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Account_Statement_{account.name.replace(" ", "_")}.pdf"'
    
    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error generating PDF', status=500)
    return response


@login_required
def unified_ledger_pdf(request):
    """
    Generates a PDF statement for all Company Accounts combined.
    """
    # Get date range from request
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    
    # Defaults
    if not start_date_str:
        start_date = timezone.now().replace(day=1).date()
    else:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = timezone.now().replace(day=1).date()
            
    if not end_date_str:
        end_date = timezone.now().date()
    else:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = timezone.now().date()

    def format_balance(val):
        formatted_val = format_indian_comma(abs(val))
        if val > 0: return f"{formatted_val} Dr"
        elif val < 0: return f"{formatted_val} Cr"
        return "0.00"

    # 1. Calculate Combined Opening Balance
    opening_bal = CompanyAccount.objects.aggregate(total=Sum('opening_balance'))['total'] or Decimal('0')
    
    pre_records = FinancialRecord.objects.filter(
        date__lt=start_date
    ).select_related('category')
    
    for rec in pre_records:
        if rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            continue # Skip accruals for cash statements
        
        if rec.is_income:
            opening_bal += rec.amount
        elif rec.is_expense:
            opening_bal -= rec.amount

    # 2. Get records in range
    records = FinancialRecord.objects.filter(
        date__range=[start_date, end_date]
    ).select_related('category', 'associated_trip', 'associated_bill', 'party', 'account').order_by('date', 'created_at')

    # 3. Build statement rows
    statement_rows = []
    current_running_bal = opening_bal
    total_period_debit = Decimal('0')
    total_period_credit = Decimal('0')
    
    for rec in records:
        if rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            continue # Skip accruals
            
        debit = rec.debit_amount or Decimal('0')
        credit = rec.credit_amount or Decimal('0')
            
        current_running_bal += (debit - credit)
        total_period_debit += debit
        total_period_credit += credit
            
        # Get reference string
        ref = "-"
        if rec.associated_bill:
            ref = f"INV: {rec.associated_bill.bill_number or 'Draft'}"
        elif rec.associated_trip:
            ref = f"TRP: {rec.associated_trip.trip_number}"
        
        # Build description
        desc = rec.description or rec.category.name
        extra_info = []
        if rec.account:
            extra_info.append(f"ACC: {rec.account.name}")
        if rec.party:
            extra_info.append(f"PRT: {rec.party.name}")
            
        if extra_info:
            desc = f"{desc} ({', '.join(extra_info)})"

        statement_rows.append({
            'date': rec.date,
            'description': desc,
            'reference': ref,
            'debit': debit,
            'credit': credit,
            'balance': current_running_bal,
            'balance_formatted': format_balance(current_running_bal)
        })

    # 4. Render to PDF
    company_main = CompanyAccount.objects.first()
    context = {
        'party': company_main, # Template placeholder
        'recipient_name': "All Company Accounts",
        'recipient_label': 'CONSOLIDATED',
        'recipient_address': "Multi-firm Consolidated Ledger",
        'title': "Unified Ledger Statement",
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_bal,
        'opening_balance_formatted': format_balance(opening_bal),
        'statement_rows': statement_rows,
        'total_debit': total_period_debit,
        'total_credit': total_period_credit,
        'closing_balance': current_running_bal,
        'closing_balance_formatted': format_balance(current_running_bal),
        'generated_at': timezone.now(),
        'company': company_main,
    }
    
    html = render_to_string('ledger/party_statement_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Unified_Ledger_{start_date}.pdf"'
    
    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error generating PDF', status=500)
    return response


@login_required
def get_party_bills(request):
    """
    AJAX endpoint to get bills for a party (excluding adjustment notes)
    Uses prefetching + Python calculation to avoid SQLite parser stack overflow.
    """
    party_id = request.GET.get('party_id')
    unpaid_only = request.GET.get('unpaid_only') == 'true'
    include_bill_id = request.GET.get('include_bill_id')

    if not party_id:
        return JsonResponse({'bills': []})

    try:
        from .models import Bill
        from django.db import models
        
        # We use prefetch_related instead of complex annotations to avoid parser stack overflow
        bills_qs = Bill.objects.filter(
            party_id=party_id
        ).select_related('issuer', 'category').prefetch_related(
            'trips',
            'bill_trips',
            'bill_trips__trip',
            'financial_records',
            'financial_records__category',
            'trips__payment_allocations',
            'adjustment_bills',
            'adjustment_bills__category'
        ).filter(
            models.Q(category__isnull=True) | ~models.Q(category__name__in=['Credit Note', 'Debit Note'])
        ).order_by('-date', '-created_at')

        data = []
        for bill in bills_qs:
            # Python-side calculation using optimized prefetch data
            outstanding = bill.outstanding_balance
            
            # Skip paid bills UNLESS specifically requested to include it (for editing)
            if unpaid_only and outstanding <= 0:
                if not include_bill_id or str(bill.id) != str(include_bill_id):
                    continue
                
            data.append({
                'id': bill.id,
                'label': f"{bill.bill_number or 'Draft'} - {bill.date.strftime('%d/%m/%Y')} (Pending: ₹{outstanding:,.2f})"
            })

        return JsonResponse({'bills': data})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e), 'detail': 'Check server logs for traceback'}, status=400)

@login_required
def get_next_invoice_number(request):
    """
    Returns the next available invoice number for a given issuer via AJAX.
    """
    issuer_id = request.GET.get('issuer_id')
    date_str = request.GET.get('date')
    category_id = request.GET.get('category_id')
    
    if not issuer_id:
        return JsonResponse({'error': 'No issuer ID provided'}, status=400)
    
    from .models import CompanyAccount, Bill, TransactionCategory
    import datetime
    
    issuer = CompanyAccount.objects.filter(pk=issuer_id).first()
    if not issuer:
        return JsonResponse({'error': 'Issuer not found'}, status=404)
    
    # Parse date if provided
    date_obj = None
    if date_str:
        try:
            date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            pass

    category = None
    if category_id:
        category = TransactionCategory.objects.filter(pk=category_id).first()

    # Use the gap-filling logic
    next_no = Bill.get_next_available_no(issuer, date_obj, category)
    
    # Get current prefix based on date_obj or now
    from django.utils import timezone
    dt = date_obj or timezone.now()
    year = dt.year
    
    if category:
        if category.name == 'Credit Note':
            prefix = issuer.cn_prefix.replace("{YYYY}", str(year))
        elif category.name == 'Debit Note':
            prefix = issuer.dn_prefix.replace("{YYYY}", str(year))
        else:
            prefix = issuer.invoice_prefix.replace("{YYYY}", str(year))
    else:
        prefix = issuer.invoice_prefix.replace("{YYYY}", str(year))
    
    return JsonResponse({
        'bill_no': next_no,
        'prefix': prefix
    })


def parse_number_range(range_str):
    """
    Parses a string like "1,3,5-8,12" into a list of integers [1, 3, 5, 6, 7, 8, 12].
    """
    if not range_str:
        return []
    nums = set()
    for part in range_str.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                start, end = part.split('-')
                nums.update(range(int(start), int(end) + 1))
            except (ValueError, TypeError):
                continue
        else:
            try:
                nums.add(int(part))
            except (ValueError, TypeError):
                continue
    return sorted(list(nums))

def get_bulk_invoices_context(request):
    """
    Helper to get the context for multiple invoices based on request filters.
    """
    issuer_id = request.GET.get('issuer')
    party_id = request.GET.get('party')
    category_id = request.GET.get('category_id') 
    category_name = request.GET.get('category') 
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    bill_numbers_str = request.GET.get('bill_numbers')

    if not issuer_id:
        return None, "Please select a Company Account."

    # Logic to determine category
    category = None
    if category_id:
        category = get_object_or_404(TransactionCategory, pk=category_id)
    elif category_name:
        if category_name == 'credit':
            category = TransactionCategory.objects.filter(name='Credit Note').first()
        elif category_name == 'debit':
            category = TransactionCategory.objects.filter(name='Debit Note').first()

    queryset = Bill.objects.filter(issuer_id=issuer_id)
    
    if party_id:
        queryset = queryset.filter(party_id=party_id)
    
    if category:
        queryset = queryset.filter(category=category)
    elif category_name == 'invoice':
         queryset = queryset.exclude(category__name__in=['Credit Note', 'Debit Note'])

    if start_date_str:
        queryset = queryset.filter(date__gte=start_date_str)
    if end_date_str:
        queryset = queryset.filter(date__lte=end_date_str)
    
    if bill_numbers_str:
        bill_nos = parse_number_range(bill_numbers_str)
        if bill_nos:
            queryset = queryset.filter(bill_no__in=bill_nos)
    
    bills = queryset.select_related('party', 'issuer', 'category').prefetch_related(
        'trips', 'bill_trips', 'bill_trips__trip', 'bill_trips__trip__vehicle'
    ).order_by('bill_no', 'date')

    if not bills.exists():
        return None, "No invoices found matching the selected criteria."

    all_bill_contexts = []
    for bill in bills:
        invoice_items = group_trips_for_bill(bill)
        bill_trips = bill.bill_trips.select_related('trip', 'trip__vehicle').order_by('trip__date')
        
        has_discount = False
        if bill.bill_type == 'Standard':
            has_discount = (bill.discount or 0) > 0
        else:
            has_discount = any((bt.discount or 0) > 0 for bt in bill_trips)

        has_lr = False
        if bill.bill_type != 'Standard':
            has_lr = any(bt.lr_no or (bt.trip and bt.trip.lr_no) for bt in bill_trips)

        all_bill_contexts.append({
            'bill': bill,
            'invoice_items': invoice_items,
            'bill_trips': bill_trips,
            'has_discount': has_discount,
            'has_lr': has_lr,
        })

    return {
        'bill_contexts': all_bill_contexts,
        'issuer_id': issuer_id,
        'is_bulk': True,
    }, None

@login_required
def bulk_print_invoices(request):
    """
    Renders an HTML page for browser printing of multiple invoices.
    """
    context, error = get_bulk_invoices_context(request)
    if error:
        messages.error(request, error)
        return redirect('bill-list')
    
    return render(request, 'ledger/bulk_bill_print_html.html', context)

