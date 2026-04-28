from django.db import models
from django.utils import timezone

def document_upload_path(instance, filename):
    """
    Determines the upload path for a document.
    Format: documents/<identifier>/<filename>
    """
    import os
    if instance.vehicle:
        identifier = str(instance.vehicle.registration_plate).replace(' ', '_').replace('/', '-')
    elif instance.driver:
        # Prefer employee ID, fallback to name
        id_part = instance.driver.employee_id or instance.driver.name
        identifier = str(id_part).replace(' ', '_').replace('/', '-')
    else:
        identifier = 'miscellaneous'
    
    # We return the full path. The storage backend will handle folder creation.
    return os.path.join('documents', identifier, filename)

class Document(models.Model):
    """
    Document model for tracking expirations (Insurance, Permits, Licenses)
    """
    vehicle = models.ForeignKey(
        'fleet.Vehicle',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='documents',
        verbose_name='Vehicle'
    )
    driver = models.ForeignKey(
        'drivers.Driver',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='documents',
        verbose_name='Driver'
    )

    document_type = models.CharField(
        max_length=100,
        verbose_name='Document Type'
    )

    document_number = models.CharField(
        max_length=100,
        verbose_name='Document Number'
    )

    expiry_date = models.DateField(
        verbose_name='Expiry Date',
        null=True,
        blank=True
    )

    never_expires = models.BooleanField(
        default=False,
        verbose_name='Never Expires'
    )

    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notes'
    )

    scanned_copy = models.FileField(
        upload_to=document_upload_path,
        null=True,
        blank=True,
        verbose_name='Scanned Copy'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Document'
        verbose_name_plural = 'Documents'
        ordering = ['expiry_date', '-created_at']

    def __str__(self):
        return f"{self.document_type} - {self.document_number}"

    @property
    def is_expired(self):
        if self.never_expires or not self.expiry_date:
            return False
        return self.expiry_date < timezone.now().date()

    @property
    def days_until_expiry(self):
        if self.never_expires or not self.expiry_date:
            return None
        delta = self.expiry_date - timezone.now().date()
        return delta.days


# --- Signals ---
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

@receiver(pre_save, sender=Document)
def delete_old_document_file_on_change(sender, instance, **kwargs):
    """
    Deletes the old scanned copy from storage when a new one is uploaded.
    """
    if not instance.pk:
        return False

    try:
        old_file = Document.objects.get(pk=instance.pk).scanned_copy
    except Document.DoesNotExist:
        return False

    new_file = instance.scanned_copy
    if old_file and old_file != new_file:
        old_file.delete(save=False)

@receiver(post_delete, sender=Document)
def delete_document_file_on_delete(sender, instance, **kwargs):
    """
    Deletes the document's scanned copy from storage when the Document instance is deleted.
    """
    if instance.scanned_copy:
        instance.scanned_copy.delete(save=False)
