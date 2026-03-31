from django.utils import timezone
from datetime import timedelta
from django.db.models import Q, F, ExpressionWrapper, DateField
from .models import Document
from fleet.models import MaintenanceTask

def document_alerts(request):
    if not request.user.is_authenticated:
        return {}
    
    # Check if user is manager or superuser efficiently
    # Avoid group lookup if superuser
    is_manager = request.user.is_superuser or request.user.groups.filter(name='manager').exists()
    if not is_manager:
        return {}

    today = timezone.now().date()
    warning_date = today + timedelta(days=30)
    
    # Documents expiring soon
    expiring_docs = Document.objects.filter(
        never_expires=False,
        expiry_date__isnull=False,
        expiry_date__lte=warning_date,
        expiry_date__gte=today
    ).select_related('vehicle', 'driver', 'driver__user').order_by('expiry_date')
    
    # Documents already expired
    expired_docs = Document.objects.filter(
        never_expires=False,
        expiry_date__isnull=False,
        expiry_date__lt=today
    ).select_related('vehicle', 'driver', 'driver__user').order_by('expiry_date')

    # Maintenance Alerts
    all_active_tasks = MaintenanceTask.objects.filter(is_active=True).select_related('vehicle').only(
        'id', 'interval_km', 'interval_days', 'last_performed_km', 'last_performed_date', 
        'vehicle__current_odometer', 'vehicle__registration_plate'
    )
    
    due_maintenance_ids = []
    for task in all_active_tasks:
        if task.is_due:
            due_maintenance_ids.append(task.id)
    
    due_maintenance = MaintenanceTask.objects.filter(id__in=due_maintenance_ids).select_related('vehicle')

    total_alerts = expiring_docs.count() + expired_docs.count() + len(due_maintenance_ids)

    return {
        'expiring_docs': expiring_docs,
        'expired_docs': expired_docs,
        'due_maintenance': due_maintenance,
        'total_alerts': total_alerts
    }
