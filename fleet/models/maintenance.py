"""
MaintenanceRecord model for Fleet application
"""
from django.db import models
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.utils import timezone
from .vehicle import Vehicle


class MaintenanceRecord(models.Model):
    """
    Unified Maintenance Record for vehicles.
    Can be a 'Pending' (Due) record or a 'Completed' (Historical) record.
    """
    
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name='maintenance_records',
        verbose_name='Vehicle'
    )
    
    name = models.CharField(
        max_length=100, 
        verbose_name='Maintenance Task Name',
        help_text='e.g., Oil Change, Brake Inspection'
    )
    
    # Status
    is_completed = models.BooleanField(
        default=False,
        verbose_name='Is Completed?'
    )
    
    # Due/Expiry info (for Pending)
    expiry_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='Next Due Date'
    )
    expiry_km = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Next Due Odometer (km)'
    )
    
    # Interval info (for automatic next entry creation)
    interval_days = models.PositiveIntegerField(
        null=True, 
        blank=True, 
        verbose_name='Interval (days)',
        help_text='Leave blank if not recurring by time.'
    )
    interval_km = models.PositiveIntegerField(
        null=True, 
        blank=True, 
        verbose_name='Interval (km)',
        help_text='Leave blank if not recurring by distance.'
    )
    
    # Completion info (for Completed)
    completion_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='Date Completed'
    )
    completion_km = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Odometer when Completed'
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='Cost'
    )
    
    service_provider = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Service Provider'
    )
    
    notes = models.TextField(
        blank=True,
        verbose_name='Notes'
    )
    
    # Metadata
    logged_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='logged_maintenance_records',
        verbose_name='Logged By'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Maintenance Record'
        verbose_name_plural = 'Maintenance Records'
        ordering = ['is_completed', 'expiry_date', '-completion_date', '-created_at']
    
    def __str__(self):
        status = "Completed" if self.is_completed else "Pending"
        try:
            plate = self.vehicle.registration_plate if self.vehicle else "No Vehicle"
        except ObjectDoesNotExist:
            plate = f"Vehicle #{self.vehicle_id}"
        return f"{plate} - {self.name} ({status})"

    @property
    def is_overdue(self):
        """Check if pending record is overdue by date or odometer"""
        if self.is_completed:
            return False
            
        today = timezone.now().date()
        if self.expiry_date and self.expiry_date < today:
            return True
        if self.expiry_km and self.vehicle.current_odometer >= self.expiry_km:
            return True
        return False
    
    def mark_as_completed(self, date, km, cost=0, provider='', notes='', user=None):
        """
        Marks this record as completed and creates a new pending record 
        if intervals are set.
        """
        self.is_completed = True
        self.completion_date = date
        self.completion_km = km
        self.cost = cost
        self.service_provider = provider
        if notes:
            self.notes = f"{self.notes}\n\nCompletion Notes: {notes}".strip()
        if user:
            self.logged_by = user
        self.save()
        
        # Create next record if intervals exist
        if self.interval_days or self.interval_km:
            next_expiry_date = None
            if self.interval_days:
                next_expiry_date = date + timezone.timedelta(days=self.interval_days)
            
            next_expiry_km = None
            if self.interval_km:
                next_expiry_km = km + self.interval_km
                
            MaintenanceRecord.objects.create(
                vehicle=self.vehicle,
                name=self.name,
                is_completed=False,
                expiry_date=next_expiry_date,
                expiry_km=next_expiry_km,
                interval_days=self.interval_days,
                interval_km=self.interval_km,
                logged_by=user
            )
