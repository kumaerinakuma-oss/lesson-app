from django.contrib import admin

from .models import Invoice, StripeEvent

admin.site.register([Invoice, StripeEvent])
