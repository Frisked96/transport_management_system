import os
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


def renewal_file_upload_path(instance, filename):
    """
    Determines the upload path for a renewal document/receipt.
    Format: documents/<identifier>/renewals/<filename>
    """
    document = instance.document
    if document.vehicle:
        identifier = str(document.vehicle.registration_plate).replace(' ', '_').replace('/', '-')
    elif document.driver:
        id_part = document.driver.employee_id or document.driver.name
        identifier = str(id_part).replace(' ', '_').replace('/', '-')
    else:
        identifier = 'miscellaneous'

    safe_name = "".join([c for c in document.document_name if c.isalnum() or c in (' ', '_', '-')]).strip().replace(' ', '_')
    ext = os.path.splitext(filename)[1]
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{safe_name}_renewal_{timestamp}{ext}"
    return os.path.join('documents', identifier, 'renewals', new_filename)


class DocumentRenewal(models.Model):
    """
    History log for document validity periods, renewal expenses, and metadata.
    """
    document = models.ForeignKey(
        'documents.Document',
        on_delete=models.CASCADE,
        related_name='renewals',
        verbose_name='Document'
    )
    document_number = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Document Number'
    )
    valid_from = models.DateField(
        null=True,
        blank=True,
        verbose_name='Valid From'
    )
    valid_to = models.DateField(
        null=True,
        blank=True,
        verbose_name='Valid To / Expiry'
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='Renewal Cost / Expense'
    )
    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notes'
    )
    receipt_file = models.FileField(
        upload_to=renewal_file_upload_path,
        null=True,
        blank=True,
        verbose_name='Receipt / Scanned Copy (Optional)'
    )
    renewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logged_document_renewals',
        verbose_name='Renewed By'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Document Renewal History'
        verbose_name_plural = 'Document Renewal Histories'
        ordering = ['-valid_to', '-created_at']

    def __str__(self):
        return f"{self.document.document_name} ({self.valid_from or 'N/A'} to {self.valid_to or 'N/A'}) - ₹{self.cost}"

    @property
    def is_expired(self):
        if not self.valid_to:
            return False
        return self.valid_to < timezone.now().date()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Resync parent document if this is the latest renewal
        latest = self.document.renewals.order_by('-valid_to', '-created_at').first()
        if latest and latest.pk == self.pk:
            doc = self.document
            updated_fields = []
            if doc.valid_from != self.valid_from:
                doc.valid_from = self.valid_from
                updated_fields.append('valid_from')
            if doc.expiry_date != self.valid_to:
                doc.expiry_date = self.valid_to
                updated_fields.append('expiry_date')
            if doc.cost != self.cost:
                doc.cost = self.cost
                updated_fields.append('cost')
            if self.document_number and doc.document_number != self.document_number:
                doc.document_number = self.document_number
                updated_fields.append('document_number')
            if updated_fields:
                doc.save(update_fields=updated_fields)

    def delete(self, *args, **kwargs):
        doc = self.document
        super().delete(*args, **kwargs)
        latest = doc.renewals.order_by('-valid_to', '-created_at').first()
        if latest:
            doc.valid_from = latest.valid_from
            doc.expiry_date = latest.valid_to
            doc.cost = latest.cost
            if latest.document_number:
                doc.document_number = latest.document_number
            doc.save(update_fields=['valid_from', 'expiry_date', 'cost', 'document_number'])
