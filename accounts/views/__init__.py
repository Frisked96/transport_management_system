"""
Modularized views for Accounts application.
Exports all views and mixins for backward compatibility.
"""
from .mixins import SuperuserRequiredMixin
from .profile import UserProfileView
from .users import (
    UserListView,
    UserCreateView,
    UserUpdateView,
    UserDeleteView,
    UserPasswordResetView,
)
from .roles import (
    RoleListView,
    RoleCreateView,
    RoleUpdateView,
    RoleDeleteView,
)

__all__ = [
    'SuperuserRequiredMixin',
    'UserProfileView',
    'UserListView',
    'UserCreateView',
    'UserUpdateView',
    'UserDeleteView',
    'UserPasswordResetView',
    'RoleListView',
    'RoleCreateView',
    'RoleUpdateView',
    'RoleDeleteView',
]
