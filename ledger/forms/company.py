"""
Form for creating and editing CompanyAccount (Firm) instances.
"""
from django import forms
from ledger.models import CompanyAccount


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
