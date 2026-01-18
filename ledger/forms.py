from django import forms
from django.contrib.auth.models import User
from .models import FinancialRecord, Party, Account
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
        fields = ['name', 'phone_number', 'state', 'address']
        
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
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