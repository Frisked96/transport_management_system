"""
Toll Calculation Service for Trips and Routes.

Provides high-performance in-memory batch mapping of TollEntry records
to their corresponding Trip operational windows (trip.date to next_trip.date),
avoiding N+1 database queries when analyzing routes or vehicles.
"""
from decimal import Decimal
from collections import defaultdict
from django.db import models
from fleet.models import Vehicle
from ledger.models import TollEntry


def calculate_trips_toll_map(trips_iterable):
    """
    Given an iterable or queryset of Trip objects, calculates the total toll
    expense for each trip based on:
      1. Tolls explicitly associated with the trip (associated_trip=trip).
      2. Tolls incurred by the vehicle during the operational window
         from trip.date up to next_trip.date (for company-owned vehicles only).

    Returns a dict: {trip_id: Decimal(total_toll_amount)}
    """
    trips_list = list(trips_iterable)
    if not trips_list:
        return {}

    # Separate company-owned vehicle trips from market/attached trips
    # Only company-owned vehicles have FASTag toll expenses tracked
    owned_trips = [
        t for t in trips_list
        if t.vehicle_id and getattr(t.vehicle, 'ownership', None) == Vehicle.OWNERSHIP_OWNED
    ]

    result_map = {t.id: Decimal('0.00') for t in trips_list}

    # If trips already have associated_trip tolls regardless of ownership, map those
    trip_ids = [t.id for t in trips_list]
    explicit_tolls = TollEntry.objects.filter(associated_trip_id__in=trip_ids).values('associated_trip_id', 'amount')
    for et in explicit_tolls:
        tid = et['associated_trip_id']
        if tid in result_map:
            result_map[tid] += et['amount']

    if not owned_trips:
        return result_map

    from trips.models import Trip

    vehicle_ids = list(set(t.vehicle_id for t in owned_trips))

    # Pre-fetch all trips for these vehicles to accurately determine next_trip boundary
    all_veh_trips = list(
        Trip.objects.filter(vehicle_id__in=vehicle_ids)
        .order_by('vehicle_id', 'date', 'id')
        .values('id', 'vehicle_id', 'date')
    )

    next_trip_date_map = {}
    for i in range(len(all_veh_trips)):
        curr = all_veh_trips[i]
        nxt_date = None
        if i + 1 < len(all_veh_trips) and all_veh_trips[i + 1]['vehicle_id'] == curr['vehicle_id']:
            nxt_date = all_veh_trips[i + 1]['date']
        next_trip_date_map[curr['id']] = nxt_date

    # Pre-fetch all TollEntry records for these vehicles from earliest trip date
    earliest_date = min(t.date for t in owned_trips)
    tolls = list(
        TollEntry.objects.filter(vehicle_id__in=vehicle_ids, date__gte=earliest_date)
        .values('id', 'vehicle_id', 'date', 'amount', 'associated_trip_id')
    )

    tolls_by_veh = defaultdict(list)
    for te in tolls:
        tolls_by_veh[te['vehicle_id']].append(te)

    # In-memory window mapping
    for t in owned_trips:
        nxt_d = next_trip_date_map.get(t.id)
        veh_tolls = tolls_by_veh.get(t.vehicle_id, [])
        window_total = Decimal('0.00')

        for te in veh_tolls:
            # Skip explicit tolls already accounted for
            if te['associated_trip_id']:
                continue

            te_date = te['date']
            if nxt_d and nxt_d > t.date:
                if t.date <= te_date < nxt_d:
                    window_total += te['amount']
            elif nxt_d and nxt_d == t.date:
                if te_date == t.date:
                    window_total += te['amount']
            elif not nxt_d:
                if te_date >= t.date:
                    window_total += te['amount']

        result_map[t.id] += window_total

    return result_map


def calculate_route_toll_stats(trips_qs):
    """
    Computes route-level toll expense statistics for a filtered Trip queryset.
    Considers company-owned vehicle trips where toll data is tracked.

    Returns dict with:
      - total_toll: Decimal
      - avg_toll: Decimal (across all owned trips)
      - avg_toll_active: Decimal (across owned trips that had tolls > 0)
      - owned_trips_count: int
      - trips_with_tolls_count: int
      - toll_map: {trip_id: Decimal}
    """
    # Select related vehicle to check ownership
    trips_list = list(trips_qs.select_related('vehicle'))
    toll_map = calculate_trips_toll_map(trips_list)

    owned_trips = [
        t for t in trips_list
        if t.vehicle and t.vehicle.ownership == Vehicle.OWNERSHIP_OWNED
    ]

    total_toll = Decimal('0.00')
    trips_with_tolls_count = 0

    for t in owned_trips:
        amt = toll_map.get(t.id, Decimal('0.00'))
        if amt > Decimal('0.00'):
            total_toll += amt
            trips_with_tolls_count += 1

    owned_count = len(owned_trips)
    avg_toll = (total_toll / owned_count) if owned_count > 0 else Decimal('0.00')
    avg_toll_active = (total_toll / trips_with_tolls_count) if trips_with_tolls_count > 0 else Decimal('0.00')

    return {
        'total_toll': total_toll,
        'avg_toll': avg_toll,
        'avg_toll_active': avg_toll_active,
        'owned_trips_count': owned_count,
        'trips_with_tolls_count': trips_with_tolls_count,
        'toll_map': toll_map,
    }
