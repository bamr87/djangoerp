#!/usr/bin/env python3
"""Generate docs/reference/api-routes.md from the project's URLconf.

The table is read from Django's own URL resolver (``djangoerp/urls.py`` and every
app ``urls.py`` it includes), so it lists exactly what the server routes: the
method, the path, the view or viewset action, the DRF permission classes that
action enforces, and the first paragraph of the handler's docstring. Django is
set up with ``djangoerp.settings`` (dev unless the module ends in ``.prod``), so
the requirements must be installed.

The output is generated, never edited by hand (UPS-DOC-05)::

    python3 tools/gen_api_routes.py --write   # rewrite docs/reference/api-routes.md
    python3 tools/gen_api_routes.py --check   # exit 1 when it is stale

``core/test_docs_reference.py`` runs the same comparison in ``manage.py test``
and ``pytest``.
"""
import argparse
import inspect
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path('docs/reference/api-routes.md')
COMMAND = 'python3 tools/gen_api_routes.py --write'
METHODS = ['get', 'post', 'put', 'patch', 'delete']


def _setup_django():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djangoerp.settings')
    import django

    django.setup()


def _local(obj):
    """True when ``obj`` is defined in this repository rather than in a dependency."""
    package = (getattr(obj, '__module__', '') or '').split('.')[0]
    return (ROOT / package).is_dir() and package not in ('tools', 'docs')


def _summary(obj):
    """First paragraph of the object's own docstring; a dependency is named instead."""
    if obj is None:
        return ''
    if not _local(obj):
        return 'Provided by `%s`.' % (obj.__module__.split('.')[0])
    doc = obj.__dict__.get('__doc__') if isinstance(obj, type) else getattr(obj, '__doc__', None)
    if not doc:
        return ''
    return ' '.join(inspect.cleandoc(doc).split('\n\n')[0].split())


def _cell(text):
    return text.replace('|', '\\|') or '—'


def _pretty(regex):
    """Turn a URL regex fragment into a readable path: named groups become ``{name}``."""
    text = regex.lstrip('^').rstrip('$')
    out, i = [], 0
    while i < len(text):
        if text.startswith('(?P<', i):
            end = text.index('>', i)
            name = text[i + 4:end]
            depth, j = 0, i
            while j < len(text):
                if text[j] == '\\':
                    j += 2
                    continue
                depth += {'(': 1, ')': -1}.get(text[j], 0)
                j += 1
                if depth == 0:
                    break
            out.append('{%s}' % name)
            i = j
        elif text[i] == '\\' and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def _segment(pattern):
    """The path fragment one URL pattern contributes (route or regex)."""
    from django.urls.resolvers import RegexPattern

    if isinstance(pattern, RegexPattern):
        return _pretty(pattern.regex.pattern)
    import re

    return re.sub(r'<(?:\w+:)?(\w+)>', r'{\1}', str(pattern))


def _names(permissions):
    return ', '.join('`%s`' % type(p).__name__ for p in permissions) or 'public'


def _access(view_cls, initkwargs, actions):
    """Permission classes per method, joined when they differ (``get_permissions`` may branch on the action)."""
    per_method = {}
    for method, action in actions.items():
        view = view_cls(**initkwargs)
        view.action, view.request = action, None
        per_method[method] = _names(view.get_permissions())
    values = list(dict.fromkeys(per_method.values()))
    if len(values) == 1:
        return values[0]
    return '; '.join('%s %s' % (m.upper(), names) for m, names in per_method.items())


def _walk(patterns, prefix='', area=None):
    """Yield (area, path, pattern) for every leaf pattern, skipping DRF's format-suffix copies.

    ``area`` is the prefix of the top-level ``include()`` a route came from (``''`` for an
    include at the site root) and ``None`` for a route declared in ``djangoerp/urls.py`` itself.
    The admin site is one row; DRF's ``.json`` twins reuse the callback of the route before them.
    """
    from django.urls import URLResolver

    previous = []
    for entry in patterns:
        path = prefix + _segment(entry.pattern)
        if isinstance(entry, URLResolver):
            if entry.app_name == 'admin':
                yield area, path, None
            else:
                yield from _walk(entry.url_patterns, path, path if area is None and prefix == '' else area)
            continue
        if 'format' in entry.pattern.regex.groupindex and any(entry.callback is p for p in previous):
            continue
        previous.append(entry.callback)
        yield area, path, entry


