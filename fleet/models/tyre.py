"""
Tyre models and signals for Fleet application
"""
import os
from django.db import models
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver
from .vehicle import Vehicle


class TyreBrand(models.Model):
    """
    Pre-defined tyre brands/makes with suggestive pricing.
    Used as reference data for tyre inventory management.
    """
    name = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='Brand / Make'
    )
    suggestive_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='Suggestive Price',
        help_text='Suggested purchase price for tyres of this brand'
    )
    vendor = models.ForeignKey(
        'ledger.Party',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={'party_type': 'Creditor'},
        verbose_name='Default Vendor',
        help_text='Vendor to automatically bill when a tyre of this brand is created'
    )

    class Meta:
        verbose_name = 'Tyre Brand'
        verbose_name_plural = 'Tyre Brands'
        ordering = ['name']

    def __str__(self):
        return self.name


def tyre_photo_upload_path(instance, filename):
    """
    Determines the upload path for a tyre photo with collision-proof naming.
    Format: tyres/<safe_serial_or_id>_<clean_filename>
    """
    ext = os.path.splitext(filename)[1]
    raw_name = os.path.splitext(filename)[0]
    safe_name = "".join([c for c in raw_name if c.isalnum() or c in ('_', '-')]).strip()
    identifier = instance.serial_number or f"tyre_{instance.pk or 'new'}"
    safe_id = "".join([c for c in identifier if c.isalnum() or c in ('_', '-')]).strip()
    new_filename = f"{safe_id}_{safe_name}{ext}" if safe_name else f"{safe_id}{ext}"
    return os.path.join('tyres', new_filename)


class Tyre(models.Model):
    """
    Inventory management for individual tyres.
    Simplified: No odometer tracking for tyres.
    """
    STATUS_IN_STOCK = 'In Stock'
    STATUS_MOUNTED = 'Mounted'
    STATUS_SCRAP = 'Scrap'
    STATUS_REPAIR = 'Under Repair'

    STATUS_CHOICES = [
        (STATUS_IN_STOCK, 'In Stock'),
        (STATUS_MOUNTED, 'Mounted'),
        (STATUS_REPAIR, 'Under Repair'),
        (STATUS_SCRAP, 'Scrap'),
    ]

    serial_number = models.CharField(max_length=100, unique=True, verbose_name='Serial Number')
    brand = models.CharField(max_length=100, verbose_name='Brand')
    size = models.CharField(max_length=50, blank=True, null=True, verbose_name='Size')
    purchase_date = models.DateField(null=True, blank=True, verbose_name='Purchase Date')
    purchase_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    vendor = models.ForeignKey(
        'ledger.Party',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={'party_type': 'Creditor'},
        verbose_name='Vendor'
    )
    
    current_vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tyres',
        verbose_name='Current Vehicle'
    )
    current_position = models.CharField(max_length=50, blank=True, verbose_name='Position')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_STOCK)
    photo = models.ImageField(upload_to=tyre_photo_upload_path, null=True, blank=True, verbose_name='Tyre Photo')
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.brand} {self.size} ({self.serial_number})"

    @property
    def added_by(self):
        initial_log = self.logs.order_by('created_at').first()
        return initial_log.logged_by if initial_log else None
        
    @property
    def added_at(self):
        initial_log = self.logs.order_by('created_at').first()
        return initial_log.created_at if initial_log else None

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        old_instance = None
        if not is_new:
            try:
                old_instance = Tyre.objects.get(pk=self.pk)
            except Tyre.DoesNotExist:
                is_new = True

        # Enforce status logic
        if self.current_vehicle:
            self.status = self.STATUS_MOUNTED
        elif self.status == self.STATUS_MOUNTED:
            # If no vehicle but status was mounted, set to in stock
            self.status = self.STATUS_IN_STOCK
            self.current_position = ''

        if not self.current_vehicle:
            self.current_position = ''

        super().save(*args, **kwargs)

        # Automatic Logging (skip if explicitly told to)
        if getattr(self, '_skip_auto_log', False):
            return

        current_user = getattr(self, '_user', None)

        if is_new:
            if self.current_vehicle:
                TyreLog.objects.create(
                    tyre=self,
                    action=TyreLog.ACTION_MOUNT,
                    vehicle=self.current_vehicle,
                    position=self.current_position,
                    notes="Initial mount on creation",
                    date=self.purchase_date or timezone.now().date(),
                    logged_by=current_user
                )
            else:
                TyreLog.objects.create(
                    tyre=self,
                    action=TyreLog.ACTION_STOCK,
                    notes="Initial addition to stock",
                    date=self.purchase_date or timezone.now().date(),
                    logged_by=current_user
                )

        else:
            # Check for changes in vehicle or position
            vehicle_changed = old_instance.current_vehicle != self.current_vehicle
            position_changed = old_instance.current_position != self.current_position

            if vehicle_changed:
                # Dismount from old vehicle if it existed
                if old_instance.current_vehicle:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_DISMOUNT,
                        vehicle=old_instance.current_vehicle,
                        position=old_instance.current_position,
                        notes=f"Automatic dismount: vehicle changed to {self.current_vehicle}" if self.current_vehicle else "Automatic dismount",
                        logged_by=current_user
                    )
                
                # Mount to new vehicle if it exists
                if self.current_vehicle:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_MOUNT,
                        vehicle=self.current_vehicle,
                        position=self.current_position,
                        notes=f"Automatic mount: vehicle changed from {old_instance.current_vehicle}" if old_instance.current_vehicle else "Automatic mount",
                        logged_by=current_user
                    )
            elif position_changed and self.current_vehicle:
                # Same vehicle, different position -> Rotation
                TyreLog.objects.create(
                    tyre=self,
                    action=TyreLog.ACTION_ROTATION,
                    vehicle=self.current_vehicle,
                    position=self.current_position,
                    notes=f"Position changed from {old_instance.current_position} to {self.current_position}",
                    logged_by=current_user
                )
            
            # Check for Status Changes (Repair/Scrap)
            status_changed = old_instance.status != self.status
            if status_changed:
                if self.status == self.STATUS_REPAIR:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_REPAIR,
                        notes="Status changed to Under Repair",
                        logged_by=current_user
                    )
                elif self.status == self.STATUS_SCRAP:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_SCRAP,
                        notes="Status changed to Scrap",
                        logged_by=current_user
                    )
                elif self.status == self.STATUS_IN_STOCK and old_instance.status == self.STATUS_REPAIR:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_STOCK,
                        notes="Repair completed, moved back to stock",
                        logged_by=current_user
                    )

        # Sync financial record for tyre purchase unconditionally
        self.sync_financial_record()

    def sync_financial_record(self):
        """
        Creates, updates, or deletes the ledger entry linked to this tyre purchase.
        """
        from ledger.models import FinancialRecord, TransactionCategory
        from django.utils import timezone
        
        final_vendor = self.vendor
        if not final_vendor and self.brand:
            try:
                tyre_brand = TyreBrand.objects.get(name=self.brand)
                final_vendor = tyre_brand.vendor
            except TyreBrand.DoesNotExist:
                pass
                
        # Find existing record
        record = FinancialRecord.objects.filter(associated_tyre=self).first()
        
        if self.purchase_cost > 0 and final_vendor:
            category, _ = TransactionCategory.objects.get_or_create(
                name='Tyre Purchase',
                defaults={
                    'type': TransactionCategory.TYPE_EXPENSE,
                    'description': 'Purchases of new tyres'
                }
            )
            
            if record:
                # Update existing record
                updated = False
                if record.amount != self.purchase_cost:
                    record.amount = self.purchase_cost
                    updated = True
                if record.party != final_vendor:
                    record.party = final_vendor
                    updated = True
                if self.purchase_date and record.date != self.purchase_date:
                    record.date = self.purchase_date
                    updated = True
                
                if record.record_type != FinancialRecord.RECORD_TYPE_INVOICE:
                    record.record_type = FinancialRecord.RECORD_TYPE_INVOICE
                    updated = True
                    
                if updated:
                    record.save()
            else:
                # Create new record
                FinancialRecord.objects.create(
                    date=self.purchase_date or getattr(self, '_created_date', None) or timezone.now().date(),
                    party=final_vendor,
                    record_type=FinancialRecord.RECORD_TYPE_INVOICE,
                    category=category,
                    amount=self.purchase_cost,
                    associated_tyre=self,
                    description=f"Auto-generated entry for tyre purchase: {self.serial_number} ({self.brand})"
                )
        else:
            # Delete record if it exists but condition is no longer met
            if record:
                record.delete()


