"""
Admin configuration for Ledger app
"""
from django.contrib import admin
from .models import FinancialRecord, Party


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone_number', 'state', 'created_at']
    search_fields = ['name', 'phone_number', 'state']
    list_filter = ['state', 'created_at']


@admin.register(FinancialRecord)
class FinancialRecordAdmin(admin.ModelAdmin):
    list_display = [
        'date',
        'category',
        'party',
        'amount',
        'associated_trip',
        'description',
        'recorded_by'
    ]
    
    list_filter = [
        'category',
        'date',
        'associated_trip',
        'party'
    ]
    
    search_fields = [
        'description',
        'associated_trip__trip_number',
        'party__name',
        'document_ref'
    ]
    
    readonly_fields = ['recorded_by']
    
    def save_model(self, request, obj, form, change):
        """Automatically set recorded_by field"""
        obj.recorded_by = request.user
        super().save_model(request, obj, form, change)