from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, FormView
from django.contrib.auth.mixins import UserPassesTestMixin
from django.urls import reverse_lazy
from django.contrib import messages
from .forms import UserForm, UserCreateForm, AdminPasswordChangeForm

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, FormView, DetailView

from django.core.cache import cache

class UserProfileView(LoginRequiredMixin, DetailView):
    model = User
    template_name = 'accounts/user_profile.html'
    context_object_name = 'profile_user'

    def get_object(self):
        pk = self.kwargs.get('pk')
        if pk and self.request.user.is_superuser:
            return get_object_or_404(User, pk=pk)
        return self.request.user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.get_object()
        
        # Last Online Logic
        last_seen = cache.get(f'last-seen-{user.id}')
        context['last_online'] = last_seen
        
        from django.contrib.admin.models import LogEntry
        from django.utils import timezone
        import datetime
        
        # Get filter parameters
        action_type = self.request.GET.get('action', '')
        time_filter = self.request.GET.get('time', '')
        
        # Fetch generic activity log
        activities = LogEntry.objects.filter(user=user).select_related('content_type')
        
        # Apply action filter
        if action_type in ['1', '2', '3']:
            activities = activities.filter(action_flag=action_type)
            
        # Apply time filter
        if time_filter == '7':
            activities = activities.filter(action_time__gte=timezone.now() - datetime.timedelta(days=7))
        elif time_filter == '30':
            activities = activities.filter(action_time__gte=timezone.now() - datetime.timedelta(days=30))
            
        activities = activities.order_by('-action_time')[:50]
        context['activities'] = activities
        
        # Pass active filters to context for template
        context['active_action'] = action_type
        context['active_time'] = time_filter
        
        return context

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
