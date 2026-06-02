import datetime
from django.core.cache import cache
from django.utils import timezone

class ActiveUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = timezone.now()
            cache.set(f'last-seen-{request.user.id}', now, 300) # Keep for 5 minutes
        
        response = self.get_response(request)
        return response
