import datetime
import threading
from django.core.cache import cache
from django.utils import timezone

_thread_locals = threading.local()

def get_current_user():
    return getattr(_thread_locals, 'user', None)

class ActiveUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_locals.user = getattr(request, 'user', None)
        
        if request.user.is_authenticated:
            now = timezone.now()
            cache.set(f'last-seen-{request.user.id}', now, 300) # Keep for 5 minutes
        
        response = self.get_response(request)
        
        # Cleanup
        if hasattr(_thread_locals, 'user'):
            del _thread_locals.user
            
        return response
