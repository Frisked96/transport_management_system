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
        
        # Add basic Tailwind styling for clarity
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Select, forms.Textarea, forms.DateTimeInput, forms.DateInput, forms.NumberInput)):
                field.widget.attrs.update({'class': tailwind_classes})
        
        # Add helper texts for clarity
        self.fields['start_odometer'].help_text = "Current vehicle odometer reading at start."
        self.fields['end_odometer'].help_text = "Current vehicle odometer reading at end (must be greater than start)."
        self.fields['diesel_liters'].help_text = "Total liters consumed during the trip."

    class Meta:
        model = Trip
        fields = [
            'vehicle',
            'driver',
            'party',
            'revenue_type',
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
            'diesel_liters',
            'diesel_rate',
            'diesel_total_cost',
            'notes'
        ]
        
        labels = {
            'rate_per_ton': 'Rate',
        }
        
        widgets = {
            'notes': forms.Textarea(
                attrs={
                    'rows': 3,
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


class TripFuelUpdateForm(forms.ModelForm):
    """
    Form to update only fuel-related data for a trip
    """
    class Meta:
        model = Trip
        fields = ['diesel_liters', 'diesel_rate', 'diesel_total_cost']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        for field_name, field in self.fields.items():
            field.widget.attrs.update({'class': tailwind_classes, 'step': '0.01', 'id': f'id_{field_name}'})


class TripStatusForm(forms.ModelForm):
    """
    Form for updating trip status only
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add basic Tailwind styling
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        self.fields['status'].widget.attrs.update({'class': tailwind_classes})
    
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
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        for field in self.fields.values():
            field.widget.attrs.update({'class': tailwind_classes})


class TripCustomExpenseForm(forms.ModelForm):
    """
    Form for adding/editing custom trip expenses
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Select, forms.Textarea, forms.DateTimeInput, forms.DateInput, forms.NumberInput)):
                field.widget.attrs.update({'class': tailwind_classes})
    
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
