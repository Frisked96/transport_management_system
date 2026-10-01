"""
Vehicle model for Fleet application
"""
from django.db import models
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver


class Vehicle(models.Model):
    """
    Vehicle model for fleet management
    """
    
    # Status choices
    STATUS_ACTIVE = 'Active'
    STATUS_MAINTENANCE = 'Maintenance'
    STATUS_RETIRED = 'Retired'
    
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_MAINTENANCE, 'Maintenance'),
        (STATUS_RETIRED, 'Retired'),
    ]

    # Ownership choices
    OWNERSHIP_OWNED = 'Owned'
    OWNERSHIP_ATTACHED = 'Attached'
    
    OWNERSHIP_CHOICES = [
        (OWNERSHIP_OWNED, 'Owned (Company Fleet)'),
        (OWNERSHIP_ATTACHED, 'Attached (Market Vehicle)'),
    ]
    
    # Vehicle registration plate (unique)
    registration_plate = models.CharField(
        max_length=20,
        unique=True,
        verbose_name='Registration Plate'
    )
    
    # Make and model
    make_model = models.CharField(
        max_length=200,
        verbose_name='Make & Model'
    )
    
    # Chassis and Engine numbers
    chassis_number = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Chassis Number'
    )
    
    engine_number = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Engine Number'
    )
    
    # Purchase date
    purchase_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='Purchase Date'
    )
    
    current_odometer = models.PositiveIntegerField(
        default=0,
        verbose_name='Current Odometer (km)'
    )

    # Status with choices
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        verbose_name='Vehicle Status'
    )
    
    ownership = models.CharField(
        max_length=20,
        choices=OWNERSHIP_CHOICES,
        default=OWNERSHIP_OWNED,
        verbose_name='Ownership Type'
    )
    
    vendor = models.ForeignKey(
        'ledger.Party',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attached_vehicles',
        verbose_name='Vendor / Owner',
        help_text='Required if Ownership Type is Attached'
    )
    
    # Deletion flag to prevent signals from trying to update a deleted object
    _is_being_deleted = False

    class Meta:
        verbose_name = 'Vehicle'
        verbose_name_plural = 'Vehicles'
        ordering = ['-registration_plate'] # Or -id/created_at if you want newest added. Registration plate descending might put newer ones on top if they follow a pattern. Let's use -id.
    
    def delete(self, *args, **kwargs):
        self._is_being_deleted = True
        self.documents.filter(is_base_document=True).update(is_base_document=False)
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.registration_plate} - {self.make_model}"
    
    @property
    def is_available(self):
        """Check if vehicle is available for assignment"""
        return self.status == self.STATUS_ACTIVE
    
    @property
    def is_attached(self):
        """Check if vehicle is an attached/market vehicle"""
        return self.ownership == self.OWNERSHIP_ATTACHED
    
    @property
    def chassie_number(self):
        """Alias for chassis_number"""
        return self.chassis_number
    
    @chassie_number.setter
    def chassie_number(self, value):
        self.chassis_number = value
    
    @property
    def last_maintenance(self):
        """Get the last maintenance log"""
        return self.maintenance_logs.order_by('-date').first()
    
    @property
    def next_due_maintenance(self):
        """Get the next due maintenance date from pending records"""
        next_record = self.maintenance_records.filter(
            is_completed=False
        ).order_by('expiry_date').first()
        if next_record:
            return next_record.expiry_date
        return None
    
    @property
    def total_maintenance_cost(self):
        """Calculate total maintenance cost from completed records"""
        return self.maintenance_records.filter(
            is_completed=True
        ).aggregate(
            total=models.Sum('cost')
        )['total'] or 0

    @property
    def base_documents(self):
        """Returns all 7 base compliance documents for this vehicle, creating them if missing"""
        from documents.models import Document
        Document.ensure_base_documents(self)
        return self.documents.filter(is_base_document=True).order_by('id')

    @property
    def custom_documents(self):
        """Returns all non-base (custom) documents for this vehicle"""
        return self.documents.filter(is_base_document=False).order_by('document_name')

    @property
    def total_compliance_expenses(self):
        """Calculate total cost of all document renewals and expenses for this vehicle"""
        from documents.models import DocumentRenewal
        total = DocumentRenewal.objects.filter(document__vehicle=self).aggregate(
            total=models.Sum('cost')
        )['total']
        return total or 0


@receiver(post_save, sender=Vehicle)
def create_vehicle_base_documents(sender, instance, created, **kwargs):
    if created:
        from documents.models import Document
        Document.ensure_base_documents(instance)
