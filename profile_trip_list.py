#!/usr/bin/env python3
"""
Performance Profiler for Trip List View in Transport Management System (TMS)

This script executes and profiles the complete loading cycle of TripListView,
measuring latency, CPU time, phase-by-phase breakdown (View/Context vs. Template
Rendering), exact database queries, redundant prefetches, and execution bottlenecks.

Usage:
    python profile_trip_list.py                     # Profiles key scenarios & filter archetypes
    python profile_trip_list.py --scenario search   # Profile specific scenario
    python profile_trip_list.py --sql               # Dump all executed SQL queries
    python profile_trip_list.py --cprofile          # Profile Python CPU hotspots
    python profile_trip_list.py --benchmark         # Compares Current vs. Optimized simulation
"""

import os
import sys
import time
import argparse
import cProfile
import pstats
import io
from collections import Counter, defaultdict
from decimal import Decimal

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'transport_mgmt.settings')

import django
django.setup()

from django.conf import settings
from django.test import RequestFactory
from django.contrib.auth.models import User
from django.db import connection, reset_queries
from django.db.models import Sum, Q, Exists, OuterRef, Case, When, Value, F, CharField
from trips.models import Trip, Route
from trips.views import TripListView


class TerminalColors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'

    @classmethod
    def disable(cls):
        cls.HEADER = ''
        cls.BLUE = ''
        cls.CYAN = ''
        cls.GREEN = ''
        cls.YELLOW = ''
        cls.RED = ''
        cls.BOLD = ''
        cls.DIM = ''
        cls.RESET = ''


def get_authenticated_user():
    """Returns a superuser or staff user for authenticating requests."""
    return User.objects.filter(is_superuser=True).first() or User.objects.filter(is_staff=True).first() or User.objects.first()


def categorize_query(sql):
    """Classifies a SQL query by subsystem/model."""
    s = sql.upper().strip()
    if 'DJANGO_SESSION' in s:
        return 'Session / Auth'
    if 'AUTH_USER' in s or 'ACCOUNTS_USERPROFILE' in s or 'AUTH_GROUP' in s:
        return 'User & Auth / Permissions'
    if 'DOCUMENTS_DOCUMENT' in s or 'FLEET_MAINTENANCERECORD' in s:
        return 'Alerts / Context Processors'
    if 'LEDGER_TRANSACTIONCATEGORY' in s:
        return 'TransactionCategory (Unneeded Prefetch)'
    if 'LEDGER_FINANCIALRECORD' in s:
        return 'FinancialRecord (Unneeded Prefetch)'
    if 'LEDGER_BILLTRIP' in s or 'LEDGER_BILL' in s:
        if 'WHERE "LEDGER_BILLTRIP"."TRIP_ID" IN' in s or 'ORIGINAL_BILL_ID' in s:
            return 'Bill Tree (Unneeded Prefetch)'
        return 'Bill Check / Subquery'
    if 'TRIPS_TRIP' in s or 'TRIPS_ROUTE' in s or 'FLEET_VEHICLE' in s or 'LEDGER_PARTY' in s:
        if 'COUNT(*)' in s:
            return 'Pagination Count Query'
        if 'SUM(' in s:
            return 'Total Weight Aggregate Query'
        return 'Trip List Primary Query'
    return 'Other'


def analyze_queries(queries):
    """Analyzes a list of Django queries to detect duplicates and categorize time."""
    normalized_queries = []
    category_counts = Counter()
    category_times = defaultdict(float)

    for q in queries:
        sql = q['sql'].strip()
        time_spent = float(q.get('time', 0.0))
        cat = categorize_query(sql)
        category_counts[cat] += 1
        category_times[cat] += time_spent

        # Simple normalization for duplicate detection
        normalized = ' '.join(sql.split())
        normalized_queries.append((normalized, time_spent, cat, sql))

    counts = Counter(item[0] for item in normalized_queries)
    duplicates = [(norm, count) for norm, count in counts.items() if count > 1]
    total_db_time = sum(float(q.get('time', 0.0)) for q in queries)

    return {
        'total_count': len(queries),
        'total_time_ms': total_db_time * 1000,
        'category_counts': category_counts,
        'category_times': category_times,
        'duplicates': duplicates,
        'queries': normalized_queries,
    }


