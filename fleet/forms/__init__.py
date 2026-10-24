"""
Modularized forms for Fleet application.
Exports all forms and choice fields for backward compatibility.
"""
from .vehicles import VehicleForm
from .maintenance import MaintenanceRecordForm, MaintenanceCompleteForm
from .tyres import (
    TyrePositionChoiceField,
    TyreForm,
    TyreBrandForm,
    TyreLogForm,
)

__all__ = [
    'VehicleForm',
    'MaintenanceRecordForm',
    'MaintenanceCompleteForm',
    'TyrePositionChoiceField',
    'TyreForm',
    'TyreBrandForm',
    'TyreLogForm',
]
