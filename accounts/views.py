from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, FormView
from django.contrib.auth.mixins import UserPassesTestMixin
from django.urls import reverse_lazy
from django.contrib import messages
from .forms import UserForm, UserCreateForm, AdminPasswordChangeForm

class SuperuserRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_superuser

class UserListView(SuperuserRequiredMixin, ListView):
    model = User
    template_name = 'accounts/user_list.html'
    context_object_name = 'users'

class UserCreateView(SuperuserRequiredMixin, CreateView):
    model = User
    form_class = UserCreateForm
    template_name = 'accounts/user_form.html'
    success_url = reverse_lazy('user-list')

    def form_valid(self, form):
        messages.success(self.request, f"User {form.cleaned_data['username']} created successfully.")
        return super().form_valid(form)

class UserUpdateView(SuperuserRequiredMixin, UpdateView):
    model = User
    form_class = UserForm
    template_name = 'accounts/user_form.html'
    success_url = reverse_lazy('user-list')

    def form_valid(self, form):
        messages.success(self.request, f"User {form.cleaned_data['username']} updated successfully.")
        return super().form_valid(form)

class UserDeleteView(SuperuserRequiredMixin, DeleteView):
    model = User
    template_name = 'accounts/user_confirm_delete.html'
    success_url = reverse_lazy('user-list')

    def delete(self, request, *args, **kwargs):
        user = self.get_object()
        if user == request.user:
            messages.error(request, "You cannot delete your own account.")
            return redirect('user-list')
        messages.success(request, f"User {user.username} deleted successfully.")
        return super().delete(request, *args, **kwargs)

class UserPasswordResetView(SuperuserRequiredMixin, FormView):
    form_class = AdminPasswordChangeForm
    template_name = 'accounts/user_password_reset.html'
    success_url = reverse_lazy('user-list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['target_user'] = User.objects.get(pk=self.kwargs['pk'])
        return context

    def form_valid(self, form):
        user = User.objects.get(pk=self.kwargs['pk'])
        user.set_password(form.cleaned_data['new_password'])
        user.save()
        messages.success(self.request, f"Password for {user.username} has been reset.")
        return super().form_valid(form)
