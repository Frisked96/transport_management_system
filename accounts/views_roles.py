from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import Group, Permission
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from django.db.models import Count
from .views import SuperuserRequiredMixin
from .forms import RoleForm


class RoleListView(SuperuserRequiredMixin, ListView):
    model = Group
    template_name = 'accounts/role_list.html'
    context_object_name = 'roles'

    def get_queryset(self):
        return Group.objects.annotate(user_count=Count('user')).prefetch_related('permissions').order_by('name')


class RoleCreateView(SuperuserRequiredMixin, CreateView):
    model = Group
    form_class = RoleForm
    template_name = 'accounts/role_form.html'
    success_url = reverse_lazy('role-list')

    def form_valid(self, form):
        messages.success(self.request, f"Role '{form.cleaned_data['name']}' created successfully.")
        return super().form_valid(form)


class RoleUpdateView(SuperuserRequiredMixin, UpdateView):
    model = Group
    form_class = RoleForm
    template_name = 'accounts/role_form.html'
    success_url = reverse_lazy('role-list')

    def form_valid(self, form):
        messages.success(self.request, f"Role '{form.cleaned_data['name']}' updated successfully.")
        return super().form_valid(form)


class RoleDeleteView(SuperuserRequiredMixin, DeleteView):
    model = Group
    template_name = 'accounts/role_confirm_delete.html'
    success_url = reverse_lazy('role-list')

    def delete(self, request, *args, **kwargs):
        role = self.get_object()
        if role.user_set.exists():
            messages.error(request, f"Cannot delete role '{role.name}' because {role.user_set.count()} user(s) are currently assigned to it.")
            return redirect('role-list')
        messages.success(request, f"Role '{role.name}' deleted successfully.")
        return super().delete(request, *args, **kwargs)