class TyreLog(models.Model):
    """
    History of tyre movements and repairs.
    Simplified: No odometer tracking for tyres.
    """
    ACTION_MOUNT = 'Mount'
    ACTION_DISMOUNT = 'Dismount'
    ACTION_ROTATION = 'Rotation'
    ACTION_REPAIR = 'Repair'
    ACTION_REMOLD = 'Remold'
    ACTION_SCRAP = 'Scrap'
    ACTION_STOCK = 'Stock'

    ACTION_CHOICES = [
        (ACTION_MOUNT, 'Mount'),
        (ACTION_DISMOUNT, 'Dismount'),
        (ACTION_ROTATION, 'Rotation'),
        (ACTION_REPAIR, 'Repair'),
        (ACTION_REMOLD, 'Remold'),
        (ACTION_SCRAP, 'Scrap'),
        (ACTION_STOCK, 'Stock'),
    ]

    tyre = models.ForeignKey(Tyre, on_delete=models.CASCADE, related_name='logs')
    date = models.DateField(default=timezone.now)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    position = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)

    logged_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logged_tyre_actions',
        verbose_name='Logged By'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at', '-id']

    def __str__(self):
        try:
            tyre_str = str(self.tyre) if self.tyre else "No Tyre"
        except ObjectDoesNotExist:
            tyre_str = f"Tyre #{self.tyre_id}"
        return f"{tyre_str} - {self.action} on {self.date}"


# --- Signals ---

@receiver(pre_save, sender=Tyre)
def delete_old_tyre_photo_on_change(sender, instance, **kwargs):
    """
    Deletes the old photo from storage when a new one is uploaded.
    """
    if not instance.pk:
        return False

    try:
        old_photo = Tyre.objects.get(pk=instance.pk).photo
    except Tyre.DoesNotExist:
        return False

    new_photo = instance.photo
    if old_photo and old_photo != new_photo:
        old_photo.delete(save=False)


@receiver(post_delete, sender=Tyre)
def delete_tyre_photo_on_delete(sender, instance, **kwargs):
    """
    Deletes the tyre's photo from storage when the Tyre instance is deleted.
    """
    if instance.photo:
        # save=False prevents the model from trying to save itself after deletion
        instance.photo.delete(save=False)
