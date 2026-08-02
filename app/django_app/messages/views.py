"""
Messages views for BriefKorb web interface
"""

from django.shortcuts import redirect, render
from django.contrib import messages as django_messages
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET
from dateutil import parser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from email_server import UnifiedEmailServer
from email_server.config import EmailServerConfig
from email_server.blocked_sender_tracking import MAX_TRACKED_SUBJECTS
from email_client.utils.sender_categorization import ImpactLevel, SenderCategorizationManager
from .services import annotate_sender_impact
from django_app.authentication import require_external_api_token


def _resolve_selected_buckets(
    server: UnifiedEmailServer, mailbox: str, selected_keys: List[str],
) -> List[Dict[str, Any]]:
    """Resolve 'provider|fromName' selection keys (from messages.html's
    checkboxes/context-menu, provider-prefixed since Gmail buckets can now
    appear alongside Microsoft ones) into their matching digest buckets.

    Fetches the *entire* mailbox (unread_only=False, a high max_messages)
    rather than reusing the display fetch, matching this view's previous
    (MessagesService-based) behavior: bulk actions apply to every message
    from a selected sender, not just the currently-displayed unread-only
    subset.
    """
    all_messages = server.get_user_messages(folder=mailbox, unread_only=False, max_messages=10000)
    digest = server.get_message_digest(messages=all_messages)
    selected_pairs = {tuple(key.split('|', 1)) for key in selected_keys if '|' in key}
    return [b for b in digest if (b['provider'], b['fromName']) in selected_pairs]


def _perform_bulk_action(request, server: UnifiedEmailServer, action: str, selected_buckets: List[Dict[str, Any]]) -> None:
    """Perform markAsRead/deleteMessage/deleteMessageBlockSender across
    however many providers the selected buckets span, and flash a summary
    message. One provider's block_senders() failing (e.g. Gmail, which has
    no server-side blocking capability -- see EmailProvider.block_senders)
    doesn't stop the other providers' actions from running.
    """
    if not selected_buckets:
        django_messages.error(request, "No matching messages found for the selected sender(s).")
        return

    buckets_by_provider: Dict[str, List[Dict[str, Any]]] = {}
    for bucket in selected_buckets:
        buckets_by_provider.setdefault(bucket['provider'], []).append(bucket)

    overall_success = True
    any_block_failed = False
    for provider_name, buckets in buckets_by_provider.items():
        authenticated = server.get_authenticated_providers(provider_name)
        if not authenticated:
            overall_success = False
            continue
        user_id = authenticated[0].user_id
        message_ids = [m['id'] for b in buckets for m in b['messages']]
        sender_names = [b['fromName'] for b in buckets]

        if action == 'markAsRead':
            success = server.mark_messages_as_read(user_id, provider_name, message_ids)
        elif action == 'deleteMessage':
            success = server.delete_user_messages(user_id, provider_name, message_ids)
        else:  # deleteMessageBlockSender
            delete_success = server.delete_user_messages(user_id, provider_name, message_ids)
            sender_details = {
                b['fromName']: {
                    'display_name': b['fromName'],
                    'subjects': [m['subject'] for m in b['messages']][:MAX_TRACKED_SUBJECTS],
                }
                for b in buckets
            }
            block_success = server.block_senders(
                user_id, provider_name, sender_names, source='django_web_messages', sender_details=sender_details,
            )
            success = delete_success and block_success
            if delete_success and not block_success:
                any_block_failed = True
        overall_success = overall_success and success

    sender_count = len(selected_buckets)
    subject_desc = selected_buckets[0]['fromName'] if sender_count == 1 else f"{sender_count} sender(s)"
    action_label = {
        'markAsRead': 'marked as read',
        'deleteMessage': 'deleted',
        'deleteMessageBlockSender': 'deleted and blocked',
    }[action]

    if overall_success:
        django_messages.success(request, f"Messages from {subject_desc} {action_label}.")
    elif action == 'deleteMessageBlockSender' and any_block_failed:
        django_messages.warning(request, f"Deleted messages from {subject_desc}, but failed to create some block rules.")
    else:
        django_messages.error(request, f"Failed to update messages from {subject_desc}.")


