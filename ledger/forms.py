from django import forms
from django.contrib.auth.models import User
from django.db import models
import json
from .models import FinancialRecord, Party, CompanyAccount, Bill
from trips.models import Trip


class FinancialRecordForm(forms.ModelForm):
    """
    Form for creating and editing financial records
    """
    # Hidden field to store JSON data for multi-trip payment distribution
    payment_distribution = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Determine context (Party or Driver entry)
        party = None
        driver_user = None
        
        # Check initial data (from URL params)
        if 'initial' in kwargs:
            party = kwargs['initial'].get('party')
            driver_user = kwargs['initial'].get('driver')
        
        # Check instance data (if editing)
        if self.instance and self.instance.pk:
            party = self.instance.party
            driver_user = self.instance.driver

        # Check POST data (if bound)
        if self.data:
            if self.data.get('party'):
                try:
                    party = Party.objects.get(pk=self.data.get('party'))
                except (ValueError, Party.DoesNotExist):
                    pass
            if self.data.get('driver'):
                try:
                    from django.contrib.auth.models import User
                    driver_user = User.objects.get(pk=self.data.get('driver'))
                except (ValueError, User.DoesNotExist):
                    pass

        # 1. If Party context: Remove Driver field
        if party:
            if 'driver' in self.fields:
                del self.fields['driver']
            
            # Setup trips for party using dynamic payment info
            # Only show trips that have been billed
            self.fields['associated_trip'].queryset = Trip.objects.with_payment_info().with_billing_info().filter(
                party=party,
                annotated_is_billed=True
            ).exclude(
                annotated_status=Trip.PAYMENT_STATUS_PAID
            ).order_by('-date')

            # Filter bills for this party
            self.fields['associated_bill'].queryset = Bill.objects.filter(party=party).order_by('-date')
        
        # 2. If Driver context: Remove Party, Trip, and Bill fields
        elif driver_user:
            if 'party' in self.fields:
                del self.fields['party']
            if 'associated_trip' in self.fields:
                del self.fields['associated_trip']
            if 'associated_bill' in self.fields:
                del self.fields['associated_bill']
        
        # 3. General Ledger context
        else:
            # Default empty queryset for trips/bills if no party selected yet
            self.fields['associated_trip'].queryset = Trip.objects.none()
            self.fields['associated_bill'].queryset = Bill.objects.none()

        # Filter drivers if field still exists
        if 'driver' in self.fields:
            from django.contrib.auth.models import User
            self.fields['driver'].queryset = User.objects.filter(
                groups__name='driver'
            ).order_by('username')
            self.fields['driver'].required = False
        
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if field_name != 'payment_distribution':
                field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    
    class Meta:
        model = FinancialRecord
        fields = [
            'date',
            'account',
            'party',
            'driver',
            'associated_trip',
            'associated_bill',
            'payment_distribution',
            'category',
            'amount',
            'description',
            'document_ref'
        ]
        
        widgets = {
            'date': forms.DateInput(
                attrs={
                    'type': 'date'
                }
            ),
            'description': forms.Textarea(
                attrs={
                    'rows': 4
                }
            ),
        }


class PartyForm(forms.ModelForm):
# ... (rest of form) ...
    """
    Form for creating and editing parties
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    
    class Meta:
        model = Party
        fields = [
            'name', 'phone_number', 'state', 'address', 'gstin',
            'bank_name', 'bank_branch', 'account_number', 'ifsc_code', 'account_holder_name',
            'bank_details'
        ]
        
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'bank_details': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Legacy details if any...'}),
        }


class CompanyAccountForm(forms.ModelForm):
    """
    Form for creating and editing company accounts (Firms)
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    
    class Meta:
        model = CompanyAccount
        fields = [
            'name', 'address', 'phone_number', 'gstin', 'pan',
            'bank_name', 'bank_branch', 'account_number', 'ifsc_code', 'account_holder_name',
            'authorized_signatory', 'invoice_template',
            'opening_balance', 'description'
        ]
        
        widgets = {
            'address': forms.Textarea(attrs={'rows': 2}),
            'description': forms.Textarea(attrs={'rows': 2}),
        }

class BillForm(forms.ModelForm):
    # Hidden field to store JSON mapping of trip_id -> lr_no
    trips_data = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Bill
        fields = [
            'bill_number', 
            'bill_type',
            'issuer',
            'party', 
            'date', 
            'status',
            'item_type',
            'amount_override',
            'gst_type', 
            'gst_rate', 
            'trips',
            'trips_data'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'trips': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Apply Tailwind classes
        for name, field in self.fields.items():
            if name not in ['trips', 'trips_data']:
                field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
        
        # Logic to filter trips based on Party
        party_id = None
        
        if self.is_bound: # POST data
            party_id = self.data.get('party')
        elif self.instance and self.instance.pk: # Edit mode
            party_id = self.instance.party_id
        else:
             if self.initial.get('party'):
                 party_id = self.initial.get('party')
            
        if party_id:
            try:
                # Show trips for this party
                qs = Trip.objects.filter(party_id=party_id).exclude(status='Cancelled')
                
                if self.instance and self.instance.pk:
                    # Include currently selected trips + unbilled ones
                    qs = qs.filter(models.Q(bills__isnull=True) | models.Q(bills=self.instance))
                else:
                     qs = qs.filter(bills__isnull=True)
                
                self.fields['trips'].queryset = qs.distinct().order_by('-date')
            except (ValueError, TypeError):
                self.fields['trips'].queryset = Trip.objects.none()
        else:
            self.fields['trips'].queryset = Trip.objects.none()

        # If editing, populate trips_data with existing LR Nos
        if self.instance and self.instance.pk:
            from .models import BillTrip
            bt_data = {bt.trip_id: bt.lr_no for bt in self.instance.bill_trips.all()}
            self.fields['trips_data'].initial = json.dumps(bt_data)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            
            # Handle BillTrip relationships with LR No
            selected_trips = self.cleaned_data.get('trips', [])
            trips_data_json = self.cleaned_data.get('trips_data', '{}')
            
            try:
                trips_extra = json.loads(trips_data_json) if trips_data_json else {}
            except json.JSONDecodeError:
                trips_extra = {}

            # Remove existing trips not in selected
            instance.bill_trips.exclude(trip__in=selected_trips).delete()

            # Create or update BillTrip for each selected trip
            from .models import BillTrip
            for trip in selected_trips:
                lr_no = trips_extra.get(str(trip.id)) or trips_extra.get(trip.id)
                BillTrip.objects.update_or_create(
                    bill=instance,
                    trip=trip,
                    defaults={'lr_no': lr_no}
                )
            
            # Sync to Ledger (After trips are established)
            instance.sync_to_ledger()
            
        return instance
