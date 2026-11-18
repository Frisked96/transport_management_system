"""
Form for creating and editing Bill (Invoice) instances and associated BillTrip relationships.
"""
import json
from decimal import Decimal, InvalidOperation
from django import forms
from django.db import models, transaction

from ledger.models import Bill, BillTrip, BillItem, CompanyAccount, TransactionCategory
from ledger.utils import sort_trips_for_bill_generation, parse_numeric_lr
from trips.models import Trip
from fleet.models import Vehicle


class BillForm(forms.ModelForm):
    # Hidden field to store JSON mapping of trip_id -> lr_no
    trips_data = forms.CharField(widget=forms.HiddenInput(), required=False)
    # Hidden field to store JSON array of standard line items (vehicle rows)
    items_data = forms.CharField(widget=forms.HiddenInput(), required=False)
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
            'trips_data',
            'items_data'
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
            if name not in ['trips', 'trips_data', 'items_data', 'prefix', 'bill_no', 'date']:
                field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})

        # Filter categories for standard invoices
        self.fields['category'].queryset = TransactionCategory.objects.filter(
            models.Q(name__in=['Halting', 'Debit Note', 'Credit Note', 'Standard'])
        ).order_by('name')
        self.fields['category'].empty_label = "Select Category (Standard Only)"
        self.fields['category'].required = False
        self.fields['discount'].required = False

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
                self.fields['prefix'].initial = self.instance.get_prefix(date=self.initial.get('date'))
        else:
             self.fields['prefix'].initial = self.instance.get_prefix()
        
        # 2. Logic to filter trips and original_bill based on Party
        party_id = None
        
        if self.is_bound:
            party_id = self.data.get('party')
        elif self.instance and self.instance.pk:
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
                    bt_map = {bt.trip_id: bt.lr_no for bt in self.instance.bill_trips.all()}
                else:
                    qs = qs.filter(bills__isnull=True)
                    bt_map = {}

                trips_list = list(qs.distinct().order_by('-date', '-created_at'))
                sorted_trips = sort_trips_for_bill_generation(trips_list, bill_trips_map=bt_map)
                sorted_ids = [t.id for t in sorted_trips]
                if sorted_ids:
                    preserved = models.Case(*[models.When(id=tid, then=pos) for pos, tid in enumerate(sorted_ids)])
                    self.fields['trips'].queryset = qs.filter(id__in=sorted_ids).order_by(preserved)
                else:
                    self.fields['trips'].queryset = qs.none()

                # Show original bills for this party
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
            bt_data = {bt.trip_id: {'lr_no': bt.lr_no, 'discount': str(bt.discount)} for bt in self.instance.bill_trips.all()}
            self.fields['trips_data'].initial = json.dumps(bt_data)
            
            if self.instance.bill_type == Bill.TYPE_STANDARD:
                items_list = [
                    {
                        'id': it.id,
                        'vehicle_id': it.vehicle_id or '',
                        'vehicle_number': it.vehicle_number or (it.vehicle.registration_plate if it.vehicle else ''),
                        'description': it.description or '',
                        'quantity': str(it.quantity) if it.quantity is not None else '1',
                        'rate': str(it.rate) if it.rate is not None else '0',
                        'amount': str(it.amount) if it.amount is not None else '0',
                        'lr_no': it.lr_no or '',
                    }
                    for it in self.instance.items.select_related('vehicle').all()
                ]
                self.fields['items_data'].initial = json.dumps(items_list)
        elif 'trips' in self.initial:
            trips = Trip.objects.filter(id__in=self.initial['trips'])
            bt_data = {trip.id: {'lr_no': trip.lr_no, 'discount': '0.00'} for trip in trips if trip.lr_no}
            self.fields['trips_data'].initial = json.dumps(bt_data)

    def clean_discount(self):
        val = self.cleaned_data.get('discount')
        if val is None or val == '':
            return Decimal('0.00')
        return val

    def clean(self):
        cleaned_data = super().clean()
        bill_type = cleaned_data.get('bill_type')
        selected_trips = cleaned_data.get('trips')

        # Logic for Trip-based Invoices
        if bill_type == 'Trip':
            if selected_trips:
                trip_gst_types = set(trip.gst_type for trip in selected_trips)
                
                if len(trip_gst_types) > 1:
                    raise forms.ValidationError(
                        "Mixed Route Types! You cannot combine 'Local' and 'Intra/Interstate' trips in the same invoice. "
                        "Please create separate invoices for different route types."
                    )
                
                derived_gst_type = list(trip_gst_types)[0]
                cleaned_data['gst_type'] = derived_gst_type

        # Logic for Standard Invoices / Halting / Credit Notes
        if bill_type == 'Standard':
            items_data_json = cleaned_data.get('items_data')
            if items_data_json:
                try:
                    parsed_items = json.loads(items_data_json)
                    if isinstance(parsed_items, list):
                        valid_items = [
                            it for it in parsed_items
                            if (it.get('amount') and float(it.get('amount') or 0) > 0)
                            or it.get('vehicle_number')
                            or it.get('vehicle_id')
                            or it.get('description')
                        ]
                        if valid_items:
                            items_total = sum(Decimal(str(it.get('amount') or 0)) for it in valid_items)
                            cleaned_data['amount_override'] = items_total
                            cat = cleaned_data.get('category')
                            if not cleaned_data.get('item_type'):
                                cleaned_data['item_type'] = cat.name if cat else 'Standard Item'
                except (json.JSONDecodeError, TypeError, ValueError, InvalidOperation):
                    pass

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
                instance._suppress_billtrip_sync = True

                # Parse items_data if Standard bill
                items_data_json = self.cleaned_data.get('items_data')
                parsed_items = []
                if items_data_json:
                    try:
                        parsed_items = json.loads(items_data_json)
                    except (json.JSONDecodeError, TypeError):
                        parsed_items = []

                if instance.bill_type == Bill.TYPE_STANDARD and parsed_items:
                    valid_items = [
                        it for it in parsed_items
                        if (it.get('amount') and float(it.get('amount') or 0) > 0)
                        or it.get('vehicle_number')
                        or it.get('vehicle_id')
                        or it.get('description')
                    ]
                    if valid_items:
                        items_total = sum(Decimal(str(it.get('amount') or 0)) for it in valid_items)
                        instance.amount_override = items_total
                        if not instance.item_type:
                            instance.item_type = instance.category.name if instance.category else 'Standard Item'

                instance.save()

                if instance.bill_type == Bill.TYPE_STANDARD:
                    instance.items.all().delete()
                    if parsed_items:
                        valid_items = [
                            it for it in parsed_items
                            if (it.get('amount') and float(it.get('amount') or 0) > 0)
                            or it.get('vehicle_number')
                            or it.get('vehicle_id')
                            or it.get('description')
                        ]
                        items_to_create = []
                        for it in valid_items:
                            v_id = it.get('vehicle_id')
                            vehicle_obj = None
                            if v_id and str(v_id).isdigit():
                                vehicle_obj = Vehicle.objects.filter(pk=int(v_id)).first()
                            
                            v_num = (it.get('vehicle_number') or '').strip()
                            if vehicle_obj and not v_num:
                                v_num = vehicle_obj.registration_plate

                            items_to_create.append(BillItem(
                                bill=instance,
                                vehicle=vehicle_obj,
                                vehicle_number=v_num,
                                description=it.get('description') or '',
                                quantity=Decimal(str(it.get('quantity') or 1)),
                                rate=Decimal(str(it.get('rate') or 0)),
                                amount=Decimal(str(it.get('amount') or 0)),
                                lr_no=it.get('lr_no') or '',
                            ))
                        if items_to_create:
                            BillItem.objects.bulk_create(items_to_create)
                
                selected_trips = list(self.cleaned_data.get('trips', []))
                trips_data_json = self.cleaned_data.get('trips_data', '{}')
                
                try:
                    trips_extra = json.loads(trips_data_json) if trips_data_json else {}
                except json.JSONDecodeError:
                    trips_extra = {}

                def get_trip_lr(t):
                    extra = trips_extra.get(str(t.id)) or trips_extra.get(t.id)
                    if isinstance(extra, dict):
                        return extra.get('lr_no') or t.lr_no
                    elif extra:
                        return extra
                    return t.lr_no

                import datetime
                selected_trips = sorted(
                    selected_trips,
                    key=lambda t: (
                        (0, parse_numeric_lr(get_trip_lr(t)), t.date or datetime.date.min, t.id)
                        if parse_numeric_lr(get_trip_lr(t)) is not None
                        else (1, 0, t.date or datetime.date.min, t.id)
                    )
                )

                instance.bill_trips.exclude(trip__in=selected_trips).delete()

                trips_to_update = []
                for trip in selected_trips:
                    extra = trips_extra.get(str(trip.id)) or trips_extra.get(trip.id)
                    
                    lr_no = None
                    discount = 0
                    
                    if isinstance(extra, dict):
                        lr_no = extra.get('lr_no')
                        discount = extra.get('discount') or 0
                    else:
                        lr_no = extra
                        discount = 0

                    if not lr_no:
                        lr_no = trip.lr_no
                    
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
                
                instance._suppress_billtrip_sync = False
                instance.update_financial_caches()
                instance.sync_to_ledger()
            
        return instance


