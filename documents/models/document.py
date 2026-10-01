import os
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied


def document_upload_path(instance, filename):
    """
    Determines the upload path for a document.
    Format: documents/<identifier>/<filename>
    """
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
    BASE_VEHICLE_DOCUMENTS = [
        'Fitness',
        '1 yr permit',
        '5yr permit',
        'Insurance',
        'Tax',
        'RC',
        'Vltd cirtificate',
    ]

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

    valid_from = models.DateField(
        verbose_name='Valid From',
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

    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='Renewal Cost / Expense'
    )

    is_base_document = models.BooleanField(
        default=False,
        verbose_name='Is Base Document',
        help_text='Base compliance document that cannot be deleted'
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
    def valid_to(self):
        return self.expiry_date

    @valid_to.setter
    def valid_to(self, value):
        self.expiry_date = value

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

    @property
    def status(self):
        if self.never_expires:
            return 'Permanent'
        if not self.expiry_date:
            return 'Not Set'
        if self.is_expired:
            return 'Expired'
        if self.days_until_expiry is not None and self.days_until_expiry <= 30:
            return 'Expiring Soon'
        return 'Valid'

    @property
    def total_expenses(self):
        total = self.renewals.aggregate(total=models.Sum('cost'))['total']
        if total is not None:
            return total
        return self.cost or 0

    @classmethod
    def ensure_base_documents(cls, vehicle):
        """
        Ensures all 7 base compliance documents exist for the given vehicle.
        Idempotent and matches case-insensitively with standard synonyms.
        """
        if not vehicle or not vehicle.pk:
            return
        synonyms = {
            '1 yr permit': ['1 yr permit', '1 year permit', '1-year permit', '1yr permit'],
            '5yr permit': ['5yr permit', '5 yr permit', '5 year permit', '5-year permit', '5yr permit'],
            'Vltd cirtificate': ['vltd cirtificate', 'vltd certificate', 'vltd', 'vltd cert', 'vltd cirtificate'],
            'Fitness': ['fitness', 'fitness certificate'],
            'Insurance': ['insurance', 'insurance policy'],
            'Tax': ['tax', 'road tax', 'vehicle tax'],
            'RC': ['rc', 'registration certificate', 'rc book'],
        }
        existing_docs = list(cls.objects.filter(vehicle=vehicle))
        for base_name in cls.BASE_VEHICLE_DOCUMENTS:
            match_names = [s.lower() for s in synonyms.get(base_name, [base_name])]
            matched_doc = None
            for doc in existing_docs:
                if doc.document_name.strip().lower() in match_names:
                    matched_doc = doc
                    break
            if matched_doc:
                if not matched_doc.is_base_document:
                    matched_doc.is_base_document = True
                    matched_doc.save(update_fields=['is_base_document'])
            else:
                new_doc = cls.objects.create(
                    vehicle=vehicle,
                    document_name=base_name,
                    is_base_document=True
                )
                existing_docs.append(new_doc)

    @property
    def latest_renewal(self):
        """Returns the most recent renewal/validity record for this document"""
        return self.renewals.order_by('-valid_to', '-created_at').first()

    def sync_to_history(self, user=None):
        """
        Ensures the current validity and cost on this Document are recorded in DocumentRenewal history,
        and associates any unassigned uploaded files with the latest renewal history record.
        """
        if not (self.valid_from or self.expiry_date or self.cost > 0 or self.document_number or self.files.exists()):
            return
        from .document_renewal import DocumentRenewal
        latest = self.renewals.order_by('-valid_to', '-created_at').first()
        if latest and (latest.valid_to == self.expiry_date and latest.valid_from == self.valid_from):
            latest.cost = self.cost or 0
            latest.document_number = self.document_number
            latest.notes = self.notes
            if user and not latest.renewed_by:
                latest.renewed_by = user
            latest.save()
            # Associate any unassigned files with this renewal
            self.files.filter(renewal__isnull=True).update(renewal=latest)
        else:
            latest = DocumentRenewal.objects.create(
                document=self,
                valid_from=self.valid_from,
                valid_to=self.expiry_date,
                cost=self.cost or 0,
                document_number=self.document_number,
                notes=self.notes,
                renewed_by=user
            )
            # Associate any unassigned files with this renewal
            self.files.filter(renewal__isnull=True).update(renewal=latest)

    def delete(self, *args, force=False, **kwargs):
        is_vehicle_deleting = self.vehicle and getattr(self.vehicle, '_is_being_deleted', False)
        if self.is_base_document and not force and not is_vehicle_deleting:
            raise PermissionDenied("Base compliance documents cannot be deleted.")
        super().delete(*args, **kwargs)
