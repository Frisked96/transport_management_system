"""
Forms for Ledger application
"""
from django import forms
from .models import FinancialRecord
from trips.models import Trip


class FinancialRecordForm(forms.ModelForm):
    """
    Form for creating and editing financial records
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter trips to show completed trips only (optional)
        self.fields['associated_trip'].queryset = Trip.objects.order_by('-created_at')
        self.fields['associated_trip'].required = False
        
        # Add basic styling for clarity
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.Select, forms.Textarea, forms.NumberInput, forms.FileInput)):
                field.widget.attrs.update({
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                })
    
    class Meta:
        model = FinancialRecord
        fields = [
            'date',
            'associated_trip',
            'category',
            'amount',
            'description',
            'document_ref'
        ]
        
        widgets = {
            'date': forms.DateInput(
                attrs={
                    'type': 'date',
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
            'description': forms.Textarea(
                attrs={
                    'rows': 4,
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
            'amount': forms.NumberInput(
                attrs={
                    'step': '0.01',
                    'min': '0',
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
            'document_ref': forms.FileInput(
                attrs={
                    'style': 'width: 100%; padding: 5px; margin: 2px 0;'
                }
            ),
        }