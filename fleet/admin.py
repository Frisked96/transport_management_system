"""
Admin configuration for Fleet app
"""
from django.contrib import admin
from .models import Vehicle, MaintenanceRecord, Tyre, TyreLog, FuelLog


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = [
        'registration_plate',
        'make_model',
        'purchase_date',
        'status',
        'current_odometer'
    ]
    
    list_filter = [
        'status',
        'purchase_date'
    ]
    
    search_fields = [
        'registration_plate',
        'make_model'
    ]


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(admin.ModelAdmin):
    list_display = [
        'vehicle',
        'name',
        'is_completed',
        'expiry_date',
        'expiry_km',
        'completion_date',
        'cost',
        'is_overdue_status'
    ]
    
    list_filter = [
        'is_completed',
        'vehicle',
        'expiry_date',
        'completion_date'
    ]
    
    search_fields = [
        'name',
        'vehicle__registration_plate',
        'service_provider'
    ]
    
    readonly_fields = ['logged_by', 'created_at', 'updated_at']

    def is_overdue_status(self, obj):
        return obj.is_overdue
    is_overdue_status.boolean = True
    is_overdue_status.short_description = 'Is Overdue?'
    
    def save_model(self, request, obj, form, change):
        """Automatically set logged_by field"""
        if not obj.logged_by:
            obj.logged_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Tyre)
class TyreAdmin(admin.ModelAdmin):
    list_display = ['serial_number', 'brand', 'size', 'status', 'current_vehicle', 'total_km']
    list_filter = ['status', 'brand']
    search_fields = ['serial_number', 'brand', 'current_vehicle__registration_plate']


@admin.register(TyreLog)
class TyreLogAdmin(admin.ModelAdmin):
    list_display = ['tyre', 'action', 'vehicle', 'date', 'distance_covered']
    list_filter = ['action', 'date', 'vehicle']


@admin.register(FuelLog)
class FuelLogAdmin(admin.ModelAdmin):
    list_display = ['vehicle', 'date', 'liters', 'total_cost', 'odometer']
    list_filter = ['date', 'vehicle']
    search_fields = ['vehicle__registration_plate']
