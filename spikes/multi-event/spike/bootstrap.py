import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spike.settings")
django.setup()
