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
    TYPE_OIL_CHANGE = 'Oil Change'
    TYPE_TYRE_WORK = 'Tyre Work'
    TYPE_MAJOR_SERVICE = 'Major Service'
    
    TYPE_CHOICES = [
        (TYPE_ROUTINE, 'Routine Service'),
        (TYPE_OIL_CHANGE, 'Oil Change'),
        (TYPE_TYRE_WORK, 'Tyre Work'),
        (TYPE_MAJOR_SERVICE, 'Major Service'),
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
        max_length=50,
        choices=TYPE_CHOICES,
        default=TYPE_ROUTINE,
        verbose_name='Maintenance Type'
    )

    odometer_reading = models.PositiveIntegerField(
        null=True, 
        blank=True, 
        verbose_name='Odometer Reading'
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
    
    # Next service due (Date and/or Odometer)
    next_service_due = models.DateField(
        null=True,
        blank=True,
        verbose_name='Next Service Due (Date)'
    )
    next_service_odometer = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Next Service Due (Odometer)'
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
        """Check if next service is overdue by date or odometer"""
        today = timezone.now().date()
        if self.next_service_due and self.next_service_due < today:
            return True
        if self.next_service_odometer and self.vehicle.current_odometer >= self.next_service_odometer:
            return True
        return False


class Tyre(models.Model):
    """
    Inventory management for individual tyres
    """
    STATUS_IN_STOCK = 'In Stock'
    STATUS_MOUNTED = 'Mounted'
    STATUS_SCRAP = 'Scrap'
    STATUS_REPAIR = 'Under Repair'

    STATUS_CHOICES = [
        (STATUS_IN_STOCK, 'In Stock'),
        (STATUS_MOUNTED, 'Mounted'),
        (STATUS_REPAIR, 'Under Repair'),
        (STATUS_SCRAP, 'Scrap'),
    ]

    serial_number = models.CharField(max_length=100, unique=True, verbose_name='Serial Number')
    brand = models.CharField(max_length=100, verbose_name='Brand')
    size = models.CharField(max_length=50, verbose_name='Size')
    purchase_date = models.DateField(null=True, blank=True, verbose_name='Purchase Date')
    purchase_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    current_vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tyres',
        verbose_name='Current Vehicle'
    )
    current_position = models.CharField(max_length=50, blank=True, verbose_name='Position')
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_STOCK)
    total_km = models.PositiveIntegerField(default=0, verbose_name='Total KM')
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.brand} {self.size} ({self.serial_number})"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        old_instance = None
        if not is_new:
            try:
                old_instance = Tyre.objects.get(pk=self.pk)
            except Tyre.DoesNotExist:
                is_new = True

        # Enforce status logic
        if self.current_vehicle:
            self.status = self.STATUS_MOUNTED
        elif self.status == self.STATUS_MOUNTED:
            # If no vehicle but status was mounted, set to in stock
            self.status = self.STATUS_IN_STOCK

        super().save(*args, **kwargs)

        # Automatic Logging (skip if explicitly told to)
        if getattr(self, '_skip_auto_log', False):
            return

        if is_new:
            if self.current_vehicle:
                TyreLog.objects.create(
                    tyre=self,
                    action=TyreLog.ACTION_MOUNT,
                    vehicle=self.current_vehicle,
                    position=self.current_position,
                    tyre_odo=self.total_km,
                    distance_covered=0,
                    notes="Initial mount on creation"
                )
        else:
            # Check for changes in vehicle or position
            vehicle_changed = old_instance.current_vehicle != self.current_vehicle
            position_changed = old_instance.current_position != self.current_position

            if vehicle_changed:
                # Dismount from old vehicle if it existed
                if old_instance.current_vehicle:
                    # Calculate distance covered since last mount/rotation on this specific vehicle
                    last_assignment_log = self.logs.filter(
                        vehicle=old_instance.current_vehicle
                    ).order_by('-date', '-id').first()
                    dist = self.total_km - (last_assignment_log.tyre_odo if last_assignment_log else 0)
                    
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_DISMOUNT,
                        vehicle=old_instance.current_vehicle,
                        position=old_instance.current_position,
                        tyre_odo=self.total_km,
                        distance_covered=max(0, dist),
                        notes=f"Automatic dismount: vehicle changed to {self.current_vehicle}" if self.current_vehicle else "Automatic dismount"
                    )
                
                # Mount to new vehicle if it exists
                if self.current_vehicle:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_MOUNT,
                        vehicle=self.current_vehicle,
                        position=self.current_position,
                        tyre_odo=self.total_km,
                        distance_covered=0,
                        notes=f"Automatic mount: vehicle changed from {old_instance.current_vehicle}" if old_instance.current_vehicle else "Automatic mount"
                    )
            elif position_changed and self.current_vehicle:
                # Same vehicle, different position -> Rotation
                last_assignment_log = self.logs.filter(
                    vehicle=self.current_vehicle
                ).order_by('-date', '-id').first()
                dist = self.total_km - (last_assignment_log.tyre_odo if last_assignment_log else 0)
                
                TyreLog.objects.create(
                    tyre=self,
                    action=TyreLog.ACTION_ROTATION,
                    vehicle=self.current_vehicle,
                    position=self.current_position,
                    tyre_odo=self.total_km,
                    distance_covered=max(0, dist),
                    notes=f"Position changed from {old_instance.current_position} to {self.current_position}"
                )
            
            # Check for Status Changes (Repair/Scrap)
            status_changed = old_instance.status != self.status
            if status_changed and not vehicle_changed: # vehicle_changed already handled Mount/Dismount
                if self.status == self.STATUS_REPAIR:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_REPAIR,
                        tyre_odo=self.total_km,
                        notes="Status changed to Under Repair"
                    )
                elif self.status == self.STATUS_SCRAP:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_SCRAP,
                        tyre_odo=self.total_km,
                        notes="Status changed to Scrap"
                    )
                elif self.status == self.STATUS_IN_STOCK and old_instance.status == self.STATUS_REPAIR:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_DISMOUNT, # Using Dismount as 'Back to Stock'
                        tyre_odo=self.total_km,
                        notes="Repair completed, moved back to stock"
                    )


class TyreLog(models.Model):
    """
    History of tyre movements and repairs
    """
    ACTION_MOUNT = 'Mount'
    ACTION_DISMOUNT = 'Dismount'
    ACTION_ROTATION = 'Rotation'
    ACTION_REPAIR = 'Repair'
    ACTION_SCRAP = 'Scrap'

    ACTION_CHOICES = [
        (ACTION_MOUNT, 'Mount'),
        (ACTION_DISMOUNT, 'Dismount'),
        (ACTION_ROTATION, 'Rotation'),
        (ACTION_REPAIR, 'Repair'),
        (ACTION_SCRAP, 'Scrap'),
    ]

    tyre = models.ForeignKey(Tyre, on_delete=models.CASCADE, related_name='logs')
    date = models.DateField(default=timezone.now)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    position = models.CharField(max_length=50, blank=True)
    tyre_odo = models.PositiveIntegerField(null=True, blank=True, verbose_name="Tyre Odo (Total KM)")
    distance_covered = models.PositiveIntegerField(default=0, verbose_name="Distance Covered on Vehicle")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f"{self.tyre} - {self.action} on {self.date}"


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