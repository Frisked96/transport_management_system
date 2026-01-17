"""
Admin configuration for Fleet app
"""
from django.contrib import admin
from .models import Vehicle, MaintenanceLog


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = [
        'registration_plate',
        'make_model',
        'purchase_date',
        'status'
    ]
    
    list_filter = [
        'status',
        'purchase_date'
    ]
    
    search_fields = [
        'registration_plate',
        'make_model'
    ]


@admin.register(MaintenanceLog)
class MaintenanceLogAdmin(admin.ModelAdmin):
    list_display = [
        'vehicle',
        'date',
        'type',
        'cost',
        'service_provider',
        'next_service_due',
        'logged_by'
    ]
    
    list_filter = [
        'type',
        'date',
        'vehicle'
    ]
    
    search_fields = [
        'vehicle__registration_plate',
        'vehicle__make_model',
        'service_provider'
    ]
    
    readonly_fields = ['logged_by']
    
    def save_model(self, request, obj, form, change):
        """Automatically set logged_by field"""
        obj.logged_by = request.user
        super().save_model(request, obj, form, change)