"""
Settings view — read and write email_server/config.yaml from the web UI.
"""

from django.shortcuts import render, redirect
from django.contrib import messages as django_messages
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from email_server.config import EmailServerConfig, ProviderConfig
from email_server.auth import TokenManager
from email_server.utils.app_paths import resolve_data_path
from email_client.utils.scope_checker import ScopeChecker

LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR']


def _get_app_dir() -> Path:
    return Path(__file__).parent.parent.parent


def _auth_status(config: EmailServerConfig) -> dict:
    """Return a dict with 'microsoft' and 'gmail' auth status strings."""
    status = {'microsoft': None, 'gmail': None}
    try:
        token_manager = TokenManager(storage_path=config.token_storage_path)
        user_ids = token_manager.get_all_user_ids()
        ms_users, gmail_users = [], []
        for uid in user_ids:
            token = token_manager.get_token(uid)
            if not token:
                continue
            if 'access_token' in token and 'token_uri' not in token:
                ms_users.append(uid)
            elif 'token' in token and 'token_uri' in token:
                gmail_users.append(uid)
        if ms_users:
            status['microsoft'] = ', '.join(ms_users)
        if gmail_users:
            status['gmail'] = ', '.join(gmail_users)
    except Exception:
        pass
    return status


def _apply_posted_settings(config: EmailServerConfig, post) -> None:
    """Copy Microsoft / Gmail / general / external-API-enabled fields from POST.

    Does not generate or revoke API tokens — those are separate POST actions
    so a Save cannot accidentally mint a secret.
    """
    ms_scopes = post.getlist('ms_scopes')

    config.microsoft = ProviderConfig(
        enabled='ms_enabled' in post,
        client_id=post.get('ms_client_id', '').strip() or None,
        client_secret=post.get('ms_client_secret', '').strip() or None,
        tenant_id=post.get('ms_tenant_id', '').strip() or None,
        redirect_uri=post.get('ms_redirect_uri', '').strip() or None,
        scopes=ms_scopes,
        additional_settings=config.microsoft.additional_settings,
    )

    config.gmail = ProviderConfig(
        enabled='gmail_enabled' in post,
        credentials_path=post.get('gmail_credentials_path', '').strip() or None,
        redirect_uri=post.get('gmail_redirect_uri', '').strip() or None,
        scopes=post.getlist('gmail_scopes'),
        additional_settings=config.gmail.additional_settings,
    )

    log_level = post.get('log_level', 'INFO')
    if log_level not in LOG_LEVELS:
        log_level = 'INFO'
    config.token_storage_path = str(resolve_data_path(post.get('token_storage_path', 'tokens').strip() or 'tokens'))
    config.log_level = log_level.lower()
    try:
        config.max_messages = max(1, int(post.get('max_messages', config.max_messages)))
    except ValueError:
        pass

    config.external_api.enabled = 'external_api_enabled' in post


def settings_view(request):
    app_dir = _get_app_dir()
    config_path = EmailServerConfig.resolve_path(app_dir)

    # Load or initialise config
    if config_path.exists():
        config = EmailServerConfig.from_file(str(config_path))
    else:
        config = EmailServerConfig(
            microsoft=ProviderConfig(enabled=False),
            gmail=ProviderConfig(enabled=False),
        )

    if request.method == 'POST':
        try:
            post = request.POST
            _apply_posted_settings(config, post)

            newly_generated = None
            if 'generate_api_token' in post:
                newly_generated = config.external_api.generate_token(
                    label=post.get('api_token_label', ''),
                )
            elif 'revoke_api_token' in post:
                try:
                    index = int(post.get('revoke_api_token', ''))
                except (TypeError, ValueError):
                    index = -1
                if config.external_api.revoke_token_at(index) is None:
                    django_messages.error(request, 'Could not revoke that token.')
                    return redirect('django_app.config:settings')

            config.save(str(config_path))
            if newly_generated is not None:
                request.session['newly_generated_api_token'] = newly_generated
                django_messages.success(
                    request,
                    'Token generated. Copy it now; it will not be shown again.',
                )
            elif 'revoke_api_token' in post:
                django_messages.success(request, 'Token revoked.')
            else:
                django_messages.success(request, 'Settings saved successfully.')
        except Exception as e:
            django_messages.error(request, f'Failed to save settings: {e}')

        return redirect('django_app.config:settings')

    # --- GET ---
    ms_scopes = set(config.microsoft.scopes or [])
    gmail_scopes = set(config.gmail.scopes or [])
    auth_status = _auth_status(config)

    context = {
        'config': config,
        'ms_available_scopes': ScopeChecker.get_available_scopes('microsoft'),
        'gmail_available_scopes': ScopeChecker.get_available_scopes('gmail'),
        'ms_current_scopes': ms_scopes,
        'gmail_current_scopes': gmail_scopes,
        'log_levels': LOG_LEVELS,
        'ms_auth_user': auth_status['microsoft'],
        'gmail_auth_user': auth_status['gmail'],
        'api_tokens': [
            {
                'index': i,
                'label': registered.display_label(),
                'masked': registered.masked_token(),
            }
            for i, registered in enumerate(config.external_api.tokens)
        ],
        'newly_generated_api_token': request.session.pop('newly_generated_api_token', None),
    }
    return render(request, 'django_app/config/settings.html', context)
