from django.contrib import admin
from .models import Membership, School, Student, StudentGroup

admin.site.register([School, Membership, Student, StudentGroup])