def execute_profile(query_params='', user=None, view_cls=TripListView, show_sql=False, run_cprofile=False):
    """Executes a profile run of TripListView with the given query params."""
    if user is None:
        user = get_authenticated_user()

    rf = RequestFactory()
    url = f"/{query_params}" if query_params else "/"
    request = rf.get(url)
    request.user = user

    settings.DEBUG = True
    reset_queries()

    pr = None
    if run_cprofile:
        pr = cProfile.Profile()
        pr.enable()

    t_start = time.perf_counter()
    view = view_cls.as_view()
    response = view(request)
    t_view = time.perf_counter()

    content = response.render()
    t_render = time.perf_counter()

    if run_cprofile and pr:
        pr.disable()

    queries = list(connection.queries)
    analysis = analyze_queries(queries)

    total_time_ms = (t_render - t_start) * 1000
    view_time_ms = (t_view - t_start) * 1000
    render_time_ms = (t_render - t_view) * 1000

    profile_result = {
        'url': url,
        'status_code': response.status_code,
        'total_time_ms': total_time_ms,
        'view_time_ms': view_time_ms,
        'render_time_ms': render_time_ms,
        'db_analysis': analysis,
        'cprofile': pr,
    }

    if show_sql:
        profile_result['raw_queries'] = queries

    return profile_result


# Optimized Simulation Class for Benchmarking
class OptimizedTripListView(TripListView):
    """
    Optimized version of TripListView that:
    1. Removes 7 unneeded nested prefetches (bills, records, categories, etc.)
    2. Removes select_related('driver__user') since driver is not in the template
    3. Reuses self.object_list and avoids redundant annotations in aggregate(Sum('weight'))
    4. Removes .distinct() in search where joins are strictly many-to-one
    """
    def get_queryset(self):
        # We start with the user-filtered trips and only select related vehicle, party, route
        user = self.request.user
        if user.is_superuser or user.has_perm('trips.can_view_all_trips'):
            queryset = Trip.objects.all()
        elif hasattr(user, 'driver_profile'):
            queryset = Trip.objects.filter(driver=user.driver_profile)
        else:
            return Trip.objects.none()

        # Search functionality without redundant distinct()
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(trip_number__icontains=search) |
                Q(party__name__icontains=search) |
                Q(pickup_location__icontains=search) |
                Q(delivery_location__icontains=search) |
                Q(route__pickup_location__icontains=search) |
                Q(route__delivery_location__icontains=search) |
                Q(vehicle__registration_plate__icontains=search) |
                Q(lr_no__icontains=search)
            )

        lr_search = self.request.GET.get('lr_search')
        if lr_search:
            queryset = queryset.filter(lr_no__icontains=lr_search)

        party_id = self.request.GET.get('party')
        if party_id:
            queryset = queryset.filter(party_id=party_id)

        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(payment_status_cached=status)

        billing_status = self.request.GET.get('billing_status')
        if billing_status:
            from ledger.models import Bill
            billed_filter = Exists(
                Bill.objects.filter(trips=OuterRef('pk'), customer_bill__isnull=True).exclude(party__party_type='Creditor')
            )
            if billing_status == 'unbilled':
                queryset = queryset.filter(~billed_filter)
            elif billing_status == 'billed':
                queryset = queryset.filter(billed_filter)

        gst_filter = self.request.GET.get('gst_filter')
        if gst_filter:
            if gst_filter == 'gst':
                queryset = queryset.filter(
                    Q(gst_type_snapshot__in=['GST', 'IGST']) |
                    (Q(gst_type_snapshot='') & ~Q(route__route_type='none'))
                )
            elif gst_filter == 'non_gst':
                queryset = queryset.filter(
                    Q(gst_type_snapshot='NONE') |
                    (Q(gst_type_snapshot='') & Q(route__route_type='none'))
                )

        # Quick Date filter
        date_filter = self.request.GET.get('date_filter')
        today = django.utils.timezone.now().date()
        if date_filter == 'today':
            queryset = queryset.filter(date=today)
        elif date_filter == 'yesterday':
            from datetime import timedelta
            queryset = queryset.filter(date=today - timedelta(days=1))
        elif date_filter == 'last_7_days':
            from datetime import timedelta
            queryset = queryset.filter(date__gte=today - timedelta(days=7))

        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        exact_date = self.request.GET.get('exact_date')

        if exact_date:
            try:
                queryset = queryset.filter(date=exact_date)
            except (ValueError, TypeError):
                pass
        else:
            if start_date:
                try:
                    queryset = queryset.filter(date__gte=start_date)
                except (ValueError, TypeError):
                    pass
            if end_date:
                try:
                    queryset = queryset.filter(date__lte=end_date)
                except (ValueError, TypeError):
                    pass

        # Apply sorting
        sort = self.request.GET.get('sort', '-date')
        sort_mapping = {
            'date': 'date',
            '-date': '-date',
            'trip_number': 'trip_number',
            '-trip_number': '-trip_number',
            'weight': 'weight',
            '-weight': '-weight',
            'revenue': 'revenue_cached',
            '-revenue': '-revenue_cached',
        }
        if sort in sort_mapping:
            queryset = queryset.order_by(sort_mapping[sort], '-created_at')
        else:
            queryset = queryset.order_by('-date', '-created_at')

        # Add payment & billing info and select_related ONLY for models actually displayed
        queryset = queryset.with_payment_info().with_billing_info().select_related(
            'vehicle', 'party', 'route'
        )
        # Prefetches explicitly removed
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Trip.PAYMENT_STATUS_CHOICES
        context['current_status'] = self.request.GET.get('status', '')
        context['current_billing_status'] = self.request.GET.get('billing_status', '')
        context['current_gst_filter'] = self.request.GET.get('gst_filter', '')
        context['search_term'] = self.request.GET.get('search', '')
        context['current_lr_search'] = self.request.GET.get('lr_search', '')
        context['start_date'] = self.request.GET.get('start_date', '')
        context['end_date'] = self.request.GET.get('end_date', '')
        context['exact_date'] = self.request.GET.get('exact_date', '')
        context['current_sort'] = self.request.GET.get('sort', '-date')
        context['current_party'] = self.request.GET.get('party', '')
        context['date_filter'] = self.request.GET.get('date_filter', '')

        # Reuse existing object_list without re-instantiating get_queryset()
        base_qs = self.object_list
        context['total_weight'] = base_qs.aggregate(total_weight=Sum('weight'))['total_weight'] or 0
        context['total_count'] = context['paginator'].count if 'paginator' in context else base_qs.count()

        return context


