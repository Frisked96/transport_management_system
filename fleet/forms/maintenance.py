"""
Maintenance record forms for Fleet application.
"""
from django import forms
from django.utils import timezone
from fleet.models import Vehicle, MaintenanceRecord


class MaintenanceRecordForm(forms.ModelForm):
    """
    Form for creating and editing maintenance records (both pending and completed)
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vehicle'].queryset = Vehicle.objects.all().order_by('registration_plate')
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
