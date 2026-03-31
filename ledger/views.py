"""
Views for Ledger application with permission checks
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Sum, F, DecimalField, Value, Case, When, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from decimal import Decimal, InvalidOperation
from datetime import datetime
import json
from django.http import JsonResponse, HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from itertools import groupby
from operator import attrgetter

from .models import FinancialRecord, Party, CompanyAccount, TripAllocation, TransactionCategory, Bill
from .forms import FinancialRecordForm, PartyForm, CompanyAccountForm, BillForm
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

    # Calculate GST portion from Final Bills
    from .models import Bill
    monthly_gst = sum(bill.gst_amount for bill in Bill.objects.filter(
        status=Bill.STATUS_FINAL,
        date__month=current_month,
        date__year=current_year
    ))
    yearly_gst = sum(bill.gst_amount for bill in Bill.objects.filter(
        status=Bill.STATUS_FINAL,
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
        # Total Revenue = Sum of all Invoices (Trip Payment + Bill GST)
        revenue_subquery = FinancialRecord.objects.filter(
            party=OuterRef('pk'),
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).values('party').annotate(
            total=Sum('amount')
        ).values('total')

        # Total Billed = Sum of Formal Bills (Records linked to a Bill or Billed Trip)
        billed_subquery = FinancialRecord.objects.filter(
            party=OuterRef('pk'),
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).filter(
            Q(associated_bill__isnull=False) | Q(associated_trip__bills__isnull=False)
        ).values('party').annotate(
            total=Sum('amount')
        ).values('total')

        # Total Received = Sum of Income Transactions (Payments) + Deductions
        # We include Deductions here because they reduce the Party's outstanding balance
        received_subquery = FinancialRecord.objects.filter(
            party=OuterRef('pk'),
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
        ).filter(
            Q(category__type=TransactionCategory.TYPE_INCOME) | Q(category__name='Deductions')
        ).values('party').annotate(
            total=Sum('amount')
        ).values('total')

        queryset = queryset.annotate(
            total_revenue=Coalesce(Subquery(revenue_subquery, output_field=DecimalField()), Value(0, output_field=DecimalField())),
            total_billed=Coalesce(Subquery(billed_subquery, output_field=DecimalField()), Value(0, output_field=DecimalField())),
            total_received=Coalesce(Subquery(received_subquery, output_field=DecimalField()), Value(0, output_field=DecimalField()))
        ).annotate(
            outstanding_balance=F('opening_balance') + F('total_revenue') - F('total_received')
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
        
        # Calculate Total Revenue (Sum of all Invoices: Trip Payment + Bill GST)
        invoiced_amount = financial_records.filter(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        total_revenue = self.object.opening_balance + invoiced_amount

        # Calculate Total Billed (Sum of Formal Bills: Linked to a Bill or Billed Trip)
        total_billed = financial_records.filter(
            record_type=FinancialRecord.RECORD_TYPE_INVOICE
        ).filter(
            Q(associated_bill__isnull=False) | Q(associated_trip__bills__isnull=False)
        ).distinct().aggregate(total=Sum('amount'))['total'] or 0

        # Calculate Total Received (Payments from Party + Deductions)
        total_received = financial_records.filter(
            record_type=FinancialRecord.RECORD_TYPE_TRANSACTION
        ).filter(
            Q(category__type=TransactionCategory.TYPE_INCOME) | Q(category__name='Deductions')
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        context['total_revenue'] = total_revenue
        context['total_billed'] = total_billed
        context['total_received'] = total_received
        context['balance'] = total_revenue - total_received # Total Outstanding
        
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
    paginate_by = 20
    
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
            
        context['financial_records'] = records.order_by('-date')
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

@login_required
def get_bill_balance(request):
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
            'total': float(bill.total_amount if bill.status == Bill.STATUS_FINAL else bill.subtotal),
            'received': float(bill.amount_received)
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

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
        qs = Trip.objects.filter(party_id=party_id).exclude(status='Cancelled')
        
        if bill_id:
            # Include currently selected trips for this bill + unbilled ones
            qs = qs.filter(Q(bills__isnull=True) | Q(bills__id=bill_id))
        else:
            qs = qs.filter(bills__isnull=True)
            
        trips = qs.distinct().order_by('-date')
        
        data = [{
            'id': trip.id,
            'date': trip.date.strftime('%d %b %Y'),
            'vehicle': trip.vehicle.registration_plate,
            'pickup': trip.pickup_location,
            'delivery': trip.delivery_location,
            'weight': float(trip.weight or 0),
            'rate': float(trip.rate_per_ton or 0),
            'revenue': float(trip.revenue or 0),
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
        return reverse_lazy('bill-list')

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

class BillDetailView(LoginRequiredMixin, BaseLedgerPermissionMixin, DetailView):
    model = Bill
    template_name = 'ledger/bill_detail.html'
    context_object_name = 'bill'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add the same summarized items used in the print view
        context['invoice_items'] = group_trips_for_bill(self.object)
        context['bill_trips'] = self.object.bill_trips.select_related('trip', 'trip__vehicle').order_by('trip__date')
        return context

def group_trips_for_bill(bill):
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
        total_amount = sum((bt.trip.revenue or 0) for bt in items)

        grouped_items.append({
            'description': desc,
            'rate': rate,
            'weight': total_weight,
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

    context = {
        'bill': bill,
        'invoice_items': invoice_items,
        'date_groups': date_groups,
        'bill_trips': bill_trips,
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

    # 1. Calculate Opening Balance (before start_date)
    opening_bal = party.opening_balance
    
    # Add all transactions before start_date
    pre_records = FinancialRecord.objects.filter(
        party=party,
        date__lt=start_date
    ).select_related('category')
    
    for rec in pre_records:
        if rec.is_income or rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            opening_bal += rec.amount
        else:
            opening_bal -= rec.amount

    # 2. Get records in range
    records = FinancialRecord.objects.filter(
        party=party,
        date__range=[start_date, end_date]
    ).select_related('category', 'associated_trip', 'associated_bill').order_by('date', 'created_at')

    # 3. Build statement rows with running balance
    statement_rows = []
    current_running_bal = opening_bal
    
    for rec in records:
        debit = 0
        credit = 0
        
        # In a typical ledger: 
        # DEBIT = Increases Asset/Decreases Liability (For us: Revenue/Invoice increases what they owe us)
        # CREDIT = Decreases Asset/Increases Liability (For us: Payment decreases what they owe us)
        
        if rec.is_income or rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            debit = rec.amount
            current_running_bal += debit
        else:
            credit = rec.amount
            current_running_bal -= credit
            
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
            'balance': current_running_bal
        })

    # 4. Render to PDF
    context = {
        'party': party,
        'recipient_name': party.name,
        'recipient_address': party.address,
        'recipient_gstin': party.gstin,
        'recipient_phone': party.phone_number,
        'title': f"Statement of Account - {party.name}",
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_bal,
        'statement_rows': statement_rows,
        'closing_balance': current_running_bal,
        'generated_at': timezone.now(),
        'company': CompanyAccount.objects.first(), # Header info
    }
    
    template = get_template('ledger/statement_pdf.html')
    html = template.render(context)
    
    response = HttpResponse(content_type='application/pdf')
    # Use inline for testing, attachment for production
    response['Content-Disposition'] = f'inline; filename="Statement_{party.name.replace(" ", "_")}_{start_date}.pdf"'
    
    # Create PDF
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

    # 1. Calculate Opening Balance (before start_date)
    opening_bal = account.opening_balance
    
    pre_records = FinancialRecord.objects.filter(
        account=account,
        date__lt=start_date
    ).select_related('category')
    
    for rec in pre_records:
        if rec.is_income or rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            opening_bal += rec.amount
        else:
            opening_bal -= rec.amount

    # 2. Get records in range
    records = FinancialRecord.objects.filter(
        account=account,
        date__range=[start_date, end_date]
    ).select_related('category', 'associated_trip', 'associated_bill', 'party').order_by('date', 'created_at')

    # 3. Build statement rows
    statement_rows = []
    current_running_bal = opening_bal
    
    for rec in records:
        debit = 0
        credit = 0
        
        if rec.is_income or rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            debit = rec.amount
            current_running_bal += debit
        else:
            credit = rec.amount
            current_running_bal -= credit
            
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
            'balance': current_running_bal
        })

    # 4. Render to PDF
    context = {
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
        'statement_rows': statement_rows,
        'closing_balance': current_running_bal,
        'generated_at': timezone.now(),
        'company': account, # This account is the company
    }
    
    template = get_template('ledger/statement_pdf.html')
    html = template.render(context)
    
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

    # 1. Calculate Combined Opening Balance
    opening_bal = CompanyAccount.objects.aggregate(total=Sum('opening_balance'))['total'] or Decimal('0')
    
    pre_records = FinancialRecord.objects.filter(
        date__lt=start_date
    ).select_related('category')
    
    for rec in pre_records:
        if rec.is_income or rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            opening_bal += rec.amount
        else:
            opening_bal -= rec.amount

    # 2. Get records in range
    records = FinancialRecord.objects.filter(
        date__range=[start_date, end_date]
    ).select_related('category', 'associated_trip', 'associated_bill', 'party', 'account').order_by('date', 'created_at')

    # 3. Build statement rows
    statement_rows = []
    current_running_bal = opening_bal
    
    for rec in records:
        debit = 0
        credit = 0
        
        if rec.is_income or rec.record_type == FinancialRecord.RECORD_TYPE_INVOICE:
            debit = rec.amount
            current_running_bal += debit
        else:
            credit = rec.amount
            current_running_bal -= credit
            
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
            'balance': current_running_bal
        })

    # 4. Render to PDF
    company_main = CompanyAccount.objects.first()
    context = {
        'recipient_name': "All Company Accounts",
        'recipient_label': 'CONSOLIDATED',
        'recipient_address': "Multi-firm Consolidated Ledger",
        'title': "Unified Ledger Statement",
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_bal,
        'statement_rows': statement_rows,
        'closing_balance': current_running_bal,
        'generated_at': timezone.now(),
        'company': company_main,
    }
    
    template = get_template('ledger/statement_pdf.html')
    html = template.render(context)
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Unified_Ledger_{start_date}.pdf"'
    
    pisa_status = pisa.CreatePDF(html, dest=response)
    if pisa_status.err:
        return HttpResponse('Error generating PDF', status=500)
    return response
