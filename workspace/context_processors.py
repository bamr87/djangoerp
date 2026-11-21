from . import registry


def navigation(request):
    """The module navigation every shell page renders server-side (the full registry is fetched after sign-in)."""
    if not request.path.startswith(('/app/', '/console/')):
        return {}
    return {'nav_groups': registry.build()['groups']}
