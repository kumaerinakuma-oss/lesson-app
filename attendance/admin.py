from django.contrib import admin

from .models import AttendanceNotice, AttendanceReply, MessageTemplate

admin.site.register([MessageTemplate, AttendanceNotice, AttendanceReply])
