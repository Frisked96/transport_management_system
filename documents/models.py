from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

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

def document_file_upload_path(instance, filename):
    """
    Determines the upload path for a document file with renaming logic.
    Format: documents/<identifier>/<DocumentName>_<index>.<ext>
    """
    import os
    document = instance.document
    if document.vehicle:
        identifier = str(document.vehicle.registration_plate).replace(' ', '_').replace('/', '-')
    elif document.driver:
        # Prefer employee ID, fallback to name
        id_part = document.driver.employee_id or document.driver.name
        identifier = str(id_part).replace(' ', '_').replace('/', '-')
    else:
        identifier = 'miscellaneous'
    
    ext = os.path.splitext(filename)[1]
    
    # Sanitize document name for filename
    safe_doc_name = "".join([c for c in document.document_name if c.isalnum() or c in (' ', '_', '-')]).strip().replace(' ', '_')
    
    # Add document number to filename if it exists
    name_part = safe_doc_name
    if document.document_number:
        safe_doc_num = "".join([c for c in document.document_number if c.isalnum() or c in (' ', '_', '-')]).strip().replace(' ', '_')
        name_part = f"{safe_doc_name}_{safe_doc_num}"

    # Check for index (passed from view)
    index = getattr(instance, '_upload_index', None)
    if index is None:
        # Fallback to counting existing files if not set
        index = document.files.count() + 1
    
    new_filename = f"{name_part}_{index}{ext}"
    
    return os.path.join('documents', identifier, new_filename)

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

    document_name = models.CharField(
        max_length=100,
        verbose_name='Document Name'
    )

    document_number = models.CharField(
        max_length=100,
        verbose_name='Document Number',
        null=True,
        blank=True
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

    added_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_documents',
        verbose_name='Added By'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Document'
        verbose_name_plural = 'Documents'
        ordering = ['expiry_date', '-created_at']

    def __str__(self):
        if self.document_number:
            return f"{self.document_name} - {self.document_number}"
        return self.document_name

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

class DocumentFile(models.Model):
    """
    Model to support multiple files for a single document
    """
    UPLOAD_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('uploading', 'Uploading'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='files',
        verbose_name='Document'
    )
    file = models.FileField(
        upload_to=document_file_upload_path,
        verbose_name='File',
        null=True,
        blank=True
    )
    
    upload_status = models.CharField(
        max_length=20,
        choices=UPLOAD_STATUS_CHOICES,
        default='pending',
        verbose_name='Upload Status'
    )
    
    local_tmp_path = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        verbose_name='Local Temp Path'
    )
    
    error_message = models.TextField(
        null=True,
        blank=True,
        verbose_name='Error Message'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Document File'
        verbose_name_plural = 'Document Files'
        ordering = ['created_at']

    def __str__(self):
        return f"File for {self.document.document_name} ({self.get_upload_status_display()})"


# --- Signals ---
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

@receiver(pre_save, sender=DocumentFile)
def delete_old_docfile_on_change(sender, instance, **kwargs):
    if not instance.pk:
        return False
    try:
        old_file = DocumentFile.objects.get(pk=instance.pk).file
    except DocumentFile.DoesNotExist:
        return False
    new_file = instance.file
    if old_file and old_file != new_file:
        old_file.delete(save=False)

@receiver(post_delete, sender=DocumentFile)
def delete_docfile_on_delete(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
