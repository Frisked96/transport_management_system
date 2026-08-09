from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE, DELETION
from .middleware import get_current_user
import json

# List of apps to track for activity
TRACKED_APPS = ['trips', 'fleet', 'ledger', 'drivers', 'documents', 'accounts']

def log_action(sender, instance, action_flag, **kwargs):
    # Ignore models not in tracked apps or LogEntry itself
    if sender._meta.app_label not in TRACKED_APPS:
        return
    if sender == LogEntry:
        return

    user = get_current_user()
    if not user or not user.is_authenticated:
        return

    content_type = ContentType.objects.get_for_model(sender)
    
    change_message = ''
    if action_flag == CHANGE:
        change_message = 'Changed'
    elif action_flag == ADDITION:
        change_message = 'Added'
    elif action_flag == DELETION:
        change_message = 'Deleted'

    # Handle object_repr being too long
    object_repr = str(instance)[:200]

    LogEntry.objects.create(
        user_id=user.pk,
        content_type_id=content_type.pk,
        object_id=str(instance.pk),
        object_repr=object_repr,
        action_flag=action_flag,
        change_message=change_message
    )

@receiver(post_save)
def create_or_update_log(sender, instance, created, **kwargs):
    action_flag = ADDITION if created else CHANGE
    log_action(sender, instance, action_flag, **kwargs)

@receiver(post_delete)
def delete_log(sender, instance, **kwargs):
    log_action(sender, instance, DELETION, **kwargs)
