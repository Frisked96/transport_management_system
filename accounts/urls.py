from django.urls import path
from . import views

urlpatterns = [
    path('profile/', views.UserProfileView.as_view(), name='user-profile'),
    path('profile/<int:pk>/', views.UserProfileView.as_view(), name='user-profile-detail'),
    path('users/', views.UserListView.as_view(), name='user-list'),
    path('users/add/', views.UserCreateView.as_view(), name='user-create'),
    path('users/<int:pk>/edit/', views.UserUpdateView.as_view(), name='user-update'),
    path('users/<int:pk>/delete/', views.UserDeleteView.as_view(), name='user-delete'),
    path('users/<int:pk>/password-reset/', views.UserPasswordResetView.as_view(), name='user-password-reset'),
]
