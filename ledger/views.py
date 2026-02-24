"""
Views for Ledger application with permission checks
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Sum, F, DecimalField, Value, Case, When, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from decimal import Decimal, InvalidOperation
from datetime import datetime
import json
from django.http import JsonResponse

from .models import FinancialRecord, Party, Account, TripAllocation, TransactionCategory, Bill, CompanyProfile
from .forms import FinancialRecordForm, PartyForm, AccountForm, BillForm, CompanyProfileForm
from trips.models import Trip


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
        
        return queryset.order_by('-date')
    
    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        context['category_choices'] = TransactionCategory.objects.all()

        context['current_category'] = self.request.GET.get('category', '')

        

        # Calculate totals for filtered records
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
        'monthly_net': monthly_income - monthly_expenses,
        'yearly_income': yearly_income,
        'yearly_expenses': yearly_expenses,
        'yearly_net': yearly_income - yearly_expenses,
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
    paginate_by = 20
    
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
            
        # Correctly calculate totals using Subquery to avoid cross-join multiplication
        # Total Billed = Sum of Invoices
        billed_subquery = FinancialRecord.objects.filter(
            party=OuterRef('pk'),
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).values('party').annotate(
            total=Sum('amount')
        ).values('total')

        # Total Received = Sum of Income Transactions (Payments)
        received_subquery = FinancialRecord.objects.filter(
            party=OuterRef('pk'),
            category__type=TransactionCategory.TYPE_INCOME,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
        ).values('party').annotate(
            total=Sum('amount')
        ).values('total')
        
        queryset = queryset.annotate(
            total_billed=Coalesce(Subquery(billed_subquery, output_field=DecimalField()), Value(0, output_field=DecimalField())),
            total_received=Coalesce(Subquery(received_subquery, output_field=DecimalField()), Value(0, output_field=DecimalField()))
        ).annotate(
            outstanding_balance=F('total_billed') - F('total_received')
        )
        
        return queryset.order_by('name')

class PartyDetailView(LoginRequiredMixin, BaseLedgerPermissionMixin, DetailView):
    """
    Detail view for a party
    """
    model = Party
    template_name = 'ledger/party_detail.html'
    context_object_name = 'party'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get associated trips with annotations (Payment & Billing Info)
        # Use Trip.objects.filter() to ensure we can use the custom Manager methods
        all_trips = Trip.objects.filter(party=self.object).with_payment_info().with_billing_info().order_by('-date')
        context['trips'] = all_trips

        # Get Bills
        context['bills'] = self.object.bills.all().order_by('-date')
        
        # Get associated financial records
        financial_records = self.object.financial_records.all().select_related('category', 'associated_trip', 'associated_bill').order_by('-date')
        context['financial_records'] = financial_records
        
        # Calculate Total Billed (Sum of Invoices)
        # This includes Trip Revenue Records AND Bill GST Records
        total_billed = financial_records.filter(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).aggregate(total=Sum('amount'))['total'] or 0

        # Calculate Total Received (Payments from Party - Transactions Only)
        total_received = financial_records.filter(
            category__type=TransactionCategory.TYPE_INCOME,
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        context['total_billed'] = total_billed
        context['total_received'] = total_received
        context['balance'] = total_billed - total_received
        
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

class AccountListView(LoginRequiredMixin, BaseLedgerPermissionMixin, ListView):
    """
    List view for company accounts
    """
    model = Account
    template_name = 'ledger/account_list.html'
    context_object_name = 'accounts'
    paginate_by = 20
    
    def get_queryset(self):
        # Drivers have no access
        if self.has_driver_permission():
            return Account.objects.none()
            
        return Account.objects.all().order_by('name')

class AccountCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """
    Create view for new account
    """
    model = Account
    form_class = AccountForm
    template_name = 'ledger/account_form.html'
    permission_required = 'ledger.add_financialrecord'
    success_url = reverse_lazy('account-list')
    
    def form_valid(self, form):
        messages.success(self.request, 'Account created successfully!')
        return super().form_valid(form)

class AccountUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    """
    Update view for existing account
    """
    model = Account
    form_class = AccountForm
    template_name = 'ledger/account_form.html'
    permission_required = 'ledger.change_financialrecord'
    success_url = reverse_lazy('account-list')

    def form_valid(self, form):
        messages.success(self.request, 'Account updated successfully!')
        return super().form_valid(form)

class AccountDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for account
    """
    model = Account
    template_name = 'ledger/account_confirm_delete.html'
    permission_required = 'ledger.delete_financialrecord'
    success_url = reverse_lazy('account-list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Account deleted successfully!')
        return super().delete(request, *args, **kwargs)

class AccountDetailView(LoginRequiredMixin, BaseLedgerPermissionMixin, DetailView):
    """
    Detail view for an account (showing transaction history)
    """
    model = Account
    template_name = 'ledger/account_detail.html'
    context_object_name = 'account'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['financial_records'] = self.object.financial_records.all().order_by('-date')
        return context


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
            'label': f"{trip.date.strftime('%d/%m/%Y')} - {trip.vehicle.registration_plate} (Bal: ${trip.outstanding_balance:.2f})",
            'balance': float(trip.outstanding_balance)
        } for trip in trips]
        
        return JsonResponse({'trips': data})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


