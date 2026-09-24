"""
Bill CRUD views, Print/Annexure views, and Bulk Printing views.
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
from itertools import groupby
from django.http import JsonResponse, HttpResponse

from ledger.models import FinancialRecord, Party, CompanyAccount, TripAllocation, TransactionCategory, Bill, BillTrip
from ledger.forms import BillForm
from trips.models import Trip
from ledger.views.base import BaseLedgerPermissionMixin
from ledger.utils import format_indian_comma, format_balance

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
        ).order_by('-date', '-category__name', '-bill_no')
        
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
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        # sync_to_ledger is already called inside BillForm.save()

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
        bill_trips = list(bill.bill_trips.select_related('trip', 'trip__vehicle', 'trip__vehicle__vendor').order_by('trip__date'))
        context['bill_trips'] = bill_trips

        # Add the same summarized items used in the print view, reusing pre-fetched bill_trips
        invoice_items = group_trips_for_bill(bill, bill_trips=bill_trips)
        context['invoice_items'] = invoice_items

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

        # Extract unique vendors for all attached vehicles in this bill
        associated_vendors = set()
        for bt in bill_trips:
            if bt.trip and bt.trip.vehicle and bt.trip.vehicle.ownership == 'Attached' and bt.trip.vehicle.vendor:
                associated_vendors.add(bt.trip.vehicle.vendor)
        context['associated_vendors'] = list(associated_vendors)

        # Related ledger entries for internal summary
        context['invoice_record'] = bill.financial_records.filter(record_type='Invoice', party=bill.party).first()
        
        # Get vendor specific invoice records (Lorry Hire accruals)
        vendor_records = bill.financial_records.filter(record_type='Invoice').exclude(party=bill.party)
        context['vendor_records'] = vendor_records

        # Comprehensive list of payments/credits contributing to this bill
        related_payments = []
        seen_records = set()

        # 1. Direct Ledger Entries (where associated_bill = bill)
        direct_records = bill.financial_records.exclude(record_type='Invoice').select_related('category')
        for rec in direct_records:
            related_payments.append({
                'financial_record': rec,
                'amount': rec.amount,
                'type': 'Direct'
            })
            seen_records.add(rec.pk)

        # 2. Bill Allocations
        for alloc in bill.payment_allocations.select_related('financial_record', 'financial_record__category').all():
            if alloc.financial_record_id not in seen_records:
                related_payments.append({
                    'financial_record': alloc.financial_record,
                    'amount': alloc.amount,
                    'type': 'Allocated'
                })
                seen_records.add(alloc.financial_record_id)
            else:
                # Update amount if already added via direct (though this shouldn't happen with clean data)
                for p in related_payments:
                    if p['financial_record'].pk == alloc.financial_record_id:
                        p['amount'] += alloc.amount
                        p['type'] = 'Direct + Allocated'

        # 3. Trip-based Payments (for trip-based bills)
        if bill.bill_type == 'Trip':
            trip_ids = bill.trips.values_list('id', flat=True)
            
            # Trip Allocations
            trip_allocations = TripAllocation.objects.filter(
                trip_id__in=trip_ids
            ).select_related('financial_record', 'financial_record__category', 'trip', 'trip__vehicle')
            
            for ta in trip_allocations:
                if ta.financial_record_id not in seen_records:
                    vehicle_plate = ta.trip.vehicle.registration_plate if (ta.trip and ta.trip.vehicle) else (ta.trip.trip_number if ta.trip else '')
                    related_payments.append({
                        'financial_record': ta.financial_record,
                        'amount': ta.amount,
                        'type': f"Trip {vehicle_plate or (ta.trip.pk if ta.trip else '')}".strip()
                    })
                    seen_records.add(ta.financial_record_id)
                else:
                    for p in related_payments:
                        if p['financial_record'].pk == ta.financial_record_id:
                            p['amount'] += ta.amount

            # Direct Trip Records
            direct_trip_records = FinancialRecord.objects.filter(
                associated_trip_id__in=trip_ids
            ).exclude(
                Q(record_type='Invoice') |
                Q(associated_bill=bill) |
                Q(bill_allocations__bill=bill)
            ).select_related('category', 'associated_trip', 'associated_trip__vehicle')
            
            for tr in direct_trip_records:
                if tr.pk not in seen_records:
                    vehicle_plate = tr.associated_trip.vehicle.registration_plate if (tr.associated_trip and tr.associated_trip.vehicle) else (tr.associated_trip.trip_number if tr.associated_trip else '')
                    related_payments.append({
                        'financial_record': tr,
                        'amount': tr.amount,
                        'type': f"Trip {vehicle_plate or (tr.associated_trip.pk if tr.associated_trip else '')}".strip()
                    })
                    seen_records.add(tr.pk)

        # Sort payments by date
        related_payments.sort(key=lambda x: (x['financial_record'].date, x['financial_record'].created_at), reverse=True)
        context['payments'] = related_payments
        context['adjustments'] = bill.adjustment_bills.select_related('category').all()

        return context

def group_trips_for_bill(bill, bill_trips=None):
    """
    Groups bill_trips by (Pickup, Delivery, Rate) and returns a list of dictionaries.
    """
    if bill_trips is None:
        bill_trips = list(bill.bill_trips.select_related('trip', 'trip__vehicle').all())
    else:
        bill_trips = list(bill_trips)

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
    for date, group in groupby(bill_trips, key=lambda bt: bt.trip.date if bt.trip else None):
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




def get_next_invoice_number(request):
    """
    Returns the next available invoice number for a given issuer via AJAX.
    """
    issuer_id = request.GET.get('issuer_id')
    date_str = request.GET.get('date')
    category_id = request.GET.get('category_id')
    
    if not issuer_id:
        return JsonResponse({'error': 'No issuer ID provided'}, status=400)
    
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

