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
            path = getattr(request, 'path_info', '')
            if not (path.startswith('/static/') or path.startswith('/media/')):
                now = timezone.now()
                cache.set(f'last-seen-{request.user.id}', now, 300) # Fast cache for active presence (5 mins)
                
                # Throttled DB write: at most once every 120 seconds per user to prevent database load
                sync_key = f'last-seen-db-sync-{request.user.id}'
                if not cache.get(sync_key):
                    cache.set(sync_key, True, 120)
                    from .models import UserProfile
                    UserProfile.objects.filter(user_id=request.user.id).update(last_seen=now)
        
        response = self.get_response(request)
        
        # Cleanup
        if hasattr(_thread_locals, 'user'):
            del _thread_locals.user
            
        return response