def print_single_report(res, title="Trip List Profile"):
    C = TerminalColors
    print(f"\n{C.HEADER}{C.BOLD}{'='*80}{C.RESET}")
    print(f"{C.HEADER}{C.BOLD}  {title}: {res['url']}{C.RESET}")
    print(f"{C.HEADER}{C.BOLD}{'='*80}{C.RESET}")

    print(f"\n{C.BOLD}Latency & Execution Breakdown:{C.RESET}")
    print(f"  • Total Request Latency:  {C.BOLD}{res['total_time_ms']:.2f} ms{C.RESET}")
    print(f"  • View / Context Stage:   {res['view_time_ms']:.2f} ms")
    print(f"  • Template Render Stage:  {res['render_time_ms']:.2f} ms")
    print(f"  • Database Query Time:    {res['db_analysis']['total_time_ms']:.2f} ms")

    db = res['db_analysis']
    print(f"\n{C.BOLD}Database Metrics:{C.RESET}")
    print(f"  • Total Queries Fired:    {C.BOLD}{C.RED if db['total_count'] > 12 else C.GREEN}{db['total_count']}{C.RESET}")
    print(f"  • Duplicate Queries:      {C.BOLD}{C.RED if db['duplicates'] else C.GREEN}{len(db['duplicates'])}{C.RESET}")

    print(f"\n{C.BOLD}Query Categorization Breakdown:{C.RESET}")
    for cat, count in db['category_counts'].most_common():
        t = db['category_times'][cat] * 1000
        highlight = C.YELLOW if 'Prefetch' in cat else C.DIM
        print(f"  {highlight}• {cat:<32} {count:>3} queries ({t:>6.2f} ms){C.RESET}")

    if db['duplicates']:
        print(f"\n{C.RED}{C.BOLD}Duplicate Query Patterns Detected:{C.RESET}")
        for norm, cnt in db['duplicates']:
            print(f"  {C.RED}• ({cnt}x): {norm[:120]}...{C.RESET}")


