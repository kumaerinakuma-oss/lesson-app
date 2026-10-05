from django.urls import path

from . import views

urlpatterns = [
    path("", views.calendar_view, name="calendar"),
    path("new/", views.event_edit, name="event_create"),
    path("<int:pk>/", views.event_edit, name="event_edit"),
    path("<int:pk>/delete/", views.event_delete, name="event_delete"),
]