# --- Bill Views ---

class BillListView(LoginRequiredMixin, BaseLedgerPermissionMixin, ListView):
    model = Bill
    template_name = 'ledger/bill_list.html'
    context_object_name = 'bills'
    paginate_by = 20
    
    def get_queryset(self):
        if self.has_driver_permission():
            return Bill.objects.none()
        return Bill.objects.all().select_related('party').order_by('-date', '-created_at')

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
        # Update GST record after M2M data is saved
        self.object.update_ledger_gst_record()

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
        return reverse_lazy('bill-list')

    def form_valid(self, form):
        response = super().form_valid(form)
        # Update GST record after M2M data is saved
        self.object.update_ledger_gst_record()
        messages.success(self.request, 'Bill updated successfully!')
        return response

class BillDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Bill
    template_name = 'ledger/bill_confirm_delete.html'
    permission_required = 'ledger.delete_financialrecord'
    success_url = reverse_lazy('bill-list')

class BillDetailView(LoginRequiredMixin, BaseLedgerPermissionMixin, DetailView):
    model = Bill
    template_name = 'ledger/bill_detail.html'
    context_object_name = 'bill'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['company_profile'] = CompanyProfile.objects.first()
        return context

class CompanyProfileUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = CompanyProfile
    form_class = CompanyProfileForm
    template_name = 'ledger/company_profile_form.html'
    permission_required = 'ledger.change_financialrecord'
    success_url = reverse_lazy('financial-summary')

    def get_object(self, queryset=None):
        obj, created = CompanyProfile.objects.get_or_create(pk=1)
        return obj

    def form_valid(self, form):
        messages.success(self.request, 'Company Settings updated!')
        return super().form_valid(form)

from django.shortcuts import get_object_or_404, render
from .models import Bill, CompanyProfile
from itertools import groupby
from operator import attrgetter

def group_trips_for_bill(bill):
    """
    Groups trips by (Pickup, Delivery, Rate) and returns a list of dictionaries.
    """
    trips = list(bill.trips.select_related('vehicle').all())

    # Pre-calculate sort key values to avoid repeated attribute access
    def get_sort_key(trip):
        return (
            trip.pickup_location or '',
            trip.delivery_location or '',
            trip.rate_per_ton or 0
        )

    # Sort trips
    trips.sort(key=get_sort_key)

    grouped_items = []

    for key, group in groupby(trips, key=get_sort_key):
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

        total_weight = sum((t.weight or 0) for t in items)
        total_amount = sum((t.revenue or 0) for t in items)

        grouped_items.append({
            'description': desc,
            'rate': rate,
            'weight': total_weight,
            'amount': total_amount,
            'count': len(items),
        })

    return grouped_items

def print_invoice(request, pk):
    """Render print‑optimized invoice."""
    bill = get_object_or_404(Bill, pk=pk)
    company_profile = CompanyProfile.objects.first()

    invoice_items = group_trips_for_bill(bill)

    context = {
        'bill': bill,
        'company_profile': company_profile,
        'invoice_items': invoice_items,
    }
    return render(request, 'ledger/invoice_print.html', context)

def print_annexure(request, pk):
    """Render annexure with trip‑by‑trip details and date‑wise subtotals."""
    bill = get_object_or_404(Bill, pk=pk)
    company_profile = CompanyProfile.objects.first()
    # Fetch trips ordered by date
    trips = bill.trips.select_related('vehicle').order_by('date')
    
    # Group by date and calculate subtotals
    date_groups = []
    for date, group in groupby(trips, key=attrgetter('date')):
        trip_list = list(group)
        date_groups.append({
            'date': date,
            'trips': trip_list,
            'total_weight': sum(t.weight or 0 for t in trip_list),
            'total_amount': sum(t.revenue or 0 for t in trip_list),
        })
    context = {
        'bill': bill,
        'company_profile': company_profile,
        'date_groups': date_groups,
    }
    return render(request, 'ledger/annexure_print.html', context)

def print_combined_bill(request, pk):
    """Render a combined invoice and annexure for printing."""
    bill = get_object_or_404(Bill, pk=pk)
    company_profile = CompanyProfile.objects.first()
    
    # For invoice section
    invoice_items = group_trips_for_bill(bill)

    # For annexure
    trips = bill.trips.select_related('vehicle').order_by('date')
    date_groups = []
    for date, group in groupby(trips, key=attrgetter('date')):
        trip_list = list(group)
        date_groups.append({
            'date': date,
            'trips': trip_list,
            'total_weight': sum(t.weight or 0 for t in trip_list),
            'total_amount': sum(t.revenue or 0 for t in trip_list),
        })

    context = {
        'bill': bill,
        'company_profile': company_profile,
        'invoice_items': invoice_items,
        'date_groups': date_groups,
    }
    return render(request, 'ledger/combined_bill_print.html', context)