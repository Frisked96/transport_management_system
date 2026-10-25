"""
Tyre, TyreBrand, and TyreLog forms for Fleet application.
"""
from django import forms
from django.db import models
from fleet.models import Vehicle, Tyre, TyreLog, TyreBrand
from fleet.constants import (
    TYRE_POSITION_CHOICES,
    TYRE_POSITION_MAP,
)
from fleet.services import normalize_tyre_position


class TyrePositionChoiceField(forms.ChoiceField):
    """
    ChoiceField enforcing selection from the 18+1 tyre positions in the UI,
    while accepting legacy position aliases (e.g., 'Front Left', 'Rear Left 1')
    to preserve backwards compatibility.
    """
    def valid_value(self, value):
        if super().valid_value(value):
            return True
        norm = normalize_tyre_position(value)
        if norm and super().valid_value(norm):
            return True
        return False


class TyreForm(forms.ModelForm):
    """
    Form for adding/editing Tyres
    """
    brand = forms.ChoiceField(choices=[], required=True, label="Brand / Make")
    current_position = TyrePositionChoiceField(
        choices=[('', 'Select Position...')] + TYRE_POSITION_CHOICES,
        required=False,
        label="Wheel Position"
    )

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

        # Pre-fill or normalize current_position
        init_pos = self.initial.get('current_position') or (self.instance.current_position if self.instance.pk else '')
        if init_pos:
            norm_init_pos = normalize_tyre_position(init_pos)
            self.initial['current_position'] = init_pos
            if norm_init_pos not in TYRE_POSITION_MAP and norm_init_pos != '':
                pos_choices = [('', 'Select Position...'), ('Current Position', [(init_pos, init_pos)])] + TYRE_POSITION_CHOICES
                self.fields['current_position'].choices = pos_choices

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
        if self.instance.pk:
            return self.instance.status
        return self.cleaned_data.get('status')

    def clean(self):
        cleaned_data = super().clean()
        vehicle = cleaned_data.get('current_vehicle')
        raw_position = (cleaned_data.get('current_position') or '').strip()
        norm_position = normalize_tyre_position(raw_position)
        position = raw_position
        cleaned_data['current_position'] = position

        if vehicle and position:
            collision = Tyre.objects.filter(
                current_vehicle=vehicle
            ).filter(
                models.Q(current_position__iexact=position) |
                models.Q(current_position__iexact=norm_position)
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
        self.fields['vendor'].queryset = self.fields['vendor'].queryset.filter(party_type__in=['Creditor', 'Both'])
        for field_name, field in self.fields.items():
            if field_name != 'suggestive_price':
                field.widget.attrs.update({'class': tailwind_classes})


class TyreLogForm(forms.ModelForm):
    """
    Form for tyre operations (Mount/Dismount/Repair)
    """
    position = TyrePositionChoiceField(
        choices=[('', 'Select Position...')] + TYRE_POSITION_CHOICES,
        required=False,
        label="Wheel Position"
    )

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
        elif self.initial.get('action') == TyreLog.ACTION_MOUNT:
            self.fields['tyre'].queryset = Tyre.objects.filter(
                models.Q(status=Tyre.STATUS_IN_STOCK) | models.Q(current_vehicle__isnull=True)
            ).order_by('brand', 'serial_number')

        # Pre-fill or normalize position
        init_pos = self.initial.get('position') or (self.instance.position if self.instance.pk else '')
        if init_pos:
            norm_init_pos = normalize_tyre_position(init_pos)
            self.initial['position'] = init_pos
            if norm_init_pos not in TYRE_POSITION_MAP and norm_init_pos != '':
                self.fields['position'].choices = [('', 'Select Position...'), ('Current Position', [(init_pos, init_pos)])] + TYRE_POSITION_CHOICES

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
        raw_position = (cleaned_data.get('position') or '').strip()
        norm_position = normalize_tyre_position(raw_position)
        position = raw_position
        cleaned_data['position'] = position

        if action == TyreLog.ACTION_MOUNT:
            if tyre and tyre.status == Tyre.STATUS_SCRAP:
                raise forms.ValidationError("Cannot mount a scrapped tyre.")
            if not vehicle:
                self.add_error('vehicle', "Vehicle is required when mounting a tyre.")
            if not position:
                self.add_error('position', "Position is required when mounting a tyre.")
            elif vehicle:
                collision = Tyre.objects.filter(
                    current_vehicle=vehicle
                ).filter(
                    models.Q(current_position__iexact=position) |
                    models.Q(current_position__iexact=norm_position)
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
                    current_vehicle=vehicle
                ).filter(
                    models.Q(current_position__iexact=position) |
                    models.Q(current_position__iexact=norm_position)
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
