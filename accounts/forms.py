from django import forms
from django.contrib.auth.models import User, Group

class UserForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'is_active', 'is_superuser']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'mt-1 block w-full rounded-md border-slate-300 shadow-sm focus:border-emerald-500 focus:ring-emerald-500 sm:text-sm'}),
            'email': forms.EmailInput(attrs={'class': 'mt-1 block w-full rounded-md border-slate-300 shadow-sm focus:border-emerald-500 focus:ring-emerald-500 sm:text-sm'}),
            'first_name': forms.TextInput(attrs={'class': 'mt-1 block w-full rounded-md border-slate-300 shadow-sm focus:border-emerald-500 focus:ring-emerald-500 sm:text-sm'}),
            'last_name': forms.TextInput(attrs={'class': 'mt-1 block w-full rounded-md border-slate-300 shadow-sm focus:border-emerald-500 focus:ring-emerald-500 sm:text-sm'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500'}),
            'is_superuser': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500'}),
        }

    def __init__(self, *args, **kwargs):
        super(UserForm, self).__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['groups'].initial = self.instance.groups.all()

    def save(self, commit=True):
        user = super(UserForm, self).save(commit=commit)
        if commit:
            user.groups.set(self.cleaned_data['groups'])
        return user

class UserCreateForm(UserForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'mt-1 block w-full rounded-md border-slate-300 shadow-sm focus:border-emerald-500 focus:ring-emerald-500 sm:text-sm'}),
        required=True
    )

    class Meta(UserForm.Meta):
        fields = UserForm.Meta.fields + ['password']

    def save(self, commit=True):
        user = super(UserCreateForm, self).save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
            user.groups.set(self.cleaned_data['groups'])
        return user

class AdminPasswordChangeForm(forms.Form):
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'mt-1 block w-full rounded-md border-slate-300 shadow-sm focus:border-emerald-500 focus:ring-emerald-500 sm:text-sm'}),
        required=True,
        label="New Password"
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'mt-1 block w-full rounded-md border-slate-300 shadow-sm focus:border-emerald-500 focus:ring-emerald-500 sm:text-sm'}),
        required=True,
        label="Confirm Password"
    )

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm_password = cleaned_data.get("confirm_password")

        if new_password != confirm_password:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned_data


from django.contrib.auth.models import Permission

