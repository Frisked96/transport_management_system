from django import forms
from django.forms import inlineformset_factory
from .models import Document, DocumentFile, DocumentRenewal


class DocumentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        
        for field_name, field in self.fields.items():
            if field_name == 'never_expires':
                field.widget.attrs.update({
                    'class': 'h-4 w-4 text-emerald-600 focus:ring-emerald-500 border-slate-300 rounded'
                })
            else:
                field.widget.attrs.update({'class': tailwind_classes})

        if self.instance and self.instance.pk and self.instance.is_base_document:
            self.fields['document_name'].disabled = True
            self.fields['document_name'].help_text = 'Base compliance document name cannot be changed.'

    class Meta:
        model = Document
        fields = ['document_name', 'document_number', 'valid_from', 'expiry_date', 'cost', 'never_expires', 'notes']
        widgets = {
            'valid_from': forms.DateInput(attrs={'type': 'date'}),
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'document_name': 'Document Name',
            'document_number': 'Document Number',
            'valid_from': 'Valid From',
            'expiry_date': 'Expiry Date / Valid To',
            'cost': 'Renewal Cost / Expense (₹)',
            'never_expires': 'Never Expires',
            'notes': 'Notes',
        }


class DocumentRenewalForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tailwind_classes = "block w-full px-3 py-2 border border-slate-300 rounded-md text-sm shadow-sm focus:ring-emerald-500 focus:border-emerald-500 bg-white"
        
        for field_name, field in self.fields.items():
            if field_name == 'receipt_file':
                field.widget.attrs.update({
                    'class': 'block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-emerald-50 file:text-emerald-700 hover:file:bg-emerald-100'
                })
            else:
                field.widget.attrs.update({'class': tailwind_classes})

    class Meta:
        model = DocumentRenewal
        fields = ['valid_from', 'valid_to', 'cost', 'document_number', 'notes', 'receipt_file']
        widgets = {
            'valid_from': forms.DateInput(attrs={'type': 'date'}),
            'valid_to': forms.DateInput(attrs={'type': 'date'}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'valid_from': 'Validity From',
            'valid_to': 'Validity To / Expiry Date',
            'cost': 'Cost of Renewal (₹)',
            'document_number': 'Document Number',
            'notes': 'Notes / Remarks',
            'receipt_file': 'Receipt / Scanned Copy (Optional)',
        }


class DocumentFileForm(forms.ModelForm):
    class Meta:
        model = DocumentFile
        fields = ['file']
        widgets = {
            'file': forms.FileInput(attrs={
                'class': 'block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-emerald-50 file:text-emerald-700 hover:file:bg-emerald-100'
            })
        }


DocumentFileFormSet = inlineformset_factory(
    Document, 
    DocumentFile, 
    form=DocumentFileForm,
    extra=1, 
    can_delete=True
)
