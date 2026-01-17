"""
Admin configuration for Ledger app
"""
from django.contrib import admin
from .models import FinancialRecord


@admin.register(FinancialRecord)
class FinancialRecordAdmin(admin.ModelAdmin):
    list_display = [
        'date',
        'category',
        'amount',
        'associated_trip',
        'description',
        'recorded_by'
    ]
    
    list_filter = [
        'category',
        'date',
        'associated_trip'
    ]
    
    search_fields = [
        'description',
        'associated_trip__trip_number',
        'document_ref'
    ]
    
    readonly_fields = ['recorded_by']
    
    def save_model(self, request, obj, form, change):
        """Automatically set recorded_by field"""
        obj.recorded_by = request.user
        super().save_model(request, obj, form, change)