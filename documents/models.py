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
        ordering = ['expiry_date']

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
