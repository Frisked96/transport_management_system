"""
Forms for Drivers application
"""
from django import forms
from django.contrib.auth.models import User, Group
from django.contrib.auth.forms import AuthenticationForm
from .models import Driver, DriverTransaction

class CustomAuthenticationForm(AuthenticationForm):
    """
    Custom authentication form that allows empty passwords
    (for driver login).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password'].required = False


class DriverForm(forms.ModelForm):
    """
    Form to create/update Driver and associated User
    """
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=30)
    last_name = forms.CharField(max_length=30)
    email = forms.EmailField(required=False)

    class Meta:
        model = Driver
        fields = ['employee_id', 'license_number', 'phone_number', 'address', 'joined_date']
        widgets = {
            'joined_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Populate user fields if updating
            self.fields['username'].initial = self.instance.user.username
            self.fields['first_name'].initial = self.instance.user.first_name
            self.fields['last_name'].initial = self.instance.user.last_name
            self.fields['email'].initial = self.instance.user.email

    def save(self, commit=True):
        driver = super().save(commit=False)

        # Handle User creation/update
        if not driver.pk and not hasattr(driver, 'user'): # New driver
            user = User.objects.create_user(
                username=self.cleaned_data['username'],
                password=None, # Unusable password
                first_name=self.cleaned_data['first_name'],
                last_name=self.cleaned_data['last_name'],
                email=self.cleaned_data['email']
            )
            user.set_unusable_password()
            user.save()
            
            # Add to group
            group, _ = Group.objects.get_or_create(name='driver')
            user.groups.add(group)
            driver.user = user
        else:
            user = driver.user
            user.username = self.cleaned_data['username']
            user.first_name = self.cleaned_data['first_name']
            user.last_name = self.cleaned_data['last_name']
            user.email = self.cleaned_data['email']
            user.save()

        if commit:
            driver.save()
        return driver


class DriverTransactionForm(forms.ModelForm):
    """
    Form for driver transactions
    """
    amount = forms.DecimalField(min_value=0.01, help_text="Enter the positive amount")

    class Meta:
        model = DriverTransaction
        fields = ['date', 'transaction_type', 'amount', 'description']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        amount = abs(self.cleaned_data['amount'])

        # Apply sign based on type
        # Debits (Negative balance): Loan, Payment (Company pays driver)
        if instance.transaction_type in [DriverTransaction.TYPE_LOAN, DriverTransaction.TYPE_PAYMENT]:
            instance.amount = -amount
        else:
            # Credits (Positive balance): Salary, Allowance, Repayment
            instance.amount = amount

        if commit:
            instance.save()
        return instance
