"""
Toll ledger signals for TollAccount, TollRecharge, and TollEntry.
"""
from decimal import Decimal
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from ledger.models import TollAccount, TollRecharge, TollEntry, FinancialRecord


@receiver(pre_save, sender=TollAccount)
def sync_toll_account_on_opening_change(sender, instance, **kwargs):
    """
    If opening_balance is changed on a TollAccount, recalculate cached balance.
    """
    if instance.pk:
        try:
            old_inst = TollAccount.objects.get(pk=instance.pk)
            if old_inst.opening_balance != instance.opening_balance:
                total_recharges = instance.recharges.aggregate(total=Decimal('0.00'))['total'] or Decimal('0.00')
                total_spent = instance.entries.aggregate(total=Decimal('0.00'))['total'] or Decimal('0.00')
                instance.current_balance_cached = instance.opening_balance + total_recharges - total_spent
        except TollAccount.DoesNotExist:
            pass
    else:
        instance.current_balance_cached = instance.opening_balance


@receiver(pre_save, sender=TollRecharge)
def track_toll_recharge_changes(sender, instance, **kwargs):
    """Track previous state of TollRecharge"""
    if instance.pk:
        try:
            instance._old_instance = TollRecharge.objects.get(pk=instance.pk)
        except TollRecharge.DoesNotExist:
            instance._old_instance = None
    else:
        instance._old_instance = None


@receiver(post_save, sender=TollRecharge)
def update_toll_recharge_on_save(sender, instance, **kwargs):
    """
    When a TollRecharge is saved:
    1. Synchronize the corresponding FinancialRecord in the Company Account.
    2. Refresh the destination TollAccount balance.
    3. If the toll account changed, refresh the old toll account too.
    """
    # 1. Sync financial record
    instance.sync_financial_record()

    # 2. Refresh current toll account
    if instance.toll_account_id:
        try:
            instance.toll_account.refresh_balance()
        except TollAccount.DoesNotExist:
            pass

    # 3. Refresh old toll account if changed
    old_instance = getattr(instance, '_old_instance', None)
    if old_instance and old_instance.toll_account_id and old_instance.toll_account_id != instance.toll_account_id:
        try:
            old_inst = old_instance.toll_account
            if old_inst:
                old_inst.refresh_balance()
        except TollAccount.DoesNotExist:
            pass


@receiver(post_delete, sender=TollRecharge)
def cleanup_toll_recharge_on_delete(sender, instance, **kwargs):
    """
    When a TollRecharge is deleted:
    1. Delete the linked FinancialRecord (which restores CompanyAccount balance via signals).
    2. Refresh the TollAccount balance.
    """
    FinancialRecord.objects.filter(associated_toll_recharge_id=instance.pk).delete()

    if instance.toll_account_id:
        try:
            acc = instance.toll_account
            if acc:
                acc.refresh_balance()
        except TollAccount.DoesNotExist:
            pass


@receiver(pre_save, sender=TollEntry)
def track_toll_entry_changes(sender, instance, **kwargs):
    """Track previous state of TollEntry"""
    if instance.pk:
        try:
            instance._old_instance = TollEntry.objects.get(pk=instance.pk)
        except TollEntry.DoesNotExist:
            instance._old_instance = None
    else:
        instance._old_instance = None


@receiver(post_save, sender=TollEntry)
def update_toll_entry_on_save(sender, instance, **kwargs):
    """
    When a TollEntry is saved, refresh the TollAccount balance.
    Note: Does NOT modify CompanyAccount!
    """
    if instance.toll_account_id:
        try:
            instance.toll_account.refresh_balance()
        except TollAccount.DoesNotExist:
            pass

    old_instance = getattr(instance, '_old_instance', None)
    if old_instance and old_instance.toll_account_id and old_instance.toll_account_id != instance.toll_account_id:
        try:
            old_inst = old_instance.toll_account
            if old_inst:
                old_inst.refresh_balance()
        except TollAccount.DoesNotExist:
            pass


@receiver(post_delete, sender=TollEntry)
def update_toll_entry_on_delete(sender, instance, **kwargs):
    """
    When a TollEntry is deleted, refresh the TollAccount balance.
    """
    if instance.toll_account_id:
        try:
            acc = instance.toll_account
            if acc:
                acc.refresh_balance()
        except TollAccount.DoesNotExist:
            pass
