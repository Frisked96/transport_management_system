"""
FinancialRecord CRUD views and Financial Summary dashboard.
"""
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum, F, DecimalField, Value, Case, When, OuterRef, Count, Max
from django.utils import timezone
from decimal import Decimal, InvalidOperation, DecimalException
from datetime import datetime
import json
from django.http import JsonResponse, HttpResponse

from ledger.models import FinancialRecord, FinancialVoucher, Party, CompanyAccount, TripAllocation, BillAllocation, TransactionCategory, Bill, BillTrip, TollRecharge
from ledger.forms import FinancialRecordForm
from trips.models import Trip
from ledger.services import BillingService, TripFinancialService
from ledger.views.base import BaseLedgerPermissionMixin
from ledger.utils import format_indian_comma, format_balance
from transport_mgmt.utils import get_local_iso_timestamp

class FinancialRecordListView(LoginRequiredMixin, PermissionRequiredMixin, BaseLedgerPermissionMixin, ListView):
    """
    List view for financial records with permission-based filtering.
    Acts as a Financial Dashboard.
    """
    model = FinancialRecord
    template_name = 'ledger/financialrecord_list.html'
    context_object_name = 'financial_records'
    permission_required = 'ledger.can_view_financial_records'
    paginate_by = 25
    
    def get_queryset(self):
        """Filter financial records based on user permissions"""
        # Drivers have no access to financial records
        if self.has_driver_profile():
            return FinancialRecord.objects.none()
        
        queryset = FinancialRecord.objects.all().select_related(
            'category', 'party', 'account', 'driver__user', 'voucher',
            'associated_trip__vehicle', 'associated_trip__route',
            'associated_bill__party', 'associated_bill__category',
            'associated_tyre', 'associated_document_renewal__document__vehicle'
        ).prefetch_related(
            'allocations__trip__vehicle', 'bill_allocations__bill'
        )
        
        # Category filter
        category_id = self.request.GET.get('category')
        if category_id:
            queryset = queryset.filter(category_id=category_id)

        # Account filter
        account_id = self.request.GET.get('account')
        if account_id:
            queryset = queryset.filter(account_id=account_id)
        
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
        
        # General text search filter
        search = self.request.GET.get('search', '').strip()
        if search:
            search_q = (
                Q(description__icontains=search) |
                Q(party__name__icontains=search) |
                Q(account__name__icontains=search) |
                Q(category__name__icontains=search) |
                Q(voucher__voucher_number__icontains=search) |
                Q(associated_trip__trip_number__icontains=search) |
                Q(associated_trip__lr_no__icontains=search) |
                Q(associated_trip__vehicle__registration_plate__icontains=search) |
                Q(associated_bill__bill_number__icontains=search) |
                Q(allocations__trip__trip_number__icontains=search) |
                Q(bill_allocations__bill__bill_number__icontains=search)
            )
            if search.isdigit():
                search_q |= Q(entry_number=search) | Q(id=search)
            queryset = queryset.filter(search_q).distinct()

        # Strict newest to oldest ordering
        return queryset.order_by('-date', '-id')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['category_choices'] = TransactionCategory.objects.all()
        context['current_category'] = self.request.GET.get('category', '')
        context['account_choices'] = CompanyAccount.objects.all().order_by('name')
        context['current_account'] = self.request.GET.get('account', '')
        context['party_choices'] = Party.objects.all().order_by('name')
        context['current_party'] = self.request.GET.get('party', '')
        context['current_search'] = self.request.GET.get('search', '').strip()

        # 1. Financial Records Totals (Filtered)
        filtered_non_invoices = self.get_queryset().exclude(record_type='Invoice')
        totals = filtered_non_invoices.aggregate(
            tds=Sum('amount', filter=Q(category__name='TDS')),
            income_all=Sum('amount', filter=Q(category__type=TransactionCategory.TYPE_INCOME)),
            expenses=Sum('amount', filter=Q(category__type=TransactionCategory.TYPE_EXPENSE)),
        )

        total_tds = totals['tds'] or Decimal('0')
        total_income_all = totals['income_all'] or Decimal('0')
        total_expenses = totals['expenses'] or Decimal('0')
        total_income = total_income_all - total_tds

        context['total_tds'] = total_tds
        context['total_income'] = total_income
        context['total_expenses'] = total_expenses
        context['net_total'] = total_income_all - total_expenses

        # 2. Party Outstanding Dashboard (Optimized db-level aggregation)
        total_outstanding = Party.objects.filter(
            party_type=Party.TYPE_DEBTOR,
            current_balance_cached__gt=0
        ).aggregate(total=Sum('current_balance_cached'))['total'] or Decimal('0')

        # Top 10 parties with highest balances (both Debtors and Creditors by magnitude of outstanding)
        from django.db.models.functions import Abs
        top_parties = list(
            Party.objects.exclude(current_balance_cached=0).order_by(Abs('current_balance_cached').desc())[:10]
        )
        top_party_ids = [p.id for p in top_parties]

        last_payments = dict(
            FinancialRecord.objects.filter(
                party_id__in=top_party_ids
            ).exclude(
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).exclude(
                category__name__in=['Credit Note', 'Debit Note', 'Opening Balance']
            ).values('party_id').annotate(
                last_date=Max('date')
            ).values_list('party_id', 'last_date')
        )

        party_dashboard = []
        for p in top_parties:
            party_dashboard.append({
                'id': p.id,
                'name': p.name,
                'balance': p.current_balance_cached,
                'party_type': p.party_type,
                'last_payment_date': last_payments.get(p.id)
            })

        context['total_outstanding'] = total_outstanding
        context['party_dashboard'] = party_dashboard
        context['all_parties_dashboard'] = party_dashboard
        
        return context


