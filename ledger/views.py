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
import json

from .models import FinancialRecord, Party, Account, TripAllocation, TransactionCategory
from .forms import FinancialRecordForm, PartyForm, AccountForm
from .utils import recalculate_trip_payment_status
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
        
        queryset = FinancialRecord.objects.all()
        
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
    
            ).aggregate(total=Sum('amount'))['total'] or 0
    
            
    
            total_expenses = records.filter(
    
                category__type=TransactionCategory.TYPE_EXPENSE
    
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
                        recalculate_trip_payment_status(trip)
                
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
        
        # Update associated trip status
        if self.object.associated_trip:
            recalculate_trip_payment_status(self.object.associated_trip)

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
        # Store old trip to recalculate if it changes
        old_trip = self.get_object().associated_trip
        
        response = super().form_valid(form)
        
        # Update current associated trip status
        if self.object.associated_trip:
            recalculate_trip_payment_status(self.object.associated_trip)
        
        # If trip was changed, update the old one too
        if old_trip and old_trip != self.object.associated_trip:
            recalculate_trip_payment_status(old_trip)

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
        trip = self.object.associated_trip
        
        response = super().delete(request, *args, **kwargs)
        
        if trip:
            recalculate_trip_payment_status(trip)
                
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
        billed_subquery = Trip.objects.filter(
            party=OuterRef('pk')
        ).values('party').annotate(
            total=Sum(F('weight') * F('rate_per_ton'))
        ).values('total')

        received_subquery = FinancialRecord.objects.filter(
            party=OuterRef('pk'),
            category__type=TransactionCategory.TYPE_INCOME
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
        
        # Get associated trips
        trips = self.object.trip_set.all().order_by('-date')
        context['trips'] = trips
        
        # Calculate Total Billed (Revenue from Trips)
        total_billed = trips.aggregate(
            total=Sum(F('weight') * F('rate_per_ton'), output_field=DecimalField())
        )['total'] or 0
        
        # Get associated financial records
        financial_records = self.object.financial_records.all().order_by('-date')
        context['financial_records'] = financial_records
        
        # Calculate Total Received (Payments from Party)
        total_received = financial_records.filter(
            category__type=TransactionCategory.TYPE_INCOME
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


from django.http import JsonResponse

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