def messages_view(request):
    """Display messages aggregated by sender, and handle mark-as-read/
    delete/block-sender/impact-override actions. Built on
    UnifiedEmailServer, same as inbox_view and messages_api_view -- this
    is the last of the three to migrate off the older, Microsoft-only
    MessagesService path."""
    config, error = _load_config()
    if not error:
        server, error = _load_authenticated_server(config)
    if error:
        return render(request, 'django_app/messages/messages.html', {
            'messageData': [],
            'messages_length': 0,
            'mailbox': 'inbox',
            'exclude_read_messages': True,
            'error': error,
            'is_authenticated': False,
        })

    mailbox = 'inbox'
    exclude_read = True
    high_impact_only = False
    has_performed_update = False

    try:
        sender_categorization = SenderCategorizationManager(config.token_storage_path)

        if request.method == 'POST':
            # Handle mailbox selection
            if 'mailbox' in request.POST:
                mailbox_list = request.POST.getlist('mailbox')
                if mailbox_list and mailbox_list[0]:
                    mailbox = mailbox_list[0]

            # Handle exclude read toggle
            if 'excludeRead' in request.POST:
                exclude_read = bool(request.POST.getlist('excludeRead'))
            if 'highImpactOnly' in request.POST:
                high_impact_only = bool(request.POST.getlist('highImpactOnly'))

            set_impact_value = request.POST.get('setImpact', '').strip()
            clear_impact_sender = request.POST.get('clearImpact', '').strip()
            if set_impact_value:
                try:
                    sender, impact = set_impact_value.split('|', 1)
                    sender_categorization.set_sender_exception(sender.strip().lower(), ImpactLevel(impact), source='django_manual_exception')
                    django_messages.success(request, f"Updated sender impact for {sender}.")
                    has_performed_update = True
                except ValueError:
                    django_messages.error(request, "Invalid sender impact update request.")
            elif clear_impact_sender:
                sender_categorization.clear_sender_exception(clear_impact_sender.strip().lower())
                django_messages.success(request, f"Cleared sender impact override for {clear_impact_sender}.")
                has_performed_update = True

            # Handle single-sender context menu actions (value is "provider|fromName")
            context_sender_key = request.POST.get('context_sender', '').strip()
            context_action = request.POST.get('context_action', '').strip()
            selected_keys: List[str] = []
            action: Optional[str] = None
            if context_sender_key and context_action:
                selected_keys = [context_sender_key]
                action = context_action
            elif 'selected_options' in request.POST:
                selected_keys = request.POST.getlist('selected_options')
                if 'markAsRead' in request.POST:
                    action = 'markAsRead'
                elif 'deleteMessage' in request.POST:
                    action = 'deleteMessage'
                elif 'deleteMessageBlockSender' in request.POST:
                    action = 'deleteMessageBlockSender'

            if action and selected_keys:
                selected_buckets = _resolve_selected_buckets(server, mailbox, selected_keys)
                _perform_bulk_action(request, server, action, selected_buckets)
                has_performed_update = True

        # Fetch fresh for display -- reflects any action just performed above,
        # or is simply the normal display fetch if this was a GET/filter-only request.
        messages = server.get_user_messages(folder=mailbox, unread_only=exclude_read, max_messages=1000)
        message_data = server.get_message_digest(messages=messages)
        message_data = annotate_sender_impact(message_data, sender_categorization)
        if high_impact_only:
            message_data = [
                msg_info for msg_info in message_data
                if msg_info.get('impact') == ImpactLevel.HIGH_IMPACT.value
            ]

        # Parse dates for template
        for msg_info in message_data:
            if msg_info.get('lastReceivedDateTime'):
                try:
                    msg_info['lastReceivedDateTime'] = parser.parse(msg_info['lastReceivedDateTime'])
                except:
                    pass

        context = {
            'messageData': message_data,
            'messages_length': len(messages),
            'mailbox': mailbox,
            'exclude_read_messages': exclude_read,
            'high_impact_only': high_impact_only,
            'has_performed_update': has_performed_update,
            'is_authenticated': True,
        }

        return render(request, 'django_app/messages/messages.html', context)

    except Exception as e:
        django_messages.error(request, f"Error loading messages: {str(e)}")
        return render(request, 'django_app/messages/messages.html', {
            'messageData': [],
            'messages_length': 0,
            'mailbox': 'inbox',
            'exclude_read_messages': True,
            'error': str(e),
            'is_authenticated': False,
        })


def _parse_bool_param(request, name: str, default: bool) -> bool:
    raw = request.GET.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _load_config() -> Tuple[Optional[EmailServerConfig], Optional[str]]:
    """Resolve and load config.yaml, or return (None, <error message>) if
    it doesn't exist yet. Shared by every view in this module that needs
    config -- each still decides for itself how to surface the error
    (JsonResponse vs render)."""
    app_dir = Path(__file__).parent.parent.parent
    config_path = EmailServerConfig.resolve_path(app_dir)
    if not config_path.exists():
        return None, 'BriefKorb is not configured on this instance.'
    return EmailServerConfig.from_file(str(config_path)), None


def _load_authenticated_server(config: EmailServerConfig) -> Tuple[Optional[UnifiedEmailServer], Optional[str]]:
    """Construct a UnifiedEmailServer for `config` and confirm at least one
    provider has an authenticated user, or return (None, <error message>).

    Shared by views that aggregate/act across "whichever providers are
    configured" (messages_api_view, inbox_view, messages_view) -- not
    message_detail_view (needs one specific provider) or
    sender_categorization_view (doesn't need a server at all).
    """
    if not (config.microsoft.enabled or config.gmail.enabled):
        return None, 'No email provider is configured on this BriefKorb instance.'
    server = UnifiedEmailServer(config=config)
    if not server.get_authenticated_providers():
        return None, 'Please authenticate with Microsoft or Gmail first. Use the BriefKorb desktop app to authenticate.'
    return server, None


@require_GET
@require_external_api_token
def messages_api_view(request):
    """Read-only JSON endpoint for external consumers. Returns messages
    aggregated by sender -- the same shape the HTML messages view renders,
    just as JSON instead of a template. Not tied to any single named
    caller: any request carrying a token from `external_api.tokens` is
    authorized.

    Aggregation across every provider this BriefKorb instance has an
    authenticated mailbox user for (Microsoft and/or Gmail) is handled by
    UnifiedEmailServer.get_message_digest() (email_server/__init__.py) --
    aggregation across accounts/providers is this application's whole
    reason for being, and that's where "the server" (as opposed to this
    Django view, which is just one interface onto it) implements it, so a
    third provider needs no new code here. Each returned bucket carries a
    `provider` field so callers can tell the source apart.

    Cost note for callers: each call is one or more live Graph/Gmail API
    fetches against BriefKorb's own token quota, not a cheap local/cached
    query. Poll infrequently -- on the order of hours, not per-page-load."""
    config, error = _load_config()
    if error:
        return JsonResponse({'error': error}, status=503)

    server, error = _load_authenticated_server(config)
    if error:
        return JsonResponse({'error': error}, status=503)

    mailbox = request.GET.get('mailbox', 'inbox')
    unread_only = _parse_bool_param(request, 'unread_only', default=True)
    high_impact_only = _parse_bool_param(request, 'high_impact_only', default=False)

    try:
        message_data = server.get_message_digest(folder=mailbox, unread_only=unread_only, max_messages=1000)
        sender_categorization = SenderCategorizationManager(config.token_storage_path)
        message_data = annotate_sender_impact(message_data, sender_categorization)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=502)

    if high_impact_only:
        message_data = [
            msg_info for msg_info in message_data
            if msg_info.get('impact') == ImpactLevel.HIGH_IMPACT.value
        ]

    return JsonResponse({'messages': message_data})


@require_GET
def inbox_view(request):
    """Browse and read individual messages across every authenticated
    provider (Microsoft and/or Gmail). Built on UnifiedEmailServer, the
    same layer messages_api_view and (as of this migration) messages_view
    both use."""
    config, error = _load_config()
    if not error:
        server, error = _load_authenticated_server(config)
    if error:
        return render(request, 'django_app/messages/inbox.html', {
            'messageData': [], 'is_authenticated': False, 'error': error,
        })

    mailbox = request.GET.get('mailbox', 'inbox')
    unread_only = _parse_bool_param(request, 'unread_only', default=True)

    try:
        messages = server.get_user_messages(folder=mailbox, unread_only=unread_only, max_messages=1000)
        message_data = server.get_message_digest(messages=messages)
        # Non-fatal if entity_graph is unavailable -- extract_entities()
        # already returns 0 in that case rather than raising.
        entity_count = server.extract_entities(messages)
    except Exception as e:
        return render(request, 'django_app/messages/inbox.html', {
            'messageData': [], 'is_authenticated': False, 'error': str(e),
        })

    return render(request, 'django_app/messages/inbox.html', {
        'messageData': message_data,
        'messages_length': len(messages),
        'mailbox': mailbox,
        'unread_only': unread_only,
        'entity_count': entity_count,
        'is_authenticated': True,
    })


