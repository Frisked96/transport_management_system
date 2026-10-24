"""
Modularized views for Fleet application.
Exports all views and mixins for backward compatibility.
"""
from .mixins import BaseFleetPermissionMixin
from .vehicles import (
    VehicleListView,
    VehicleDetailView,
    VehicleCreateView,
    VehicleUpdateView,
    vehicle_bulk_update,
    VehicleDeleteView,
)
from .maintenance import (
    MaintenanceListView,
    MaintenanceRecordCreateView,
    MaintenanceRecordUpdateView,
    MaintenanceRecordDetailView,
    MaintenanceRecordDeleteView,
    maintenance_record_complete,
)
from .tyres import (
    TyreListView,
    TyreDetailView,
    TyreCreateView,
    TyreUpdateView,
    TyreDeleteView,
    TyreBrandListView,
    TyreBrandCreateView,
    TyreBrandUpdateView,
    TyreBrandDeleteView,
    TyreLogCreateView,
    tyre_log_delete,
    tyre_quick_action,
    tyre_photo_serve,
)

__all__ = [
    'BaseFleetPermissionMixin',
    'VehicleListView',
    'VehicleDetailView',
    'VehicleCreateView',
    'VehicleUpdateView',
    'vehicle_bulk_update',
    'VehicleDeleteView',
    'MaintenanceListView',
    'MaintenanceRecordCreateView',
    'MaintenanceRecordUpdateView',
    'MaintenanceRecordDetailView',
    'MaintenanceRecordDeleteView',
    'maintenance_record_complete',
    'TyreListView',
    'TyreDetailView',
    'TyreCreateView',
    'TyreUpdateView',
    'TyreDeleteView',
    'TyreBrandListView',
    'TyreBrandCreateView',
    'TyreBrandUpdateView',
    'TyreBrandDeleteView',
    'TyreLogCreateView',
    'tyre_log_delete',
    'tyre_quick_action',
    'tyre_photo_serve',
]
