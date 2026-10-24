"""
Permission mixins for Trips views.
"""
from trips.models import Trip


class BaseTripPermissionMixin:
    """Base mixin for trip permissions"""
    
    def has_driver_profile(self):
        """Check if user has an associated driver profile"""
        return hasattr(self.request.user, 'driver_profile')

    def has_driver_permission(self):
        """Check if user has driver access (is a driver)"""
        return self.has_driver_profile()
    
    def get_queryset_for_user(self):
        """Filter trips based on user permissions"""
        user = self.request.user
        
        # Admin or user with explicit permission can see all trips
        if user.is_superuser or user.has_perm('trips.can_view_all_trips'):
            return Trip.objects.all()
        
        # Driver can only see their own trips
        if hasattr(user, 'driver_profile'):
            return Trip.objects.filter(driver=user.driver_profile)
        
        # Default: no trips
        return Trip.objects.none()
