"""Proving tests for the source-connection OAuth authorize URLs.

The two halves of one flow have to name the same callback. `/source-connections
/<provider>/authorize` requires a `redirect_uri` and hands it to the generator;
`/source-connections/<provider>/callback` posts that same value to the provider's
token endpoint (`complete_*_callback`). A generator that accepts the parameter
and leaves it out of the authorize URL makes the provider return the user to
whatever callback is registered on the OAuth app instead of the one the panel
asked for, while the token exchange still claims the one it asked for.

Parametrised across every provider so the next one added is covered by the same
assertion instead of a fourth copy of it.
"""
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services.settings_service import SettingsService
from app.services.source_connection_service import SourceConnectionService as SC

# provider -> (client-id setting, client-secret setting, generator method)
PROVIDERS = [
    ('github', 'source_github_client_id', 'source_github_client_secret',
     'generate_github_authorize_url'),
    ('gitlab', 'source_gitlab_client_id', 'source_gitlab_client_secret',
     'generate_gitlab_authorize_url'),
    ('bitbucket', 'source_bitbucket_client_id', 'source_bitbucket_client_secret',
     'generate_bitbucket_authorize_url'),
]


@pytest.mark.parametrize('provider,id_key,secret_key,generator', PROVIDERS)
def test_authorize_url_carries_the_callers_redirect_uri(
        app, provider, id_key, secret_key, generator):
    redirect_uri = f'https://panel.example/connections/callback/{provider}'
    with app.test_request_context():
        SettingsService.set(id_key, 'cid')
        SettingsService.set(secret_key, 'sec')
        # Classic OAuth screen: the GitHub App install path is a deliberate
        # exception with its own coverage in test_github_app_manifest.py.
        SettingsService.set('source_github_app_slug', '')

        url, state = getattr(SC, generator)(redirect_uri)

        params = parse_qs(urlsplit(url).query)
        assert params['redirect_uri'] == [redirect_uri]
        assert params['client_id'] == ['cid']
        assert params['state'] == [state]