ROLE_MODULE_PERMISSIONS = [
    {
        'module': 'Trips & Operations',
        'key': 'trips',
        'icon': 'fa-solid fa-truck',
        'permissions': [
            ('trips.view_trip', 'View Trips', 'Access trips list and trip details'),
            ('trips.add_trip', 'Add Trips', 'Create new single or bulk trips'),
            ('trips.change_trip', 'Edit Trips', 'Update details of unbilled trips'),
            ('trips.delete_trip', 'Delete Trips', 'Remove trips from the system'),
            ('trips.can_view_all_trips', 'View All Fleet Trips', 'Bypass driver-only trip restrictions'),
            ('trips.can_view_manager_dashboard', 'Manager Dashboard', 'Access operational KPI dashboard'),
            ('trips.view_route', 'View Routes', 'Access route directory and rate trends'),
            ('trips.add_route', 'Add Routes', 'Define and create new transport routes'),
            ('trips.change_route', 'Edit Routes', 'Modify existing routes and freight rates'),
            ('trips.delete_route', 'Delete Routes', 'Remove routes from the system'),
        ]
    },
    {
        'module': 'Fleet & Maintenance',
        'key': 'fleet',
        'icon': 'fa-solid fa-truck-fast',
        'permissions': [
            ('fleet.view_vehicle', 'View Vehicles', 'Access vehicle fleet directory and details'),
            ('fleet.add_vehicle', 'Add Vehicles', 'Register new vehicles into the fleet'),
            ('fleet.change_vehicle', 'Edit Vehicles', 'Update vehicle specifications and status'),
            ('fleet.delete_vehicle', 'Delete Vehicles', 'Remove vehicles from fleet'),
            ('fleet.can_view_all_vehicles', 'View All Vehicles', 'Access all company assets'),
            ('fleet.view_maintenancerecord', 'View Maintenance', 'Access maintenance schedules and logs'),
            ('fleet.add_maintenancerecord', 'Log Maintenance', 'Record scheduled or completed service'),
            ('fleet.change_maintenancerecord', 'Edit Maintenance', 'Update maintenance records'),
            ('fleet.delete_maintenancerecord', 'Delete Maintenance', 'Remove maintenance entries'),
            ('fleet.view_tyre', 'View Tyres', 'Access tyre inventory and history'),
            ('fleet.add_tyre', 'Add Tyre', 'Register new tyres into inventory'),
            ('fleet.change_tyre', 'Manage Tyres', 'Mount, dismount, or update tyres'),
            ('fleet.delete_tyre', 'Delete Tyre', 'Scrap or remove tyres'),
        ]
    },
    {
        'module': 'Drivers',
        'key': 'drivers',
        'icon': 'fa-solid fa-users',
        'permissions': [
            ('drivers.can_view_all_drivers', 'View Drivers', 'Access driver directory and profiles'),
            ('drivers.add_driver', 'Add Drivers', 'Create new driver profiles'),
            ('drivers.change_driver', 'Edit Drivers', 'Update driver personal and license information'),
            ('drivers.can_manage_driver_finance', 'Manage Driver Finance', 'Record salary, advances, and settle balances'),
        ]
    },
    {
        'module': 'Finance & Invoicing (Ledger)',
        'key': 'ledger',
        'icon': 'fa-solid fa-wallet',
        'permissions': [
            ('ledger.can_view_financial_records', 'View Ledger & Reports', 'Access full ledger, cash book, and reports'),
            ('ledger.add_financialrecord', 'Record Payments', 'Post receipts, payments, and settlements'),
            ('ledger.change_financialrecord', 'Edit Payments', 'Modify payment entries and distributions'),
            ('ledger.delete_financialrecord', 'Delete Payments', 'Remove financial entries'),
            ('ledger.view_party', 'View Parties', 'Access party list and party balances'),
            ('ledger.add_party', 'Add Parties', 'Register new clients and vendors'),
            ('ledger.change_party', 'Edit Parties', 'Update party details and credit settings'),
            ('ledger.delete_party', 'Delete Parties', 'Remove parties with no active records'),
            ('ledger.view_bill', 'View Invoices', 'Access invoice lists and invoice details'),
            ('ledger.add_bill', 'Create Invoices', 'Generate consolidated or single bills'),
            ('ledger.change_bill', 'Edit Invoices', 'Update invoice details'),
            ('ledger.delete_bill', 'Delete Invoices', 'Cancel/delete bills and un-bill trips'),
            ('ledger.view_companyaccount', 'View Company Accounts', 'Access company accounts and firms'),
            ('ledger.add_companyaccount', 'Add Company Accounts', 'Create company firms or bank profiles'),
            ('ledger.change_companyaccount', 'Edit Company Accounts', 'Update firm details and bank details'),
            ('ledger.delete_companyaccount', 'Delete Company Accounts', 'Remove accounts'),
        ]
    },
    {
        'module': 'Documents & Expiries',
        'key': 'documents',
        'icon': 'fa-solid fa-file-lines',
        'permissions': [
            ('documents.view_document', 'View Documents', 'Access centralized document repository'),
            ('documents.add_document', 'Upload Documents', 'Upload vehicle or driver documents'),
            ('documents.change_document', 'Update Documents', 'Update expiry dates and replace scans'),
            ('documents.delete_document', 'Delete Documents', 'Remove document records'),
        ]
    }
]


class RoleForm(forms.ModelForm):
    name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'mt-1 block w-full rounded-md border-slate-300 shadow-sm focus:border-emerald-500 focus:ring-emerald-500 sm:text-sm',
            'placeholder': 'e.g. Office, Dispatcher, Accountant'
        })
    )

    class Meta:
        model = Group
        fields = ['name']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Fetch current assigned permissions
        self.assigned_perm_ids = set()
        if self.instance.pk:
            self.assigned_perm_ids = set(self.instance.permissions.values_list('id', flat=True))

        # Build perm lookup: (app_label, codename) -> Permission obj
        all_perms = Permission.objects.select_related('content_type').all()
        perm_map = {f"{p.content_type.app_label}.{p.codename}": p for p in all_perms}

        self.module_sections = []
        for mod in ROLE_MODULE_PERMISSIONS:
            perm_items = []
            for perm_key, label, desc in mod['permissions']:
                perm_obj = perm_map.get(perm_key)
                if perm_obj:
                    perm_items.append({
                        'id': perm_obj.id,
                        'key': perm_key,
                        'label': label,
                        'desc': desc,
                        'is_checked': perm_obj.id in self.assigned_perm_ids,
                    })
            self.module_sections.append({
                'name': mod['module'],
                'key': mod['key'],
                'icon': mod['icon'],
                'items': perm_items,
            })

    def save(self, commit=True):
        group = super().save(commit=commit)
        # Process selected permissions from request.POST
        selected_ids = self.data.getlist('selected_permissions')
        if not selected_ids:
            selected_ids = self.data.getlist('permissions')
        if commit:
            group.permissions.set(selected_ids)
        return group