def _row(path, entry):
    if entry is None:
        return '| ANY | `/%s` | Django admin | staff session | The Django admin site. |' % path
    callback = entry.callback
    initkwargs = getattr(callback, 'initkwargs', {}) or {}
    actions = getattr(callback, 'actions', None)
    if actions:  # a DRF viewset route
        view_cls = callback.cls
        methods = [m for m in METHODS if m in actions]
        mapping = {m: actions[m] for m in methods}
        handlers = [getattr(view_cls, a) for a in dict.fromkeys(mapping.values())]
        extra = next((h for h in handlers if hasattr(h, 'mapping')), None)
        if extra is not None:
            summary = _summary(extra)
        elif 'list' in mapping.values():
            summary = _summary(view_cls)
        else:
            summary = ''
        view = '`%s.%s`' % (view_cls.__name__, '/'.join(dict.fromkeys(mapping.values())))
        return '| %s | `/%s` | %s | %s | %s |' % (
            ' '.join(m.upper() for m in methods), path, view,
            _access(view_cls, initkwargs, mapping), _cell(summary))
    view_cls = getattr(callback, 'view_class', None) or getattr(callback, 'cls', None)
    if view_cls is not None:  # a class-based view
        methods = [m for m in METHODS if hasattr(view_cls, m)]
        access = _access(view_cls, initkwargs, {m: None for m in methods}) if hasattr(
            view_cls, 'get_permissions') else 'public'
        return '| %s | `/%s` | `%s` | %s | %s |' % (
            ' '.join(m.upper() for m in methods), path, view_cls.__name__, access, _cell(_summary(view_cls)))
    return '| ANY | `/%s` | `%s.%s` | public | %s |' % (
        path, callback.__module__, callback.__name__, _cell(_summary(callback)))


def render():
    _setup_django()
    from django.conf import settings
    from django.urls import get_resolver

    lines = [
        '<!-- GENERATED by `%s` — do not edit by hand -->' % COMMAND,
        '# API routes',
        '',
        '> Every route DjangoERP serves — method, path, view or viewset action, the DRF permission classes '
        'it enforces, and its summary — read from Django\'s URL resolver.',
        '',
        'Paths are relative to the host (`http://localhost:8000` in development). Every `/api/` route needs a '
        'JWT bearer token from `POST /api/auth/login/` unless its **Access** column says `public`. DRF\'s '
        '`DefaultRouter` also serves each route with a format suffix (for example `.json`); those copies are '
        'not listed. Collection routes take the filters, search and ordering their viewset declares — see the '
        'viewset in each app\'s `views.py`.',
    ]
    sections = {}
    for area, path, entry in _walk(get_resolver(settings.ROOT_URLCONF).url_patterns):
        if area is None:
            heading = 'Site, admin and API docs'
        elif area == '':
            heading = 'Site root (`company/urls_web.py`)'
        else:
            heading = '`/%s`' % area
        sections.setdefault(heading, []).append(_row(path, entry))
    for heading, rows in sections.items():
        lines += ['', '## %s' % heading, '', '| Method | Path | View | Access | Summary |',
                  '| --- | --- | --- | --- | --- |'] + rows
    return '\n'.join(lines) + '\n'


def stale(root=ROOT):
    target = root / OUTPUT
    return not target.is_file() or target.read_text(encoding='utf-8') != render()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--write', action='store_true', help='rewrite the generated file')
    mode.add_argument('--check', action='store_true', help='exit 1 when the generated file is stale')
    args = parser.parse_args(argv)
    if args.check:
        if stale():
            print('stale: %s — run %s' % (OUTPUT, COMMAND), file=sys.stderr)
            return 1
        return 0
    (ROOT / OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / OUTPUT).write_text(render(), encoding='utf-8')
    print('wrote %s' % OUTPUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
