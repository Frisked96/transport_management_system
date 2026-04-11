"""
Admin configuration for Trips app
"""
from django.contrib import admin
from .models import Trip

@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = [
        'trip_number', 
        'date',
        'vehicle', 
        'party',
        'weight',
        'rate_per_ton',
        'driver'
    ]
    
    list_filter = [
        'vehicle',
        'party',
        'date',
        'created_at'
    ]
    
    search_fields = [
        'trip_number',
        'vehicle__registration_plate',
        'party__name'
    ]
    
    readonly_fields = [
        'created_at',
        'trip_number'
    ]
    
    fieldsets = (
        ('Trip Information', {
            'fields': ('trip_number', 'date', 'created_at')
        }),
        ('Details', {
            'fields': ('vehicle', 'party', 'revenue_type', 'weight', 'rate_per_ton')
        }),
        ('Driver Assignment', {
            'fields': ('driver',)
        }),
        ('Locations', {
            'fields': ('pickup_location', 'delivery_location')
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
