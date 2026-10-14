"""
Manager dashboard views for Trips app.
"""
from datetime import timedelta
from decimal import Decimal
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import models
from django.db.models import Q, Sum, F, Case, When, Value, DecimalField, ExpressionWrapper, Count, OuterRef, Subquery
from django.db.models.functions import Coalesce

from trips.models import Trip
from fleet.models import Vehicle, MaintenanceRecord
from ledger.models import FinancialRecord, TransactionCategory, Bill


@login_required
def manager_dashboard(request):
    """
    Manager dashboard - optimized with SQL-level aggregations
    """
    if not (request.user.is_superuser or 
            request.user.has_perm('trips.can_view_manager_dashboard')):
        messages.error(request, 'Access denied. Manager dashboard is only for managers.')
        return redirect('trip-list')
    
    current_date = timezone.now().date()
    current_month = current_date.month
    current_year = current_date.year
    
    # 1. Overall Stats
    trip_stats = Trip.objects.with_payment_info().aggregate(
        total_lifetime=models.Count('id'),
        active_count=models.Count('id', filter=Q(annotated_status__in=['Unpaid', 'Partially Paid'])),
        completed_month=models.Count('id', filter=Q(
            annotated_status='Paid',
            date__month=current_month,
            date__year=current_year
        ))
    )
    
    # 2. Optimized Financials (Aggregated by type in SQL)
    # Filter for the current month
    month_records = FinancialRecord.objects.filter(
        date__month=current_month,
        date__year=current_year
    ).exclude(record_type=FinancialRecord.RECORD_TYPE_INVOICE)

    financial_totals = month_records.aggregate(
        income=Sum('amount', filter=Q(category__type=TransactionCategory.TYPE_INCOME)),
        expenses=Sum('amount', filter=Q(category__type=TransactionCategory.TYPE_EXPENSE) & ~Q(category__name='Deductions'))
    )

    income_this_month = financial_totals['income'] or 0
    expenses_this_month = financial_totals['expenses'] or 0

    # 3. Optimized GST Calculation (Direct SQL aggregation from Bills)
    gst_totals = Bill.objects.filter(
        date__month=current_month,
        date__year=current_year
    ).aggregate(
        total_gst=Sum(
            ExpressionWrapper(
                (F('standard_weight') * F('standard_rate') * F('gst_rate') / Decimal('100')),
                output_field=DecimalField()
            ),
            filter=Q(bill_type='Standard'),
            output_field=DecimalField()
        )
    )
    
    gst_this_month = gst_totals['total_gst'] or 0
    
    # Add trip-based GST (much faster than looping over bill objects)
    trip_gst = Trip.objects.filter(
        bills__date__month=current_month,
        bills__date__year=current_year
    ).aggregate(
        val=Sum(
            ExpressionWrapper(
                Case(
                    When(revenue_type='fixed', then=F('rate_per_ton')),
                    default=F('weight') * F('rate_per_ton'),
                    output_field=DecimalField()
                ) * F('bills__gst_rate') / Decimal('100'),
                output_field=DecimalField()
            )
        )
    )['val'] or 0
    
    gst_this_month += trip_gst

    # 4. Deep Vehicle Utilization & Health (Optimized with Subqueries to avoid Cartesian products and sum-distinct bugs)
    trip_qs = Trip.objects.filter(vehicle=OuterRef('pk'))
    maint_qs = MaintenanceRecord.objects.filter(vehicle=OuterRef('pk'))

    sub_trips_lifetime = Subquery(
        trip_qs.values('vehicle').annotate(cnt=Count('id')).values('cnt')[:1],
        output_field=models.IntegerField()
    )
    sub_trips_month = Subquery(
        trip_qs.filter(date__month=current_month, date__year=current_year).values('vehicle').annotate(cnt=Count('id')).values('cnt')[:1],
        output_field=models.IntegerField()
    )
    sub_rev_lifetime = Subquery(
        trip_qs.values('vehicle').annotate(total=Sum('revenue_cached')).values('total')[:1],
        output_field=DecimalField()
    )
    sub_rev_month = Subquery(
        trip_qs.filter(date__month=current_month, date__year=current_year).values('vehicle').annotate(total=Sum('revenue_cached')).values('total')[:1],
        output_field=DecimalField()
    )
    sub_maint_month = Subquery(
        maint_qs.filter(
            is_completed=True,
            completion_date__month=current_month,
            completion_date__year=current_year
        ).values('vehicle').annotate(total=Sum('cost')).values('total')[:1],
        output_field=DecimalField()
    )
    sub_next_maint = Subquery(
        maint_qs.filter(is_completed=False, expiry_date__isnull=False).order_by('expiry_date').values('expiry_date')[:1]
    )

    vehicles = Vehicle.objects.exclude(status=Vehicle.STATUS_RETIRED).annotate(
        trips_lifetime=Coalesce(sub_trips_lifetime, Value(0)),
        trips_this_month=Coalesce(sub_trips_month, Value(0)),
        revenue_lifetime=Coalesce(sub_rev_lifetime, Value(Decimal('0'))),
        revenue_this_month=Coalesce(sub_rev_month, Value(Decimal('0'))),
        maintenance_cost_month=Coalesce(sub_maint_month, Value(Decimal('0'))),
        next_maint_date=sub_next_maint,
    ).order_by('registration_plate')

    # 5. Global Alerts
    vehicles_due_maintenance = MaintenanceRecord.objects.filter(
        is_completed=False,
        expiry_date__lte=current_date + timedelta(days=7)
    ).values('vehicle').distinct().count()
    
    recent_trips = Trip.objects.with_payment_info().order_by('-created_at')[:10]
    vehicles_in_maintenance = Vehicle.objects.filter(status=Vehicle.STATUS_MAINTENANCE).count()
    
    context = {
        'today': current_date,
        'total_trips_lifetime': trip_stats['total_lifetime'],
        'active_trips': trip_stats['active_count'],
        'completed_this_month': trip_stats['completed_month'],
        'vehicles_due_maintenance': vehicles_due_maintenance,
        'income_this_month': income_this_month,
        'expenses_this_month': expenses_this_month,
        'net_profit_incl_gst': income_this_month - expenses_this_month,
        'net_profit_excl_gst': (income_this_month - gst_this_month) - expenses_this_month,
        'recent_trips': recent_trips,
        'vehicles_in_maintenance': vehicles_in_maintenance,
        'vehicle_stats': vehicles,
    }
    
    return render(request, 'trips/manager_dashboard.html', context)
