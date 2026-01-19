"""
Models for Fleet application
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


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
    
    # Purchase date
    purchase_date = models.DateField(
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
    
    class Meta:
        verbose_name = 'Vehicle'
        verbose_name_plural = 'Vehicles'
        ordering = ['registration_plate']
        permissions = [
            ('can_view_all_vehicles', 'Can view all vehicles'),
        ]
    
    def __str__(self):
        return f"{self.registration_plate} - {self.make_model}"
    
    @property
    def is_available(self):
        """Check if vehicle is available for assignment"""
        return self.status == self.STATUS_ACTIVE
    
    @property
    def last_maintenance(self):
        """Get the last maintenance log"""
        return self.maintenance_logs.order_by('-date').first()
    
    @property
    def next_due_maintenance(self):
        """Get the next due maintenance date"""
        last_log = self.last_maintenance
        if last_log and last_log.next_service_due:
            return last_log.next_service_due
        return None
    
    @property
    def total_maintenance_cost(self):
        """Calculate total maintenance cost"""
        return self.maintenance_logs.aggregate(
            total=models.Sum('cost')
        )['total'] or 0


class MaintenanceLog(models.Model):
    """
    Maintenance log for vehicles
    """
    
    # Maintenance type choices
    TYPE_ROUTINE = 'Routine Service'
    TYPE_REPAIR = 'Repair'
    
    TYPE_CHOICES = [
        (TYPE_ROUTINE, 'Routine Service'),
        (TYPE_REPAIR, 'Repair'),
    ]
    
    # Vehicle (ForeignKey)
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name='maintenance_logs',
        verbose_name='Vehicle'
    )
    
    # Date of maintenance
    date = models.DateField(
        verbose_name='Maintenance Date'
    )
    
    # Type of maintenance
    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        verbose_name='Maintenance Type'
    )
    
    # Description of work done
    description = models.TextField(
        verbose_name='Description of Work'
    )
    
    # Cost of maintenance
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Cost'
    )
    
    # Service provider
    service_provider = models.CharField(
        max_length=200,
        verbose_name='Service Provider'
    )
    
    # Next service due date
    next_service_due = models.DateField(
        null=True,
        blank=True,
        verbose_name='Next Service Due'
    )
    
    # Who logged this entry
    logged_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='logged_maintenance',
        verbose_name='Logged By'
    )
    
    class Meta:
        verbose_name = 'Maintenance Log'
        verbose_name_plural = 'Maintenance Logs'
        ordering = ['-date']
        permissions = [
            ('can_create_maintenance_log', 'Can create maintenance log'),
        ]
    
    def __str__(self):
        return f"{self.vehicle.registration_plate} - {self.type} - {self.date}"
    
    @property
    def is_overdue(self):
        """Check if next service is overdue"""
        if self.next_service_due:
            return self.next_service_due < timezone.now().date()
        return False


class FuelLog(models.Model):
    """
    Fuel Log to track fueling events
    """
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name='fuel_logs',
        verbose_name='Vehicle'
    )
    trip = models.ForeignKey(
        'trips.Trip',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fuel_logs',
        verbose_name='Related Trip'
    )
    date = models.DateField(default=timezone.now, verbose_name='Date')
    liters = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Liters')
    rate = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Rate per Liter')
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Total Cost')
    odometer = models.PositiveIntegerField(verbose_name='Odometer Reading')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Fuel Log'
        verbose_name_plural = 'Fuel Logs'
        ordering = ['-date', '-odometer']

    def __str__(self):
        return f"{self.vehicle} - {self.liters}L - {self.date}"