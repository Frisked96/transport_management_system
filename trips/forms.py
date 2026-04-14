"""
Forms for Trips application
"""
from django import forms
from .models import Trip
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

    class Meta:
        model = Trip
        fields = [
            'date',
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
