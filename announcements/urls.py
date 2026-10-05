from django.urls import path

from . import views

urlpatterns = [
    path("", views.announcement_list, name="announcement_list"),
    path("new/", views.announcement_create, name="announcement_create"),
    path("<int:pk>/", views.announcement_detail, name="announcement_detail"),
    path("<int:pk>/edit/", views.announcement_edit, name="announcement_edit"),
    path("<int:pk>/files/<int:attachment_id>/", views.attachment_file, name="attachment_file"),
    path("<int:pk>/delete/", views.announcement_delete, name="announcement_delete"),
    path("<int:pk>/comments/", views.comment_create, name="comment_create"),
    path("<int:pk>/comments/<int:comment_id>/delete/", views.comment_delete, name="comment_delete"),
    path("<int:pk>/react/", views.toggle_reaction, name="toggle_reaction"),
]
