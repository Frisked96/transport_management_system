from django import forms
from django.contrib.auth.models import User
from django.db import models, transaction
import json
from .models import FinancialRecord, Party, CompanyAccount, Bill, TollAccount, TollRecharge, TollEntry
from trips.models import Trip
from fleet.models import Vehicle


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

    deduction_amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        label='Deduction Amount',
        help_text='If deductions (shortage, charges, etc.) were made, enter the amount here.',
        widget=forms.NumberInput(attrs={
            'step': '0.01',
            'min': '0',
            'placeholder': 'Deductions (e.g. shortage, charges)',
            'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'
        })
    )

    deduction_notes = forms.CharField(
        max_length=255,
        required=False,
        label='Deduction Reason',
        widget=forms.TextInput(attrs={
            'placeholder': 'Reason (e.g. shortage, charges, round-off)',
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
                # Creditors: No per-trip allocations, but support bill settlements
                if 'associated_trip' in self.fields: del self.fields['associated_trip']
                if 'payment_distribution' in self.fields: del self.fields['payment_distribution']
                
                # Filter bills for this creditor: Exclude CN/DN and already Paid bills
                bills_qs = Bill.objects.with_payment_info().filter(party=party).filter(
                    models.Q(category__isnull=True) | ~models.Q(category__name__in=['Credit Note', 'Debit Note'])
                )
                unpaid_bill_ids = [b.id for b in bills_qs if b.payment_status != Bill.PAYMENT_STATUS_PAID]
                if self.instance and self.instance.associated_bill:
                    if self.instance.associated_bill.pk not in unpaid_bill_ids:
                        unpaid_bill_ids.append(self.instance.associated_bill.pk)
                initial_bill = kwargs.get('initial', {}).get('associated_bill')
                if initial_bill and initial_bill.pk not in unpaid_bill_ids:
                    unpaid_bill_ids.append(initial_bill.pk)

                self.fields['associated_bill'].queryset = Bill.objects.with_payment_info().filter(id__in=unpaid_bill_ids).order_by('date', 'id')

                # Filter categories for Creditor
                from .models import TransactionCategory
                self.fields['category'].queryset = TransactionCategory.objects.filter(
                    models.Q(name__in=['Payment Out', 'Expense', 'Deductions', 'TDS', 'Debit Note', 'Credit Note'])
                ).order_by('name')

                # Default to Payment Out if not set
                if not self.initial.get('category'):
                    p_out = TransactionCategory.objects.filter(name='Payment Out').first()
                    if p_out:
                        self.fields['category'].initial = p_out.id
            else:
                # Setup trips for debtor party using dynamic payment info
                # Allow all trips that are not fully paid
                trips_qs = Trip.objects.with_payment_info().with_billing_info().filter(
                    party=party
                ).exclude(
                    annotated_status=Trip.PAYMENT_STATUS_PAID
                )
                
                # Include current trip if editing or from initial
                if self.instance and self.instance.associated_trip:
                    trips_qs = trips_qs | Trip.objects.filter(pk=self.instance.associated_trip.pk).with_payment_info().with_billing_info()
                initial_trip = kwargs.get('initial', {}).get('associated_trip')
                if initial_trip:
                    trips_qs = trips_qs | Trip.objects.filter(pk=initial_trip.pk).with_payment_info().with_billing_info()
                
                self.fields['associated_trip'].queryset = trips_qs.distinct().order_by('-date')

                # Filter bills for this debtor: Exclude CN/DN and already Paid bills
                bills_qs = Bill.objects.with_payment_info().filter(party=party).filter(
                    models.Q(category__isnull=True) | ~models.Q(category__name__in=['Credit Note', 'Debit Note'])
                )
                
                # Further filter to only show unpaid bills
                unpaid_bill_ids = [b.id for b in bills_qs if b.payment_status != Bill.PAYMENT_STATUS_PAID]
                
                # Include current bill if editing or from initial
                if self.instance and self.instance.associated_bill:
                    if self.instance.associated_bill.pk not in unpaid_bill_ids:
                        unpaid_bill_ids.append(self.instance.associated_bill.pk)
                initial_bill = kwargs.get('initial', {}).get('associated_bill')
                if initial_bill and initial_bill.pk not in unpaid_bill_ids:
                    unpaid_bill_ids.append(initial_bill.pk)
                
                self.fields['associated_bill'].queryset = Bill.objects.with_payment_info().filter(id__in=unpaid_bill_ids).order_by('date', 'id')
                
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
        
        # Make amount not strictly required at field level to allow multi-trip or deduction-only settlements
        if 'amount' in self.fields:
            self.fields['amount'].required = False

        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if field_name not in ['payment_distribution', 'bill_distribution']:
                field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        if category and category.name in ['Deductions', 'TDS', 'Shortage']:
            cleaned_data['account'] = None

        # Auto-populate amount from multi-trip distribution if amount was not provided
        distribution_json = cleaned_data.get('payment_distribution')
        if distribution_json:
            try:
                import json
                from decimal import Decimal
                dist_data = json.loads(distribution_json)
                if dist_data:
                    total_p = sum(Decimal(str(item.get('payment', item.get('amount', 0)) or 0)) for item in dist_data)
                    total_tds = sum(Decimal(str(item.get('tds', 0) or 0)) for item in dist_data)
                    total_ded = sum(Decimal(str(item.get('deduction', 0) or 0)) for item in dist_data)
                    
                    if not cleaned_data.get('amount') or cleaned_data.get('amount') == 0:
                        cleaned_data['amount'] = total_p
                    if total_tds > 0 and (not cleaned_data.get('tds_amount') or cleaned_data.get('tds_amount') == 0):
                        cleaned_data['tds_amount'] = total_tds
                    if total_ded > 0 and (not cleaned_data.get('deduction_amount') or cleaned_data.get('deduction_amount') == 0):
                        cleaned_data['deduction_amount'] = total_ded
            except Exception:
                pass

        bill_distribution_json = cleaned_data.get('bill_distribution')
        if bill_distribution_json:
            try:
                import json
                from decimal import Decimal
                b_dist_data = json.loads(bill_distribution_json)
                if b_dist_data:
                    total_b = sum(Decimal(str(item.get('amount', 0) or 0)) for item in b_dist_data)
                    if not cleaned_data.get('amount') or cleaned_data.get('amount') == 0:
                        cleaned_data['amount'] = total_b
            except Exception:
                pass

        # Handle zero/empty bank payment when TDS or deduction is specified
        amt = cleaned_data.get('amount')
        tds = cleaned_data.get('tds_amount')
        ded = cleaned_data.get('deduction_amount')
        dist = cleaned_data.get('payment_distribution') or cleaned_data.get('bill_distribution')

        if (amt is None or amt == 0) and not dist:
            if (tds and tds > 0) or (ded and ded > 0):
                from decimal import Decimal
                cleaned_data['amount'] = Decimal('0.00')
            else:
                self.add_error('amount', 'Please specify an amount, TDS, or deduction.')

        return cleaned_data
    
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
            'tds_amount',
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
            'creditor_invoice_number',
            'creditor_invoice_date',
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
            'creditor_invoice_date': forms.DateInput(attrs={'type': 'date', 'id': 'id_creditor_invoice_date', 'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'}),
            'creditor_invoice_number': forms.TextInput(attrs={'id': 'id_creditor_invoice_number', 'placeholder': 'e.g. CR-INV-001 or Vendor Bill #', 'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'}),
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
            with transaction.atomic():
                # Temporarily suppress post_save on BillTrip from repeatedly saving Bill
                instance._suppress_billtrip_sync = True
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
                
                # Unsuppress, update financial caches and sync to ledger ONCE
                instance._suppress_billtrip_sync = False
                instance.update_financial_caches()
                instance.sync_to_ledger()
            
        return instance


# ==========================================
# TOLL LEDGER FORMS
# ==========================================

class TollAccountForm(forms.ModelForm):
    """
    Form for creating and editing Toll/FASTag accounts.
    """
    deduct_from_company_account = forms.ModelChoiceField(
        queryset=CompanyAccount.objects.all().order_by('name'),
        required=False,
        label="Fund Initial Balance from Bank?",
        help_text="Optional: If selected, an initial top-up entry is recorded from this company bank account."
    )

    class Meta:
        model = TollAccount
        fields = [
            'name', 'provider', 'account_number', 'opening_balance',
            'opening_balance_date', 'is_default', 'notes'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Primary FASTag Wallet'}),
            'provider': forms.TextInput(attrs={'placeholder': 'e.g. ICICI Bank, NHAI, IDFC First Bank'}),
            'account_number': forms.TextInput(attrs={'placeholder': 'e.g. Wallet ID or FASTag Account No.'}),
            'opening_balance': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'opening_balance_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional account notes or instructions'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({
                    'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'
                })
            else:
                field.widget.attrs.update({
                    'class': 'h-4 w-4 text-emerald-600 focus:ring-emerald-500 border-slate-300 rounded'
                })

    def save(self, commit=True):
        instance = super().save(commit=commit)
        company_acc = self.cleaned_data.get('deduct_from_company_account')
        if commit and company_acc and instance.opening_balance > 0:
            # Check if an initial recharge already exists for this account
            existing = TollRecharge.objects.filter(toll_account=instance, description__icontains='Initial starting balance').first()
            if not existing:
                recharge = TollRecharge.objects.create(
                    toll_account=instance,
                    company_account=company_acc,
                    date=instance.opening_balance_date,
                    amount=instance.opening_balance,
                    payment_mode=TollRecharge.MODE_BANK_TRANSFER,
                    reference_number='SETUP-OPENING-BAL',
                    description=f"Initial starting balance for {instance.name}"
                )
                recharge.sync_financial_record()
        return instance


class TollRechargeForm(forms.ModelForm):
    """
    Form for adding lumpsum money into a Toll Account from a Company Account.
    """
    class Meta:
        model = TollRecharge
        fields = [
            'toll_account', 'company_account', 'date', 'amount',
            'payment_mode', 'reference_number', 'receipt_document', 'description'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'placeholder': 'Amount in ₹'}),
            'reference_number': forms.TextInput(attrs={'placeholder': 'UTR / Txn ID / Cheque No / Reference'}),
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional recharge notes or bank narration'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['toll_account'].queryset = TollAccount.objects.all().order_by('-is_default', 'name')
        self.fields['company_account'].queryset = CompanyAccount.objects.all().order_by('name')

        # Auto-select default toll account if only 1 or has default
        if not self.initial.get('toll_account') and not self.data.get('toll_account'):
            default_acc = TollAccount.objects.filter(is_default=True).first() or TollAccount.objects.first()
            if default_acc:
                self.fields['toll_account'].initial = default_acc

        for name, field in self.fields.items():
            if field.widget.__class__.__name__ == 'ClearableFileInput':
                field.widget.attrs.update({
                    'class': 'block w-full text-xs text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-xs file:font-semibold file:bg-emerald-50 file:text-emerald-700 hover:file:bg-emerald-100'
                })
            else:
                field.widget.attrs.update({
                    'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'
                })


class TollEntryForm(forms.ModelForm):
    """
    Form for recording an individual toll charge against a vehicle.
    Deducts strictly from the Toll balance.
    """
    class Meta:
        model = TollEntry
        fields = [
            'toll_account', 'vehicle', 'date', 'amount', 'toll_plaza',
            'associated_trip', 'transaction_reference', 'receipt_document', 'description'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'placeholder': 'Toll Amount in ₹'}),
            'toll_plaza': forms.TextInput(attrs={'placeholder': 'e.g. Kherki Daula, Shahjahanpur, Barhi'}),
            'transaction_reference': forms.TextInput(attrs={'placeholder': 'Slip No / FASTag Txn ID'}),
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional remarks or driver notes'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['toll_account'].queryset = TollAccount.objects.all().order_by('-is_default', 'name')
        self.fields['vehicle'].queryset = Vehicle.objects.all().order_by('registration_plate')
        self.fields['associated_trip'].queryset = Trip.objects.all().order_by('-date')[:50]
        self.fields['associated_trip'].required = False

        # Auto-select default toll account
        if not self.initial.get('toll_account') and not self.data.get('toll_account'):
            default_acc = TollAccount.objects.filter(is_default=True).first() or TollAccount.objects.first()
            if default_acc:
                self.fields['toll_account'].initial = default_acc

        # Preselect vehicle if provided in initial
        vehicle = self.initial.get('vehicle')
        if vehicle:
            self.fields['associated_trip'].queryset = Trip.objects.filter(vehicle=vehicle).order_by('-date')[:30]

        for name, field in self.fields.items():
            if field.widget.__class__.__name__ == 'ClearableFileInput':
                field.widget.attrs.update({
                    'class': 'block w-full text-xs text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-xs file:font-semibold file:bg-emerald-50 file:text-emerald-700 hover:file:bg-emerald-100'
                })
            else:
                field.widget.attrs.update({
                    'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'
                })


class TollEntryBulkRowForm(forms.ModelForm):
    """
    Sub-form for a single row in bulk toll entry.
    """
    class Meta:
        model = TollEntry
        fields = ['date', 'vehicle', 'amount', 'toll_plaza', 'transaction_reference', 'description']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-2 py-1.5 border border-slate-300 rounded text-xs'}),
            'vehicle': forms.Select(attrs={'class': 'w-full px-2 py-1.5 border border-slate-300 rounded text-xs'}),
            'amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'placeholder': '₹', 'class': 'w-full px-2 py-1.5 border border-slate-300 rounded text-xs'}),
            'toll_plaza': forms.TextInput(attrs={'placeholder': 'Plaza name', 'class': 'w-full px-2 py-1.5 border border-slate-300 rounded text-xs'}),
            'transaction_reference': forms.TextInput(attrs={'placeholder': 'Slip / Ref', 'class': 'w-full px-2 py-1.5 border border-slate-300 rounded text-xs'}),
            'description': forms.TextInput(attrs={'placeholder': 'Notes', 'class': 'w-full px-2 py-1.5 border border-slate-300 rounded text-xs'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vehicle'].queryset = Vehicle.objects.all().order_by('registration_plate')
        self.fields['toll_plaza'].required = False
        self.fields['transaction_reference'].required = False
        self.fields['description'].required = False

