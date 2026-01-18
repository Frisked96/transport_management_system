from django.urls import path
from . import views

urlpatterns = [
    path('', views.DriverListView.as_view(), name='driver-list'),
    path('add/', views.DriverCreateView.as_view(), name='driver-create'),
    path('<int:pk>/', views.DriverDetailView.as_view(), name='driver-detail'),
    path('<int:pk>/edit/', views.DriverUpdateView.as_view(), name='driver-update'),
    path('<int:pk>/ledger/', views.DriverLedgerView.as_view(), name='driver-ledger'),
    path('<int:driver_pk>/transaction/add/', views.DriverTransactionCreateView.as_view(), name='driver-transaction-create'),
]
