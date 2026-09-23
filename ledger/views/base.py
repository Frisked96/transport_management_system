"""
Base mixins and shared utilities for ledger views.
"""
from decimal import Decimal
from ledger.utils import format_indian_comma, format_balance, parse_date_range


class BaseLedgerPermissionMixin:
    """Base mixin for ledger permissions"""
    
    def has_manager_permission(self):
        """Check if user has manager dashboard permission"""
        return self.request.user.has_perm('trips.can_view_manager_dashboard')
    
    def has_supervisor_permission(self):
        """Check if user has view financial records permission"""
        return self.request.user.has_perm('ledger.can_view_financial_records')
    
    def has_driver_profile(self):
        """Check if user has an associated driver profile"""
        return hasattr(self.request.user, 'driver_profile')

    def has_driver_permission(self):
        """Check if user has driver access (is a driver)"""
        return self.has_driver_profile()
