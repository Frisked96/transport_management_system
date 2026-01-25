"""
Forms for Trips application
"""
from django import forms
from django.contrib.auth.models import User
from .models import Trip, TripExpense
from fleet.models import Vehicle


class TripForm(forms.ModelForm):
    """
    Form for creating and editing trips
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Filter vehicles to only show active ones
        self.fields['vehicle'].queryset = Vehicle.objects.filter(
            status=Vehicle.STATUS_ACTIVE
        ).order_by('registration_plate')
        
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Select, forms.Textarea, forms.DateTimeInput, forms.DateInput, forms.NumberInput)):
                field.widget.attrs.update({'class': 'form-control'})
    
    class Meta:
        model = Trip
        fields = [
            'vehicle',
            'driver',
            'party',
            'pickup_location',
            'pickup_lat',
            'pickup_lng',
            'delivery_location',
            'delivery_lat',
            'delivery_lng',
            'weight',
            'rate_per_ton',
            'start_odometer',
            'end_odometer',
            'notes'
        ]
        
        widgets = {
            'notes': forms.Textarea(
                attrs={
                    'rows': 3
                }
            ),
            'pickup_lat': forms.HiddenInput(),
            'pickup_lng': forms.HiddenInput(),
            'delivery_lat': forms.HiddenInput(),
            'delivery_lng': forms.HiddenInput(),
        }

    def clean(self):
        cleaned_data = super().clean()
        # Ensure empty strings for coordinates become None
        for field in ['pickup_lat', 'pickup_lng', 'delivery_lat', 'delivery_lng']:
            if not cleaned_data.get(field):
                cleaned_data[field] = None
        return cleaned_data


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
            'status': forms.Select()
        }


class TripExpenseUpdateForm(forms.Form):
    diesel_expense = forms.DecimalField(max_digits=10, decimal_places=2, required=False)
    toll_expense = forms.DecimalField(max_digits=10, decimal_places=2, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})


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
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 1}),
        }


# Inline Formset for Trip Expenses
TripExpenseFormSet = forms.inlineformset_factory(
    Trip,
    TripExpense,
    form=TripCustomExpenseForm,
    extra=1,
    can_delete=True
)