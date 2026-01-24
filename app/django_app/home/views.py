"""
Home page view for BriefKorb web interface
"""

from django.shortcuts import render
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from email_server.config import EmailServerConfig
from email_server.auth import TokenManager


def home_view(request):
    """Display home page with authentication status"""
    context = {
        'user': None,
        'is_authenticated': False
    }
    
    # Check session first (for web-based authentication)
    user = request.session.get('user', {})
    if user and user.get('is_authenticated'):
        context['user'] = user
        context['is_authenticated'] = True
        return render(request, 'django_app/home/home.html', context)
    
    # Fallback: Try to get authenticated user from email_server (for desktop app tokens)
    try:
        app_dir = Path(__file__).parent.parent.parent
        config_path = app_dir / 'email_server' / 'config.yaml'
        
        if config_path.exists():
            config = EmailServerConfig.from_file(str(config_path))
            token_manager = TokenManager(storage_path=config.token_storage_path)
            
            # Get all user IDs and find a Microsoft-authenticated one
            all_user_ids = token_manager.get_all_user_ids()
            if all_user_ids:
                # Get first user's info
                user_id = all_user_ids[0]
                user_info = token_manager.get_user_info(user_id)
                if user_info:
                    context['user'] = {
                        'name': user_info.get('displayName') or user_info.get('emailAddress') or user_id,
                        'email': user_info.get('email') or user_info.get('emailAddress') or user_id,
                        'is_authenticated': True
                    }
                    context['is_authenticated'] = True
    except Exception as e:
        # If there's an error, just show unauthenticated state
        pass
    
    return render(request, 'django_app/home/home.html', context)
