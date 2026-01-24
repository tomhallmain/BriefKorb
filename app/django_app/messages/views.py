"""
Messages views for BriefKorb web interface
"""

from django.shortcuts import render
from django.contrib import messages as django_messages
from dateutil import parser
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from email_server.config import EmailServerConfig
from email_server.auth import TokenManager
from .services import MessagesService
from django_app.calendar.services import get_iana_from_windows


def _get_authenticated_user_id(request):
    """Get authenticated user ID from session or request"""
    # Try to get from session first (legacy UI compatibility)
    user = request.session.get('user', {})
    if user and user.get('is_authenticated'):
        return user.get('email') or user.get('userPrincipalName')
    
    # Try to get from email_server token manager
    app_dir = Path(__file__).parent.parent.parent
    config_path = app_dir / 'email_server' / 'config.yaml'
    
    if config_path.exists():
        config = EmailServerConfig.from_file(str(config_path))
        token_manager = TokenManager(storage_path=config.token_storage_path)
        
        # Get all user IDs and find a Microsoft-authenticated one
        all_user_ids = token_manager.get_all_user_ids()
        for user_id in all_user_ids:
            token_data = token_manager.get_token(user_id)
            if token_data and 'access_token' in token_data:
                return user_id
    
    return None


def messages_view(request):
    """Display messages aggregated by sender"""
    user_id = _get_authenticated_user_id(request)
    
    if not user_id:
        django_messages.error(request, "Please authenticate with Microsoft first.")
        return render(request, 'django_app/messages/messages.html', {
            'messageData': [],
            'messages_length': 0,
            'mailbox': 'inbox',
            'exclude_read_messages': True,
            'error': 'Please authenticate with Microsoft first. Use the BriefKorb desktop app to authenticate.',
            'is_authenticated': False,
        })
    
    try:
        messages_service = MessagesService(user_id)
        user_info = messages_service.get_user_info()
        
        # Get user's timezone
        user_timezone = user_info.get('mailboxSettings', {}).get('timeZone') or 'UTC'
        iana_timezone = get_iana_from_windows(user_timezone)
        
        # Default values
        default_mailbox = 'inbox'
        mailbox = default_mailbox
        exclude_read = True
        has_performed_update = False
        
        # Handle POST requests
        if request.method == 'POST':
            # Handle mailbox selection
            if 'mailbox' in request.POST:
                mailbox_list = request.POST.getlist('mailbox')
                if mailbox_list and mailbox_list[0]:
                    mailbox = mailbox_list[0]
            
            # Handle exclude read toggle
            if 'excludeRead' in request.POST:
                exclude_read = bool(request.POST.getlist('excludeRead'))
            
            # Handle actions on selected senders
            if 'selected_options' in request.POST:
                selected_senders = request.POST.getlist('selected_options')
                action = None
                
                if 'markAsRead' in request.POST:
                    action = 'markAsRead'
                elif 'deleteMessage' in request.POST:
                    action = 'deleteMessage'
                elif 'deleteMessageBlockSender' in request.POST:
                    action = 'deleteMessageBlockSender'
                
                if action and selected_senders:
                    if action == 'markAsRead':
                        success = messages_service.mark_messages_as_read(selected_senders, mailbox)
                        if success:
                            django_messages.success(request, f"Marked messages from {len(selected_senders)} sender(s) as read.")
                        else:
                            django_messages.error(request, "Failed to mark some messages as read.")
                    elif action == 'deleteMessage':
                        success = messages_service.delete_messages(selected_senders, mailbox)
                        if success:
                            django_messages.success(request, f"Deleted messages from {len(selected_senders)} sender(s).")
                        else:
                            django_messages.error(request, "Failed to delete some messages.")
                    elif action == 'deleteMessageBlockSender':
                        # Delete messages first
                        delete_success = messages_service.delete_messages(selected_senders, mailbox)
                        # Then create blocking rules
                        block_success = messages_service.block_senders(selected_senders)
                        
                        if delete_success and block_success:
                            django_messages.success(request, f"Deleted messages and blocked {len(selected_senders)} sender(s).")
                        elif delete_success:
                            django_messages.warning(request, f"Deleted messages from {len(selected_senders)} sender(s), but failed to create some block rules.")
                        else:
                            django_messages.error(request, "Failed to delete some messages.")
                    
                    has_performed_update = True
        
        # Get messages
        messages = messages_service.get_messages(
            mailbox=mailbox,
            exclude_read=exclude_read,
            max_messages=1000,
            timezone=iana_timezone
        )
        
        # Aggregate by sender
        message_data = messages_service.aggregate_messages_by_sender(messages)
        
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
