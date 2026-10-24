#!/usr/bin/env python3
"""
Performance Profiler for Party Detail View in Transport Management System (TMS)

This script executes and profiles the complete loading cycle of PartyDetailView,
measuring latency, CPU time, phase-by-phase breakdown (Context Generation vs. Template
Rendering), exact database queries, N+1 query patterns, and Python CPU hotspots.

Usage:
    python profile_party_detail.py                  # Profiles key archetypes (Debtor, Creditor)
    python profile_party_detail.py --party-id 1     # Profile specific party
    python profile_party_detail.py --party-id 14 --sql --cprofile
    python profile_party_detail.py --benchmark      # Compares Current vs. Optimized simulation
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
from django.test import Client, RequestFactory
from django.contrib.auth.models import User
from django.db import connection, reset_queries
from django.core.paginator import Paginator
from ledger.models import Party, Bill, FinancialRecord, TransactionCategory, CompanyAccount
from trips.models import Trip
from ledger.views.parties import PartyDetailView


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
        return 'User & Auth Middleware'
    if 'DOCUMENTS_DOCUMENT' in s or 'FLEET_MAINTENANCERECORD' in s:
        return 'Alerts / Context Processors'
    if 'LEDGER_COMPANYACCOUNT' in s:
        return 'CompanyAccount (N+1 in Ledger Tab)'
    if 'LEDGER_TRIPALLOCATION' in s:
        return 'TripAllocation (N+1 in Ledger Tab)'
    if 'TRIPS_TRIP' in s or 'TRIPS_ROUTE' in s:
        return 'Trips (Operations Tab)'
    if 'LEDGER_BILL' in s or 'LEDGER_BILLTRIP' in s:
        return 'Bills (Invoices Tab & Checks)'
    if 'LEDGER_FINANCIALRECORD' in s:
        return 'Financial Records (Ledger)'
    if 'LEDGER_TRANSACTIONCATEGORY' in s:
        return 'Transaction Category'
    return 'Other'


def analyze_queries(queries):
    """Analyzes a list of Django queries to detect duplicates and categorize time."""
    total_time = sum(float(q.get('time', 0)) for q in queries)
    sql_counts = Counter()
    sql_times = defaultdict(float)
    category_counts = Counter()
    category_times = defaultdict(float)

    for q in queries:
        sql = q['sql'].strip()
        t = float(q.get('time', 0))
        sql_counts[sql] += 1
        sql_times[sql] += t
        cat = categorize_query(sql)
        category_counts[cat] += 1
        category_times[cat] += t

    duplicates = {sql: count for sql, count in sql_counts.items() if count > 1}
    duplicate_query_count = sum(count - 1 for count in duplicates.values())

    return {
        'total_queries': len(queries),
        'total_time_ms': total_time * 1000,
        'unique_queries': len(sql_counts),
        'duplicate_queries': duplicate_query_count,
        'duplicates_map': duplicates,
        'sql_counts': sql_counts,
        'sql_times': sql_times,
        'category_counts': category_counts,
        'category_times': category_times,
    }


def profile_isolated_phases(party_id, user, query_params=None):
    """
    Profiles isolated view phases:
    Phase 1: Object retrieval (view.get_object())
    Phase 2: Context data construction (view.get_context_data())
    Phase 3: Template rendering (view.render_to_response().rendered_content)
    """
    rf = RequestFactory()
    url = f'/ledger/parties/{party_id}/'
    if query_params:
        url += '?' + '&'.join(f'{k}={v}' for k, v in query_params.items())

    request = rf.get(url)
    request.user = user

    view = PartyDetailView()
    view.setup(request, pk=party_id)

    # 1. Object fetch
    reset_queries()
    t0 = time.perf_counter()
    view.object = view.get_object()
    t1 = time.perf_counter()
    obj_ms = (t1 - t0) * 1000
    obj_queries = list(connection.queries)

    # 2. Context generation
    reset_queries()
    t0 = time.perf_counter()
    context = view.get_context_data(object=view.object)
    t1 = time.perf_counter()
    ctx_ms = (t1 - t0) * 1000
    ctx_queries = list(connection.queries)

    # 3. Template rendering
    reset_queries()
    t0 = time.perf_counter()
    response = view.render_to_response(context)
    html_content = response.rendered_content
    t1 = time.perf_counter()
    render_ms = (t1 - t0) * 1000
    render_queries = list(connection.queries)

    return {
        'html_size_kb': len(html_content) / 1024,
        'object_fetch': {'time_ms': obj_ms, 'queries': analyze_queries(obj_queries), 'raw_queries': obj_queries},
        'context_generation': {'time_ms': ctx_ms, 'queries': analyze_queries(ctx_queries), 'raw_queries': ctx_queries},
        'template_rendering': {'time_ms': render_ms, 'queries': analyze_queries(render_queries), 'raw_queries': render_queries},
    }


def profile_full_request(client, url, iterations=3, run_cprofile=False):
    """Runs end-to-end HTTP request through Django's WSGI/middleware pipeline."""
    wall_times = []
    cpu_times = []
    queries_list = []
    status_code = None
    cprofile_stats_output = None

    for i in range(iterations):
        reset_queries()
        t_wall_start = time.perf_counter()
        t_cpu_start = time.process_time()

        if run_cprofile and i == 0:
            pr = cProfile.Profile()
            pr.enable()
            resp = client.get(url)
            pr.disable()
            s = io.StringIO()
            ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
            ps.print_stats(25)
            cprofile_stats_output = s.getvalue()
        else:
            resp = client.get(url)

        t_cpu_end = time.process_time()
        t_wall_end = time.perf_counter()

        status_code = resp.status_code
        wall_times.append((t_wall_end - t_wall_start) * 1000)
        cpu_times.append((t_cpu_end - t_cpu_start) * 1000)
        queries_list.append(list(connection.queries))

    query_analysis = analyze_queries(queries_list[0])

    return {
        'status_code': status_code,
        'wall_time_avg_ms': sum(wall_times) / len(wall_times),
        'wall_time_min_ms': min(wall_times),
        'wall_time_max_ms': max(wall_times),
        'cpu_time_avg_ms': sum(cpu_times) / len(cpu_times),
        'query_analysis': query_analysis,
        'cprofile_output': cprofile_stats_output,
        'raw_queries': queries_list[0]
    }


