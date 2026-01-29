"""
Admin configuration for Ledger app
"""
from django.contrib import admin
from .models import FinancialRecord, Party, TransactionCategory, Account, TripAllocation, Bill, CompanyProfile


@admin.register(TransactionCategory)
class TransactionCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'type', 'description']
    list_filter = ['type']
    search_fields = ['name', 'description']


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ['name', 'account_number', 'opening_balance', 'created_at']
    search_fields = ['name', 'account_number']


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone_number', 'state', 'created_at']
    search_fields = ['name', 'phone_number', 'state']
    list_filter = ['state', 'created_at']


@admin.register(TripAllocation)
class TripAllocationAdmin(admin.ModelAdmin):
    list_display = ['financial_record', 'trip', 'amount', 'created_at']
    search_fields = ['trip__trip_number', 'financial_record__description']

@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ['company_name', 'invoice_template']

@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ['bill_number', 'date', 'party', 'status', 'total_amount']
    list_filter = ['status', 'gst_rate', 'date']
    search_fields = ['bill_number', 'party__name']
    filter_horizontal = ['trips']

@admin.register(FinancialRecord)
class FinancialRecordAdmin(admin.ModelAdmin):
    list_display = [
        'entry_number',
        'date',
        'category',
        'party',
        'amount',
        'associated_trip',
        'recorded_by'
    ]
    
    list_filter = [
        'category',
        'date',
        'party'
    ]
    
    search_fields = [
        'entry_number',
        'description',
        'associated_trip__trip_number',
        'party__name'
    ]
    
    readonly_fields = ['recorded_by', 'entry_number']
    
    def save_model(self, request, obj, form, change):
        """Automatically set recorded_by field"""
        obj.recorded_by = request.user
        super().save_model(request, obj, form, change)
