"""
Views for Toll and FASTag Ledger application.
Handles Toll Accounts, Recharges, Toll Entries, Bulk Entries, and Statement generation.
"""
from decimal import Decimal
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.forms import modelformset_factory

from ledger.models import TollAccount, TollRecharge, TollEntry, CompanyAccount
from ledger.forms import (
    TollAccountForm, TollRechargeForm, TollEntryForm,
    TollEntryBulkRowForm
)
from fleet.models import Vehicle
from trips.models import Trip


class BaseTollPermissionMixin:
    """Helper to check role permissions for Toll module"""
    def has_driver_profile(self):
        user = self.request.user
        return hasattr(user, 'driver_profile') or user.groups.filter(name='driver').exists()


class TollLedgerView(LoginRequiredMixin, PermissionRequiredMixin, BaseTollPermissionMixin, View):
    """
    Central Toll Ledger Dashboard and Statement View.
    Displays running balance, metric cards, chronological entries, and filters.
    """
    permission_required = 'ledger.view_tollaccount'
    template_name = 'ledger/toll_ledger.html'

    def get(self, request, *args, **kwargs):
        if self.has_driver_profile():
            messages.error(request, 'Access restricted.')
            return redirect('trip-list')

        # Ensure at least one TollAccount exists
        accounts = TollAccount.objects.all().order_by('-is_default', 'name')
        if not accounts.exists():
            default_acc = TollAccount.objects.create(
                name='Primary FASTag Wallet',
                provider='FASTag',
                opening_balance=Decimal('0.00'),
                opening_balance_date=timezone.now().date(),
                is_default=True,
                notes='Default system Toll wallet'
            )
            accounts = TollAccount.objects.all()

        # Selected Toll Account
        account_id = request.GET.get('account')
        if account_id:
            toll_account = get_object_or_404(TollAccount, pk=account_id)
        else:
            toll_account = accounts.filter(is_default=True).first() or accounts.first()

        # Filters
        vehicle_id = request.GET.get('vehicle', '').strip()
        start_date_str = request.GET.get('start_date', '').strip()
        end_date_str = request.GET.get('end_date', '').strip()
        entry_type = request.GET.get('type', 'all').strip()
        search_query = request.GET.get('search', '').strip()

        # Date parsing
        start_date = None
        end_date = None
        if start_date_str:
            try:
                start_date = timezone.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        if end_date_str:
            try:
                end_date = timezone.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        # 1. Base querysets for this account
        recharges_qs = toll_account.recharges.select_related('company_account', 'created_by')
        entries_qs = toll_account.entries.select_related('vehicle', 'associated_trip', 'created_by')

        # 2. Vehicle filter
        selected_vehicle = None
        if vehicle_id:
            try:
                selected_vehicle = Vehicle.objects.get(pk=vehicle_id)
                entries_qs = entries_qs.filter(vehicle=selected_vehicle)
                # When filtered by vehicle, recharges are not vehicle-specific
                if entry_type != 'recharges':
                    recharges_qs = recharges_qs.none()
            except Vehicle.DoesNotExist:
                pass

        # 3. Calculate Period Starting Balance
        # Period opening = opening_balance + sum(prior recharges) - sum(prior entries)
        prior_recharges = Decimal('0.00')
        prior_entries = Decimal('0.00')
        if start_date:
            prior_recharges = toll_account.recharges.filter(date__lt=start_date).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            prior_entries = toll_account.entries.filter(date__lt=start_date).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            if selected_vehicle:
                # If vehicle filtered, opening baseline reflects prior vehicle expenses only
                period_opening_balance = -(toll_account.entries.filter(vehicle=selected_vehicle, date__lt=start_date).aggregate(total=Sum('amount'))['total'] or Decimal('0.00'))
            else:
                period_opening_balance = toll_account.opening_balance + prior_recharges - prior_entries
        else:
            if selected_vehicle:
                period_opening_balance = Decimal('0.00')
            else:
                period_opening_balance = toll_account.opening_balance

        # 4. Filter by Date range
        if start_date:
            recharges_qs = recharges_qs.filter(date__gte=start_date)
            entries_qs = entries_qs.filter(date__gte=start_date)
        if end_date:
            recharges_qs = recharges_qs.filter(date__lte=end_date)
            entries_qs = entries_qs.filter(date__lte=end_date)

        # 5. Search filter
        if search_query:
            recharges_qs = recharges_qs.filter(
                Q(reference_number__icontains=search_query) |
                Q(description__icontains=search_query) |
                Q(company_account__name__icontains=search_query)
            )
            entries_qs = entries_qs.filter(
                Q(toll_plaza__icontains=search_query) |
                Q(transaction_reference__icontains=search_query) |
                Q(description__icontains=search_query) |
                Q(vehicle__registration_plate__icontains=search_query)
            )

        # 6. Type filter
        include_recharges = entry_type in ('all', 'recharges') and not selected_vehicle
        include_entries = entry_type in ('all', 'entries')

        # 7. Merge and sort transactions into Chronological Ledger
        transactions = []
        if include_recharges:
            for r in recharges_qs:
                transactions.append({
                    'id': f"recharge_{r.pk}",
                    'raw_id': r.pk,
                    'date': r.date,
                    'created_at': r.created_at,
                    'type': 'Recharge',
                    'vehicle': None,
                    'party_or_plaza': r.company_account.name,
                    'reference': r.reference_number or '-',
                    'payment_mode': r.payment_mode,
                    'credit': r.amount,
                    'debit': None,
                    'document': r.receipt_document,
                    'notes': r.description,
                    'edit_url': reverse('toll-recharge-update', kwargs={'pk': r.pk}),
                    'delete_url': reverse('toll-recharge-delete', kwargs={'pk': r.pk}),
                    'created_by': r.created_by,
                })

        if include_entries:
            for e in entries_qs:
                transactions.append({
                    'id': f"entry_{e.pk}",
                    'raw_id': e.pk,
                    'date': e.date,
                    'created_at': e.created_at,
                    'type': 'Toll',
                    'vehicle': e.vehicle,
                    'party_or_plaza': e.toll_plaza or '-',
                    'reference': e.transaction_reference or '-',
                    'payment_mode': 'FASTag / Toll Balance',
                    'credit': None,
                    'debit': e.amount,
                    'document': e.receipt_document,
                    'notes': e.description,
                    'edit_url': reverse('toll-entry-update', kwargs={'pk': e.pk}),
                    'delete_url': reverse('toll-entry-delete', kwargs={'pk': e.pk}),
                    'created_by': e.created_by,
                })

        # Sort ascending by date & created_at to calculate running balance accurately
        transactions.sort(key=lambda x: (x['date'], x['created_at']))

        running = period_opening_balance
        for item in transactions:
            if item['credit']:
                running += item['credit']
            if item['debit']:
                running -= item['debit']
            item['running_balance'] = running

        # Metrics for dashboard
        total_period_recharges = sum(t['credit'] for t in transactions if t['credit']) or Decimal('0.00')
        total_period_spent = sum(t['debit'] for t in transactions if t['debit']) or Decimal('0.00')
        closing_balance = running

        # Sort reverse for displaying recent first in the table
        display_transactions = list(reversed(transactions))

        context = {
            'toll_accounts': accounts,
            'current_account': toll_account,
            'selected_vehicle': selected_vehicle,
            'vehicles': Vehicle.objects.all().order_by('registration_plate'),
            'transactions': display_transactions,
            'period_opening_balance': period_opening_balance,
            'closing_balance': closing_balance,
            'current_balance': toll_account.current_balance_cached,
            'initial_starting_balance': toll_account.opening_balance,
            'total_period_recharges': total_period_recharges,
            'total_period_spent': total_period_spent,
            'total_transactions_count': len(transactions),
            'start_date': start_date_str,
            'end_date': end_date_str,
            'current_type': entry_type,
            'search_query': search_query,
        }
        return render(request, self.template_name, context)