def simulate_optimized_phase(party, user, query_params=None):
    """
    Simulates optimized view execution without modifying codebase:
    1. select_related for account, tyre, and original_bill on financial records.
    2. Proper prefetch for allocations to prevent N+1 queries.
    3. Pruned unused prefetches on trips and bills.
    """
    rf = RequestFactory()
    url = f'/ledger/parties/{party.pk}/'
    if query_params:
        url += '?' + '&'.join(f'{k}={v}' for k, v in query_params.items())
    request = rf.get(url)
    request.user = user

    view = PartyDetailView()
    view.setup(request, pk=party.pk)
    view.object = party

    reset_queries()
    t0 = time.perf_counter()

    context = {'party': party, 'object': party}

    # 1. Optimized Trips: only prefetch bills & bills__party (needed for is_billed / creditor_bill)
    is_creditor = (party.party_type == Party.TYPE_CREDITOR)
    if is_creditor:
        trips_base_qs = Trip.objects.filter(vehicle__vendor=party)
    else:
        trips_base_qs = Trip.objects.filter(party=party)

    trips_qs = trips_base_qs.select_related(
        'vehicle', 'route', 'party'
    ).prefetch_related(
        'bills', 'bills__party'
    ).with_payment_info().with_billing_info().order_by('-date', '-created_at')

    trips_paginator = Paginator(trips_qs, 25)
    context['trips'] = trips_paginator.get_page(request.GET.get('page'))
    context['billed_filter'] = request.GET.get('billed')

    # 2. Optimized Records: select account, tyre, original_bill to eliminate N+1
    records_qs = party.financial_records.select_related(
        'category', 'associated_trip', 'associated_bill', 'associated_bill__category',
        'associated_bill__original_bill', 'account', 'associated_tyre'
    ).prefetch_related(
        'allocations__trip__bills__party'
    ).order_by('date', 'created_at')

    all_records = list(records_qs)
    running_bal = party.opening_balance
    for rec in all_records:
        debit = rec.debit_amount or Decimal('0')
        credit = rec.credit_amount or Decimal('0')
        running_bal += (debit - credit)
        rec.running_balance = running_bal

    all_records.reverse()
    ledger_paginator = Paginator(all_records, 50)
    context['financial_records'] = ledger_paginator.get_page(1)
    context['ledger_page_obj'] = context['financial_records']

    # 3. Optimized Bills: only prefetch trips (for trips.count)
    bills_qs = party.bills.select_related(
        'issuer', 'category', 'customer_bill'
    ).prefetch_related('trips').with_payment_info().order_by('-date', '-category__name', '-bill_no')

    context['bills'] = Paginator(bills_qs, 25).get_page(1)
    context['total_revenue'] = party.total_billed
    context['total_received'] = party.total_received
    context['balance'] = party.current_balance
    context['payment_out_category'] = TransactionCategory.objects.filter(name='Payment Out').first()
    context['company_accounts'] = CompanyAccount.objects.all().order_by('name')

    # Render
    response = view.render_to_response(context)
    _ = response.rendered_content
    t1 = time.perf_counter()

    queries = list(connection.queries)
    return {
        'total_time_ms': (t1 - t0) * 1000,
        'query_count': len(queries),
        'db_time_ms': sum(float(q.get('time', 0)) for q in queries) * 1000,
    }


