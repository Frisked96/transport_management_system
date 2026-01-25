from django.db import models
from django.utils import timezone

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
        verbose_name='Expiry Date'
    )

    scanned_copy = models.FileField(
        upload_to='documents/%Y/%m/',
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
        return self.expiry_date < timezone.now().date()

    @property
    def days_until_expiry(self):
        delta = self.expiry_date - timezone.now().date()
        return delta.days
