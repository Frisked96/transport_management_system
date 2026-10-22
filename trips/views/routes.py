"""
Route CRUD and Network Intelligence Analytics Dashboard views.
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Q, Sum, Avg, Min, Max, Count
from django.db.models.functions import TruncMonth
from django.utils import timezone

from trips.models import Route, Trip
from trips.forms import RouteForm
from trips.services.toll_service import calculate_route_toll_stats
from fleet.models import Vehicle
from ledger.models import Party


class RouteListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Route
    template_name = 'trips/route_list.html'
    context_object_name = 'routes'
    permission_required = 'trips.view_route'
    paginate_by = 25

    def get_queryset(self):
        queryset = Route.objects.all().order_by('pickup_location', 'delivery_location')
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(pickup_location__icontains=search) |
                Q(delivery_location__icontains=search) |
                Q(route_type__icontains=search)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_term'] = self.request.GET.get('search', '')
        return context


class RoutesDashboardView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Fleet-wide Routes Network Intelligence & Analytics Dashboard.
    Aggregates volume, revenue, freight rates, and corridor performance across all routes.
    """
    template_name = 'trips/routes_dashboard.html'
    permission_required = 'trips.view_route'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 1. Filters: Period, Route Type, Party, Ownership
        period = self.request.GET.get('period', 'all')
        start_date_str = self.request.GET.get('start_date')
        end_date_str = self.request.GET.get('end_date')
        selected_route_type = self.request.GET.get('route_type', '')
        party_id = self.request.GET.get('party', '')
        ownership = self.request.GET.get('ownership', '')

        now = timezone.now().date()
        start_date = None
        end_date = None

        if period == '30d':
            start_date = now - timedelta(days=30)
            end_date = now
        elif period == '90d':
            start_date = now - timedelta(days=90)
            end_date = now
        elif period == '6m':
            start_date = now - timedelta(days=180)
            end_date = now
        elif period == '1y':
            start_date = now - timedelta(days=365)
            end_date = now
        elif period == 'custom' or (start_date_str and end_date_str):
            period = 'custom'
            if start_date_str:
                try:
                    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    start_date = None
            if end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    end_date = None
        else:
            period = 'all'

        # Base trips queryset
        trips_qs = Trip.objects.all()

        if start_date:
            trips_qs = trips_qs.filter(date__gte=start_date)
        if end_date:
            trips_qs = trips_qs.filter(date__lte=end_date)
        if selected_route_type:
            trips_qs = trips_qs.filter(route__route_type=selected_route_type)
        if ownership == 'owned':
            trips_qs = trips_qs.filter(vehicle__ownership=Vehicle.OWNERSHIP_OWNED)
        elif ownership == 'attached':
            trips_qs = trips_qs.filter(vehicle__ownership=Vehicle.OWNERSHIP_ATTACHED)
        if party_id:
            try:
                trips_qs = trips_qs.filter(party_id=int(party_id))
            except (ValueError, TypeError):
                pass

        # 2. Network-Wide High-Level KPI Aggregates
        all_routes_count = Route.objects.count()
        network_agg = trips_qs.aggregate(
            total_trips=Count('id'),
            total_weight=Sum('weight'),
            total_revenue=Sum('revenue_cached'),
            avg_rate=Avg('rate_per_ton'),
        )
        total_trips = network_agg['total_trips'] or 0
        total_weight = network_agg['total_weight'] or Decimal('0')
        total_revenue = network_agg['total_revenue'] or Decimal('0')
        avg_rate = network_agg['avg_rate'] or Decimal('0')

        # Weighted Average Rate across all per-ton trips: Total Revenue / Total Weight
        per_ton_trips = trips_qs.filter(revenue_type=Trip.REVENUE_PER_TON)
        per_ton_agg = per_ton_trips.aggregate(
            rev=Sum('revenue_cached'),
            wt=Sum('weight')
        )
        if per_ton_agg['wt'] and per_ton_agg['wt'] > 0:
            weighted_avg_rate = (per_ton_agg['rev'] or Decimal('0')) / per_ton_agg['wt']
        else:
            weighted_avg_rate = avg_rate

        avg_load_per_trip = (total_weight / total_trips) if total_trips > 0 else Decimal('0')

        # 3. Route Network Performance
        routes_qs = Route.objects.all().order_by('pickup_location', 'delivery_location')
        if selected_route_type:
            routes_qs = routes_qs.filter(route_type=selected_route_type)

        trips_by_route = trips_qs.filter(route__isnull=False).values(
            'route_id'
        ).annotate(
            trips=Count('id'),
            total_weight=Sum('weight'),
            total_revenue=Sum('revenue_cached'),
            avg_rate=Avg('rate_per_ton'),
            min_rate=Min('rate_per_ton'),
            max_rate=Max('rate_per_ton'),
            latest_date=Max('date')
        )
        route_stats_map = {item['route_id']: item for item in trips_by_route}

        # Trips with unlinked routes matching pickup & delivery
        unlinked_trips = trips_qs.filter(route__isnull=True).values(
            'pickup_location', 'delivery_location'
        ).annotate(
            trips=Count('id'),
            total_weight=Sum('weight'),
            total_revenue=Sum('revenue_cached'),
            avg_rate=Avg('rate_per_ton'),
            min_rate=Min('rate_per_ton'),
            max_rate=Max('rate_per_ton'),
            latest_date=Max('date')
        )
        unlinked_map = {
            (u['pickup_location'].strip().lower(), u['delivery_location'].strip().lower()): u
            for u in unlinked_trips if u['pickup_location'] and u['delivery_location']
        }

        route_performance_list = []
        inactive_routes_list = []
        active_routes_count = 0

        for r in routes_qs:
            st = route_stats_map.get(r.id)
            loc_key = (r.pickup_location.strip().lower(), r.delivery_location.strip().lower())
            unlinked_st = unlinked_map.get(loc_key)

            r_trips = (st['trips'] if st else 0) + (unlinked_st['trips'] if unlinked_st else 0)
            r_weight = (st['total_weight'] if st and st['total_weight'] else Decimal('0')) + (unlinked_st['total_weight'] if unlinked_st and unlinked_st['total_weight'] else Decimal('0'))
            r_revenue = (st['total_revenue'] if st and st['total_revenue'] else Decimal('0')) + (unlinked_st['total_revenue'] if unlinked_st and unlinked_st['total_revenue'] else Decimal('0'))

            latest_date = None
            if st and st['latest_date']:
                latest_date = st['latest_date']
            if unlinked_st and unlinked_st['latest_date']:
                if not latest_date or unlinked_st['latest_date'] > latest_date:
                    latest_date = unlinked_st['latest_date']

            if r_weight > 0 and r_revenue > 0:
                r_weighted_rate = r_revenue / r_weight
            elif r_trips > 0 and st and st['avg_rate']:
                r_weighted_rate = st['avg_rate']
            else:
                r_weighted_rate = Decimal('0')

            default_rate = r.default_rate or Decimal('0')
            variance = (r_weighted_rate - default_rate) if r_trips > 0 else Decimal('0')

            route_item = {
                'route': r,
                'trips': r_trips,
                'total_weight': r_weight,
                'total_revenue': r_revenue,
                'weighted_avg_rate': r_weighted_rate,
                'default_rate': default_rate,
                'rate_variance': variance,
                'rate_variance_abs': abs(variance),
                'latest_date': latest_date,
            }

            if r_trips > 0:
                active_routes_count += 1
                route_performance_list.append(route_item)
            else:
                inactive_routes_list.append(route_item)

        # Sort active routes by gross revenue descending
        route_performance_list.sort(key=lambda x: x['total_revenue'], reverse=True)

        # Calculate revenue share % for each route
        for r_item in route_performance_list:
            if total_revenue > 0:
                r_item['revenue_share'] = round(float((r_item['total_revenue'] / total_revenue) * 100), 1)
            else:
                r_item['revenue_share'] = 0.0

        # 4. Leaderboards
        top_by_revenue = route_performance_list[:5]
        top_by_volume = sorted(route_performance_list, key=lambda x: x['total_weight'], reverse=True)[:5]
        top_by_trips = sorted(route_performance_list, key=lambda x: x['trips'], reverse=True)[:5]

        # 5. Chart 1: Top 10 Routes Bar Chart (Volume & Revenue)
        top_routes_for_chart = route_performance_list[:10]
        chart_route_labels = [f"{item['route'].pickup_location} → {item['route'].delivery_location}" for item in top_routes_for_chart]
        chart_route_revenues = [float(item['total_revenue']) for item in top_routes_for_chart]
        chart_route_weights = [float(item['total_weight']) for item in top_routes_for_chart]
        chart_route_rates = [round(float(item['weighted_avg_rate']), 2) for item in top_routes_for_chart]
        chart_route_benchmarks = [round(float(item['default_rate']), 2) for item in top_routes_for_chart]

        # 6. Chart 2: Monthly Network Progression (Volume, Revenue, Avg Rate)
        monthly_data = trips_qs.annotate(
            month=TruncMonth('date')
        ).values('month').annotate(
            trips=Count('id'),
            total_weight=Sum('weight'),
            total_revenue=Sum('revenue_cached'),
            avg_rate=Avg('rate_per_ton'),
        ).order_by('month')

        monthly_labels = []
        monthly_weights = []
        monthly_revenues = []
        monthly_rates = []

        for m in monthly_data:
            month_date = m['month']
            label = month_date.strftime('%b %Y') if month_date else 'Unknown'
            monthly_labels.append(label)
            m_rev = float(m['total_revenue'] or 0)
            m_wt = float(m['total_weight'] or 0)
            monthly_revenues.append(round(m_rev, 2))
            monthly_weights.append(round(m_wt, 2))
            if m_wt > 0 and m_rev > 0:
                monthly_rates.append(round(m_rev / m_wt, 2))
            else:
                monthly_rates.append(round(float(m['avg_rate'] or 0), 2))

        # 7. Chart 3: Route Type Distribution (Local vs Intra vs Non-GST)
        type_counts = {'local': 0, 'intra': 0, 'none': 0}
        type_weights = {'local': 0.0, 'intra': 0.0, 'none': 0.0}
        type_revenues = {'local': 0.0, 'intra': 0.0, 'none': 0.0}

        for item in route_performance_list:
            rtype = item['route'].route_type or 'local'
            if rtype in type_counts:
                type_counts[rtype] += item['trips']
                type_weights[rtype] += float(item['total_weight'])
                type_revenues[rtype] += float(item['total_revenue'])

        # Dropdowns
        all_parties = Party.objects.filter(
            id__in=Trip.objects.values_list('party_id', flat=True).distinct()
        ).order_by('name')

        active_percentage = round((active_routes_count / all_routes_count * 100), 1) if all_routes_count > 0 else 0

        has_chart_data = bool(chart_route_labels or monthly_labels)

        context.update({
            # Filters
            'period': period,
            'start_date': start_date_str or (start_date.strftime('%Y-%m-%d') if start_date else ''),
            'end_date': end_date_str or (end_date.strftime('%Y-%m-%d') if end_date else ''),
            'selected_route_type': selected_route_type,
            'selected_party': int(party_id) if party_id and party_id.isdigit() else '',
            'selected_ownership': ownership,
            'route_type_choices': Route.ROUTE_TYPE_CHOICES,
            'all_parties': all_parties,

            # Summary KPIs
            'all_routes_count': all_routes_count,
            'active_routes_count': active_routes_count,
            'inactive_routes_count': len(inactive_routes_list),
            'active_percentage': active_percentage,
            'total_trips': total_trips,
            'total_weight': total_weight,
            'total_revenue': total_revenue,
            'weighted_avg_rate': weighted_avg_rate,
            'avg_rate': avg_rate,
            'avg_load_per_trip': avg_load_per_trip,

            # Matrices & Leaderboards
            'route_performance_list': route_performance_list,
            'inactive_routes_list': inactive_routes_list,
            'top_by_revenue': top_by_revenue,
            'top_by_volume': top_by_volume,
            'top_by_trips': top_by_trips,

            # Chart JSON payloads
            'has_chart_data': has_chart_data,
            'chart_route_labels_json': json.dumps(chart_route_labels),
            'chart_route_revenues_json': json.dumps(chart_route_revenues),
            'chart_route_weights_json': json.dumps(chart_route_weights),
            'chart_route_rates_json': json.dumps(chart_route_rates),
            'chart_route_benchmarks_json': json.dumps(chart_route_benchmarks),
            'chart_monthly_labels_json': json.dumps(monthly_labels),
            'chart_monthly_weights_json': json.dumps(monthly_weights),
            'chart_monthly_revenues_json': json.dumps(monthly_revenues),
            'chart_monthly_rates_json': json.dumps(monthly_rates),
            'type_counts_json': json.dumps(type_counts),
            'type_weights_json': json.dumps(type_weights),
            'type_revenues_json': json.dumps(type_revenues),
            'type_counts': type_counts,
            'type_weights': type_weights,
            'type_revenues': type_revenues,
        })
        return context


