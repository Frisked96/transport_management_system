"""
Permission mixins for Fleet views.
"""


class BaseFleetPermissionMixin:
    """Base mixin for fleet permissions"""
    
    def has_driver_profile(self):
        """Check if user has an associated driver profile"""
        return hasattr(self.request.user, 'driver_profile')

    def has_driver_permission(self):
        """Check if user has driver access (is a driver)"""
        return self.has_driver_profile()