class VendorBillUpdateForm(forms.ModelForm):
    """
    Focused form to update Vendor Bill Number and Date on Creditor Bills directly.
    """
    class Meta:
        model = Bill
        fields = [
            'creditor_invoice_number',
            'creditor_invoice_date',
        ]
        widgets = {
            'creditor_invoice_number': forms.TextInput(attrs={
                'id': 'id_creditor_invoice_number',
                'placeholder': 'e.g. VEND-INV-101 or Bill #',
                'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white font-medium',
            }),
            'creditor_invoice_date': forms.DateInput(attrs={
                'type': 'date',
                'id': 'id_creditor_invoice_date',
                'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white',
            }),
        }
        labels = {
            'creditor_invoice_number': 'Vendor Bill / Invoice Number',
            'creditor_invoice_date': 'Vendor Bill Date',
        }
        help_texts = {
            'creditor_invoice_number': 'Enter the invoice/bill number printed on the vendor’s physical document.',
            'creditor_invoice_date': 'Enter the date of the vendor’s physical invoice/bill.',
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.creditor_invoice_date:
            instance.date = instance.creditor_invoice_date

        if commit:
            instance.save()

            # Update associated consolidated financial record in vendor ledger
            from ledger.models import FinancialRecord
            inv_disp = instance.creditor_invoice_number or instance.bill_number or 'Pending'
            cust_ref = instance.customer_bill.bill_number if instance.customer_bill else f"Draft-{instance.pk}"
            FinancialRecord.objects.filter(
                associated_bill=instance,
                record_type=FinancialRecord.RECORD_TYPE_INVOICE
            ).update(
                date=instance.date,
                description=f"Lorry Hire Inv #{inv_disp} (Against Bill {cust_ref})"
            )

            # Sync back to linked customer bill if it has a single vendor bill
            if instance.customer_bill:
                cust_bill = instance.customer_bill
                if cust_bill.creditor_bills.count() == 1:
                    cust_bill.creditor_invoice_number = instance.creditor_invoice_number
                    cust_bill.creditor_invoice_date = instance.creditor_invoice_date
                    cust_bill.save(update_fields=['creditor_invoice_number', 'creditor_invoice_date'])

            if instance.party:
                instance.party.refresh_balance()

        return instance