# ==========================================
# TOLL ACCOUNT CRUD VIEWS
# ==========================================

class TollAccountListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = TollAccount
    template_name = 'ledger/toll_account_list.html'
    context_object_name = 'accounts'
    permission_required = 'ledger.view_tollaccount'

    def get_queryset(self):
        return TollAccount.objects.all().order_by('-is_default', 'name')


class TollAccountCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = TollAccount
    form_class = TollAccountForm
    template_name = 'ledger/toll_account_form.html'
    permission_required = 'ledger.add_tollaccount'
    success_url = reverse_lazy('toll-ledger')

    def form_valid(self, form):
        messages.success(self.request, f"Toll Account '{form.instance.name}' created successfully.")
        return super().form_valid(form)


class TollAccountUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = TollAccount
    form_class = TollAccountForm
    template_name = 'ledger/toll_account_form.html'
    permission_required = 'ledger.change_tollaccount'
    success_url = reverse_lazy('toll-ledger')

    def form_valid(self, form):
        messages.success(self.request, f"Toll Account '{form.instance.name}' updated successfully.")
        return super().form_valid(form)


class TollAccountDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = TollAccount
    template_name = 'ledger/toll_confirm_delete.html'
    permission_required = 'ledger.delete_tollaccount'
    success_url = reverse_lazy('toll-ledger')

    def delete(self, request, *args, **kwargs):
        account = self.get_object()
        if account.recharges.exists() or account.entries.exists():
            messages.error(request, f"Cannot delete '{account.name}' because it contains recharges or toll entries. Delete the entries first.")
            return redirect('toll-ledger')
        messages.success(request, f"Toll Account '{account.name}' deleted successfully.")
        return super().delete(request, *args, **kwargs)


