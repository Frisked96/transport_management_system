"""
Models for Trips application
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from fleet.models import Vehicle


class Trip(models.Model):
    """
    Trip model to manage transport operations
    """
    
    # Status choices
    STATUS_SCHEDULED = 'Scheduled'
    STATUS_IN_PROGRESS = 'In Progress'
    STATUS_COMPLETED = 'Completed'
    STATUS_CANCELLED = 'Cancelled'
    
    STATUS_CHOICES = [
        (STATUS_SCHEDULED, 'Scheduled'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]
    
    # Unique trip identifier
    trip_number = models.CharField(
        max_length=50, 
        unique=True,
        verbose_name='Trip Number'
    )
    
    # Driver assignment (ForeignKey to User)
    driver = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='assigned_trips',
        verbose_name='Assigned Driver'
    )
    
    # Vehicle assignment (ForeignKey to Vehicle)
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name='trips',
        verbose_name='Assigned Vehicle'
    )
    
    # Client information
    client_name = models.CharField(
        max_length=200,
        verbose_name='Client Name'
    )
    
    pickup_location = models.CharField(
        max_length=300,
        verbose_name='Pickup Location'
    )
    
    delivery_location = models.CharField(
        max_length=300,
        verbose_name='Delivery Location'
    )
    
    # Scheduling
    scheduled_datetime = models.DateTimeField(
        verbose_name='Scheduled Date & Time'
    )
    
    actual_completion_datetime = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Actual Completion Date & Time'
    )
    
    # Status with choices
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_SCHEDULED,
        verbose_name='Trip Status'
    )
    
    # Additional notes
    notes = models.TextField(
        blank=True,
        verbose_name='Trip Notes'
    )
    
    # Audit fields
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_trips',
        verbose_name='Created By'
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Created At'
    )
    
    class Meta:
        verbose_name = 'Trip'
        verbose_name_plural = 'Trips'
        ordering = ['-scheduled_datetime']
        permissions = [
            ('can_view_all_trips', 'Can view all trips'),
            ('can_update_trip_status', 'Can update trip status'),
            ('can_view_driver_dashboard', 'Can access driver dashboard'),
            ('can_view_manager_dashboard', 'Can access manager dashboard'),
        ]
    
    def __str__(self):
        return f"{self.trip_number} - {self.client_name}"
    
    def save(self, *args, **kwargs):
        """
        Override save to handle business logic:
        - Set actual_completion_datetime when status changes to Completed
        """
        if self.status == self.STATUS_COMPLETED and not self.actual_completion_datetime:
            self.actual_completion_datetime = timezone.now()
        elif self.status != self.STATUS_COMPLETED:
            self.actual_completion_datetime = None
        
        super().save(*args, **kwargs)
    
    @property
    def is_overdue(self):
        """Check if trip is overdue"""
        if self.status == self.STATUS_COMPLETED:
            return False
        return self.scheduled_datetime < timezone.now()
    
    @property
    def duration_display(self):
        """Display trip duration if completed"""
        if self.actual_completion_datetime and self.scheduled_datetime:
            duration = self.actual_completion_datetime - self.scheduled_datetime
            days = duration.days
            hours = duration.seconds // 3600
            minutes = (duration.seconds % 3600) // 60
            
            if days > 0:
                return f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
        return "N/A"