class RouteDashboardView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """
    Route Specific Analytics & Rate Trends Dashboard.
    Provides deep performance, volume, and rate intelligence for a specific route.
    Rates and rate trends are calculated dynamically from actual trips executed on the route.
    """
    model = Route
    template_name = 'trips/route_dashboard.html'
    context_object_name = 'route'
    permission_required = 'trips.view_route'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        route = self.object

        # 1. Base query for trips on this route (including fallback by location match)
        trips_qs = Trip.objects.filter(
            Q(route=route) | (
                Q(route__isnull=True) & 
                Q(pickup_location__iexact=route.pickup_location, delivery_location__iexact=route.delivery_location)
            )
        )

        # 2. Date Filtering
        period = self.request.GET.get('period', 'all')
        start_date_str = self.request.GET.get('start_date')
        end_date_str = self.request.GET.get('end_date')
        party_id = self.request.GET.get('party')

        now = timezone.now().date()
        start_date = None
        end_date = None

        if period == '30d':
            start_date = now - timedelta(days=30)
            end_date = now
        elif period == '90d':
            start_date = now - timedelta(days=90)
            end_date = now
        elif period == '6m':
            start_date = now - timedelta(days=180)
            end_date = now
        elif period == '1y':
            start_date = now - timedelta(days=365)
            end_date = now
        elif period == 'custom' or (start_date_str and end_date_str):
            period = 'custom'
            if start_date_str:
                try:
                    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    start_date = None
            if end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    end_date = None
        else:
            period = 'all'

        if start_date:
            trips_qs = trips_qs.filter(date__gte=start_date)
        if end_date:
            trips_qs = trips_qs.filter(date__lte=end_date)
        if party_id:
            try:
                trips_qs = trips_qs.filter(party_id=int(party_id))
            except (ValueError, TypeError):
                pass

        # Ownership filter
        ownership = self.request.GET.get('ownership', 'all')
        if ownership == 'owned':
            trips_qs = trips_qs.filter(vehicle__ownership=Vehicle.OWNERSHIP_OWNED)
        elif ownership == 'attached':
            trips_qs = trips_qs.filter(vehicle__ownership=Vehicle.OWNERSHIP_ATTACHED)

        # Toll Statistics for Route (calculated from company-owned fleet trips)
        toll_stats = calculate_route_toll_stats(trips_qs)
        total_toll_expense = toll_stats['total_toll']
        avg_toll_per_trip = toll_stats['avg_toll']
        owned_trips_count = toll_stats['owned_trips_count']
        trips_with_tolls_count = toll_stats['trips_with_tolls_count']
        toll_map = toll_stats['toll_map']

        # 3. Overall Route Aggregate Metrics
        agg = trips_qs.aggregate(
            total_trips=Count('id'),
            total_weight=Sum('weight'),
            total_revenue=Sum('revenue_cached'),
            avg_rate=Avg('rate_per_ton'),
            min_rate=Min('rate_per_ton'),
            max_rate=Max('rate_per_ton'),
        )

        total_trips = agg['total_trips'] or 0
        total_weight = agg['total_weight'] or Decimal('0')
        total_revenue = agg['total_revenue'] or Decimal('0')
        avg_rate = agg['avg_rate'] or Decimal('0')
        min_rate = agg['min_rate'] or Decimal('0')
        max_rate = agg['max_rate'] or Decimal('0')

        # Weighted Average Rate: Total Revenue / Total Weight (for per-ton trips)
        per_ton_trips = trips_qs.filter(revenue_type=Trip.REVENUE_PER_TON)
        per_ton_agg = per_ton_trips.aggregate(
            rev=Sum('revenue_cached'),
            wt=Sum('weight')
        )
        if per_ton_agg['wt'] and per_ton_agg['wt'] > 0:
            weighted_avg_rate = (per_ton_agg['rev'] or Decimal('0')) / per_ton_agg['wt']
        else:
            weighted_avg_rate = avg_rate

        # Latest trip rate on this route
        latest_trip = trips_qs.order_by('-date', '-created_at').first()
        latest_rate = latest_trip.rate_per_ton if latest_trip else (route.default_rate or Decimal('0'))

        # Variance from default/suggested rate
        default_rate = route.default_rate or Decimal('0')
        rate_variance = latest_rate - default_rate
        rate_variance_abs = abs(rate_variance)
        rate_spread = (max_rate - min_rate) if max_rate and min_rate else Decimal('0')
        avg_load_per_trip = (total_weight / total_trips) if total_trips > 0 else Decimal('0')

        # 4. Monthly Time Series (Rate Trends, Volume, Revenue)
        monthly_data = trips_qs.annotate(
            month=TruncMonth('date')
        ).values('month').annotate(
            trips=Count('id'),
            total_weight=Sum('weight'),
            total_revenue=Sum('revenue_cached'),
            avg_rate=Avg('rate_per_ton'),
            min_rate=Min('rate_per_ton'),
            max_rate=Max('rate_per_ton')
        ).order_by('month')

        chart_labels = []
        chart_avg_rates = []
        chart_min_rates = []
        chart_max_rates = []
        chart_default_rates = []
        chart_weights = []
        chart_revenues = []
        chart_trips = []

        default_rate_val = float(default_rate)

        for m in monthly_data:
            month_date = m['month']
            label = month_date.strftime('%b %Y') if month_date else 'Unknown'
            chart_labels.append(label)

            m_rev = float(m['total_revenue'] or 0)
            m_wt = float(m['total_weight'] or 0)
            m_avg = round(float(m['avg_rate'] or 0), 2)
            if m_wt > 0 and m_rev > 0:
                weighted_m_rate = round(m_rev / m_wt, 2)
            else:
                weighted_m_rate = m_avg

            chart_avg_rates.append(weighted_m_rate)
            chart_min_rates.append(round(float(m['min_rate'] or 0), 2))
            chart_max_rates.append(round(float(m['max_rate'] or 0), 2))
            chart_default_rates.append(default_rate_val)
            chart_weights.append(round(m_wt, 2))
            chart_revenues.append(round(m_rev, 2))
            chart_trips.append(m['trips'])

        # 5. Party Breakdown on this Route
        parties_data = trips_qs.values('party__id', 'party__name').annotate(
            trips=Count('id'),
            total_weight=Sum('weight'),
            total_revenue=Sum('revenue_cached'),
            avg_rate=Avg('rate_per_ton'),
            min_rate=Min('rate_per_ton'),
            max_rate=Max('rate_per_ton'),
            latest_date=Max('date')
        ).order_by('-total_revenue')

        party_stats = []
        for p in parties_data:
            p_wt = p['total_weight'] or Decimal('0')
            p_rev = p['total_revenue'] or Decimal('0')
            p_avg = (p_rev / p_wt) if p_wt > 0 else (p['avg_rate'] or Decimal('0'))
            party_stats.append({
                'id': p['party__id'],
                'name': p['party__name'],
                'trips': p['trips'],
                'total_weight': p_wt,
                'total_revenue': p_rev,
                'avg_rate': p_avg,
                'min_rate': p['min_rate'] or Decimal('0'),
                'max_rate': p['max_rate'] or Decimal('0'),
                'latest_date': p['latest_date'],
            })

        # 6. Fleet Vehicle Breakdown
        vehicles_data = trips_qs.values(
            'vehicle__id', 'vehicle__registration_plate', 'vehicle__make_model'
        ).annotate(
            trips=Count('id'),
            total_weight=Sum('weight'),
            total_revenue=Sum('revenue_cached'),
            avg_rate=Avg('rate_per_ton')
        ).order_by('-trips')[:10]

        # 7. Recent Trips
        recent_trips = list(trips_qs.select_related(
            'vehicle', 'party', 'driver__user'
        ).with_billing_info().order_by('-date', '-created_at')[:15])

        for t in recent_trips:
            t.annotated_toll_expense = toll_map.get(t.id, Decimal('0.00'))

        # 8. All routes list for switcher dropdown
        all_routes = Route.objects.all().order_by('pickup_location', 'delivery_location')
        
        # 9. Parties participating in this route for the filter dropdown
        participating_party_ids = Trip.objects.filter(route=route).values_list('party_id', flat=True).distinct()
        participating_parties = Party.objects.filter(id__in=participating_party_ids).order_by('name')

        context.update({
            'period': period,
            'start_date': start_date_str or (start_date.strftime('%Y-%m-%d') if start_date else ''),
            'end_date': end_date_str or (end_date.strftime('%Y-%m-%d') if end_date else ''),
            'selected_party': int(party_id) if party_id and party_id.isdigit() else '',
            'selected_ownership': ownership,
            'participating_parties': participating_parties,

            # Toll Metrics
            'total_toll_expense': total_toll_expense,
            'avg_toll_per_trip': avg_toll_per_trip,
            'owned_trips_count': owned_trips_count,
            'trips_with_tolls_count': trips_with_tolls_count,

            # Metrics
            'total_trips': total_trips,
            'total_weight': total_weight,
            'total_revenue': total_revenue,
            'avg_rate': avg_rate,
            'weighted_avg_rate': weighted_avg_rate,
            'latest_rate': latest_rate,
            'default_rate': default_rate,
            'min_rate': min_rate,
            'max_rate': max_rate,
            'rate_spread': rate_spread,
            'rate_variance': rate_variance,
            'rate_variance_abs': rate_variance_abs,
            'avg_load_per_trip': avg_load_per_trip,

            # Breakdowns
            'party_stats': party_stats,
            'vehicle_stats': vehicles_data,
            'recent_trips': recent_trips,
            'all_routes': all_routes,

            # Charts JSON
            'chart_labels_json': json.dumps(chart_labels),
            'chart_avg_rates_json': json.dumps(chart_avg_rates),
            'chart_min_rates_json': json.dumps(chart_min_rates),
            'chart_max_rates_json': json.dumps(chart_max_rates),
            'chart_default_rates_json': json.dumps(chart_default_rates),
            'chart_weights_json': json.dumps(chart_weights),
            'chart_revenues_json': json.dumps(chart_revenues),
            'chart_trips_json': json.dumps(chart_trips),
            'has_chart_data': len(chart_labels) > 0,
        })
        return context


class RouteCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Route
    form_class = RouteForm
    template_name = 'trips/route_form.html'
    permission_required = 'trips.add_route'
    success_url = reverse_lazy('route-list')

    def form_valid(self, form):
        messages.success(self.request, 'Route created successfully.')
        return super().form_valid(form)


class RouteUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Route
    form_class = RouteForm
    template_name = 'trips/route_form.html'
    permission_required = 'trips.change_route'
    success_url = reverse_lazy('route-list')

    def form_valid(self, form):
        response = super().form_valid(form)
        if 'route_type' in form.changed_data or 'pickup_location' in form.changed_data or 'delivery_location' in form.changed_data:
            from trips.models import Trip
            for trip in Trip.objects.filter(route=self.object):
                if not trip.is_billed:
                    trip.save()
        messages.success(self.request, 'Route updated successfully.')
        return response


class RouteDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Route
    template_name = 'trips/route_confirm_delete.html'
    permission_required = 'trips.delete_route'
    success_url = reverse_lazy('route-list')

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, 'Route deleted successfully.')
        return super().delete(request, *args, **kwargs)
