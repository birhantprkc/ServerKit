"""The shipped nginx configs must serve the runtime-extension vendor shims as
JavaScript.

A runtime-loaded extension bundle resolves ``react``, ``serverkit-sdk`` and
friends through the import map to ``/serverkit-vendor/<name>.mjs``. Stock
nginx (1.18-1.24 on Debian/Ubuntu) has no ``.mjs`` entry in mime.types and
serves those files as ``application/octet-stream``, which the browser refuses
to execute as an ES module -- every runtime extension then fails with
"Failed to fetch dynamically imported module: blob:...". Each vhost we ship
therefore carries a location block that forces ``text/javascript``.

The block is a regex location, and regex locations beat the ``/api`` prefix
block. The regex MUST stay anchored to ``/serverkit-vendor/``: a bare
``\\.mjs$`` also captured ``/api/v1/plugins/<slug>/assets/dist/index.mjs`` and
404'd every extension bundle instead of fixing it.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

VHOSTS = [
    ROOT / 'nginx' / 'sites-available' / 'serverkit.conf',
    ROOT / 'nginx' / 'sites-available' / 'serverkit-insecure.conf',
    ROOT / 'frontend' / 'nginx.conf',
]

# `location ~* <regex> {` followed by the block body up to its closing brace.
# The body may contain one level of nested braces (`types { }`).
_LOCATION = re.compile(
    r'location\s+~\*\s+(?P<regex>\S+)\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}',
    re.DOTALL)


def _mjs_locations(text):
    return [m for m in _LOCATION.finditer(text) if 'mjs' in m.group('regex')]


@pytest.mark.parametrize('vhost', VHOSTS, ids=lambda p: p.name)
def test_every_vhost_forces_javascript_for_vendor_shims(vhost):
    matches = _mjs_locations(vhost.read_text(encoding='utf-8'))
    assert len(matches) == 1, f'{vhost.name}: expected exactly one .mjs location block'
    body = matches[0].group('body')
    # `types { }` empties the inherited MIME table inside the block so
    # default_type is what actually gets sent.
    assert re.search(r'types\s*\{\s*\}', body), f'{vhost.name}: missing `types {{ }}`'
    assert re.search(r'default_type\s+(text|application)/javascript\s*;', body), (
        f'{vhost.name}: default_type must be a JavaScript MIME type')


@pytest.mark.parametrize('vhost', VHOSTS, ids=lambda p: p.name)
def test_vendor_shim_regex_matches_shims_but_not_api_assets(vhost):
    regex = _mjs_locations(vhost.read_text(encoding='utf-8'))[0].group('regex')
    # nginx `~*` is a case-insensitive PCRE; Python's `re` is close enough for
    # the anchors and character classes used here.
    pattern = re.compile(regex, re.IGNORECASE)

    for shim in ('react', 'react-dom-client', 'react-jsx-runtime', 'serverkit-sdk'):
        assert pattern.search(f'/serverkit-vendor/{shim}.mjs'), (
            f'{vhost.name}: {regex} must match /serverkit-vendor/{shim}.mjs')

    for uri in (
        '/api/v1/plugins/serverkit-wordpress/assets/dist/index.mjs',
        '/api/v1/plugins/serverkit-git/assets/dist/chunk.mjs',
        '/assets/index-abc123.js',
        '/serverkit-vendor/react.js',
    ):
        assert not pattern.search(uri), (
            f'{vhost.name}: {regex} must NOT capture {uri} '
            '(regex locations take precedence over the /api prefix block)')
