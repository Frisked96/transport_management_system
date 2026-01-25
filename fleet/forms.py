"""
Forms for Fleet application
"""
from django import forms
from .models import Vehicle, MaintenanceLog, Tyre, TyreLog


class VehicleForm(forms.ModelForm):
    """
    Form for creating and editing vehicles
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select,)):
                field.widget.attrs.update({'class': 'form-select'})
            else:
                field.widget.attrs.update({'class': 'form-control'})
    
    class Meta:
        model = Vehicle
        fields = [
            'registration_plate',
            'make_model',
            'purchase_date',
            'current_odometer',
            'status'
        ]
        
        widgets = {
            'purchase_date': forms.DateInput(
                attrs={
                    'type': 'date'
                }
            ),
        }


class MaintenanceLogForm(forms.ModelForm):
    """
    Form for creating and editing maintenance logs
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter vehicles to show all
        self.fields['vehicle'].queryset = Vehicle.objects.all().order_by('registration_plate')
        
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.Select,)):
                field.widget.attrs.update({'class': 'form-select'})
            else:
                field.widget.attrs.update({'class': 'form-control'})
    
    class Meta:
        model = MaintenanceLog
        fields = [
            'vehicle',
            'date',
            'type',
            'odometer_reading',
            'description',
            'cost',
            'service_provider',
            'next_service_due',
            'next_service_odometer'
        ]
        
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'next_service_due': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }


class TyreForm(forms.ModelForm):
    """
    Form for adding/editing Tyres
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.Select,)):
                field.widget.attrs.update({'class': 'form-select'})
            else:
                field.widget.attrs.update({'class': 'form-control'})
        
        # Add data-autocomplete-field for JS to hook into
        self.fields['brand'].widget.attrs.update({'data-autocomplete': 'tyre_brand', 'list': 'tyre_brand_list'})
        self.fields['size'].widget.attrs.update({'data-autocomplete': 'tyre_size', 'list': 'tyre_size_list'})

    class Meta:
        model = Tyre
        fields = [
            'serial_number', 'brand', 'size', 'purchase_date', 
            'purchase_cost', 'current_vehicle', 'current_position', 'status', 'notes'
        ]
        widgets = {
            'purchase_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }


class TyreLogForm(forms.ModelForm):
    """
    Form for tyre operations (Mount/Dismount/Repair)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.Select,)):
                field.widget.attrs.update({'class': 'form-select'})
            else:
                field.widget.attrs.update({'class': 'form-control'})

    class Meta:
        model = TyreLog
        fields = ['tyre', 'date', 'action', 'vehicle', 'position', 'odometer', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }