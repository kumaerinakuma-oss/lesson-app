from django.urls import path

from . import views

urlpatterns = [
    path("", views.invoice_list, name="invoice_list"),
    path("new/", views.invoice_bulk_create, name="invoice_bulk_create"),
    path("<int:pk>/pay/", views.invoice_pay, name="invoice_pay"),
    path("<int:pk>/return/", views.invoice_return, name="invoice_return"),
    path("<int:pk>/cash/", views.invoice_cash, name="invoice_cash"),
    path("<int:pk>/cancel/", views.invoice_cancel, name="invoice_cancel"),
]