@require_GET
def message_detail_view(request, provider, message_id):
    """Read a single message's full body -- the drill-down target from
    inbox_view's per-sender message list."""
    config, error = _load_config()
    if error:
        return render(request, 'django_app/messages/message_detail.html', {'error': error})

    try:
        server = UnifiedEmailServer(config=config)
        authenticated = server.get_authenticated_providers(provider)
        if not authenticated:
            return render(request, 'django_app/messages/message_detail.html', {
                'error': f'No authenticated {provider} mailbox user is configured on this BriefKorb instance.',
            })
        message = server.get_message(authenticated[0].user_id, provider, message_id)
    except Exception as e:
        return render(request, 'django_app/messages/message_detail.html', {'error': str(e)})

    if message is None:
        return render(request, 'django_app/messages/message_detail.html', {
            'error': 'Message not found -- it may have been deleted, or the provider is unavailable.',
        })

    return render(request, 'django_app/messages/message_detail.html', {'message': message})


def sender_categorization_view(request):
    """Browse every sender categorization record, inspect why a sender was
    categorized a given way (its decision trace), and apply/clear a
    per-sender impact override -- the same SenderCategorizationManager data
    and controls messages_view's inline setImpact/clearImpact already use,
    as a dedicated browse/inspect page rather than only an inline action.

    Constructs SenderCategorizationManager directly rather than via
    MessagesService, the same reason messages_api_view does: categorization
    doesn't depend on Microsoft being configured, and gating it behind a
    Microsoft-only service would make this page unusable on a Gmail-only
    instance for no real reason.
    """
    config, error = _load_config()
    if error:
        return render(request, 'django_app/messages/sender_categorization.html', {
            'records': [], 'error': error,
        })
    sender_categorization = SenderCategorizationManager(config.token_storage_path)

    if request.method == 'POST':
        set_impact_value = request.POST.get('setImpact', '').strip()
        clear_impact_sender = request.POST.get('clearImpact', '').strip()
        if set_impact_value:
            try:
                sender, impact = set_impact_value.split('|', 1)
                sender = sender.strip().lower()
                sender_categorization.set_sender_exception(sender, ImpactLevel(impact), source='django_categorization_page')
                django_messages.success(request, f"Updated impact override for {sender}.")
            except ValueError:
                django_messages.error(request, "Invalid impact update request.")
        elif clear_impact_sender:
            sender = clear_impact_sender.strip().lower()
            sender_categorization.clear_sender_exception(sender)
            django_messages.success(request, f"Cleared impact override for {sender}.")
        redirect_url = reverse('django_app.messages:sender_categorization')
        if clear_impact_sender or set_impact_value:
            selected = clear_impact_sender or set_impact_value.split('|', 1)[0]
            redirect_url += f'?sender={selected.strip().lower()}'
        return redirect(redirect_url)

    records = sender_categorization.list_sender_records()
    selected_sender = request.GET.get('sender', '').strip().lower()
    selected_record = next((r for r in records if r['sender'] == selected_sender), None) if selected_sender else None

    return render(request, 'django_app/messages/sender_categorization.html', {
        'records': records,
        'selected_record': selected_record,
        'selected_sender': selected_sender,
    })


def blocked_senders_view(request):
    """Browse blocked-sender history (audit log, not live provider-side
    enforcement state -- see UnifiedEmailServer.get_blocked_sender_summary)
    and unblock a sender's local suppression. Same list + `?sender=`
    -selected-detail shape as sender_categorization_view.

    Deliberately constructs UnifiedEmailServer directly rather than via
    _load_authenticated_server: viewing/unblocking is pure local-cache
    work and shouldn't require a currently-authenticated provider, the
    same reasoning sender_categorization_view already applies by skipping
    that helper entirely.
    """
    config, error = _load_config()
    if not error and not (config.microsoft.enabled or config.gmail.enabled):
        error = 'No email provider is configured on this BriefKorb instance.'
    if error:
        return render(request, 'django_app/messages/blocked_senders.html', {
            'summaries': [], 'error': error,
        })
    server = UnifiedEmailServer(config=config)

    if request.method == 'POST':
        unblock_sender = request.POST.get('unblock', '').strip().lower()
        if unblock_sender:
            server.unblock_sender(unblock_sender)
            django_messages.success(request, f"Unblocked {unblock_sender}.")
        redirect_url = reverse('django_app.messages:blocked_senders')
        if unblock_sender:
            redirect_url += f'?sender={unblock_sender}'
        return redirect(redirect_url)

    summaries = server.get_blocked_sender_summary()
    selected_sender = request.GET.get('sender', '').strip().lower()
    selected_summary = next((s for s in summaries if s['sender'] == selected_sender), None) if selected_sender else None

    return render(request, 'django_app/messages/blocked_senders.html', {
        'summaries': summaries,
        'selected_summary': selected_summary,
        'selected_sender': selected_sender,
    })
