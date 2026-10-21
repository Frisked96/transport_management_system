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

    today = timezone.now().date()
    warning_date = today + timedelta(days=30)
    
    expiring_docs_qs = Document.objects.filter(
        never_expires=False,
        expiry_date__isnull=False,
        expiry_date__lte=warning_date,
        expiry_date__gte=today
    ).order_by('expiry_date')
    
    expired_docs_qs = Document.objects.filter(
        never_expires=False,
        expiry_date__isnull=False,
        expiry_date__lt=today
    ).order_by('expiry_date')

    # 2. Maintenance Alerts (Moved logic to SQL)
    due_maintenance_qs = MaintenanceRecord.objects.filter(
        is_completed=False
    ).filter(
        Q(expiry_date__lte=today) | 
        Q(expiry_km__lte=F('vehicle__current_odometer'))
    )

    # Counts are efficient in SQL (uses SELECT COUNT(*))
    expiring_count = expiring_docs_qs.count()
    expired_count = expired_docs_qs.count()
    due_maint_count = due_maintenance_qs.count()
    total_alerts = expiring_count + expired_count + due_maint_count

    # Sliced querysets for the navbar dropdown (top 10 most urgent to prevent memory bloat)
    return {
        'expiring_docs': expiring_docs_qs.select_related('vehicle', 'driver', 'driver__user')[:10],
        'expired_docs': expired_docs_qs.select_related('vehicle', 'driver', 'driver__user')[:10],
        'due_maintenance': due_maintenance_qs.select_related('vehicle')[:10],
        'total_alerts': total_alerts,
        'is_manager': is_manager,
    }
