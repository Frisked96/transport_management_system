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
        
        from django.utils import timezone
        import datetime
        from django.urls import reverse, NoReverseMatch
        from django.contrib.admin.models import LogEntry
        from .models import UserProfile
        
        # Last Online Logic
        profile, _ = UserProfile.objects.get_or_create(user=user)
        cached_seen = cache.get(f'last-seen-{user.id}')
        
        is_online = False
        if cached_seen:
            is_online = True
            last_seen_time = cached_seen
        elif profile.last_seen:
            last_seen_time = profile.last_seen
            if (timezone.now() - profile.last_seen).total_seconds() < 300:
                is_online = True
        else:
            last_seen_time = user.last_login

        context['is_online'] = is_online
        context['last_seen_time'] = last_seen_time
        context['last_online'] = is_online # maintain compatibility
        
        # Get filter parameters
        action_type = self.request.GET.get('action', '')
        time_filter = self.request.GET.get('time', '')
        
        # Fetch activity log, excluding internal system models
        excluded_models = ['sequence', 'tripallocation', 'billallocation', 'billtrip', 'documentfile', 'userprofile', 'logentry']
        activities = LogEntry.objects.filter(user=user).select_related('content_type').exclude(
            content_type__model__in=excluded_models
        )
        
        # Apply action filter
        if action_type in ['1', '2', '3']:
            activities = activities.filter(action_flag=action_type)
            
        # Apply time filter
        if time_filter == '7':
            activities = activities.filter(action_time__gte=timezone.now() - datetime.timedelta(days=7))
        elif time_filter == '30':
            activities = activities.filter(action_time__gte=timezone.now() - datetime.timedelta(days=30))
            
        activities = list(activities.order_by('-action_time')[:50])

        # Resolve stylized front-end detail URL for each activity
        def get_frontend_url(activity):
            if activity.action_flag == 3: # Deletion: record no longer exists
                return None
            if not activity.content_type or not activity.object_id:
                return None
            
            app_label = activity.content_type.app_label
            model_name = activity.content_type.model
            pk = activity.object_id

            try:
                model_class = activity.content_type.model_class()
                if not model_class:
                    return None
                obj = model_class.objects.filter(pk=pk).first()
                if not obj:
                    return None
            except Exception:
                return None

            url_map = {
                ('trips', 'trip'): ('trip-detail', [pk]),
                ('trips', 'route'): ('route-list', []),
                ('ledger', 'financialrecord'): ('financialrecord-detail', [pk]),
                ('ledger', 'bill'): ('bill-detail', [pk]),
                ('ledger', 'party'): ('party-detail', [pk]),
                ('ledger', 'companyaccount'): ('account-detail', [pk]),
                ('fleet', 'vehicle'): ('vehicle-detail', [pk]),
                ('fleet', 'maintenancerecord'): ('maintenance-detail', [pk]),
                ('fleet', 'tyre'): ('tyre-detail', [pk]),
                ('fleet', 'tyrebrand'): ('tyre-brand-list', []),
                ('drivers', 'driver'): ('driver-detail', [pk]),
                ('documents', 'document'): ('document-update', [pk]),
                ('auth', 'user'): ('user-profile-detail', [pk]),
            }

            if (app_label, model_name) in url_map:
                view_name, args = url_map[(app_label, model_name)]
                try:
                    return reverse(view_name, args=args)
                except NoReverseMatch:
                    return None
            
            # Special relations
            if app_label == 'drivers' and model_name == 'drivertransaction' and hasattr(obj, 'driver_id'):
                try:
                    return reverse('driver-ledger', args=[obj.driver_id])
                except NoReverseMatch:
                    return None

            if app_label == 'fleet' and model_name == 'tyrelog' and hasattr(obj, 'tyre_id'):
                try:
                    return reverse('tyre-detail', args=[obj.tyre_id])
                except NoReverseMatch:
                    return None

            return None

        for activity in activities:
            activity.frontend_url = get_frontend_url(activity)

        context['activities'] = activities
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