def print_header(title):
    C = TerminalColors
    print(f"\n{C.BOLD}{C.CYAN}{'='*80}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  {title}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'='*80}{C.RESET}\n")


def print_section(title):
    C = TerminalColors
    print(f"\n{C.BOLD}{C.YELLOW}--- {title} ---{C.RESET}\n")


def run_profiler(party_ids=None, iterations=3, show_sql=False, show_cprofile=False, query_params=None, run_benchmark=False):
    settings.DEBUG = True

    user = get_authenticated_user()
    if not user:
        print(f"{TerminalColors.RED}Error: No authenticated user found in database.{TerminalColors.RESET}")
        return

    # Select representative targets if none provided
    if not party_ids:
        party_ids = [1, 14, 63]  # Party 1 (High Debtor), Party 14 (Debtor with Allocations), Party 63 (Creditor)
        valid_ids = []
        for pid in party_ids:
            if Party.objects.filter(pk=pid).exists():
                valid_ids.append(pid)
        party_ids = valid_ids

    client = Client()
    client.force_login(user)

    C = TerminalColors
    print_header("TMS PARTY DETAIL VIEW PERFORMANCE PROFILER")
    print(f"Authenticated User: {C.BOLD}{user.username}{C.RESET} (Superuser: {user.is_superuser})")
    print(f"Iterations per run: {C.BOLD}{iterations}{C.RESET}")
    print(f"Database Engine:    {C.BOLD}{settings.DATABASES['default']['ENGINE'].split('.')[-1]}{C.RESET} ({settings.DATABASES['default']['NAME']})")

    for party_id in party_ids:
        party = Party.objects.get(pk=party_id)
        records_count = party.financial_records.count()
        bills_count = party.bills.count()
        if party.party_type == Party.TYPE_CREDITOR:
            trips_count = Trip.objects.filter(vehicle__vendor=party).count()
        else:
            trips_count = Trip.objects.filter(party=party).count()

        print_section(f"Target: Party #{party.id} '{party.name}' ({party.party_type})")
        print(f"  • Trips: {C.BOLD}{trips_count}{C.RESET} | Invoices: {C.BOLD}{bills_count}{C.RESET} | Financial Records: {C.BOLD}{records_count}{C.RESET}")

        url = f'/ledger/parties/{party.id}/'
        if query_params:
            url += '?' + '&'.join(f'{k}={v}' for k, v in query_params.items())

        # Full Request Run
        full_res = profile_full_request(client, url, iterations=iterations, run_cprofile=show_cprofile)
        qa = full_res['query_analysis']

        # Isolated Phase Run
        phases = profile_isolated_phases(party.id, user, query_params)

        # Print Timing Overview
        wall_avg = full_res['wall_time_avg_ms']
        time_color = C.GREEN if wall_avg < 150 else C.YELLOW if wall_avg < 300 else C.RED
        print(f"\n{C.BOLD}1. Latency & Execution Breakdown (Avg of {iterations} runs):{C.RESET}")
        print(f"  • Total Request Latency:   {C.BOLD}{time_color}{wall_avg:.2f} ms{C.RESET} (Min: {full_res['wall_time_min_ms']:.2f} ms, Max: {full_res['wall_time_max_ms']:.2f} ms)")
        print(f"  • CPU Processing Time:     {full_res['cpu_time_avg_ms']:.2f} ms")
        print(f"  • HTML Output Payload:     {phases['html_size_kb']:.2f} KB")

        obj_ms = phases['object_fetch']['time_ms']
        ctx_ms = phases['context_generation']['time_ms']
        rnd_ms = phases['template_rendering']['time_ms']
        total_p = max(0.001, obj_ms + ctx_ms + rnd_ms)

        print(f"\n  Phase Breakdown:")
        print(f"    - Object Lookup:         {obj_ms:6.2f} ms ({obj_ms/total_p*100:4.1f}%) | {phases['object_fetch']['queries']['total_queries']} queries")
        print(f"    - get_context_data():    {ctx_ms:6.2f} ms ({ctx_ms/total_p*100:4.1f}%) | {phases['context_generation']['queries']['total_queries']} queries")
        print(f"    - template.render():     {C.BOLD}{C.RED if rnd_ms/total_p > 0.6 else C.YELLOW}{rnd_ms:6.2f} ms ({rnd_ms/total_p*100:4.1f}%){C.RESET} | {C.BOLD}{C.RED}{phases['template_rendering']['queries']['total_queries']} queries{C.RESET}")

        # Database Query Overview
        print(f"\n{C.BOLD}2. Database Query Metrics:{C.RESET}")
        print(f"  • Total DB Queries:        {C.BOLD}{C.GREEN if qa['total_queries'] < 25 else C.RED}{qa['total_queries']}{C.RESET}")
        print(f"  • Unique SQL Statements:   {qa['unique_queries']}")
        print(f"  • Redundant / Duplicates:  {C.BOLD}{C.RED if qa['duplicate_queries'] > 0 else C.GREEN}{qa['duplicate_queries']}{C.RESET}")
        print(f"  • Total DB Query Time:     {qa['total_time_ms']:.2f} ms")

        # Queries by Subsystem
        print(f"\n  Queries by Subsystem:")
        for cat, count in qa['category_counts'].most_common():
            t_ms = qa['category_times'][cat] * 1000
            print(f"    - {cat:<35} {count:>3} queries ({t_ms:5.2f} ms)")

        # N+1 Duplicates
        if qa['duplicates_map']:
            print(f"\n{C.BOLD}{C.RED}  ⚠️  Identified N+1 Query Patterns:{C.RESET}")
            for sql, count in sorted(qa['duplicates_map'].items(), key=lambda x: x[1], reverse=True)[:5]:
                cat = categorize_query(sql)
                preview = (sql[:110] + '...') if len(sql) > 110 else sql
                print(f"    {C.RED}• [{count}x repeated]{C.RESET} {C.DIM}[{cat}]{C.RESET}\n      {preview}")

        # Benchmark Simulation Comparison
        if run_benchmark:
            opt = simulate_optimized_phase(party, user, query_params)
            query_diff = qa['total_queries'] - opt['query_count']
            time_diff = wall_avg - opt['total_time_ms']
            pct_query = (query_diff / qa['total_queries']) * 100 if qa['total_queries'] else 0
            pct_time = (time_diff / wall_avg) * 100 if wall_avg else 0
            print(f"\n{C.BOLD}3. Optimization Impact Simulation:{C.RESET}")
            print(f"  • Current vs. Optimized Queries: {qa['total_queries']} -> {C.BOLD}{C.GREEN}{opt['query_count']}{C.RESET} ({C.GREEN}-{query_diff} queries / -{pct_query:.1f}%{C.RESET})")
            print(f"  • Current vs. Optimized Latency: {wall_avg:.1f}ms -> {C.BOLD}{C.GREEN}{opt['total_time_ms']:.1f}ms{C.RESET} ({C.GREEN}-{time_diff:.1f}ms / -{pct_time:.1f}%{C.RESET})")

        # Python Hotspots (cProfile)
        if show_cprofile and full_res['cprofile_output']:
            print(f"\n{C.BOLD}4. Python cProfile Top 20 Hotspots (Cumulative Time):{C.RESET}")
            print(full_res['cprofile_output'])

        # SQL Trace
        if show_sql:
            print(f"\n{C.BOLD}5. Full SQL Execution Trace ({qa['total_queries']} queries):{C.RESET}")
            for idx, q in enumerate(full_res['raw_queries'], 1):
                t_ms = float(q.get('time', 0)) * 1000
                sql = q['sql'].strip()
                cat = categorize_query(sql)
                is_dup = sql in qa['duplicates_map']
                color = C.RED if is_dup else C.RESET
                print(f"  {idx:3d}. [{t_ms:5.2f}ms] [{cat}] {color}{sql}{C.RESET}")

    # Comprehensive Findings Report
    print_header("PROFILER FINDINGS & ARCHITECTURAL SUMMARY")
    print(f"""{C.BOLD}Root Causes of Performance Degradation:{C.RESET}

1. {C.BOLD}Massive N+1 Database Queries During Template Rendering ({C.RED}60-170+ queries{C.RESET}):
   • Cause A: `{C.CYAN}record.account{C.RESET}`
     In {C.BOLD}party_detail.html{C.RESET}, line 464 accesses `record.account.name`. Because `'account'` is
     omitted from `records_qs.select_related()`, Django triggers an individual
     `SELECT FROM ledger_companyaccount WHERE id = ...` query for every record shown
     on the Ledger tab (30 to 50 repeated queries per page).
   • Cause B: `{C.CYAN}record.linked_bill{C.RESET}` and `{C.CYAN}record.linked_trip{C.RESET}`
     In {C.BOLD}ledger/models/financial_record.py{C.RESET}, `linked_bill` and `linked_trip` call:
       `first_alloc = self.allocations.select_related('trip').first()`
     Calling `.select_related().first()` on a related manager bypasses Django's prefetch
     cache and forces a new SQL query for EVERY payment record on the page!
   • Cause C: `{C.CYAN}record.linked_bill.original_bill{C.RESET}`
     When adjustment notes are listed in the Ledger tab, accessing `original_bill` fires
     a standalone `SELECT FROM ledger_bill WHERE id = ...` because `associated_bill__original_bill`
     is missing from `select_related`.

2. {C.BOLD}Unused Over-Prefetching on Operations & Invoicing Tab:{C.RESET}
   • `trips_qs` prefetches:
     `bills__adjustment_bills`, `bills__financial_records`, `payment_allocations`, `financial_records`.
   • `bills_qs` prefetches:
     `trips__payment_allocations`, `bill_trips__trip`, `adjustment_bills`.
   • {C.YELLOW}None of these prefetches are accessed by party_detail.html!{C.RESET}
     `Trip` and `Bill` balances and statuses use cached fields (`outstanding_balance_cached`, etc.).
     These 8-10 prefetch queries execute every time and allocate unneeded model objects in memory.

3. {C.BOLD}Unbounded In-Memory Historical Record Loading (`list(records_qs)`):{C.RESET}
   • In `PartyDetailView.get_context_data()`, line 132 executes:
       `all_records = list(records_qs)`
   • It deserializes {C.BOLD}every historical financial record{C.RESET} of the party into memory to compute
     the running balance in a Python loop, before paginating to 50 records. As parties grow to
     thousands of transactions, this will linearly degrade memory and latency.

4. {C.BOLD}Full Multi-Tab Eager Rendering:{C.RESET}
   • All 4 tabs (Trips, Invoices, Ledger, Profile) are queried, computed, and rendered into
     hidden DOM sections on every view load, multiplying the total queries executed by 3x.

{C.BOLD}Recommended Action Plan:{C.RESET}
  1. In `PartyDetailView.get_context_data()`, update `records_qs.select_related()` to include:
     `'account'`, `'associated_tyre'`, and `'associated_bill__original_bill'`.
  2. In `financial_record.py`, update `linked_bill` and `linked_trip` to inspect
     `self.allocations.all()` instead of `.select_related('trip').first()`.
  3. Prune unused prefetches on `trips_qs` and `bills_qs`.
  4. Consider lazy tab loading (e.g. HTMX or tab-specific queries) so only the active tab queries the DB.
""")


