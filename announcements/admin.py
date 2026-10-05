from django.contrib import admin

from .models import Announcement, AnnouncementGrade, AnnouncementAttachment, AnnouncementRead, AnnouncementRevision, Comment, Reaction

admin.site.register([Announcement, AnnouncementRevision, Comment, Reaction, AnnouncementRead, AnnouncementAttachment, AnnouncementGrade])
