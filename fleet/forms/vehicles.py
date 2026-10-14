"""
Vehicle form for Fleet application.
"""
from django import forms
from fleet.models import Vehicle
from ledger.models import Party, CompanyAccount


class VehicleForm(forms.ModelForm):
    """
    Form for creating and editing vehicles
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
        
        # Filter vendor to only show Creditor-type parties
        self.fields['vendor'].queryset = Party.objects.filter(
            party_type=Party.TYPE_CREDITOR
        ).order_by('name')
        self.fields['company_account'].queryset = CompanyAccount.objects.all().order_by('name')
        self.fields['company_account'].empty_label = "Select Company Account..."
    
    class Meta:
        model = Vehicle
        fields = [
            'registration_plate',
            'make_model',
            'chassis_number',
            'engine_number',
            'purchase_date',
            'current_odometer',
            'status',
            'ownership',
            'vendor',
            'company_account',
        ]
        
        widgets = {
            'purchase_date': forms.DateInput(
                attrs={
                    'type': 'date'
                }
            ),
            'chassis_number': forms.TextInput(
                attrs={
                    'placeholder': 'Enter chassis number (optional)'
                }
            ),
            'engine_number': forms.TextInput(
                attrs={
                    'placeholder': 'Enter engine number (optional)'
                }
            ),
            'current_odometer': forms.NumberInput(
                attrs={
                    'placeholder': 'Current KM reading'
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        ownership = cleaned_data.get('ownership')
        vendor = cleaned_data.get('vendor')
        company_account = cleaned_data.get('company_account')

        if ownership == Vehicle.OWNERSHIP_ATTACHED:
            if not vendor:
                self.add_error('vendor', 'Vendor (Creditor) must be selected for attached vehicles.')
            cleaned_data['company_account'] = None
        elif ownership == Vehicle.OWNERSHIP_OWNED:
            cleaned_data['vendor'] = None

        return cleaned_data
