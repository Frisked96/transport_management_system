from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

User = get_user_model()

class DriverAuthBackend(ModelBackend):
    """
    Custom authentication backend that allows drivers to login
    without a password (using just their username).
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        try:
            # Try to fetch the user by username
            user = User.objects.get(username=username)
            
            # Check if the user has a driver profile
            if hasattr(user, 'driver_profile'):
                # Success! Return the user, bypassing password check
                return user
                
        except User.DoesNotExist:
            return None
            
        # If user exists but is not a driver, return None
        # (ModelBackend will handle the standard password check for them)
        return None
