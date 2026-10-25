"""
Forms for FinancialRecord creation, editing, and payment distributions.
"""
import json
from decimal import Decimal
from django import forms
from django.contrib.auth.models import User
from django.db import models

from ledger.models import FinancialRecord, Party, Bill, TransactionCategory
from trips.models import Trip


class TripChoiceField(forms.ModelChoiceField):
    def __init__(self, *args, **kwargs):
        self.record_contributions = kwargs.pop('record_contributions', {})
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        outstanding = Decimal(str(getattr(obj, 'annotated_outstanding', 0)))
        if hasattr(self, 'record_contributions') and obj.pk in self.record_contributions:
            outstanding += self.record_contributions[obj.pk]
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

    paid_by_party = forms.ModelChoiceField(
        queryset=Party.objects.all().order_by('name'),
        required=False,
        label="Paid By / Remitter (Optional)",
        help_text="Specify if payment was made by a third party (e.g. Party B paying for Party A)."
    )

    is_inter_party_adjustment = forms.BooleanField(
        required=False,
        label="Inter-Party Contra Set-off",
        help_text="Check if this is a mutual adjustment/set-off between two parties without bank movement."
    )

    adjustment_target_party = forms.ModelChoiceField(
        queryset=Party.objects.all().order_by('name'),
        required=False,
        label="Offset Against Party",
        help_text="Select the party whose balance or bill is being offset."
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
        target_party = None
        if 'initial' in kwargs:
            target_party = kwargs['initial'].get('adjustment_target_party')
        if self.instance and self.instance.pk and self.instance.adjustment_target_party:
            target_party = self.instance.adjustment_target_party

        if self.data:
            if self.data.get('party'):
                try:
                    party = Party.objects.get(pk=self.data.get('party'))
                except (ValueError, Party.DoesNotExist):
                    pass
            if self.data.get('adjustment_target_party'):
                try:
                    target_party = Party.objects.get(pk=self.data.get('adjustment_target_party'))
                except (ValueError, Party.DoesNotExist):
                    pass
            if self.data.get('driver'):
                try:
                    driver_user = User.objects.get(pk=self.data.get('driver'))
                except (ValueError, User.DoesNotExist):
                    pass

        # 1. If Party context: Remove Driver field
        if party:
            if 'driver' in self.fields:
                del self.fields['driver']

            parties_for_bills = [party]
            if target_party:
                parties_for_bills.append(target_party)
            
            if party.party_type == Party.TYPE_CREDITOR and not target_party:
                # Creditors: No per-trip allocations, but support bill settlements
                if 'associated_trip' in self.fields: del self.fields['associated_trip']
                if 'payment_distribution' in self.fields: del self.fields['payment_distribution']
                
                # Filter bills for this creditor: Exclude CN/DN and already Paid bills
                bills_qs = Bill.objects.with_payment_info().filter(party__in=parties_for_bills).filter(
                    models.Q(category__isnull=True) | ~models.Q(category__name__in=['Credit Note', 'Debit Note'])
                )
                unpaid_bill_ids = [b.id for b in bills_qs if b.payment_status != Bill.PAYMENT_STATUS_PAID]
                if self.instance and self.instance.associated_bill:
                    if self.instance.associated_bill.pk not in unpaid_bill_ids:
                        unpaid_bill_ids.append(self.instance.associated_bill.pk)
                initial_bill = kwargs.get('initial', {}).get('associated_bill')
                if initial_bill and initial_bill.pk not in unpaid_bill_ids:
                    unpaid_bill_ids.append(initial_bill.pk)

                self.fields['associated_bill'].queryset = Bill.objects.with_payment_info().filter(id__in=unpaid_bill_ids).order_by('-date')

                # Filter categories for Creditor
                self.fields['category'].queryset = TransactionCategory.objects.filter(
                    models.Q(name__in=['Payment Out', 'Expense', 'Deductions', 'TDS', 'Debit Note', 'Credit Note', 'Standard'])
                ).order_by('name')

                # Default to Payment Out if not set
                if not self.initial.get('category'):
                    p_out = TransactionCategory.objects.filter(name='Payment Out').first()
                    if p_out:
                        self.fields['category'].initial = p_out.id
            elif party.party_type == Party.TYPE_BOTH or target_party:
                # Dual-role or Contra Adjustment: Can settle trips (client), vendor bills (creditor), or customer invoices
                record_trip_ids = set()
                record_contributions = {}
                if self.instance and self.instance.pk:
                    if self.instance.associated_trip_id:
                        record_trip_ids.add(self.instance.associated_trip_id)
                        record_contributions[self.instance.associated_trip_id] = self.instance.amount or Decimal('0')
                    if hasattr(self.instance, 'allocations'):
                        for alloc in self.instance.allocations.all():
                            record_trip_ids.add(alloc.trip_id)
                            record_contributions[alloc.trip_id] = record_contributions.get(alloc.trip_id, Decimal('0')) + (alloc.amount or Decimal('0'))
                
                initial_trip = kwargs.get('initial', {}).get('associated_trip')
                if initial_trip:
                    initial_trip_pk = initial_trip.pk if hasattr(initial_trip, 'pk') else initial_trip
                    try:
                        record_trip_ids.add(int(initial_trip_pk))
                    except (ValueError, TypeError):
                        pass

                trips_qs = Trip.objects.with_payment_info().with_billing_info().filter(party__in=parties_for_bills)
                if record_trip_ids:
                    trips_qs = trips_qs.filter(models.Q(id__in=record_trip_ids) | ~models.Q(annotated_status=Trip.PAYMENT_STATUS_PAID))
                else:
                    trips_qs = trips_qs.exclude(annotated_status=Trip.PAYMENT_STATUS_PAID)

                self.fields['associated_trip'].record_contributions = record_contributions
                self.fields['associated_trip'].queryset = trips_qs.distinct().order_by('-date')

                bills_qs = Bill.objects.with_payment_info().filter(party__in=parties_for_bills).filter(
                    models.Q(category__isnull=True) | ~models.Q(category__name__in=['Credit Note', 'Debit Note'])
                )
                unpaid_bill_ids = [b.id for b in bills_qs if b.payment_status != Bill.PAYMENT_STATUS_PAID]
                if self.instance and self.instance.associated_bill:
                    if self.instance.associated_bill.pk not in unpaid_bill_ids:
                        unpaid_bill_ids.append(self.instance.associated_bill.pk)
                initial_bill = kwargs.get('initial', {}).get('associated_bill')
                if initial_bill and initial_bill.pk not in unpaid_bill_ids:
                    unpaid_bill_ids.append(initial_bill.pk)

                self.fields['associated_bill'].queryset = Bill.objects.with_payment_info().filter(id__in=unpaid_bill_ids).order_by('-date')
                self.fields['category'].queryset = TransactionCategory.objects.filter(
                    models.Q(name__in=['Trip Payment', 'Invoice Payment', 'Payment In', 'Payment Out', 'Standard', 'TDS', 'Deductions', 'Expense'])
                ).order_by('name')
            else:
                # Setup trips for debtor party using dynamic payment info
                record_trip_ids = set()
                record_contributions = {}
                if self.instance and self.instance.pk:
                    if self.instance.associated_trip_id:
                        record_trip_ids.add(self.instance.associated_trip_id)
                        record_contributions[self.instance.associated_trip_id] = self.instance.amount or Decimal('0')
                    if hasattr(self.instance, 'allocations'):
                        for alloc in self.instance.allocations.all():
                            record_trip_ids.add(alloc.trip_id)
                            record_contributions[alloc.trip_id] = record_contributions.get(alloc.trip_id, Decimal('0')) + (alloc.amount or Decimal('0'))
                
                initial_trip = kwargs.get('initial', {}).get('associated_trip')
                if initial_trip:
                    initial_trip_pk = initial_trip.pk if hasattr(initial_trip, 'pk') else initial_trip
                    try:
                        record_trip_ids.add(int(initial_trip_pk))
                    except (ValueError, TypeError):
                        pass

                trips_qs = Trip.objects.with_payment_info().with_billing_info().filter(
                    party=party
                )
                if record_trip_ids:
                    trips_qs = trips_qs.filter(
                        models.Q(id__in=record_trip_ids) | ~models.Q(annotated_status=Trip.PAYMENT_STATUS_PAID)
                    )
                else:
                    trips_qs = trips_qs.exclude(annotated_status=Trip.PAYMENT_STATUS_PAID)

                self.fields['associated_trip'].record_contributions = record_contributions
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
                
                self.fields['associated_bill'].queryset = Bill.objects.with_payment_info().filter(id__in=unpaid_bill_ids).order_by('-date')
                
                # Filter categories for Debtor
                self.fields['category'].queryset = TransactionCategory.objects.filter(
                    models.Q(name__in=['Trip Payment', 'Invoice Payment', 'Payment In', 'Standard', 'TDS', 'Deductions'])
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
            self.fields['associated_trip'].queryset = Trip.objects.none()
            self.fields['associated_bill'].queryset = Bill.objects.none()

        # Filter drivers if field still exists
        if 'driver' in self.fields:
            self.fields['driver'].queryset = User.objects.filter(
                groups__name='driver'
            ).order_by('username')
            self.fields['driver'].required = False
        
        # Make amount not strictly required at field level to allow multi-trip or deduction-only settlements
        if 'amount' in self.fields:
            self.fields['amount'].required = False

        if 'paid_by_party' in self.fields:
            self.fields['paid_by_party'].empty_label = "Same as Party (Direct Payment)"
            self.fields['paid_by_party'].queryset = Party.objects.all().order_by('name')

        if 'adjustment_target_party' in self.fields:
            self.fields['adjustment_target_party'].empty_label = "Select Offset Party..."
            if party:
                self.fields['adjustment_target_party'].queryset = Party.objects.exclude(pk=party.pk).order_by('name')
            else:
                self.fields['adjustment_target_party'].queryset = Party.objects.all().order_by('name')

        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if field_name not in ['payment_distribution', 'bill_distribution', 'is_inter_party_adjustment']:
                field.widget.attrs.update({'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'})

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        party = cleaned_data.get('party')
        if category and category.name in ['Deductions', 'TDS', 'Shortage']:
            cleaned_data['account'] = None

        is_inter_party = cleaned_data.get('is_inter_party_adjustment')
        if is_inter_party:
            cleaned_data['account'] = None
            if not cleaned_data.get('adjustment_target_party'):
                self.add_error('adjustment_target_party', 'Please select the target party to offset against.')
            elif party and cleaned_data.get('adjustment_target_party') and party.pk == cleaned_data.get('adjustment_target_party').pk:
                self.add_error('adjustment_target_party', 'Offset party cannot be the same as the primary party.')

        # Auto-populate amount from multi-trip distribution if amount was not provided
        distribution_json = cleaned_data.get('payment_distribution')
        if distribution_json:
            try:
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
                b_dist_data = json.loads(bill_distribution_json)
                if b_dist_data:
                    total_b = sum(Decimal(str(item.get('amount', 0) or 0)) for item in b_dist_data)
                    if not cleaned_data.get('amount') or cleaned_data.get('amount') == 0:
                        cleaned_data['amount'] = total_b
            except Exception:
                pass

        amt = cleaned_data.get('amount')
        tds = cleaned_data.get('tds_amount')
        ded = cleaned_data.get('deduction_amount')
        dist = cleaned_data.get('payment_distribution') or cleaned_data.get('bill_distribution')

        if (amt is None or amt == 0) and not dist:
            if (tds and tds > 0) or (ded and ded > 0):
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
            'paid_by_party',
            'is_inter_party_adjustment',
            'adjustment_target_party',
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
