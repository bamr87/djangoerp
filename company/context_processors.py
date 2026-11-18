"""Template context every page gets (registered in settings.TEMPLATES)."""
from django.conf import settings


def fleet(request):
    """
    What the fleet feedback widget (templates/includes/fleet-feedback.html) reads: the repo an "Improve this
    page" report is filed against, its branch, and the version that served the page.

    Settings values only, never per-request state. The widget's `env` comes from Django's own `debug` context
    variable, which is true only under DEBUG for a request from INTERNAL_IPS (set in settings/dev.py).
    """
    return {
        'FLEET_REPO': settings.FLEET_REPO,
        'FLEET_BRANCH': settings.FLEET_BRANCH,
        'FLEET_VERSION': settings.APP_VERSION,
    }
