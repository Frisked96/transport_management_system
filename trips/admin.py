"""
Admin configuration for Trips app
"""
from django.contrib import admin
from .models import Trip, TripLeg


class TripLegInline(admin.TabularInline):
    model = TripLeg
    extra = 1


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = [
        'trip_number', 
        'driver', 
        'vehicle', 
        'status',
        'created_at'
    ]
    
    list_filter = [
        'status',
        'driver',
        'vehicle',
        'created_at'
    ]
    
    search_fields = [
        'trip_number',
    ]
    
    readonly_fields = ['created_at', 'actual_completion_datetime']
    
    inlines = [TripLegInline]

    fieldsets = (
        ('Trip Information', {
            'fields': ('trip_number', 'status', 'created_at')
        }),
        ('Assignment', {
            'fields': ('driver', 'vehicle')
        }),
        ('Completion Info', {
            'fields': ('actual_completion_datetime',)
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


@admin.register(TripLeg)
class TripLegAdmin(admin.ModelAdmin):
    list_display = ['trip', 'client_name', 'pickup_location', 'delivery_location', 'weight']
    search_fields = ['client_name', 'pickup_location', 'delivery_location']
