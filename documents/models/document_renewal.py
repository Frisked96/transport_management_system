import os
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver


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

    @property
    def file_extension(self):
        if self.receipt_file and self.receipt_file.name:
            return os.path.splitext(self.receipt_file.name)[1].lstrip('.').upper()
        return ''

    @property
    def is_pdf(self):
        return self.file_extension.lower() == 'pdf'

    @property
    def is_image(self):
        return self.file_extension.lower() in ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'svg']

    def sync_financial_record(self):
        """
        Synchronizes this renewal expense with the vehicle's company account in the ledger.
        - If the vehicle belongs to a CompanyAccount and cost > 0: creates or updates FinancialRecord.
        - If cost <= 0 or vehicle has no CompanyAccount: deletes any existing FinancialRecord.
        """
        from ledger.models import FinancialRecord, TransactionCategory

        vehicle = getattr(self.document, 'vehicle', None)
        record = FinancialRecord.objects.filter(associated_document_renewal=self).first()

        if vehicle and vehicle.company_account and self.cost and self.cost > 0:
            category, _ = TransactionCategory.objects.get_or_create(
                name='Document Renewal',
                defaults={
                    'type': TransactionCategory.TYPE_EXPENSE,
                    'description': 'Vehicle document renewal expenses'
                }
            )
            date = self.valid_from or (self.created_at.date() if self.created_at else timezone.now().date())
            description = f"Document renewal: {self.document.document_name} ({vehicle.registration_plate})"
            if self.document_number:
                description += f" - Doc #{self.document_number}"

            if record:
                updated = False
                if record.account != vehicle.company_account:
                    record.account = vehicle.company_account
                    updated = True
                if record.amount != self.cost:
                    record.amount = self.cost
                    updated = True
                if record.date != date:
                    record.date = date
                    updated = True
                if record.description != description:
                    record.description = description
                    updated = True
                if record.category != category:
                    record.category = category
                    updated = True
                if updated:
                    record.save()
            else:
                FinancialRecord.objects.create(
                    account=vehicle.company_account,
                    category=category,
                    record_type=FinancialRecord.RECORD_TYPE_TRANSACTION,
                    amount=self.cost,
                    date=date,
                    associated_document_renewal=self,
                    recorded_by=self.renewed_by,
                    description=description
                )
        else:
            if record:
                record.delete()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.sync_financial_record()
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


# --- Signals ---

@receiver(pre_save, sender=DocumentRenewal)
def delete_old_renewal_receipt_on_change(sender, instance, **kwargs):
    if not instance.pk:
        return False
    try:
        old_file = DocumentRenewal.objects.get(pk=instance.pk).receipt_file
    except DocumentRenewal.DoesNotExist:
        return False
    new_file = instance.receipt_file
    if old_file and old_file != new_file:
        old_file.delete(save=False)


@receiver(post_delete, sender=DocumentRenewal)
def delete_renewal_receipt_on_delete(sender, instance, **kwargs):
    if instance.receipt_file:
        instance.receipt_file.delete(save=False)
