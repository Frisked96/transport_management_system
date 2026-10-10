from django.utils import timezone
from datetime import timedelta
from django.db import models
from django.db.models import Q, F
from .models import Document
from fleet.models import MaintenanceRecord

def document_alerts(request):
    """
    Returns counts and querysets for expiring/expired documents and maintenance alerts.
    Uses optimized database-level logic to avoid N+1 and memory issues.
    """
    if not request.user.is_authenticated:
        return {}
    
    # Manager check - cached on request if possible
    is_manager = getattr(request, '_is_manager', None)
    if is_manager is None:
        is_manager = request.user.is_superuser or request.user.has_perm('trips.can_view_manager_dashboard')
        request._is_manager = is_manager
        
    if not is_manager:
        return {}

    today = timezone.now().date()
    warning_date = today + timedelta(days=30)
    
    # 1. Documents (Efficient Counts + Limited QuerySets)
    expiring_docs = Document.objects.filter(
        never_expires=False,
        expiry_date__isnull=False,
        expiry_date__lte=warning_date,
        expiry_date__gte=today
    ).select_related('vehicle', 'driver', 'driver__user').order_by('expiry_date')
    
    expired_docs = Document.objects.filter(
        never_expires=False,
        expiry_date__isnull=False,
        expiry_date__lt=today
    ).select_related('vehicle', 'driver', 'driver__user').order_by('expiry_date')

    # 2. Maintenance Alerts (Moved logic to SQL)
    due_maintenance = MaintenanceRecord.objects.filter(
        is_completed=False
    ).filter(
        Q(expiry_date__lte=today) | 
        Q(expiry_km__lte=F('vehicle__current_odometer'))
    ).select_related('vehicle')

    # counts are efficient in Django (uses SELECT COUNT(*))
    total_alerts = expiring_docs.count() + expired_docs.count() + due_maintenance.count()

    return {
        'expiring_docs': expiring_docs,
        'expired_docs': expired_docs,
        'due_maintenance': due_maintenance,
        'total_alerts': total_alerts
    }
