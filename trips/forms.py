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
        fields = ['pickup_location', 'delivery_location', 'route_type', 'default_rate']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        # Special styling for default_rate to accommodate the '₹' icon
        self.fields['default_rate'].widget.attrs.update({'class': tailwind_classes + " pl-7"})
        for field_name, field in self.fields.items():
            if field_name != 'default_rate':
                field.widget.attrs.update({'class': tailwind_classes})


class TripForm(forms.ModelForm):
    """
    Form for creating and editing trips
    """
    
    class Meta:
        model = Trip
        fields = [
            'date',
            'lr_no',
            'vehicle',
            'driver',
            'party',
            'route',
            'revenue_type',
            'pickup_location',
            'delivery_location',
            'weight',
            'rate_per_ton',
            'vendor_hire_amount',
            'notes'
        ]
        
        labels = {
            'rate_per_ton': 'Rate',
            'lr_no': 'LR Number',
            'vendor_hire_amount': 'Vendor Hire (₹)',
        }
        
        widgets = {
            'notes': forms.Textarea(
                attrs={
                    'rows': 3,
                }
            ),
            'date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'weight': forms.NumberInput(attrs={'step': '0.01', 'inputmode': 'decimal'}),
            'rate_per_ton': forms.NumberInput(attrs={'step': '0.01', 'inputmode': 'decimal'}),
            'vendor_hire_amount': forms.NumberInput(attrs={'step': '0.01', 'inputmode': 'decimal'}),
            'pickup_location': forms.HiddenInput(),
            'delivery_location': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Route and Party are now mandatory
        self.fields['route'].required = True
        self.fields['party'].required = True
        self.fields['date'].required = True
        
        # Filter vehicles to only show active ones
        self.fields['vehicle'].queryset = Vehicle.objects.filter(
            status=Vehicle.STATUS_ACTIVE
        ).order_by('registration_plate')
        
        # Disable fields if trip is billed
        if self.instance and self.instance.pk and self.instance.is_billed:
            billed_fields = ['party', 'route', 'revenue_type', 'weight', 'rate_per_ton', 'vendor_hire_amount']
            for field_name in billed_fields:
                if field_name in self.fields:
                    self.fields[field_name].disabled = True
                    # Add help text only if not already present or to the main financial ones
                    if field_name in ['party', 'rate_per_ton']:
                        self.fields[field_name].help_text = f"{self.fields[field_name].label} cannot be changed because this trip is already billed. Delete the associated bill first."
        
        # Add basic Tailwind styling for clarity
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Select, forms.Textarea, forms.DateTimeInput, forms.DateInput, forms.NumberInput)):
                field.widget.attrs.update({'class': tailwind_classes})

    def clean(self):
        cleaned_data = super().clean()
        return cleaned_data
