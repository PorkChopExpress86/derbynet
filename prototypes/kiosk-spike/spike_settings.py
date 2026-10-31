"""SPIKE #71 ONLY. A settings overlay that adds the throwaway `spike` app to
the Django skeleton without touching server/. Run through serve.sh."""

from derbynet.settings import *  # noqa: F403
from derbynet.settings import INSTALLED_APPS

INSTALLED_APPS = [*INSTALLED_APPS, "spike"]
ROOT_URLCONF = "spike_urls"
