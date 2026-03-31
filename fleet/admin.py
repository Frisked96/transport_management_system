"""
Admin configuration for Fleet app
"""
from django.contrib import admin
from .models import Vehicle, MaintenanceLog, MaintenanceTask, Tyre, TyreLog, FuelLog


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


@admin.register(MaintenanceTask)
class MaintenanceTaskAdmin(admin.ModelAdmin):
    list_display = [
        'vehicle',
        'name',
        'interval_km',
        'interval_days',
        'last_performed_km',
        'last_performed_date',
        'is_due_status',
        'is_active'
    ]
    list_filter = ['is_active', 'vehicle']
    search_fields = ['name', 'vehicle__registration_plate']

    def is_due_status(self, obj):
        return obj.is_due
    is_due_status.boolean = True
    is_due_status.short_description = 'Is Due?'


@admin.register(MaintenanceLog)
class MaintenanceLogAdmin(admin.ModelAdmin):
    list_display = [
        'vehicle',
        'task',
        'date',
        'type',
        'cost',
        'service_provider',
        'logged_by'
    ]
    
    list_filter = [
        'type',
        'date',
        'vehicle',
        'task'
    ]
    
    search_fields = [
        'vehicle__registration_plate',
        'vehicle__make_model',
        'service_provider',
        'task__name'
    ]
    
    readonly_fields = ['logged_by']
    
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