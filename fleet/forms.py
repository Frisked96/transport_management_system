"""
Forms for Fleet application
"""
from django import forms
from .models import Vehicle, MaintenanceLog


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
        # Filter vehicles to show all (not just active, for maintenance history)
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
            'description',
            'cost',
            'service_provider',
            'next_service_due'
        ]
        
        widgets = {
            'date': forms.DateInput(
                attrs={
                    'type': 'date'
                }
            ),
            'next_service_due': forms.DateInput(
                attrs={
                    'type': 'date'
                }
            ),
            'description': forms.Textarea(
                attrs={
                    'rows': 4
                }
            ),
            'cost': forms.NumberInput(
                attrs={
                    'step': '0.01',
                    'min': '0'
                }
            ),
        }