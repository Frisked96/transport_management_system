from django import forms
from django.contrib.auth.models import User
from django.db import models
import json
from .models import FinancialRecord, Party, CompanyAccount, Bill
from trips.models import Trip


class TripChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        outstanding = getattr(obj, 'annotated_outstanding', 0)
        return f"{obj} | Pending: ₹{outstanding:,.2f}"

class BillChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        outstanding = getattr(obj, 'annotated_outstanding', 0)
        return f"{obj} | Pending: ₹{outstanding:,.2f}"

class FinancialRecordForm(forms.ModelForm):
    """
    Form for creating and editing financial records
    """
    # Hidden field to store JSON data for multi-trip payment distribution
    payment_distribution = forms.CharField(widget=forms.HiddenInput(), required=False)
    # Hidden field to store JSON data for multi-bill payment distribution
    bill_distribution = forms.CharField(widget=forms.HiddenInput(), required=False)
    
    associated_trip = TripChoiceField(
        queryset=Trip.objects.none(),
        required=False,
        label="Associated Trip"
    )
    associated_bill = BillChoiceField(
        queryset=Bill.objects.none(),
        required=False,
        label="Associated Bill"
    )

    tds_amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        label='TDS Amount',
        help_text='If TDS was deducted, enter the exact TDS amount here to automatically create a TDS entry.',
        widget=forms.NumberInput(attrs={
            'step': '0.01',
            'min': '0',
            'placeholder': 'TDS Amount',
            'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'
        })
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Determine context (Party or Driver entry)
        party = None
        driver_user = None
        
        # Check initial data (from URL params)
        if 'initial' in kwargs:
            party = kwargs['initial'].get('party')
            driver_user = kwargs['initial'].get('driver')
        
        # Check instance data (if editing)
        if self.instance and self.instance.pk:
            party = self.instance.party
            driver_user = self.instance.driver

        # Check POST data (if bound)
        if self.data:
            if self.data.get('party'):
                try:
                    party = Party.objects.get(pk=self.data.get('party'))
                except (ValueError, Party.DoesNotExist):
                    pass
            if self.data.get('driver'):
                try:
                    from django.contrib.auth.models import User
                    driver_user = User.objects.get(pk=self.data.get('driver'))
                except (ValueError, User.DoesNotExist):
                    pass

        # 1. If Party context: Remove Driver field
        if party:
            if 'driver' in self.fields:
                del self.fields['driver']
            
            if party.party_type == Party.TYPE_CREDITOR:
                # Creditors: No trips or bills (except manual entries)
                if 'associated_trip' in self.fields: del self.fields['associated_trip']
                if 'associated_bill' in self.fields: del self.fields['associated_bill']
                if 'payment_distribution' in self.fields: del self.fields['payment_distribution']
                if 'bill_distribution' in self.fields: del self.fields['bill_distribution']
                
                # Filter categories for Creditor
                from .models import TransactionCategory
                self.fields['category'].queryset = TransactionCategory.objects.filter(
                    models.Q(name__in=['Payment Out', 'Expense', 'Deductions', 'Debit Note', 'Credit Note'])
                ).order_by('name')
            else:
                # Setup trips for debtor party using dynamic payment info
                # Allow all trips that are not fully paid
                trips_qs = Trip.objects.with_payment_info().with_billing_info().filter(
                    party=party
                ).exclude(
                    annotated_status=Trip.PAYMENT_STATUS_PAID
                )
                
                # Include current trip if editing
                if self.instance and self.instance.associated_trip:
                    trips_qs = trips_qs | Trip.objects.filter(pk=self.instance.associated_trip.pk).with_payment_info().with_billing_info()
                
                self.fields['associated_trip'].queryset = trips_qs.distinct().order_by('-date')

                # Filter bills for this debtor: Exclude CN/DN and already Paid bills
                bills_qs = Bill.objects.with_payment_info().filter(party=party).filter(
                    models.Q(category__isnull=True) | ~models.Q(category__name__in=['Credit Note', 'Debit Note'])
                )
                
                # Further filter to only show unpaid bills
                unpaid_bill_ids = [b.id for b in bills_qs if b.payment_status != Bill.PAYMENT_STATUS_PAID]
                
                # Include current bill if editing
                if self.instance and self.instance.associated_bill:
                    if self.instance.associated_bill.pk not in unpaid_bill_ids:
                        unpaid_bill_ids.append(self.instance.associated_bill.pk)
                
                self.fields['associated_bill'].queryset = Bill.objects.with_payment_info().filter(id__in=unpaid_bill_ids).order_by('-date')
                
                # Filter categories for Debtor
                from .models import TransactionCategory
                self.fields['category'].queryset = TransactionCategory.objects.exclude(
                    models.Q(name='Payment Out') |
                    models.Q(name='Halting') |
                    models.Q(name='Debit Note') |
                    models.Q(name='Credit Note')
                ).order_by('name')
        
        # 2. If Driver context: Remove Party, Trip, and Bill fields
        elif driver_user:
            if 'party' in self.fields:
                del self.fields['party']
            if 'associated_trip' in self.fields:
                del self.fields['associated_trip']
            if 'associated_bill' in self.fields:
                del self.fields['associated_bill']
            if 'bill_distribution' in self.fields:
                del self.fields['bill_distribution']
        
        # 3. General Ledger context
        else:
            # Default empty queryset for trips/bills if no party selected yet
            self.fields['associated_trip'].queryset = Trip.objects.none()
            self.fields['associated_bill'].queryset = Bill.objects.none()

        # Filter drivers if field still exists
        if 'driver' in self.fields:
            from django.contrib.auth.models import User
            self.fields['driver'].queryset = User.objects.filter(
                groups__name='driver'
            ).order_by('username')
            self.fields['driver'].required = False
        
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if field_name not in ['payment_distribution', 'bill_distribution']:
                field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    
    class Meta:
        model = FinancialRecord
        fields = [
            'date',
            'record_type',
            'account',
            'party',
            'driver',
            'associated_trip',
            'associated_bill',
            'payment_distribution',
            'bill_distribution',
            'category',
            'amount',
            'tds_percentage',
            'description',
            'document_ref'
        ]
        
        widgets = {
            'date': forms.DateInput(
                attrs={
                    'type': 'date'
                }
            ),
            'tds_percentage': forms.NumberInput(
                attrs={
                    'step': '0.01',
                    'min': '0',
                    'max': '100',
                    'placeholder': 'TDS %'
                }
            ),
            'description': forms.Textarea(
                attrs={
                    'rows': 4
                }
            ),
        }


