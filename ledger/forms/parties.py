"""
Form for creating and editing Party instances.
"""
from django import forms
from ledger.models import Party


class PartyForm(forms.ModelForm):
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
