from django import forms
from django.contrib.auth.models import User
from .models import FinancialRecord, Party, Account
from trips.models import Trip, TripLeg


class FinancialRecordForm(forms.ModelForm):
    """
    Form for creating and editing financial records
    """
    
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
            
            # Setup legs for party
            self.fields['associated_legs'].queryset = TripLeg.objects.filter(
                party=party
            ).exclude(
                payment_status=TripLeg.PAYMENT_STATUS_PAID
            ).order_by('-date')
            
            if self.instance and self.instance.pk:
                current_legs = self.instance.associated_legs.all()
                self.fields['associated_legs'].queryset = (
                    self.fields['associated_legs'].queryset | current_legs
                ).distinct()
        
        # 2. If Driver context: Remove Party and Legs fields
        elif driver_user:
            if 'party' in self.fields:
                del self.fields['party']
            if 'associated_legs' in self.fields:
                del self.fields['associated_legs']
        
        # 3. General Ledger context (neither pre-selected)
        else:
            # Default empty queryset for legs if no party selected yet
            if 'associated_legs' in self.fields:
                self.fields['associated_legs'].queryset = TripLeg.objects.none()

        # Filter drivers if field still exists
        if 'driver' in self.fields:
            self.fields['driver'].queryset = User.objects.filter(
                groups__name='driver'
            ).order_by('username')
            self.fields['driver'].required = False
        
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Select, forms.Textarea, forms.NumberInput, forms.FileInput, forms.SelectMultiple)):
                field.widget.attrs.update({
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                })
            
            if field_name == 'associated_legs':
                field.widget = forms.CheckboxSelectMultiple()
                field.widget.attrs.update({
                    'style': 'list-style-type: none; padding: 0; margin: 0;'
                })
    
    class Meta:
        model = FinancialRecord
        fields = [
            'date',
            'account',
            'party',
            'driver',
            'associated_legs',
            'category',
            'amount',
            'description',
            'document_ref'
        ]
        
        widgets = {
            'date': forms.DateInput(
                attrs={
                    'type': 'date',
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
            'description': forms.Textarea(
                attrs={
                    'rows': 4,
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
            'amount': forms.NumberInput(
                attrs={
                    'step': '0.01',
                    'min': '0',
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
            'document_ref': forms.FileInput(
                attrs={
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
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
            field.widget.attrs.update({
                'style': 'width: 100%; padding: 5px; margin: 2px 0;'
            })
    
    class Meta:
        model = Party
        fields = ['name', 'phone_number', 'state', 'address']
        
        widgets = {
            'address': forms.Textarea(
                attrs={
                    'rows': 3,
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
        }


class AccountForm(forms.ModelForm):
    """
    Form for creating and editing company accounts
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({
                'style': 'width: 100%; padding: 5px; margin: 2px 0;'
            })
    
    class Meta:
        model = Account
        fields = ['name', 'account_number', 'opening_balance', 'description']
        
        widgets = {
            'description': forms.Textarea(
                attrs={
                    'rows': 3,
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
        }