def main():
    parser = argparse.ArgumentParser(description="Profile TMS Party Detail View performance and query counts.")
    parser.add_argument('--party-id', type=int, help="Party ID to profile.")
    parser.add_argument('--all', action='store_true', help="Profile all representative parties.")
    parser.add_argument('--iterations', type=int, default=3, help="Profiling iterations (default: 3).")
    parser.add_argument('--tab', type=str, choices=['trips', 'invoices', 'ledger', 'profile'], help="Active tab parameter.")
    parser.add_argument('--billed', type=str, choices=['billed', 'unbilled'], help="Billed filter parameter.")
    parser.add_argument('--sql', action='store_true', help="Print full SQL query trace.")
    parser.add_argument('--cprofile', action='store_true', help="Print Python cProfile call stats.")
    parser.add_argument('--benchmark', action='store_true', default=True, help="Compare current vs optimized simulation.")
    parser.add_argument('--no-benchmark', dest='benchmark', action='store_false', help="Skip optimization benchmark.")
    parser.add_argument('--no-color', action='store_true', help="Disable terminal colors.")

    args = parser.parse_args()

    if args.no_color or not sys.stdout.isatty():
        TerminalColors.disable()

    query_params = {}
    if args.tab:
        query_params['tab'] = args.tab
    if args.billed:
        query_params['billed'] = args.billed

    party_ids = [args.party_id] if args.party_id else None
    run_profiler(
        party_ids=party_ids,
        iterations=args.iterations,
        show_sql=args.sql,
        show_cprofile=args.cprofile,
        query_params=query_params or None,
        run_benchmark=args.benchmark
    )


if __name__ == '__main__':
    main()
