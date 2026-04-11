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
        ordering = ['-registration_plate'] # Or -id/created_at if you want newest added. Registration plate descending might put newer ones on top if they follow a pattern. Let's use -id.
    
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
        return f"{self.vehicle.registration_plate} - {self.name} ({status})"

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


class Tyre(models.Model):
    """
    Inventory management for individual tyres.
    Simplified: No odometer tracking for tyres.
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
    photo = models.ImageField(upload_to='tyres/', null=True, blank=True, verbose_name='Tyre Photo')
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
                    notes="Initial mount on creation"
                )
        else:
            # Check for changes in vehicle or position
            vehicle_changed = old_instance.current_vehicle != self.current_vehicle
            position_changed = old_instance.current_position != self.current_position

            if vehicle_changed:
                # Dismount from old vehicle if it existed
                if old_instance.current_vehicle:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_DISMOUNT,
                        vehicle=old_instance.current_vehicle,
                        position=old_instance.current_position,
                        notes=f"Automatic dismount: vehicle changed to {self.current_vehicle}" if self.current_vehicle else "Automatic dismount"
                    )
                
                # Mount to new vehicle if it exists
                if self.current_vehicle:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_MOUNT,
                        vehicle=self.current_vehicle,
                        position=self.current_position,
                        notes=f"Automatic mount: vehicle changed from {old_instance.current_vehicle}" if old_instance.current_vehicle else "Automatic mount"
                    )
            elif position_changed and self.current_vehicle:
                # Same vehicle, different position -> Rotation
                TyreLog.objects.create(
                    tyre=self,
                    action=TyreLog.ACTION_ROTATION,
                    vehicle=self.current_vehicle,
                    position=self.current_position,
                    notes=f"Position changed from {old_instance.current_position} to {self.current_position}"
                )
            
            # Check for Status Changes (Repair/Scrap)
            status_changed = old_instance.status != self.status
            if status_changed and not vehicle_changed: # vehicle_changed already handled Mount/Dismount
                if self.status == self.STATUS_REPAIR:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_REPAIR,
                        notes="Status changed to Under Repair"
                    )
                elif self.status == self.STATUS_SCRAP:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_SCRAP,
                        notes="Status changed to Scrap"
                    )
                elif self.status == self.STATUS_IN_STOCK and old_instance.status == self.STATUS_REPAIR:
                    TyreLog.objects.create(
                        tyre=self,
                        action=TyreLog.ACTION_DISMOUNT, # Using Dismount as 'Back to Stock'
                        notes="Repair completed, moved back to stock"
                    )


class TyreLog(models.Model):
    """
    History of tyre movements and repairs.
    Simplified: No odometer tracking for tyres.
    """
    ACTION_MOUNT = 'Mount'
    ACTION_DISMOUNT = 'Dismount'
    ACTION_ROTATION = 'Rotation'
    ACTION_REPAIR = 'Repair'
    ACTION_REMOLD = 'Remold'
    ACTION_SCRAP = 'Scrap'

    ACTION_CHOICES = [
        (ACTION_MOUNT, 'Mount'),
        (ACTION_DISMOUNT, 'Dismount'),
        (ACTION_ROTATION, 'Rotation'),
        (ACTION_REPAIR, 'Repair'),
        (ACTION_REMOLD, 'Remold'),
        (ACTION_SCRAP, 'Scrap'),
    ]

    tyre = models.ForeignKey(Tyre, on_delete=models.CASCADE, related_name='logs')
    date = models.DateField(default=timezone.now)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True)
    position = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f"{self.tyre} - {self.action} on {self.date}"
