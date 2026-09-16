from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.contrib.contenttypes.models import ContentType
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE, DELETION
from .middleware import get_current_user

# List of apps to track for activity
TRACKED_APPS = {'trips', 'fleet', 'ledger', 'drivers', 'documents', 'accounts', 'auth'}

# Internal or automated models that should NEVER appear in user activity logs
EXCLUDED_MODELS = {
    'sequence',
    'tripallocation',
    'billallocation',
    'billtrip',
    'documentfile',
    'userprofile',
    'logentry',
}

# Automated, cached, or secret fields that should not trigger or show in change diffs
IGNORED_FIELDS = {
    'updated_at',
    'created_at',
    'modified_at',
    'last_updated',
    'last_seen',
    'password',
    'current_balance_cached',
    'total_debit_amount',
    'total_credit_amount',
    'amount_received_cached',
    'gst_amount_cached',
    'tds_amount_cached',
}

@receiver(pre_save)
def snapshot_instance_before_save(sender, instance, **kwargs):
    """
    Take a snapshot of model fields before saving to detect exact changes on update.
    """
    if sender._meta.app_label not in TRACKED_APPS:
        return
    model_name = sender._meta.model_name.lower()
    if model_name in EXCLUDED_MODELS:
        return
    if not instance.pk:
        return

    try:
        existing = sender.objects.filter(pk=instance.pk).first()
        if existing:
            snapshot = {}
            for field in sender._meta.fields:
                if field.name in IGNORED_FIELDS:
                    continue
                snapshot[field.name] = getattr(existing, field.name, None)
            instance._activity_old_snapshot = snapshot
    except Exception:
        pass

def compute_field_diff(sender, instance):
    """
    Compare old snapshot with new instance fields to determine what changed.
    """
    snapshot = getattr(instance, '_activity_old_snapshot', None)
    if snapshot is None:
        return None

    changes = []
    for field in sender._meta.fields:
        fname = field.name
        if fname in IGNORED_FIELDS or fname not in snapshot:
            continue

        old_val = snapshot[fname]
        new_val = getattr(instance, fname, None)

        if old_val != new_val:
            verbose = field.verbose_name.title() if hasattr(field, 'verbose_name') else fname.replace('_', ' ').title()

            def format_val(val, fld):
                if val is None or val == '':
                    return 'Empty'
                if isinstance(val, bool):
                    return 'Yes' if val else 'No'
                if hasattr(val, 'strftime'):
                    return val.strftime('%d %b %Y')
                # If relation, attempt to show readable representation
                if fld.is_relation and fld.many_to_one:
                    try:
                        rel_obj = getattr(instance, fld.name, None)
                        if rel_obj and getattr(rel_obj, 'pk', None) == val:
                            return str(rel_obj)
                    except Exception:
                        pass
                return str(val)

            old_str = format_val(old_val, field)
            new_str = format_val(new_val, field)
            changes.append(f"{verbose}: {old_str} → {new_str}")

    return changes

def log_action(sender, instance, action_flag, **kwargs):
    try:
        # Ignore models not in tracked apps
        if sender._meta.app_label not in TRACKED_APPS:
            return
        model_name = sender._meta.model_name.lower()
        if model_name in EXCLUDED_MODELS:
            return

        user = get_current_user()
        if not user or not user.is_authenticated:
            return

        change_message = ''
        if action_flag == CHANGE:
            changes = compute_field_diff(sender, instance)
            # If we had a snapshot and nothing user-facing changed, skip logging to avoid false clutter!
            if changes is not None and len(changes) == 0:
                return

            if changes:
                if len(changes) > 4:
                    change_message = "; ".join(changes[:4]) + f" (+{len(changes)-4} more)"
                else:
                    change_message = "; ".join(changes)
            else:
                change_message = 'Updated record'
        elif action_flag == ADDITION:
            change_message = 'Added'
        elif action_flag == DELETION:
            change_message = 'Deleted'

        content_type = ContentType.objects.get_for_model(sender)
        try:
            object_repr = str(instance)[:200]
        except Exception:
            object_repr = f"{sender._meta.verbose_name} #{getattr(instance, 'pk', '')}"

        LogEntry.objects.create(
            user_id=user.pk,
            content_type_id=content_type.pk,
            object_id=str(instance.pk),
            object_repr=object_repr,
            action_flag=action_flag,
            change_message=change_message
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to log action for {sender.__name__}: {e}")

@receiver(post_save)
def create_or_update_log(sender, instance, created, **kwargs):
    action_flag = ADDITION if created else CHANGE
    log_action(sender, instance, action_flag, **kwargs)

@receiver(post_delete)
def delete_log(sender, instance, **kwargs):
    log_action(sender, instance, DELETION, **kwargs)
