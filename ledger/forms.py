from django import forms
from django.contrib.auth.models import User
from django.db import models
from .models import FinancialRecord, Party, Account, Bill, CompanyProfile
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
                    driver_user = User.objects.get(pk=self.data.get('driver'))
                except (ValueError, User.DoesNotExist):
                    pass

        # 1. If Party context: Remove Driver field
        if party:
            if 'driver' in self.fields:
                del self.fields['driver']
            
            # Setup trips for party using dynamic payment info
            self.fields['associated_trip'].queryset = Trip.objects.with_payment_info().filter(
                party=party
            ).exclude(
                annotated_status=Trip.PAYMENT_STATUS_PAID
            ).order_by('-date')
        
        # 2. If Driver context: Remove Party and Trip fields
        elif driver_user:
            if 'party' in self.fields:
                del self.fields['party']
            if 'associated_trip' in self.fields:
                del self.fields['associated_trip']
        
        # 3. General Ledger context
        else:
            # Default empty queryset for trips if no party selected yet
            self.fields['associated_trip'].queryset = Trip.objects.none()

        # Filter drivers if field still exists
        if 'driver' in self.fields:
            self.fields['driver'].queryset = User.objects.filter(
                groups__name='driver'
            ).order_by('username')
            self.fields['driver'].required = False
        
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if field_name != 'payment_distribution':
                field.widget.attrs.update({'class': 'form-control'})
    
    class Meta:
        model = FinancialRecord
        fields = [
            'date',
            'account',
            'party',
            'driver',
            'associated_trip',
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
    """
    Form for creating and editing parties
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
    
    class Meta:
        model = Party
        fields = ['name', 'phone_number', 'state', 'address', 'gstin', 'bank_details']
        
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'bank_details': forms.Textarea(attrs={'rows': 3}),
        }


class AccountForm(forms.ModelForm):
    """
    Form for creating and editing company accounts
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
    
    class Meta:
        model = Account
        fields = ['name', 'account_number', 'opening_balance', 'description']
        
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

class BillForm(forms.ModelForm):
    class Meta:
        model = Bill
        fields = [
            'bill_number', 
            'party', 
            'date', 
            'gst_type', 
            'gst_rate', 
            'invoice_company_name',
            'invoice_company_address',
            'invoice_company_mobile',
            'invoice_company_gstin',
            'trips'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'invoice_company_address': forms.Textarea(attrs={'rows': 3}),
            'trips': forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Apply bootstrap classes
        for name, field in self.fields.items():
            if name != 'trips': # Checkboxes look bad with form-control
                field.widget.attrs.update({'class': 'form-control'})
        
        # Logic to filter trips based on Party
        party_id = None
        
        if self.is_bound: # POST data
            party_id = self.data.get('party')
        elif self.instance and self.instance.pk: # Edit mode
            party_id = self.instance.party_id
        elif 'initial' in kwargs and 'party' in kwargs['initial']: # Pre-filled
            party_id = kwargs['initial']['party']
            
        if party_id:
            try:
                # Show unbilled trips OR trips already in this bill (if editing)
                # We usually want 'Completed' trips only
                qs = Trip.objects.filter(party_id=party_id).exclude(status='Cancelled')
                
                if self.instance and self.instance.pk:
                    # In edit mode, include currently selected trips + unbilled ones
                    qs = qs.filter(models.Q(bills__isnull=True) | models.Q(bills=self.instance))
                else:
                     # Create mode: only unbilled
                     qs = qs.filter(bills__isnull=True)
                
                self.fields['trips'].queryset = qs.distinct().order_by('-date')
            except (ValueError, TypeError):
                self.fields['trips'].queryset = Trip.objects.none()
        else:
            self.fields['trips'].queryset = Trip.objects.none()
        
        # Pre-fill Company Details and Bill Number if creating new
        if not self.instance.pk:
            profile = CompanyProfile.objects.first()
            if profile:
                self.fields['invoice_company_name'].initial = profile.company_name
                self.fields['invoice_company_address'].initial = profile.address
                self.fields['invoice_company_mobile'].initial = profile.phone_number
                self.fields['invoice_company_gstin'].initial = profile.gstin
                
                # Predict next Bill Number
                from .models import Sequence
                import datetime
                
                try:
                    seq_obj = Sequence.objects.get(key="bill_sequence")
                    next_val = seq_obj.value + 1
                except Sequence.DoesNotExist:
                    next_val = 1
                
                template = profile.invoice_template or "INV-{YYYY}-{SEQ}"
                now = datetime.datetime.now()
                pred_num = template.replace("{YYYY}", str(now.year)).replace("{SEQ}", f"{next_val:04d}")
                self.fields['bill_number'].initial = pred_num

        # Add labels if needed
        self.fields['gst_type'].label = "GST Type"

class CompanyProfileForm(forms.ModelForm):
    class Meta:
        model = CompanyProfile
        fields = [
            'company_name', 
            'address', 
            'phone_number', 
            'gstin', 
            'bank_details', 
            'authorized_signatory', 
            'invoice_template'
        ]
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'bank_details': forms.Textarea(attrs={'rows': 3}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
