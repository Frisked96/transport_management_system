"""
Admin configuration for Trips app
"""
from django.contrib import admin
from .models import Trip


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = [
        'trip_number', 
        'driver', 
        'vehicle', 
        'client_name',
        'pickup_location',
        'delivery_location',
        'scheduled_datetime',
        'status',
        'created_at'
    ]
    
    list_filter = [
        'status',
        'driver',
        'vehicle',
        'scheduled_datetime',
        'created_at'
    ]
    
    search_fields = [
        'trip_number',
        'client_name',
        'pickup_location',
        'delivery_location'
    ]
    
    readonly_fields = ['created_at', 'actual_completion_datetime']
    
    fieldsets = (
        ('Trip Information', {
            'fields': ('trip_number', 'status', 'created_at')
        }),
        ('Assignment', {
            'fields': ('driver', 'vehicle')
        }),
        ('Client Details', {
            'fields': ('client_name', 'pickup_location', 'delivery_location')
        }),
        ('Schedule', {
            'fields': ('scheduled_datetime', 'actual_completion_datetime')
        }),
        ('Additional Information', {
            'fields': ('notes',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        """Automatically set created_by field"""
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)