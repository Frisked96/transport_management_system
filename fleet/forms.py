"""
Forms for Fleet application
"""
from django import forms
from django.utils import timezone
from .models import Vehicle, MaintenanceRecord, Tyre, TyreLog


class VehicleForm(forms.ModelForm):
    """
    Form for creating and editing vehicles
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    
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


class MaintenanceRecordForm(forms.ModelForm):
    """
    Form for creating and editing maintenance records (both pending and completed)
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter vehicles to show all
        self.fields['vehicle'].queryset = Vehicle.objects.all().order_by('registration_plate')
        
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    
    class Meta:
        model = MaintenanceRecord
        fields = [
            'vehicle',
            'name',
            'is_completed',
            'expiry_date',
            'expiry_km',
            'interval_days',
            'interval_km',
            'completion_date',
            'completion_km',
            'cost',
            'service_provider',
            'notes'
        ]
        
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'completion_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }


class MaintenanceCompleteForm(forms.Form):
    """
    Form for marking a pending maintenance record as completed
    """
    completion_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'}),
        initial=timezone.now
    )
    completion_km = forms.IntegerField(
        min_value=0,
        widget=forms.NumberInput(attrs={'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    )
    cost = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        initial=0,
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    )
    service_provider = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'}),
        required=False
    )


class TyreForm(forms.ModelForm):
    """
    Form for adding/editing Tyres
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
        
        # Status is enforced in model.save(), so we can make it informative but read-only if editing
        if self.instance.pk:
            self.fields['status'].widget.attrs['disabled'] = True
            self.fields['status'].required = False

        # Add data-autocomplete-field for JS to hook into
        self.fields['brand'].widget.attrs.update({'data-autocomplete': 'tyre_brand', 'list': 'tyre_brand_list'})
        self.fields['size'].widget.attrs.update({'data-autocomplete': 'tyre_size', 'list': 'tyre_size_list'})

    class Meta:
        model = Tyre
        fields = [
            'serial_number', 'brand', 'size', 'purchase_date', 
            'purchase_cost', 'current_vehicle', 'current_position', 'status', 'photo', 'notes'
        ]
        widgets = {
            'purchase_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_status(self):
        # Return current status if disabled
        if self.instance.pk:
            return self.instance.status
        return self.cleaned_data.get('status')


class TyreLogForm(forms.ModelForm):
    """
    Form for tyre operations (Mount/Dismount/Repair)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})

        # Filter tyre if provided
        tyre_val = self.initial.get('tyre') or self.data.get('tyre')
        if tyre_val:
            if isinstance(tyre_val, Tyre):
                tyre = tyre_val
            else:
                try:
                    tyre = Tyre.objects.get(pk=tyre_val)
                except (Tyre.DoesNotExist, ValueError, TypeError):
                    tyre = None
            
            if tyre:
                self.fields['tyre'].initial = tyre
                self.fields['tyre'].widget = forms.HiddenInput()

    class Meta:
        model = TyreLog
        fields = ['tyre', 'date', 'action', 'vehicle', 'position', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }