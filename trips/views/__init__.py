"""
Modularized views for Trips application.
Exports all views and mixins for backward compatibility.
"""
from .mixins import BaseTripPermissionMixin
from .trips import (
    TripListView,
    TripDetailView,
    TripBulkCreateView,
    TripCreateView,
    TripUpdateView,
    TripDeleteView,
)
from .dashboard import manager_dashboard
from .routes import (
    RouteListView,
    RoutesDashboardView,
    RouteDashboardView,
    RouteCreateView,
    RouteUpdateView,
    RouteDeleteView,
)
from .api import (
    reference_data,
    get_autocomplete_suggestions,
    trip_export_excel,
)

__all__ = [
    'BaseTripPermissionMixin',
    'TripListView',
    'TripDetailView',
    'TripBulkCreateView',
    'TripCreateView',
    'TripUpdateView',
    'TripDeleteView',
    'manager_dashboard',
    'RouteListView',
    'RoutesDashboardView',
    'RouteDashboardView',
    'RouteCreateView',
    'RouteUpdateView',
    'RouteDeleteView',
    'reference_data',
    'get_autocomplete_suggestions',
    'trip_export_excel',
]
