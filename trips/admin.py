"""
Admin configuration for Trips app
"""
from django.contrib import admin
from .models import Trip, TripExpense


class TripExpenseInline(admin.TabularInline):
    model = TripExpense
    extra = 1


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = [
        'trip_number', 
        'date',
        'vehicle', 
        'party',
        'weight',
        'rate_per_ton',
        'status',
        'driver'
    ]
    
    list_filter = [
        'status',
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
        'actual_completion_datetime', 
        'payment_status', 
        'amount_received'
    ]
    
    inlines = [TripExpenseInline]

    fieldsets = (
        ('Trip Information', {
            'fields': ('trip_number', 'status', 'date', 'created_at')
        }),
        ('Details', {
            'fields': ('vehicle', 'party', 'weight', 'rate_per_ton')
        }),
        ('Driver Assignment', {
            'fields': ('driver',)
        }),
        ('Locations', {
            'fields': ('pickup_location', 'delivery_location')
        }),
        ('Financials', {
            'fields': (
                'payment_status', 
                'amount_received', 
                'diesel_expense', 
                'toll_expense'
            )
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