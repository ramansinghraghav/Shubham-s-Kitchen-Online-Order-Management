"""
WSGI config for Skproject project.
"""
import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Skproject.settings')
application = get_wsgi_application()
