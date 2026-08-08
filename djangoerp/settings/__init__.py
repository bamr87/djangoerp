import os

_env = os.environ.get('DJANGO_SETTINGS_MODULE', '')

if _env.endswith('.prod'):
    from .prod import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403