class FinancialRecordDetailView(LoginRequiredMixin, PermissionRequiredMixin, BaseLedgerPermissionMixin, DetailView):
    """
    Detail view for a single financial record
    """
    model = FinancialRecord
    template_name = 'ledger/financialrecord_detail.html'
    context_object_name = 'record'
    permission_required = 'ledger.can_view_financial_records'
    
    def get_queryset(self):
        """Ensure user has permission to view financial records"""
        # Drivers cannot view financial records
        if self.has_driver_permission():
            return FinancialRecord.objects.none()
        
        return FinancialRecord.objects.all().select_related(
            'category', 'party', 'account', 'driver', 'associated_trip', 'associated_bill', 'associated_tyre', 'associated_document_renewal__document__vehicle', 'recorded_by'
        ).prefetch_related(
            'allocations__trip', 'bill_allocations__bill', 'associated_bill__original_bill'
        )


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
                
        account_id = self.request.GET.get('account')
        if account_id:
            try:
                account = CompanyAccount.objects.get(pk=account_id)
                initial['account'] = account
            except CompanyAccount.DoesNotExist:
                pass

        trip_id = self.request.GET.get('associated_trip')
        if trip_id:
            try:
                trip = Trip.objects.get(pk=trip_id)
                initial['associated_trip'] = trip
                if 'party' not in initial and trip.party:
                    initial['party'] = trip.party
            except Trip.DoesNotExist:
                pass

        bill_id = self.request.GET.get('associated_bill')
        if bill_id:
            try:
                bill = Bill.objects.get(pk=bill_id)
                initial['associated_bill'] = bill
                if 'party' not in initial and bill.party:
                    initial['party'] = bill.party
                if 'category' not in initial and bill.party and bill.party.party_type == Party.TYPE_CREDITOR:
                    p_out = TransactionCategory.objects.filter(name='Payment Out').first()
                    if p_out:
                        initial['category'] = p_out
            except Bill.DoesNotExist:
                pass

        category_id = self.request.GET.get('category')
        if category_id:
            try:
                category = TransactionCategory.objects.get(pk=category_id)
                initial['category'] = category
            except (TransactionCategory.DoesNotExist, ValueError):
                pass
        elif trip_id:
            try:
                initial['category'] = TransactionCategory.objects.get(name='Trip Payment')
            except TransactionCategory.DoesNotExist:
                pass

        if 'date' in self.request.GET:
            initial['date'] = self.request.GET.get('date')
        
        if 'amount' in self.request.GET:
            initial['amount'] = self.request.GET.get('amount')
            
        if 'description' in self.request.GET:
            initial['description'] = self.request.GET.get('description')
                
        return initial

    def form_valid(self, form):
        from decimal import InvalidOperation, Decimal
        import json

        distribution_json = form.cleaned_data.get('payment_distribution')
        bill_distribution_json = form.cleaned_data.get('bill_distribution')
        tds_amount = form.cleaned_data.get('tds_amount')
        deduction_amount = form.cleaned_data.get('deduction_amount')
        deduction_notes = form.cleaned_data.get('deduction_notes') or ''

        has_tds = bool(tds_amount and tds_amount > 0)
        has_deduction = bool(deduction_amount and deduction_amount > 0)

        # Determine voucher type and generate unified voucher reference
        if distribution_json:
            v_type = FinancialVoucher.VOUCHER_TYPE_TRIP_PAYMENT
        elif bill_distribution_json:
            v_type = FinancialVoucher.VOUCHER_TYPE_BILL_PAYMENT
        elif form.cleaned_data.get('associated_trip'):
            v_type = FinancialVoucher.VOUCHER_TYPE_TRIP_PAYMENT
        elif form.cleaned_data.get('associated_bill'):
            v_type = FinancialVoucher.VOUCHER_TYPE_BILL_PAYMENT
        elif form.cleaned_data.get('category') and form.cleaned_data['category'].name == 'Payment Out':
            v_type = FinancialVoucher.VOUCHER_TYPE_PAYMENT_OUT
        elif form.cleaned_data.get('category') and form.cleaned_data['category'].type == TransactionCategory.TYPE_EXPENSE:
            v_type = FinancialVoucher.VOUCHER_TYPE_EXPENSE
        elif form.cleaned_data.get('driver'):
            v_type = FinancialVoucher.VOUCHER_TYPE_DRIVER_SETTLEMENT
        else:
            v_type = FinancialVoucher.VOUCHER_TYPE_DIRECT

        voucher_ref = FinancialVoucher.generate_voucher_number(form.cleaned_data['date'])
        voucher = FinancialVoucher.objects.create(
            voucher_number=voucher_ref,
            date=form.cleaned_data['date'],
            voucher_type=v_type,
            party=form.cleaned_data.get('party'),
            account=form.cleaned_data.get('account'),
            driver=form.cleaned_data.get('driver'),
            created_by=self.request.user,
            verification_status=FinancialVoucher.STATUS_PENDING,
            description=form.cleaned_data.get('description') or '',
            document_ref=form.cleaned_data.get('document_ref'),
            audit_log=[{
                'timestamp': get_local_iso_timestamp(),
                'action': 'created',
                'user': self.request.user.username,
                'details': {
                    'voucher_type': v_type,
                    'reference': voucher_ref,
                }
            }]
        )

        # 1. Multi-Trip Distribution Flow
        if distribution_json:
            try:
                with transaction.atomic():
                    distribution_data = json.loads(distribution_json)
                    if not isinstance(distribution_data, list) or len(distribution_data) == 0:
                        raise ValueError("No trip allocation data found")

                    total_payment = sum(Decimal(str(item.get('payment', item.get('amount', 0)) or 0)) for item in distribution_data)
                    total_tds = sum(Decimal(str(item.get('tds', 0) or 0)) for item in distribution_data)
                    total_deduction = sum(Decimal(str(item.get('deduction', 0) or 0)) for item in distribution_data)

                    # Fallback to form-level values if per-trip was not provided
                    if total_tds == 0 and has_tds:
                        total_tds = tds_amount
                    if total_deduction == 0 and has_deduction:
                        total_deduction = deduction_amount

                    self.object = None

                    trip_ids = [item.get('trip_id') for item in distribution_data if item.get('trip_id')]
                    trips_by_id = Trip.objects.in_bulk(trip_ids)

                    # 1.1 Bank Payment Record
                    if total_payment > 0:
                        self.object = form.save(commit=False)
                        self.object.amount = total_payment
                        self.object.recorded_by = self.request.user
                        self.object.voucher = voucher
                        self.object.verification_status = FinancialVoucher.STATUS_PENDING
                        self.object.save()

                        for item in distribution_data:
                            trip_id = item.get('trip_id')
                            p_amt = Decimal(str(item.get('payment', item.get('amount', 0)) or 0))
                            if p_amt > 0 and trip_id in trips_by_id:
                                alloc = TripAllocation(
                                    financial_record=self.object,
                                    trip=trips_by_id[trip_id],
                                    amount=p_amt
                                )
                                alloc._suppress_cache_cascade = True
                                alloc.save()

                    # 1.2 TDS Record
                    if total_tds > 0:
                        tds_category, _ = TransactionCategory.objects.get_or_create(
                            name='TDS',
                            defaults={'type': TransactionCategory.TYPE_EXPENSE, 'description': 'Tax Deducted at Source'}
                        )
                        tds_record = FinancialRecord.objects.create(
                            date=self.object.date if self.object else form.cleaned_data['date'],
                            account=None,
                            party=self.object.party if self.object else form.cleaned_data['party'],
                            driver=None,
                            record_type=self.object.record_type if self.object else form.cleaned_data.get('record_type', 'Transaction'),
                            category=tds_category,
                            amount=total_tds,
                            description=f"Auto-generated TDS for Trip Payment across {len(distribution_data)} trips",
                            recorded_by=self.request.user,
                            voucher=voucher,
                            verification_status=FinancialVoucher.STATUS_PENDING
                        )
                        if not self.object:
                            self.object = tds_record

                        # Allocate TDS per trip
                        has_explicit_tds = any(Decimal(str(item.get('tds', 0) or 0)) > 0 for item in distribution_data)
                        for item in distribution_data:
                            trip_id = item.get('trip_id')
                            trip = trips_by_id.get(trip_id)
                            if not trip:
                                continue
                            if has_explicit_tds:
                                t_amt = Decimal(str(item.get('tds', 0) or 0))
                                if t_amt > 0:
                                    alloc = TripAllocation(financial_record=tds_record, trip=trip, amount=t_amt)
                                    alloc._suppress_cache_cascade = True
                                    alloc.save()
                            elif total_payment > 0:
                                p_amt = Decimal(str(item.get('payment', item.get('amount', 0)) or 0))
                                if p_amt > 0:
                                    ratio = p_amt / total_payment
                                    alloc = TripAllocation(financial_record=tds_record, trip=trip, amount=total_tds * ratio)
                                    alloc._suppress_cache_cascade = True
                                    alloc.save()

                    # 1.3 Deductions Record
                    if total_deduction > 0:
                        deductions_category, _ = TransactionCategory.objects.get_or_create(
                            name='Deductions',
                            defaults={'type': TransactionCategory.TYPE_EXPENSE, 'description': 'Deductions (shortage, charges, etc.)'}
                        )
                        notes_list = [str(item.get('deduction_notes', '')).strip() for item in distribution_data if str(item.get('deduction_notes', '')).strip()]
                        if deduction_notes.strip():
                            notes_list.append(deduction_notes.strip())
                        ded_desc = f"Deductions: {', '.join(notes_list)}" if notes_list else f"Deductions across {len(distribution_data)} trips"

                        ded_record = FinancialRecord.objects.create(
                            date=self.object.date if self.object else form.cleaned_data['date'],
                            account=None,
                            party=self.object.party if self.object else form.cleaned_data['party'],
                            driver=None,
                            record_type=self.object.record_type if self.object else form.cleaned_data.get('record_type', 'Transaction'),
                            category=deductions_category,
                            amount=total_deduction,
                            description=ded_desc,
                            recorded_by=self.request.user,
                            voucher=voucher,
                            verification_status=FinancialVoucher.STATUS_PENDING
                        )
                        if not self.object:
                            self.object = ded_record

                        # Allocate Deductions per trip
                        has_explicit_ded = any(Decimal(str(item.get('deduction', 0) or 0)) > 0 for item in distribution_data)
                        for item in distribution_data:
                            trip_id = item.get('trip_id')
                            trip = trips_by_id.get(trip_id)
                            if not trip:
                                continue
                            if has_explicit_ded:
                                d_amt = Decimal(str(item.get('deduction', 0) or 0))
                                if d_amt > 0:
                                    alloc = TripAllocation(financial_record=ded_record, trip=trip, amount=d_amt)
                                    alloc._suppress_cache_cascade = True
                                    alloc.save()
                            elif total_payment > 0:
                                p_amt = Decimal(str(item.get('payment', item.get('amount', 0)) or 0))
                                if p_amt > 0:
                                    ratio = p_amt / total_payment
                                    alloc = TripAllocation(financial_record=ded_record, trip=trip, amount=total_deduction * ratio)
                                    alloc._suppress_cache_cascade = True
                                    alloc.save()

                    # Fallback if no payment, tds, or deduction created an object
                    if not self.object:
                        self.object = form.save(commit=False)
                        self.object.recorded_by = self.request.user
                        self.object.voucher = voucher
                        self.object.verification_status = FinancialVoucher.STATUS_PENDING
                        self.object.save()

                    # Single consolidated cache refresh pass on unique touched trips and bills
                    touched_trips = list(trips_by_id.values())
                    touched_bills = {t.associated_bill for t in touched_trips if t.associated_bill}
                    for trip in touched_trips:
                        TripFinancialService.update_trip_financial_caches(trip)
                    for bill in touched_bills:
                        BillingService.update_bill_financial_caches(bill)

                    # Auto-generate description if blank
                    if not self.object.description:
                        trip_nums = [a.trip.trip_number for a in self.object.allocations.select_related('trip').all()]
                        if trip_nums:
                            self.object.description = f"Paid across trips: {', '.join(trip_nums)}"
                            self.object.save(update_fields=['description'])

                    voucher.update_totals()
                    messages.success(self.request, f'Settlement recorded across {len(distribution_data)} trips under Reference #{voucher.voucher_number}. (Pending Verification)')
                    if '_save_same_party' in self.request.POST:
                        return self._redirect_same_party(form)
                    if self.object.party:
                        return redirect('party-detail', pk=self.object.party.pk)
                    return redirect('financialrecord-list')

            except Exception as e:
                form.add_error(None, f"Error processing trip distribution: {str(e)}")
                return self.form_invalid(form)

        # 2. Multi-Bill Distribution Flow
        if bill_distribution_json:
            try:
                with transaction.atomic():
                    bill_data = json.loads(bill_distribution_json)
                    self.object = form.save(commit=False)
                    self.object.recorded_by = self.request.user
                    self.object.voucher = voucher
                    self.object.verification_status = FinancialVoucher.STATUS_PENDING
                    self.object.save()

                    # TDS record if specified
                    tds_record = None
                    if has_tds:
                        tds_category, _ = TransactionCategory.objects.get_or_create(
                            name='TDS',
                            defaults={'type': TransactionCategory.TYPE_EXPENSE, 'description': 'Tax Deducted at Source'}
                        )
                        tds_record = FinancialRecord.objects.create(
                            date=self.object.date,
                            account=None,
                            party=self.object.party,
                            driver=self.object.driver,
                            associated_bill=self.object.associated_bill,
                            record_type=self.object.record_type,
                            category=tds_category,
                            amount=tds_amount,
                            description=f"Auto-generated TDS for {self.object.category.name} entry #{self.object.entry_number}",
                            recorded_by=self.request.user,
                            voucher=voucher,
                            verification_status=FinancialVoucher.STATUS_PENDING
                        )

                    bill_ids = [item.get('bill_id') for item in bill_data if item.get('bill_id')]
                    bills_by_id = Bill.objects.in_bulk(bill_ids)

                    total_allocated = sum(Decimal(str(item.get('amount', 0))) for item in bill_data)
                    for item in bill_data:
                        bill_id = item.get('bill_id')
                        try:
                            amount = Decimal(str(item.get('amount')))
                        except (ValueError, InvalidOperation):
                            raise ValueError(f"Invalid amount format for bill {bill_id}")

                        if amount > 0 and bill_id in bills_by_id:
                            bill = bills_by_id[bill_id]
                            if has_tds and total_allocated > 0:
                                ratio = amount / total_allocated
                                payment_alloc = self.object.amount * ratio
                                tds_alloc = tds_amount * ratio
                                ba1 = BillAllocation(financial_record=self.object, bill=bill, amount=payment_alloc)
                                ba1._suppress_cache_cascade = True
                                ba1.save()
                                ba2 = BillAllocation(financial_record=tds_record, bill=bill, amount=tds_alloc)
                                ba2._suppress_cache_cascade = True
                                ba2.save()
                            else:
                                ba = BillAllocation(
                                    financial_record=self.object,
                                    bill=bill,
                                    amount=amount
                                )
                                ba._suppress_cache_cascade = True
                                ba.save()

                    for bill in bills_by_id.values():
                        BillingService.update_bill_financial_caches(bill)

                    if not self.object.description:
                        bill_nums = [a.bill.bill_number or 'Draft' for a in self.object.bill_allocations.select_related('bill').all()]
                        if bill_nums:
                            self.object.description = f"Paid across invoices: {', '.join(bill_nums)}"
                            self.object.save(update_fields=['description'])

                    voucher.update_totals()
                    messages.success(self.request, f'Payment recorded across {len(bill_data)} bills under Reference #{voucher.voucher_number}. (Pending Verification)')
                    if '_save_same_party' in self.request.POST:
                        return self._redirect_same_party(form)
                    if self.object.party:
                        return redirect('party-detail', pk=self.object.party.pk)
                    return redirect('financialrecord-list')

            except Exception as e:
                form.add_error(None, f"Error processing bill distribution: {str(e)}")
                return self.form_invalid(form)

        # 3. Single Trip or Standard Financial Record Creation
        with transaction.atomic():
            payment_amount = form.cleaned_data.get('amount') or Decimal('0.00')
            self.object = None

            if payment_amount > 0:
                self.object = form.save(commit=False)
                self.object.recorded_by = self.request.user
                self.object.voucher = voucher
                self.object.verification_status = FinancialVoucher.STATUS_PENDING
                self.object.save()

            # Auto-create TDS record if specified
            if has_tds:
                tds_category, _ = TransactionCategory.objects.get_or_create(
                    name='TDS',
                    defaults={'type': TransactionCategory.TYPE_EXPENSE, 'description': 'Tax Deducted at Source'}
                )
                tds_record = FinancialRecord.objects.create(
                    date=self.object.date if self.object else form.cleaned_data['date'],
                    account=None,
                    party=self.object.party if self.object else form.cleaned_data['party'],
                    driver=self.object.driver if self.object else form.cleaned_data.get('driver'),
                    associated_trip=form.cleaned_data.get('associated_trip'),
                    associated_bill=form.cleaned_data.get('associated_bill'),
                    record_type=self.object.record_type if self.object else form.cleaned_data.get('record_type', 'Transaction'),
                    category=tds_category,
                    amount=tds_amount,
                    description=f"Auto-generated TDS for {form.cleaned_data.get('associated_trip') or self.object or 'Trip'}",
                    recorded_by=self.request.user,
                    voucher=voucher,
                    verification_status=FinancialVoucher.STATUS_PENDING
                )
                if not self.object:
                    self.object = tds_record

            # Auto-create Deductions record if specified
            if has_deduction:
                deductions_category, _ = TransactionCategory.objects.get_or_create(
                    name='Deductions',
                    defaults={'type': TransactionCategory.TYPE_EXPENSE, 'description': 'Deductions (shortage, charges, etc.)'}
                )
                ded_desc = deduction_notes.strip() if deduction_notes.strip() else f"Deductions for {form.cleaned_data.get('associated_trip') or 'Trip'}"
                ded_record = FinancialRecord.objects.create(
                    date=self.object.date if self.object else form.cleaned_data['date'],
                    account=None,
                    party=self.object.party if self.object else form.cleaned_data['party'],
                    driver=self.object.driver if self.object else form.cleaned_data.get('driver'),
                    associated_trip=form.cleaned_data.get('associated_trip'),
                    associated_bill=form.cleaned_data.get('associated_bill'),
                    record_type=self.object.record_type if self.object else form.cleaned_data.get('record_type', 'Transaction'),
                    category=deductions_category,
                    amount=deduction_amount,
                    description=ded_desc,
                    recorded_by=self.request.user,
                    voucher=voucher,
                    verification_status=FinancialVoucher.STATUS_PENDING
                )
                if not self.object:
                    self.object = ded_record

            # Fallback if no payment, tds, or deduction created an object
            if not self.object:
                self.object = form.save(commit=False)
                self.object.recorded_by = self.request.user
                self.object.voucher = voucher
                self.object.verification_status = FinancialVoucher.STATUS_PENDING
                self.object.save()

            voucher.update_totals()

        messages.success(self.request, f'Financial record recorded under Reference #{voucher.voucher_number}. (Pending Verification)')

        if '_save_same_party' in self.request.POST:
            return self._redirect_same_party(form)

        if self.object.party:
            return redirect('party-detail', pk=self.object.party.pk)
        return redirect('financialrecord-detail', pk=self.object.pk)

    def _redirect_same_party(self, form):
        party_id = (self.object.party_id if self.object and self.object.party_id else '') or self.request.POST.get('party', '')
        account_id = self.request.POST.get('account') or (self.object.account_id if self.object and self.object.account_id else '')
        date_val = self.request.POST.get('date') or (str(self.object.date) if self.object else '')
        category_id = self.request.POST.get('category') or (self.object.category_id if self.object and self.object.category_id else '')

        from django.urls import reverse
        redirect_url = reverse('financialrecord-create') + f"?party={party_id}&account={account_id}&date={date_val}&category={category_id}"
        return redirect(redirect_url)
    
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
    
    def get_initial(self):
        initial = super().get_initial()
        # Pre-populate distributions for editing
        if self.object.allocations.exists():
            initial['payment_distribution'] = json.dumps([
                {'trip_id': a.trip_id, 'amount': float(a.amount)}
                for a in self.object.allocations.all()
            ])
        
        if self.object.bill_allocations.exists():
            initial['bill_distribution'] = json.dumps([
                {'bill_id': a.bill_id, 'amount': float(a.amount)}
                for a in self.object.bill_allocations.all()
            ])
        return initial

    def form_valid(self, form):
        # Capture pre-update state for alteration auditing
        old_record = FinancialRecord.all_objects.filter(pk=self.object.pk).first()
        old_data = {
            'amount': old_record.amount if old_record else None,
            'date': str(old_record.date) if old_record else None,
            'party': str(old_record.party) if old_record and old_record.party else 'None',
            'account': str(old_record.account) if old_record and old_record.account else 'None',
            'category': str(old_record.category) if old_record and old_record.category else 'None',
            'description': old_record.description if old_record else '',
        }

        distribution_json = form.cleaned_data.get('payment_distribution')
        bill_distribution_json = form.cleaned_data.get('bill_distribution')
        
        if distribution_json or bill_distribution_json:
            try:
                with transaction.atomic():
                    # 1. Update the parent FinancialRecord
                    self.object = form.save()
                    
                    # 2. Update Trip Allocations (Delete old ones first)
                    if distribution_json:
                        distribution_data = json.loads(distribution_json)
                        old_allocs = list(self.object.allocations.all())
                        old_trip_ids = [a.trip_id for a in old_allocs]
                        for a in old_allocs:
                            a._suppress_cache_cascade = True
                            a.delete()

                        trip_ids = [item.get('trip_id') for item in distribution_data if item.get('trip_id')]
                        trips_by_id = Trip.objects.in_bulk(list(set(old_trip_ids + trip_ids)))

                        for item in distribution_data:
                            trip_id = item.get('trip_id')
                            try:
                                amount = Decimal(str(item.get('amount')))
                            except (ValueError, InvalidOperation):
                                raise ValueError(f"Invalid amount format for trip {trip_id}")
                            
                            if amount > 0 and trip_id in trips_by_id:
                                trip = trips_by_id[trip_id]
                                alloc = TripAllocation(
                                    financial_record=self.object,
                                    trip=trip,
                                    amount=amount
                                )
                                alloc._suppress_cache_cascade = True
                                alloc.save()

                        # Refresh caches once for all affected trips and their bills
                        touched_trips = list(trips_by_id.values())
                        touched_bills = {t.associated_bill for t in touched_trips if t.associated_bill}
                        for trip in touched_trips:
                            TripFinancialService.update_trip_financial_caches(trip)
                        for bill in touched_bills:
                            BillingService.update_bill_financial_caches(bill)

                        messages.success(self.request, f'Financial record updated and redistributed across trips!')

                    # 3. Update Bill Allocations (Delete old ones first)
                    if bill_distribution_json:
                        bill_data = json.loads(bill_distribution_json)
                        old_allocs = list(self.object.bill_allocations.all())
                        old_bill_ids = [a.bill_id for a in old_allocs]
                        for a in old_allocs:
                            a._suppress_cache_cascade = True
                            a.delete()

                        bill_ids = [item.get('bill_id') for item in bill_data if item.get('bill_id')]
                        bills_by_id = Bill.objects.in_bulk(list(set(old_bill_ids + bill_ids)))

                        for item in bill_data:
                            bill_id = item.get('bill_id')
                            try:
                                amount = Decimal(str(item.get('amount')))
                            except (ValueError, InvalidOperation):
                                raise ValueError(f"Invalid amount format for bill {bill_id}")
                            
                            if amount > 0 and bill_id in bills_by_id:
                                bill = bills_by_id[bill_id]
                                alloc = BillAllocation(
                                    financial_record=self.object,
                                    bill=bill,
                                    amount=amount
                                )
                                alloc._suppress_cache_cascade = True
                                alloc.save()

                        for bill in bills_by_id.values():
                            BillingService.update_bill_financial_caches(bill)

                        messages.success(self.request, f'Financial record updated and redistributed across bills!')
                    
                    return redirect(self.get_success_url())

            except Exception as e:
                form.add_error(None, f"Error processing distribution: {str(e)}")
                return self.form_invalid(form)

        # Clean up old allocations if switching to a non-distribution flow
        if not distribution_json and self.object.allocations.exists():
            old_allocs = list(self.object.allocations.all())
            old_trips = [a.trip for a in old_allocs]
            for a in old_allocs:
                a._suppress_cache_cascade = True
                a.delete()
            for t in old_trips:
                TripFinancialService.update_trip_financial_caches(t)

        if not bill_distribution_json and self.object.bill_allocations.exists():
            old_bill_allocs = list(self.object.bill_allocations.all())
            old_bills = [a.bill for a in old_bill_allocs]
            for a in old_bill_allocs:
                a._suppress_cache_cascade = True
                a.delete()
            for b in old_bills:
                BillingService.update_bill_financial_caches(b)

        response = super().form_valid(form)

        # Check for field diffs and log to voucher audit history
        new_data = {
            'amount': self.object.amount,
            'date': str(self.object.date),
            'party': str(self.object.party) if self.object.party else 'None',
            'account': str(self.object.account) if self.object.account else 'None',
            'category': str(self.object.category) if self.object.category else 'None',
            'description': self.object.description or '',
        }
        diffs = {}
        for k, old_val in old_data.items():
            if old_val != new_data[k]:
                diffs[k] = (old_val, new_data[k])

        # Mark record and associated voucher as pending re-verification upon edit
        self.object.verification_status = FinancialVoucher.STATUS_PENDING
        self.object.verified_by = None
        self.object.verified_at = None
        self.object.save(update_fields=['verification_status', 'verified_by', 'verified_at'])
        if self.object.voucher:
            from ledger.services import VoucherService
            VoucherService.record_alteration(self.object.voucher, self.request.user, diffs)
            self.object.voucher.update_totals()

        messages.success(self.request, 'Financial record updated successfully! (Submitted for re-verification)')
        return response
    
    def get_success_url(self):
        return reverse_lazy('financialrecord-detail', kwargs={'pk': self.object.pk})


class FinancialRecordDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    """
    Delete view for financial records
    Soft-deletes the record and archives it under its immutable voucher.
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
        
        impact.append("This entry will be soft-deleted and preserved in the Verification tab where it can be inspected or restored.")
        context['impact_statements'] = impact
        return context
    
    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        reason = request.POST.get('deletion_reason', '').strip()
        v_num = self.object.voucher.voucher_number if self.object.voucher else f"#{self.object.entry_number}"
        self.object.soft_delete(user=self.request.user, reason=reason)
        messages.success(
            self.request,
            f'Financial record #{self.object.entry_number} has been soft-deleted and archived under Voucher {v_num}. You can review or restore it in Entry Verification.'
        )
        return redirect(self.get_success_url())


@login_required
@permission_required('ledger.can_view_financial_records', raise_exception=True)
def financial_summary(request):
    """
    Financial summary report view.
    Accounts for all real-world debits and credits in the system regardless of whether
    they are attached to a company firm account (e.g. unallocated toll top-ups),
    excluding accruals (invoices).
    """
    now = timezone.now()
    try:
        current_month = int(request.GET.get('month', now.month))
        if not (1 <= current_month <= 12):
            current_month = now.month
    except (ValueError, TypeError):
        current_month = now.month

    try:
        current_year = int(request.GET.get('year', now.year))
        if current_year < 2000 or current_year > 2100:
            current_year = now.year
    except (ValueError, TypeError):
        current_year = now.year

    # Ensure any toll recharges without a FinancialRecord are auto-synced
    unsynced_recharges = TollRecharge.objects.filter(financial_records__isnull=True)
    if unsynced_recharges.exists():
        for r in unsynced_recharges:
            r.sync_financial_record()

    monthly_tds = FinancialRecord.objects.filter(
        category__name='TDS',
        date__month=current_month,
        date__year=current_year
    ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    monthly_income_all = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_INCOME,
        date__month=current_month,
        date__year=current_year
    ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    monthly_income = monthly_income_all - monthly_tds

    monthly_expenses = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_EXPENSE,
        date__month=current_month,
        date__year=current_year
    ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # Year calculations
    yearly_tds = FinancialRecord.objects.filter(
        category__name='TDS',
        date__year=current_year
    ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    yearly_income_all = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_INCOME,
        date__year=current_year
    ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    yearly_income = yearly_income_all - yearly_tds

    yearly_expenses = FinancialRecord.objects.filter(
        category__type=TransactionCategory.TYPE_EXPENSE,
        date__year=current_year
    ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # Calculate GST portion from all Bills using database-level cached values
    monthly_gst = Bill.objects.filter(
        date__month=current_month,
        date__year=current_year
    ).aggregate(total=Sum('gst_amount_cached'))['total'] or Decimal('0.00')
    yearly_gst = Bill.objects.filter(
        date__year=current_year
    ).aggregate(total=Sum('gst_amount_cached'))['total'] or Decimal('0.00')

    # Category breakdown for selected month (single SQL GROUP BY query)
    cat_totals = FinancialRecord.objects.filter(
        date__month=current_month,
        date__year=current_year
    ).exclude(
        record_type='Invoice'
    ).values(
        'category__name', 'category__type'
    ).annotate(
        total=Sum('amount')
    ).filter(total__gt=0).order_by('-total')

    category_breakdown = [
        {'name': row['category__name'], 'type': row['category__type'], 'amount': row['total']}
        for row in cat_totals
    ]

    # Transparency breakdown: Unallocated expenses (e.g. FASTag recharges pending firm assignment)
    unallocated_monthly_expenses = FinancialRecord.objects.filter(
        account__isnull=True,
        category__type=TransactionCategory.TYPE_EXPENSE,
        date__month=current_month,
        date__year=current_year
    ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    unallocated_yearly_expenses = FinancialRecord.objects.filter(
        account__isnull=True,
        category__type=TransactionCategory.TYPE_EXPENSE,
        date__year=current_year
    ).exclude(record_type='Invoice').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # Available months and years for filter selector
    import calendar
    months_list = [(i, calendar.month_name[i]) for i in range(1, 13)]
    min_year = FinancialRecord.objects.order_by('date').values_list('date__year', flat=True).first() or now.year
    years_list = list(range(min(min_year, now.year - 1), max(now.year + 1, current_year + 1) + 1))

    # Calculate start and end date for unified ledger link
    import calendar as cal
    last_day = cal.monthrange(current_year, current_month)[1]
    month_start_str = f"{current_year:04d}-{current_month:02d}-01"
    month_end_str = f"{current_year:04d}-{current_month:02d}-{last_day:02d}"

    context = {
        'monthly_income': monthly_income,
        'monthly_expenses': monthly_expenses,
        'monthly_net_incl_gst': monthly_income_all - monthly_expenses,
        'monthly_net_excl_gst': (monthly_income_all - monthly_gst) - monthly_expenses,
        'yearly_income': yearly_income,
        'yearly_expenses': yearly_expenses,
        'yearly_net_incl_gst': yearly_income_all - yearly_expenses,
        'yearly_net_excl_gst': (yearly_income_all - yearly_gst) - yearly_expenses,
        'unallocated_monthly_expenses': unallocated_monthly_expenses,
        'unallocated_yearly_expenses': unallocated_yearly_expenses,
        'category_breakdown': category_breakdown,
        'current_month': datetime(current_year, current_month, 1).strftime('%B %Y'),
        'current_month_num': current_month,
        'current_year': current_year,
        'months': months_list,
        'years': years_list,
        'month_start_date': month_start_str,
        'month_end_date': month_end_str,
    }

    return render(request, 'ledger/financial_summary.html', context)

