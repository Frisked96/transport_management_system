"""
Forms for Trips application
"""
from django import forms
from django.contrib.auth.models import User
from .models import Trip, TripLeg, TripExpense
from fleet.models import Vehicle


class TripForm(forms.ModelForm):
    """
    Form for creating and editing trips
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter drivers to only show users in the 'driver' group
        self.fields['driver'].queryset = User.objects.filter(
            groups__name='driver'
        ).order_by('username')
        
        # Filter vehicles to only show active ones
        self.fields['vehicle'].queryset = Vehicle.objects.filter(
            status=Vehicle.STATUS_ACTIVE
        ).order_by('registration_plate')
        
        # Make trip_number readonly as it is auto-generated
        if 'trip_number' in self.fields:
            self.fields['trip_number'].required = False
            self.fields['trip_number'].widget.attrs['readonly'] = True
            self.fields['trip_number'].help_text = "Auto-generated upon creation"

        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Select, forms.Textarea, forms.DateTimeInput, forms.DateInput)):
                field.widget.attrs.update({'class': 'form-control'})
    
    class Meta:
        model = Trip
        fields = [
            'trip_number',
            'driver',
            'vehicle',
            'status',
            'notes'
        ]
        
        widgets = {
            'notes': forms.Textarea(
                attrs={
                    'rows': 4,
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
        }


class TripLegForm(forms.ModelForm):
    """
    Form for creating and editing trip legs
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add basic styling
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})

    class Meta:
        model = TripLeg
        fields = [
            'date',
            'party',
            'pickup_location',
            'delivery_location',
            'weight',
            'price_per_ton'
        ]
        
        widgets = {
            'date': forms.DateTimeInput(
                attrs={
                    'type': 'datetime-local',
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
        }


class TripStatusForm(forms.ModelForm):
    """
    Form for updating trip status only
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add basic styling
        self.fields['status'].widget.attrs.update({'class': 'form-select'})
    
    class Meta:
        model = Trip
        fields = ['status']
        
        widgets = {
            'status': forms.Select(
                attrs={
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            )
        }


class TripExpenseUpdateForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
    
    class Meta:
        model = Trip
        fields = ['diesel_expense', 'toll_expense']


class TripCustomExpenseForm(forms.ModelForm):
    """
    Form for adding/editing custom trip expenses
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
    
    class Meta:
        model = TripExpense
        fields = ['name', 'amount', 'notes']
