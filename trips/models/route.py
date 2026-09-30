"""
Route model for Trips application
"""
from django.db import models


class Route(models.Model):
    """
    Pre-defined routes with pickup and delivery locations.
    Also defines if the route is local (GST) or intra/interstate (IGST).
    """
    pickup_location = models.CharField(
        max_length=300,
        verbose_name='Pickup Location'
    )
    delivery_location = models.CharField(
        max_length=300,
        verbose_name='Delivery Location'
    )
    
    ROUTE_TYPE_LOCAL = 'local'
    ROUTE_TYPE_INTRA = 'intra'
    ROUTE_TYPE_NONE = 'none'
    ROUTE_TYPE_CHOICES = [
        (ROUTE_TYPE_LOCAL, 'Local (GST)'),
        (ROUTE_TYPE_INTRA, 'Intra/Interstate (IGST)'),
        (ROUTE_TYPE_NONE, 'Non-GST'),
    ]
    route_type = models.CharField(
        max_length=10, 
        choices=ROUTE_TYPE_CHOICES, 
        default=ROUTE_TYPE_LOCAL,
        verbose_name='Route Type'
    )

    default_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name='Default Rate',
        help_text='Suggested rate for trips on this route'
    )

    class Meta:
        verbose_name = 'Route'
        verbose_name_plural = 'Routes'
        unique_together = ['pickup_location', 'delivery_location', 'route_type']

    def __str__(self):
        return f"{self.pickup_location} to {self.delivery_location} ({self.get_route_type_display()})"
