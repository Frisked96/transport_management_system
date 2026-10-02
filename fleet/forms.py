"""
Forms for Fleet application
"""
from django import forms
from django.utils import timezone
from .models import Vehicle, MaintenanceRecord, Tyre, TyreLog, TyreBrand


class VehicleForm(forms.ModelForm):
    """
    Form for creating and editing vehicles
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
        
        # Filter vendor to only show Creditor-type parties
        from ledger.models import Party
        self.fields['vendor'].queryset = Party.objects.filter(
            party_type=Party.TYPE_CREDITOR
        ).order_by('name')
    
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
        }
    
    def clean(self):
        cleaned_data = super().clean()
        ownership = cleaned_data.get('ownership')
        vendor = cleaned_data.get('vendor')
        
        if ownership == Vehicle.OWNERSHIP_ATTACHED and not vendor:
            self.add_error('vendor', 'Vendor is required for Attached vehicles.')
        
        if ownership == Vehicle.OWNERSHIP_OWNED and vendor:
            cleaned_data['vendor'] = None
        
        return cleaned_data


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
    brand = forms.ChoiceField(choices=[], required=True, label="Brand / Make")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Populate brand choices from TyreBrand model and existing tyre records
        brand_choices = [('', '---------')]
        brands_from_brand = list(TyreBrand.objects.values_list('name', flat=True).order_by('name'))
        brands_from_tyres = list(Tyre.objects.exclude(brand='').values_list('brand', flat=True).distinct().order_by('brand'))
        combined_brands = sorted(set(brands_from_brand + brands_from_tyres))
        brand_choices.extend([(b, b) for b in combined_brands])
        if self.instance.pk and self.instance.brand and (self.instance.brand, self.instance.brand) not in brand_choices:
            brand_choices.append((self.instance.brand, self.instance.brand))
        self.fields['brand'].choices = brand_choices

        for field in self.fields.values():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
        
        # Status is enforced in model.save(), so we can make it informative but read-only if editing
        if self.instance.pk:
            self.fields['status'].widget.attrs['disabled'] = True
            self.fields['status'].required = False

        self.fields['size'].widget.attrs.update({'data-autocomplete': 'tyre_size', 'list': 'tyre_size_list'})

    class Meta:
        model = Tyre
        fields = [
            'serial_number', 'brand', 'size', 'purchase_date', 
            'purchase_cost', 'vendor', 'current_vehicle', 'current_position', 'status', 'photo', 'notes'
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

    def clean(self):
        cleaned_data = super().clean()
        vehicle = cleaned_data.get('current_vehicle')
        position = (cleaned_data.get('current_position') or '').strip()

        if vehicle and position:
            collision = Tyre.objects.filter(
                current_vehicle=vehicle,
                current_position__iexact=position
            )
            if self.instance.pk:
                collision = collision.exclude(pk=self.instance.pk)
            if collision.exists():
                other = collision.first()
                self.add_error(
                    'current_position',
                    f"Position '{position}' is already occupied by Tyre {other.serial_number} ({other.brand}) on vehicle {vehicle.registration_plate}."
                )
        elif vehicle and not position:
            self.add_error('current_position', 'Position is required when assigning a tyre to a vehicle.')

        return cleaned_data


class TyreBrandForm(forms.ModelForm):
    """
    Form for creating and editing Tyre Brands
    """
    class Meta:
        model = TyreBrand
        fields = ['name', 'suggestive_price', 'vendor']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        self.fields['suggestive_price'].widget.attrs.update({'class': tailwind_classes + " pl-7"})
        self.fields['vendor'].queryset = self.fields['vendor'].queryset.filter(party_type='Creditor')
        for field_name, field in self.fields.items():
            if field_name != 'suggestive_price':
                field.widget.attrs.update({'class': tailwind_classes})


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

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get('action')
        tyre = cleaned_data.get('tyre')
        vehicle = cleaned_data.get('vehicle')
        position = (cleaned_data.get('position') or '').strip()

        if action == TyreLog.ACTION_MOUNT:
            if tyre and tyre.status == Tyre.STATUS_SCRAP:
                raise forms.ValidationError("Cannot mount a scrapped tyre.")
            if not vehicle:
                self.add_error('vehicle', "Vehicle is required when mounting a tyre.")
            if not position:
                self.add_error('position', "Position is required when mounting a tyre.")
            elif vehicle:
                collision = Tyre.objects.filter(
                    current_vehicle=vehicle,
                    current_position__iexact=position
                )
                if tyre:
                    collision = collision.exclude(pk=tyre.pk)
                if collision.exists():
                    other = collision.first()
                    self.add_error(
                        'position',
                        f"Position '{position}' on vehicle {vehicle.registration_plate} is already occupied by Tyre {other.serial_number} ({other.brand})."
                    )
        elif action == TyreLog.ACTION_ROTATION:
            if not vehicle and tyre and tyre.current_vehicle:
                vehicle = tyre.current_vehicle
                cleaned_data['vehicle'] = vehicle
            if not position:
                self.add_error('position', "Target position is required for tyre rotation.")
            elif vehicle:
                collision = Tyre.objects.filter(
                    current_vehicle=vehicle,
                    current_position__iexact=position
                )
                if tyre:
                    collision = collision.exclude(pk=tyre.pk)
                if collision.exists():
                    other = collision.first()
                    self.add_error(
                        'position',
                        f"Position '{position}' on vehicle {vehicle.registration_plate} is already occupied by Tyre {other.serial_number} ({other.brand})."
                    )

        return cleaned_data