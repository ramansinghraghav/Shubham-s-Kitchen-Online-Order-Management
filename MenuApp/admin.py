from django.contrib import admin
from .models import MenuItem, Profile

admin.site.site_header = "SHUBHAM's KITCHEN Admin Portal"
admin.site.site_title = "SHUBHAM's KITCHEN Admin Portal"
admin.site.index_title = "Welcome to SHUBHAM's KITCHEN Admin   Portal"

admin.site.register(MenuItem)
admin.site.register(Profile)
