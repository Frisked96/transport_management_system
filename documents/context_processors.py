from django.utils import timezone
from datetime import timedelta
from .models import Document
from fleet.models import MaintenanceTask

def document_alerts(request):
    if not request.user.is_authenticated:
        return {}
    if not (request.user.is_superuser or request.user.groups.filter(name='manager').exists()):
        return {}

    today = timezone.now().date()
    warning_date = today + timedelta(days=30)
    
    expiring_docs = Document.objects.filter(
        never_expires=False,
        expiry_date__isnull=False,
        expiry_date__lte=warning_date,
        expiry_date__gte=today
    ).order_by('expiry_date')
    
    expired_docs = Document.objects.filter(
        never_expires=False,
        expiry_date__isnull=False,
        expiry_date__lt=today
    ).order_by('expiry_date')

    # Maintenance Alerts
    all_active_tasks = MaintenanceTask.objects.filter(is_active=True).select_related('vehicle')
    due_maintenance = [task for task in all_active_tasks if task.is_due]

    total_alerts = expiring_docs.count() + expired_docs.count() + len(due_maintenance)

    return {
        'expiring_docs': expiring_docs,
        'expired_docs': expired_docs,
        'due_maintenance': due_maintenance,
        'total_alerts': total_alerts
    }
