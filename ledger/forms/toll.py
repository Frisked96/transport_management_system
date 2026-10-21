"""
Forms for Toll Account, Recharge, and Toll Entry management.
"""
from django import forms
from ledger.models import TollAccount, TollRecharge, TollEntry, CompanyAccount
from fleet.models import Vehicle
from trips.models import Trip


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
        self.fields['company_account'].required = False
        self.fields['company_account'].empty_label = '-- Unallocated / Reconcile Later --'

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

        if not self.initial.get('toll_account') and not self.data.get('toll_account'):
            default_acc = TollAccount.objects.filter(is_default=True).first() or TollAccount.objects.first()
            if default_acc:
                self.fields['toll_account'].initial = default_acc

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


class TollStatementImportForm(forms.Form):
    """
    Form for uploading FASTag XLSX statement files.
    Auto-detects whether the file is a bank statement (AXIS/IDFC/LIVQUIK)
    or a Boss Account Summary.
    """
    file = forms.FileField(
        label='FASTag Statement File (.xlsx)',
        help_text='Upload an AXIS, IDFC, LivQuik bank statement or Boss Account Summary.',
        widget=forms.ClearableFileInput(attrs={
            'accept': '.xlsx',
            'class': 'block w-full text-sm text-slate-500 file:mr-4 file:py-2.5 file:px-5 file:rounded-lg file:border-0 file:text-sm file:font-bold file:bg-emerald-50 file:text-emerald-700 hover:file:bg-emerald-100 cursor-pointer'
        })
    )
    toll_account = forms.ModelChoiceField(
        queryset=TollAccount.objects.all().order_by('-is_default', 'name'),
        required=False,
        label='Toll Account (Optional)',
        empty_label='-- Auto-detect from file --',
        help_text='Leave blank to auto-detect the toll account from the file vendor column.',
        widget=forms.Select(attrs={
            'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'
        })
    )

    def clean_file(self):
        f = self.cleaned_data.get('file')
        if f:
            if not f.name.lower().endswith('.xlsx'):
                raise forms.ValidationError('Only .xlsx files are supported.')
            # 10 MB limit
            if f.size > 10 * 1024 * 1024:
                raise forms.ValidationError('File size must be under 10 MB.')
        return f


class TollRechargeReconcileForm(forms.Form):
    """
    Form for assigning a CompanyAccount to an unallocated TollRecharge.
    """
    company_account = forms.ModelChoiceField(
        queryset=CompanyAccount.objects.all().order_by('name'),
        label='Firm / Company Account',
        empty_label='-- Select Firm --',
        widget=forms.Select(attrs={
            'class': 'block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white'
        })
    )
