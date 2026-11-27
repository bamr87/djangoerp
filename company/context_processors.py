"""Template context every page gets (registered in settings.TEMPLATES)."""
from django.conf import settings


def fleet(request):
    """
    What the fleet kits' includes read. The feedback widget (templates/includes/fleet-feedback.html) and the
    assistant panel (templates/includes/fleet-assist.html) both name the repo, its branch and the version that
    served the page: the first to file an "Improve this page" report, the second to tell a paired Claude Code
    session which repo is behind the address.

    FLEET_ASSIST switches the assistant panel on. It is a tool for whoever runs the app, so it follows
    settings.DEBUG: a production page carries neither the tag nor its script.

    Settings values only, never per-request state. Each widget's `env` comes from Django's own `debug` context
    variable, which is true only under DEBUG for a request from INTERNAL_IPS (set in settings/dev.py).
    """
    return {
        'FLEET_REPO': settings.FLEET_REPO,
        'FLEET_BRANCH': settings.FLEET_BRANCH,
        'FLEET_VERSION': settings.APP_VERSION,
        'FLEET_ASSIST': settings.DEBUG,
    }