def run_benchmark_comparison(user):
    """Compares Current vs Optimized across archetypes."""
    C = TerminalColors
    print(f"\n{C.HEADER}{C.BOLD}{'='*80}{C.RESET}")
    print(f"{C.HEADER}{C.BOLD}  TripListView Performance Benchmark: Current vs. Optimized{C.RESET}")
    print(f"{C.HEADER}{C.BOLD}{'='*80}{C.RESET}")

    scenarios = [
        ('Default (Page 1)', ''),
        ('Pagination (Page 2)', '?page=2'),
        ('General Search (?search=MH)', '?search=MH'),
        ('Filter Status: Paid', '?status=Paid'),
        ('Filter Status: Unpaid', '?status=Unpaid'),
        ('Filter Billing: Unbilled', '?billing_status=unbilled'),
        ('Filter Billing: Billed', '?billing_status=billed'),
        ('Filter GST: GST', '?gst_filter=gst'),
        ('Sort by Revenue DESC', '?sort=-revenue'),
    ]

    header_fmt = "{:<28} | {:^17} | {:^17} | {:^13}"
    print(f"\n{C.BOLD}" + header_fmt.format("Scenario", "Current (Q / Time)", "Optimized (Q / Time)", "Query Reduction") + f"{C.RESET}")
    print("-" * 84)

    for desc, query_str in scenarios:
        # Warmup both
        execute_profile(query_str, user=user, view_cls=TripListView)
        execute_profile(query_str, user=user, view_cls=OptimizedTripListView)

        # Timed runs
        cur = execute_profile(query_str, user=user, view_cls=TripListView)
        opt = execute_profile(query_str, user=user, view_cls=OptimizedTripListView)

        q_cur = cur['db_analysis']['total_count']
        t_cur = cur['total_time_ms']
        q_opt = opt['db_analysis']['total_count']
        t_opt = opt['total_time_ms']
        reduction = f"{q_cur} -> {q_opt} (-{q_cur - q_opt})"

        cur_str = f"{q_cur:2d} q / {t_cur:5.1f}ms"
        opt_str = f"{q_opt:2d} q / {t_opt:5.1f}ms"

        print(header_fmt.format(desc, cur_str, opt_str, reduction))

    print("-" * 84)


def main():
    parser = argparse.ArgumentParser(description="Profile TripListView performance in TMS.")
    parser.add_argument('--scenario', type=str, default='', help='Specific GET query string (e.g. "?search=MH")')
    parser.add_argument('--sql', action='store_true', help='Print all SQL queries executed.')
    parser.add_argument('--cprofile', action='store_true', help='Output cProfile top CPU consumers.')
    parser.add_argument('--benchmark', action='store_true', help='Run before/after comparison against optimized simulation.')
    parser.add_argument('--no-color', action='store_true', help='Disable colored terminal output.')

    args = parser.parse_args()

    if args.no_color:
        TerminalColors.disable()

    user = get_authenticated_user()
    if not user:
        print("Error: No authenticated user found in database.")
        sys.exit(1)

    # Warmup Django template cache and URL resolvers
    execute_profile('', user=user)

    if args.benchmark:
        run_benchmark_comparison(user)
        return

    # Single or default profile
    res = execute_profile(args.scenario, user=user, show_sql=args.sql, run_cprofile=args.cprofile)
    print_single_report(res)

    if args.sql and 'raw_queries' in res:
        print(f"\n{TerminalColors.BOLD}Detailed SQL Query Log:{TerminalColors.RESET}")
        for i, q in enumerate(res['raw_queries'], 1):
            cat = categorize_query(q['sql'])
            print(f"\n  [{i}] ({q.get('time', '0')}s) [{cat}]")
            print(f"      {q['sql']}")

    if args.cprofile and res['cprofile']:
        print(f"\n{TerminalColors.BOLD}cProfile Top 25 Functions by Internal Time:{TerminalColors.RESET}")
        s = io.StringIO()
        ps = pstats.Stats(res['cprofile'], stream=s).sort_stats('tottime')
        ps.print_stats(25)
        print(s.getvalue())


if __name__ == '__main__':
    main()
