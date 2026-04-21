"""
Forms for Trips application
"""
from django import forms
from .models import Trip, Route
from fleet.models import Vehicle

class RouteForm(forms.ModelForm):
    """
    Form for creating and editing routes
    """
    class Meta:
        model = Route
        fields = ['pickup_location', 'delivery_location', 'route_type']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        for field_name, field in self.fields.items():
            field.widget.attrs.update({'class': tailwind_classes})


class TripForm(forms.ModelForm):
    """
    Form for creating and editing trips
    """
    
    class Meta:
        model = Trip
        fields = [
            'date',
            'vehicle',
            'driver',
            'party',
            'route',
            'revenue_type',
            'pickup_location',
            'delivery_location',
            'weight',
            'rate_per_ton',
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
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'pickup_location': forms.HiddenInput(),
            'delivery_location': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Route is now the primary way to set locations
        self.fields['route'].required = True
        
        # Filter vehicles to only show active ones
        self.fields['vehicle'].queryset = Vehicle.objects.filter(
            status=Vehicle.STATUS_ACTIVE
        ).order_by('registration_plate')
        
        # Add basic Tailwind styling for clarity
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Select, forms.Textarea, forms.DateTimeInput, forms.DateInput, forms.NumberInput)):
                field.widget.attrs.update({'class': tailwind_classes})

    def clean(self):
        cleaned_data = super().clean()
        return cleaned_data
