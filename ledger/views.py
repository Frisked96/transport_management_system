"""
Views for Ledger application with permission checks
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Sum, F, DecimalField, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import FinancialRecord, Party, Account
from .forms import FinancialRecordForm, PartyForm, AccountForm
from .utils import recalculate_leg_status


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
        # associated_trip removed from form, so no need to init it from URL
        
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
        form.instance.recorded_by = self.request.user
        response = super().form_valid(form)
        
        # Auto-populate associated_trip if legs are selected and belong to the same trip
        # We need to do this after saving to access the many-to-many field
        legs = self.object.associated_legs.all()
        if legs.exists():
            first_trip = legs.first().trip
            # Check if all legs belong to the same trip
            if all(leg.trip == first_trip for leg in legs):
                self.object.associated_trip = first_trip
                self.object.save()
            
            # Update Payment Status of Legs
            if self.object.category in [FinancialRecord.CATEGORY_FREIGHT_INCOME, FinancialRecord.CATEGORY_PARTY_PAYMENT]:
                 for leg in legs:
                     recalculate_leg_status(leg)

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
        # We should probably revert previous amounts if this is an update?
        # This is getting complex. Ideally, we recalculate leg status from scratch based on all records.
        # But for this iteration, let's keep it simple and just apply the *difference* or re-apply?
        # Re-calculating is safer.
        
        response = super().form_valid(form)
        
        # Auto-populate associated_trip if legs are selected and belong to the same trip
        legs = self.object.associated_legs.all()
        if legs.exists():
            first_trip = legs.first().trip
            if all(leg.trip == first_trip for leg in legs):
                self.object.associated_trip = first_trip
                self.object.save()
        
        # Recalculate payment status for ALL legs associated with this record
        if legs.exists() and self.object.category in [FinancialRecord.CATEGORY_FREIGHT_INCOME, FinancialRecord.CATEGORY_PARTY_PAYMENT]:
             for leg in legs:
                 recalculate_leg_status(leg)
        
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
        legs = list(self.object.associated_legs.all())
        is_income = self.object.category in [FinancialRecord.CATEGORY_FREIGHT_INCOME, FinancialRecord.CATEGORY_PARTY_PAYMENT]
        
        response = super().delete(request, *args, **kwargs)
        
        if is_income:
            for leg in legs:
                recalculate_leg_status(leg)
                
        messages.success(self.request, 'Financial record deleted successfully!')
        return response


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
            
        # Annotate with outstanding balance
        # 1. Total Billed (Sum of TripLeg revenue)
        # Note: We use Coalesce to handle None (if no legs)
        
        # 2. Total Received (Sum of FinancialRecords with specific categories)
        # We need to filter the relation sum. 
        # Since Django < 2.0 (or simplistic), conditional aggregation is better.
        # But FilteredRelation is cleaner in modern Django.
        # Let's use Sum with Case/When for 'total_received'
        from django.db.models import Case, When
        
        queryset = queryset.annotate(
            total_billed=Coalesce(
                Sum(F('tripleg__weight') * F('tripleg__price_per_ton'), output_field=DecimalField()), 
                Value(0, output_field=DecimalField())
            ),
            total_received=Coalesce(
                Sum(
                    Case(
                        When(
                            financial_records__category__in=[
                                FinancialRecord.CATEGORY_FREIGHT_INCOME,
                                FinancialRecord.CATEGORY_PARTY_PAYMENT
                            ],
                            then=F('financial_records__amount')
                        ),
                        default=Value(0),
                        output_field=DecimalField()
                    )
                ),
                Value(0, output_field=DecimalField())
            )
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
        
        # Get associated trip legs
        trip_legs = self.object.tripleg_set.all().order_by('-date')
        context['trip_legs'] = trip_legs
        
        # Calculate Total Billed (Revenue from Trip Legs)
        total_billed = trip_legs.aggregate(
            total=Sum(F('weight') * F('price_per_ton'), output_field=DecimalField())
        )['total'] or 0
        
        # Get associated financial records
        financial_records = self.object.financial_records.all().order_by('-date')
        context['financial_records'] = financial_records
        
        # Calculate Total Received (Payments from Party)
        # Includes both FREIGHT_INCOME (Legacy) and PARTY_PAYMENT
        total_received = financial_records.filter(
            category__in=[
                FinancialRecord.CATEGORY_FREIGHT_INCOME,
                FinancialRecord.CATEGORY_PARTY_PAYMENT
            ]
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # Expenses (We paid for them / Other expenses linked to party)
        # These increase the amount they owe us? Or reduce?
        # Typically "Expenses" linked to a party might be billable expenses.
        # If billable, they should ADD to the balance (They owe us).
        # But for now, let's keep it simple: Balance = Billed - Received.
        # Unless 'total_expenses' logic was intended to separate "Our Cost" from "Their Bill".
        
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
    permission_required = 'ledger.add_financialrecord' # Using existing permission for now
    
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
    permission_required = 'ledger.change_financialrecord' # Using existing permission for now
    
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
    permission_required = 'ledger.delete_financialrecord' # Using existing permission for now
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
    permission_required = 'ledger.add_financialrecord' # Using existing permission
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
from trips.models import TripLeg

@login_required
def get_party_unpaid_legs(request):
    """
    AJAX endpoint to get unpaid/partial legs for a party
    """
    party_id = request.GET.get('party_id')
    if not party_id:
        return JsonResponse({'legs': []})
    
    try:
        legs = TripLeg.objects.filter(
            party_id=party_id
        ).exclude(
            payment_status=TripLeg.PAYMENT_STATUS_PAID
        ).order_by('date')
        
        data = [{
            'id': leg.id,
            'label': f"{leg.date.strftime('%d/%m/%Y')} - {leg.pickup_location} to {leg.delivery_location} (Bal: ${leg.outstanding_balance:.2f})",
            'balance': float(leg.outstanding_balance)
        } for leg in legs]
        
        return JsonResponse({'legs': data})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)