from django.urls import path

from . import views

urlpatterns = [
    path("", views.notice_list, name="notice_list"),
    path("new/", views.notice_create, name="notice_create"),
    path("<int:pk>/", views.notice_detail, name="notice_detail"),
    path("<int:pk>/confirm/", views.notice_confirm, name="notice_confirm"),
    path("templates/", views.template_list, name="template_list"),
    path("templates/new/", views.template_edit, name="template_create"),
    path("templates/<int:pk>/", views.template_edit, name="template_edit"),
    path("templates/<int:pk>/delete/", views.template_delete, name="template_delete"),
]