# ==========================================
# TOLL RECHARGE CRUD VIEWS
# ==========================================

class TollRechargeCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = TollRecharge
    form_class = TollRechargeForm
    template_name = 'ledger/toll_recharge_form.html'
    permission_required = 'ledger.add_tollrecharge'
    success_url = reverse_lazy('toll-ledger')

    def get_initial(self):
        initial = super().get_initial()
        acc_id = self.request.GET.get('account')
        if acc_id:
            try:
                initial['toll_account'] = TollAccount.objects.get(pk=acc_id)
            except TollAccount.DoesNotExist:
                pass
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, f"Top-up of ₹{self.object.amount:,.2f} recorded successfully from {self.object.company_account.name}.")
        return response


class TollRechargeUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = TollRecharge
    form_class = TollRechargeForm
    template_name = 'ledger/toll_recharge_form.html'
    permission_required = 'ledger.change_tollrecharge'
    success_url = reverse_lazy('toll-ledger')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Top-up entry updated successfully.')
        return response


class TollRechargeDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = TollRecharge
    template_name = 'ledger/toll_confirm_delete.html'
    permission_required = 'ledger.delete_tollrecharge'
    success_url = reverse_lazy('toll-ledger')

    def delete(self, request, *args, **kwargs):
        recharge = self.get_object()
        amt = recharge.amount
        acc_name = recharge.company_account.name
        response = super().delete(request, *args, **kwargs)
        messages.success(request, f"Top-up of ₹{amt:,.2f} deleted. Company Account {acc_name} balance restored.")
        return response


# ==========================================
# TOLL ENTRY CRUD VIEWS
# ==========================================

class TollEntryCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = TollEntry
    form_class = TollEntryForm
    template_name = 'ledger/toll_entry_form.html'
    permission_required = 'ledger.add_tollentry'
    success_url = reverse_lazy('toll-ledger')

    def get_initial(self):
        initial = super().get_initial()
        acc_id = self.request.GET.get('account')
        vehicle_id = self.request.GET.get('vehicle')
        if acc_id:
            try:
                initial['toll_account'] = TollAccount.objects.get(pk=acc_id)
            except TollAccount.DoesNotExist:
                pass
        if vehicle_id:
            try:
                initial['vehicle'] = Vehicle.objects.get(pk=vehicle_id)
            except Vehicle.DoesNotExist:
                pass
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, f"Toll entry of ₹{self.object.amount:,.2f} recorded for {self.object.vehicle.registration_plate}.")
        return response


class TollEntryUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = TollEntry
    form_class = TollEntryForm
    template_name = 'ledger/toll_entry_form.html'
    permission_required = 'ledger.change_tollentry'
    success_url = reverse_lazy('toll-ledger')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Toll entry updated successfully.')
        return response


class TollEntryDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = TollEntry
    template_name = 'ledger/toll_confirm_delete.html'
    permission_required = 'ledger.delete_tollentry'
    success_url = reverse_lazy('toll-ledger')

    def delete(self, request, *args, **kwargs):
        entry = self.get_object()
        amt = entry.amount
        plate = entry.vehicle.registration_plate
        response = super().delete(request, *args, **kwargs)
        messages.success(request, f"Toll entry of ₹{amt:,.2f} for {plate} deleted. Toll balance restored.")
        return response


# ==========================================
# BULK TOLL ENTRY VIEW
# ==========================================

class TollEntryBulkCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Spreadsheet-style multi-row form for quick entry of multiple toll charges at once.
    """
    permission_required = 'ledger.add_tollentry'
    template_name = 'ledger/toll_entry_bulk_form.html'

    def get(self, request, *args, **kwargs):
        accounts = TollAccount.objects.all().order_by('-is_default', 'name')
        default_account = accounts.filter(is_default=True).first() or accounts.first()
        vehicles = Vehicle.objects.all().order_by('registration_plate')

        context = {
            'accounts': accounts,
            'default_account': default_account,
            'vehicles': vehicles,
            'today': timezone.now().date().isoformat(),
        }
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        account_id = request.POST.get('toll_account')
        toll_account = get_object_or_404(TollAccount, pk=account_id)

        dates = request.POST.getlist('date[]')
        vehicles = request.POST.getlist('vehicle[]')
        amounts = request.POST.getlist('amount[]')
        plazas = request.POST.getlist('toll_plaza[]')
        refs = request.POST.getlist('reference[]')
        descriptions = request.POST.getlist('description[]')

        entries_to_create = []
        errors = []

        valid_veh_ids = [int(v.strip()) for v in vehicles if v and v.strip().isdigit()]
        vehicles_by_id = Vehicle.objects.in_bulk(valid_veh_ids)

        for i in range(len(amounts)):
            amt_str = amounts[i].strip() if i < len(amounts) else ''
            veh_id = vehicles[i].strip() if i < len(vehicles) else ''
            date_str = dates[i].strip() if i < len(dates) else ''
            plaza = plazas[i].strip() if i < len(plazas) else ''
            ref = refs[i].strip() if i < len(refs) else ''
            desc = descriptions[i].strip() if i < len(descriptions) else ''

            # Skip completely empty rows
            if not amt_str and not veh_id:
                continue

            # Validate amount
            try:
                amt = Decimal(amt_str)
                if amt <= 0:
                    errors.append(f"Row {i+1}: Amount must be positive.")
                    continue
            except Exception:
                errors.append(f"Row {i+1}: Invalid amount '{amt_str}'.")
                continue

            # Validate vehicle
            try:
                vehicle = vehicles_by_id.get(int(veh_id))
                if not vehicle:
                    raise ValueError
            except (ValueError, TypeError):
                errors.append(f"Row {i+1}: Please select a valid vehicle.")
                continue

            # Validate date
            try:
                entry_date = timezone.datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else timezone.now().date()
            except ValueError:
                entry_date = timezone.now().date()

            entries_to_create.append(
                TollEntry(
                    toll_account=toll_account,
                    vehicle=vehicle,
                    date=entry_date,
                    amount=amt,
                    toll_plaza=plaza,
                    transaction_reference=ref,
                    description=desc,
                    created_by=request.user
                )
            )

        if errors:
            for err in errors:
                messages.error(request, err)
            return redirect('toll-entry-bulk-create')

        if not entries_to_create:
            messages.warning(request, "No rows entered.")
            return redirect('toll-entry-bulk-create')

        with transaction.atomic():
            TollEntry.objects.bulk_create(entries_to_create)
            toll_account.refresh_balance()

        total_sum = sum(e.amount for e in entries_to_create)
        messages.success(request, f"Successfully created {len(entries_to_create)} toll entries totaling ₹{total_sum:,.2f}.")
        return redirect('toll-ledger')


# ==========================================
# STATEMENT PRINT VIEW
# ==========================================

@login_required
@permission_required('ledger.view_tollaccount', raise_exception=True)
def toll_statement_print(request):
    """
    Clean, print-ready HTML view for toll ledger statements.
    """
    account_id = request.GET.get('account')
    toll_account = get_object_or_404(TollAccount, pk=account_id) if account_id else (TollAccount.objects.filter(is_default=True).first() or TollAccount.objects.first())
    
    vehicle_id = request.GET.get('vehicle', '').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()
    
    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = timezone.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError: pass
    if end_date_str:
        try:
            end_date = timezone.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError: pass

    recharges_qs = toll_account.recharges.all()
    entries_qs = toll_account.entries.select_related('vehicle').all()

    selected_vehicle = None
    if vehicle_id:
        try:
            selected_vehicle = Vehicle.objects.get(pk=vehicle_id)
            entries_qs = entries_qs.filter(vehicle=selected_vehicle)
            recharges_qs = recharges_qs.none()
        except Vehicle.DoesNotExist: pass

    # Opening balance calculation
    if start_date:
        prior_recharges = toll_account.recharges.filter(date__lt=start_date).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        prior_entries = toll_account.entries.filter(date__lt=start_date).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        if selected_vehicle:
            period_opening = -(toll_account.entries.filter(vehicle=selected_vehicle, date__lt=start_date).aggregate(total=Sum('amount'))['total'] or Decimal('0.00'))
        else:
            period_opening = toll_account.opening_balance + prior_recharges - prior_entries
    else:
        period_opening = Decimal('0.00') if selected_vehicle else toll_account.opening_balance

    if start_date:
        recharges_qs = recharges_qs.filter(date__gte=start_date)
        entries_qs = entries_qs.filter(date__gte=start_date)
    if end_date:
        recharges_qs = recharges_qs.filter(date__lte=end_date)
        entries_qs = entries_qs.filter(date__lte=end_date)

    items = []
    if not selected_vehicle:
        for r in recharges_qs:
            items.append({
                'date': r.date,
                'created_at': r.created_at,
                'type': 'Top-up',
                'description': f"Top-up from {r.company_account.name} (Ref: {r.reference_number or '-'})",
                'vehicle': '-',
                'credit': r.amount,
                'debit': Decimal('0.00')
            })
    for e in entries_qs:
        items.append({
            'date': e.date,
            'created_at': e.created_at,
            'type': 'Toll',
            'description': f"{e.toll_plaza or 'Toll Plaza'} (Slip: {e.transaction_reference or '-'})",
            'vehicle': e.vehicle.registration_plate,
            'credit': Decimal('0.00'),
            'debit': e.amount
        })

    items.sort(key=lambda x: (x['date'], x['created_at']))

    running = period_opening
    for item in items:
        running = running + item['credit'] - item['debit']
        item['balance'] = running

    context = {
        'account': toll_account,
        'selected_vehicle': selected_vehicle,
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': period_opening,
        'closing_balance': running,
        'items': items,
        'now': timezone.now(),
    }
    return render(request, 'ledger/toll_statement_print.html', context)