class PartyForm(forms.ModelForm):
# ... (rest of form) ...
    """
    Form for creating and editing parties
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    
    class Meta:
        model = Party
        fields = [
            'name', 'party_type', 'phone_number', 'state', 'address', 'gstin',
            'bank_name', 'bank_branch', 'account_number', 'ifsc_code', 'account_holder_name',
            'bank_details', 'opening_balance'
        ]
        
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
            'bank_details': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Legacy details if any...'}),
        }


class CompanyAccountForm(forms.ModelForm):
    """
    Form for creating and editing company accounts (Firms)
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})
    
    class Meta:
        model = CompanyAccount
        fields = [
            'name', 'address', 'phone_number', 'gstin', 'pan',
            'bank_name', 'bank_branch', 'account_number', 'ifsc_code', 'account_holder_name',
            'authorized_signatory', 'invoice_prefix', 'cn_prefix', 'dn_prefix', 'invoice_suffix', 'invoice_padding', 'invoice_sequence_start',
            'opening_balance', 'description'
        ]
        
        widgets = {
            'address': forms.Textarea(attrs={'rows': 2}),
            'description': forms.Textarea(attrs={'rows': 2}),
        }

class BillForm(forms.ModelForm):
    # Hidden field to store JSON mapping of trip_id -> lr_no
    trips_data = forms.CharField(widget=forms.HiddenInput(), required=False)
    prefix = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'id': 'id_prefix',
        'readonly': 'readonly',
        'tabindex': '-1',
        'style': 'width: 130px; border-right: none; border-top-right-radius: 0; border-bottom-right-radius: 0;',
        'class': 'bg-slate-100 border border-slate-300 px-3 py-2 text-sm text-slate-500 focus:outline-none'
    }))

    class Meta:
        model = Bill
        fields = [
            'bill_no',
            'bill_type',
            'category',
            'original_bill',
            'manual_original_bill_number',
            'manual_original_bill_date',
            'issuer',
            'party',
            'date',
            'item_type',
            'standard_weight',
            'standard_rate',
            'amount_override',
            'discount',
            'gst_rate',
            'gst_type',
            'use_roundoff',
            'trips',
            'trips_data'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'id': 'id_date'}),
            'manual_original_bill_date': forms.DateInput(attrs={'type': 'date', 'id': 'id_manual_original_bill_date'}),
            'trips': forms.CheckboxSelectMultiple(),
            'bill_no': forms.NumberInput(attrs={
                'id': 'id_bill_no',
                'min': '1',
                'style': 'border-top-left-radius: 0; border-bottom-left-radius: 0;',
                'class': 'flex-1 px-3 py-2 border border-slate-300 text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'
            }),
            'standard_weight': forms.NumberInput(attrs={'step': '0.001', 'id': 'id_standard_weight'}),
            'standard_rate': forms.NumberInput(attrs={'step': '0.01', 'id': 'id_standard_rate'}),
            'amount_override': forms.NumberInput(attrs={'step': '0.01', 'id': 'id_amount_override'}),
            'discount': forms.NumberInput(attrs={'step': '0.01', 'id': 'id_discount'}),
            'use_roundoff': forms.CheckboxInput(attrs={'id': 'id_use_roundoff', 'class': 'w-4 h-4 text-emerald-600 border-slate-300 rounded focus:ring-emerald-500'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Apply Tailwind classes
        for name, field in self.fields.items():
            if name not in ['trips', 'trips_data', 'prefix', 'bill_no', 'date']:
                field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})

        # Filter categories for standard invoices
        from .models import TransactionCategory
        self.fields['category'].queryset = TransactionCategory.objects.filter(
            models.Q(name__in=['Halting', 'Debit Note', 'Credit Note', 'Standard'])
        ).order_by('name')
        self.fields['category'].empty_label = "Select Category (Standard Only)"
        self.fields['category'].required = False

        # 1. Pre-populate bill_no for new records if issuer exists
        if not self.instance.pk:
            issuer = None
            category = None
            
            if self.data.get('issuer'):
                issuer = CompanyAccount.objects.filter(pk=self.data.get('issuer')).first()
            elif self.initial.get('issuer'):
                issuer = CompanyAccount.objects.filter(pk=self.initial.get('issuer')).first()
            elif CompanyAccount.objects.count() == 1:
                issuer = CompanyAccount.objects.first()
                self.initial['issuer'] = issuer.id
                self.fields['issuer'].initial = issuer.id

            if self.data.get('category'):
                category = TransactionCategory.objects.filter(pk=self.data.get('category')).first()
            elif self.initial.get('category'):
                category = TransactionCategory.objects.filter(pk=self.initial.get('category')).first()

            if issuer:
                self.fields['bill_no'].initial = Bill.get_next_available_no(issuer, self.initial.get('date'), category)
                # Set prefix initial using model method
                self.fields['prefix'].initial = self.instance.get_prefix(date=self.initial.get('date'))
        else:
             self.fields['prefix'].initial = self.instance.get_prefix()
        
        # 2. Logic to filter trips and original_bill based on Party
        party_id = None
        
        if self.is_bound: # POST data
            party_id = self.data.get('party')
        elif self.instance and self.instance.pk: # Edit mode
            party_id = self.instance.party_id
        else:
             if self.initial.get('party'):
                 party_id = self.initial.get('party')
            
        if party_id:
            try:
                # Show trips for this party
                qs = Trip.objects.filter(party_id=party_id)
                if self.instance and self.instance.pk:
                    qs = qs.filter(models.Q(bills__isnull=True) | models.Q(bills=self.instance))
                else:
                     qs = qs.filter(bills__isnull=True)
                self.fields['trips'].queryset = qs.distinct().order_by('-date')

                # Show original bills for this party (excluding CN/DN themselves if possible, but keep it simple)
                self.fields['original_bill'].queryset = Bill.objects.filter(
                    party_id=party_id
                ).exclude(
                    category__name__in=['Credit Note', 'Debit Note']
                ).order_by('-date')
            except (ValueError, TypeError):
                self.fields['trips'].queryset = Trip.objects.none()
                self.fields['original_bill'].queryset = Bill.objects.none()
        else:
            self.fields['trips'].queryset = Trip.objects.none()
            self.fields['original_bill'].queryset = Bill.objects.none()

        # If editing, populate trips_data with existing LR Nos and Discounts
        if self.instance and self.instance.pk:
            from .models import BillTrip
            bt_data = {bt.trip_id: {'lr_no': bt.lr_no, 'discount': str(bt.discount)} for bt in self.instance.bill_trips.all()}
            self.fields['trips_data'].initial = json.dumps(bt_data)
        elif 'trips' in self.initial:
            # For new bills with pre-selected trips, pre-populate LR Nos
            trips = Trip.objects.filter(id__in=self.initial['trips'])
            bt_data = {trip.id: {'lr_no': trip.lr_no, 'discount': '0.00'} for trip in trips if trip.lr_no}
            self.fields['trips_data'].initial = json.dumps(bt_data)

    def clean(self):
        cleaned_data = super().clean()
        bill_type = cleaned_data.get('bill_type')
        selected_trips = cleaned_data.get('trips')
        gst_type = cleaned_data.get('gst_type')

        # Logic for Trip-based Invoices
        if bill_type == 'Trip':
            if selected_trips:
                # Check for mixed GST types among selected trips
                trip_gst_types = set(trip.gst_type for trip in selected_trips)
                
                if len(trip_gst_types) > 1:
                    raise forms.ValidationError(
                        "Mixed Route Types! You cannot combine 'Local' and 'Intra/Interstate' trips in the same invoice. "
                        "Please create separate invoices for different route types."
                    )
                
                # Automatically set the Bill's GST type based on the trips
                derived_gst_type = list(trip_gst_types)[0]
                cleaned_data['gst_type'] = derived_gst_type
            else:
                # If no trips selected for a Trip-based bill, it should fail anyway if required
                pass
        
        # For Standard invoices, we keep the user-selected gst_type

        # Validation for Credit/Debit Notes
        category = cleaned_data.get('category')
        if category and category.name in ['Credit Note', 'Debit Note']:
            original_bill = cleaned_data.get('original_bill')
            manual_bill_no = cleaned_data.get('manual_original_bill_number')
            manual_bill_date = cleaned_data.get('manual_original_bill_date')
            
            if not original_bill and not manual_bill_no:
                raise forms.ValidationError("For Credit/Debit Notes, you must either select an existing invoice or enter manual invoice details.")
            
            if manual_bill_no and not manual_bill_date:
                raise forms.ValidationError("If entering a manual invoice number, you must also provide the invoice date.")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            
            # Handle BillTrip relationships with LR No and Discount
            selected_trips = self.cleaned_data.get('trips', [])
            trips_data_json = self.cleaned_data.get('trips_data', '{}')
            
            try:
                trips_extra = json.loads(trips_data_json) if trips_data_json else {}
            except json.JSONDecodeError:
                trips_extra = {}

            # Remove existing trips not in selected
            instance.bill_trips.exclude(trip__in=selected_trips).delete()

            # Create or update BillTrip for each selected trip
            from .models import BillTrip
            trips_to_update = []
            for trip in selected_trips:
                extra = trips_extra.get(str(trip.id)) or trips_extra.get(trip.id)
                
                lr_no = None
                discount = 0
                
                if isinstance(extra, dict):
                    lr_no = extra.get('lr_no')
                    discount = extra.get('discount') or 0
                else:
                    # Backward compatibility for old trips_data format (just LR No string)
                    lr_no = extra
                    discount = 0

                # Fallback to trip.lr_no if not provided in extra data
                if not lr_no:
                    lr_no = trip.lr_no
                
                # Sync LR No back to Trip if changed
                if lr_no and trip.lr_no != lr_no:
                    trip.lr_no = lr_no
                    trips_to_update.append(trip)

                # Still need to handle BillTrip per trip, but we've avoided Trip.save() in loop
                BillTrip.objects.update_or_create(
                    bill=instance,
                    trip=trip,
                    defaults={
                        'lr_no': lr_no,
                        'discount': discount
                    }
                )
            
            if trips_to_update:
                Trip.objects.bulk_update(trips_to_update, ['lr_no'])
            
            # Sync to Ledger (After trips are established)
            instance.sync_to_ledger()
            
        return instance
