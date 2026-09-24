"""
Party CRUD views and Party AJAX endpoints.
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
from ledger.forms import PartyForm
from trips.models import Trip
from ledger.views.base import BaseLedgerPermissionMixin
from ledger.utils import format_indian_comma, format_balance

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
        ).order_by('-date', '-category__name', '-bill_no')
        
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



def get_party_unpaid_trips(request):
    """
    AJAX endpoint to get unpaid trips for a party (used in payment distribution)
    """
    party_id = request.GET.get('party_id')
    if not party_id:
        return JsonResponse({'trips': []})
    
    try:
        trips = Trip.objects.with_payment_info().filter(
            party_id=party_id
        ).exclude(
            annotated_status=Trip.PAYMENT_STATUS_PAID
        ).select_related('vehicle', 'route').order_by('date')
        
        data = [{
            'id': trip.id,
            'trip_number': trip.trip_number,
            'lr_no': trip.lr_no or '',
            'vehicle': trip.vehicle.registration_plate if trip.vehicle else 'No Vehicle',
            'date': trip.date.strftime('%d/%m/%Y') if trip.date else '',
            'route': str(trip.route) if trip.route else (f"{trip.pickup_location} → {trip.delivery_location}" if trip.pickup_location else ''),
            'total': float(trip.total_revenue),
            'received': float(trip.amount_received),
            'balance': float(trip.outstanding_balance),
            'label': f"{(trip.date.strftime('%d/%m/%Y') if trip.date else '')} - {trip.vehicle.registration_plate if trip.vehicle else ''} (Pending: ₹{trip.outstanding_balance:,.2f})",
        } for trip in trips]
        
        return JsonResponse({'trips': data})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e), 'detail': 'Check server logs for traceback'}, status=400)

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
            'total': float(bill.rounded_total),
            'received': float(bill.amount_received),
            'subtotal': float(bill.subtotal),
        })
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JsonResponse({'error': str(e), 'detail': 'Check server logs for traceback'}, status=400)

@login_required
def get_trip_balance(request):
    """
    AJAX endpoint to get outstanding balance and details for a trip
    """
    trip_id = request.GET.get('trip_id')
    if not trip_id:
        return JsonResponse({'balance': 0})

    try:
        trip = get_object_or_404(
            Trip.objects.with_payment_info().select_related('vehicle', 'route', 'party'),
            pk=trip_id
        )
        return JsonResponse({
            'id': trip.id,
            'trip_number': trip.trip_number,
            'vehicle': trip.vehicle.registration_plate if trip.vehicle else 'No Vehicle',
            'date': trip.date.strftime('%d/%m/%Y') if trip.date else '',
            'route': str(trip.route) if trip.route else (f"{trip.pickup_location} → {trip.delivery_location}" if trip.pickup_location else ''),
            'party_id': trip.party_id,
            'party_name': trip.party.name if trip.party else '',
            'total': float(trip.total_revenue),
            'received': float(trip.amount_received),
            'balance': float(trip.outstanding_balance),
            'lr_no': trip.lr_no or '',
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
        # Show trips for this party with vehicle pre-fetched
        qs = Trip.objects.filter(party_id=party_id).select_related('vehicle')
        
        if bill_id:
            # Include currently selected trips for this bill + unbilled ones
            qs = qs.filter(Q(bills__isnull=True) | Q(bills__id=bill_id))
        else:
            qs = qs.filter(bills__isnull=True)
            
        trips = list(qs.distinct().order_by('-date', '-created_at'))
        
        # Batch fetch BillTrip data if bill_id provided to avoid N queries in loop
        bill_trips_map = {}
        if bill_id and trips:
            for bt in BillTrip.objects.filter(bill_id=bill_id, trip__in=trips):
                bill_trips_map[bt.trip_id] = bt

        data = []
        for trip in trips:
            lr_no = trip.lr_no or ''
            discount = 0.0

            # If editing a bill, get the specific LR No or Discount saved for this bill
            if bill_id:
                bt = bill_trips_map.get(trip.id)
                if bt:
                    lr_no = bt.lr_no or ''
                    discount = float(bt.discount or 0)

            data.append({
                'id': trip.id,
                'date': trip.date.strftime('%d %b %Y') if trip.date else '',
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
        ).order_by('-date', '-category__name', '-bill_no')

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

