"""
OAuth callback views for BriefKorb

These views handle OAuth callbacks from Microsoft and Gmail providers,
exchange authorization codes for tokens, and save them using the email_server system.
Also provides web-based sign-in/sign-out functionality.
"""

from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from pathlib import Path
from datetime import datetime
import json
import traceback

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from email_server.config import EmailServerConfig
from email_server.auth import MicrosoftOAuth, GmailOAuth, TokenManager


def _get_app_dir():
    """Get the app directory (parent of django_app/)"""
    return Path(__file__).parent.parent.parent


def _error_response(title: str, message: str, details: str = None) -> HttpResponse:
    """Generate an error response HTML"""
    details_html = f"<p style='font-size: 10px; color: #888; margin-top: 20px;'>{details}</p>" if details else ""
    return HttpResponse(
        f"<html><body style='font-family: Arial; padding: 20px; text-align: center; background-color: #2b2d2c; color: #ffffff;'>"
        f"<h1 style='color: #ff4444;'>✗ {title}</h1>"
        f"<p>{message}</p>"
        f"<p>Please try again from BriefKorb.</p>"
        f"{details_html}"
        f"</body></html>"
    )


def _success_response(title: str, message: str) -> HttpResponse:
    """Generate a success response HTML"""
    return HttpResponse(
        f"<html><body style='font-family: Arial; padding: 20px; text-align: center; background-color: #2b2d2c; color: #ffffff;'>"
        f"<h1 style='color: #008a8f;'>✓ {title}</h1>"
        f"<p>{message}</p>"
        f"<p>Tokens have been saved. You can close this window and return to BriefKorb.</p>"
        f"</body></html>"
    )


def microsoft_callback(request):
    """Handle OAuth callback for Microsoft Graph API"""
    try:
        # Get authorization code from callback
        code = request.GET.get('code')
        if not code:
            error = request.GET.get('error', 'Unknown error')
            return _error_response("Authentication Failed", f"Error: {error}")
        
        # Load config
        app_dir = _get_app_dir()
        config_path = app_dir / 'email_server' / 'config.yaml'
        
        if not config_path.exists():
            return _error_response("Configuration Error", "Configuration file not found. Please configure BriefKorb first.")
        
        config = EmailServerConfig.from_file(str(config_path))
        
        if not config.microsoft.enabled:
            return _error_response("Configuration Error", "Microsoft Graph is not configured. Please configure it in BriefKorb settings.")
        
        # Initialize OAuth and token manager
        token_manager = TokenManager(storage_path=config.token_storage_path)
        microsoft_oauth = MicrosoftOAuth(
            client_id=config.microsoft.client_id,
            client_secret=config.microsoft.client_secret,
            tenant_id=config.microsoft.tenant_id or 'common',
            redirect_uri=config.microsoft.redirect_uri,
            token_manager=token_manager,
            scopes=config.microsoft.scopes
        )
        
        # Try to get flow from session (web-based sign-in) or create new one
        flow = request.session.pop('microsoft_auth_flow', None)
        
        # Exchange code for token using MSAL
        import msal
        cache = msal.SerializableTokenCache()
        auth_app = msal.ConfidentialClientApplication(
            config.microsoft.client_id,
            authority=microsoft_oauth.authority,
            client_credential=config.microsoft.client_secret,
            token_cache=cache
        )
        
        # Use flow from session if available, otherwise use acquire_token_by_authorization_code
        if flow:
            # Use the stored flow from web sign-in
            request_dict = {'code': code}
            result = auth_app.acquire_token_by_auth_code_flow(flow, request_dict)
        else:
            # Fallback for desktop app (no flow in session)
            result = auth_app.acquire_token_by_authorization_code(
                code,
                scopes=config.microsoft.scopes or microsoft_oauth.scopes,
                redirect_uri=config.microsoft.redirect_uri
            )
        
        if "error" in result:
            return _error_response("Authentication Failed", f"Error: {result.get('error_description', result.get('error'))}")
        
        # Get user info
        user = microsoft_oauth.get_user_info(result.get('access_token'))
        user_email = user.get('email') or user.get('userPrincipalName') or 'microsoft_user'
        
        # Convert MSAL token format to our token format
        token_data = {
            'access_token': result.get('access_token'),
            'refresh_token': result.get('refresh_token'),
            'expires_in': result.get('expires_in', 3600),
            'token_type': result.get('token_type', 'Bearer'),
            'scope': ' '.join(result.get('scope', [])),
        }
        
        # Store tokens
        token_manager.store_token(user_email, token_data)
        token_manager.store_user_info(user_email, user)
        
        # Store user in session for web-based authentication
        request.session['user'] = {
            'is_authenticated': True,
            'name': user.get('displayName') or user_email,
            'email': user_email,
            'userPrincipalName': user.get('userPrincipalName', user_email)
        }
        
        # Write status file (for desktop app compatibility)
        status_file = app_dir / 'email_server' / '.microsoft_auth_status.json'
        status_data = {
            'status': 'success',
            'user_email': user_email,
            'provider': 'microsoft',
            'timestamp': str(datetime.now())
        }
        with open(status_file, 'w') as f:
            json.dump(status_data, f)
        
        # Redirect to home page if this was a web sign-in (user is now in session), otherwise show success page
        if request.session.get('user', {}).get('is_authenticated'):
            return HttpResponseRedirect(reverse('django_app.home:home'))
        
        return _success_response("Authentication Successful!", "You have successfully authenticated with Microsoft Graph API.")
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"Microsoft callback error: {error_details}")
        
        # Write error status file
        try:
            app_dir = _get_app_dir()
            status_file = app_dir / 'email_server' / '.microsoft_auth_status.json'
            status_data = {
                'status': 'error',
                'error': str(e),
                'provider': 'microsoft',
                'timestamp': str(datetime.now())
            }
            with open(status_file, 'w') as f:
                json.dump(status_data, f)
        except:
            pass
        
        return _error_response("Authentication Failed", str(e), "Error details have been logged. Please restart BriefKorb if you just updated the code.")


def gmail_callback(request):
    """Handle OAuth callback for Gmail API - exchange code for tokens and save them"""
    try:
        # Get authorization code from callback
        code = request.GET.get('code')
        if not code:
            error = request.GET.get('error', 'Unknown error')
            return _error_response("Authentication Failed", f"Error: {error}")
        
        # Load config
        app_dir = _get_app_dir()
        config_path = app_dir / 'email_server' / 'config.yaml'
        
        if not config_path.exists():
            return _error_response("Configuration Error", "Configuration file not found. Please configure BriefKorb first.")
        
        config = EmailServerConfig.from_file(str(config_path))
        
        if not config.gmail.enabled or not config.gmail.credentials_path:
            return _error_response("Configuration Error", "Gmail is not configured. Please configure it in BriefKorb settings.")
        
        # Get credentials path
        credentials_path = Path(config.gmail.credentials_path)
        if not credentials_path.is_absolute():
            credentials_path = app_dir / credentials_path
        
        if not credentials_path.exists():
            return _error_response("Configuration Error", f"Gmail credentials file not found at: {credentials_path}")
        
        # Initialize Gmail OAuth
        token_manager = TokenManager(storage_path=config.token_storage_path)
        redirect_uri = config.gmail.redirect_uri or "http://localhost:8000/auth/gmail/callback"
        scopes = config.gmail.scopes or GmailOAuth.SCOPES
        
        # Initialize GmailOAuth
        gmail_oauth = GmailOAuth(
            credentials_path=str(credentials_path),
            redirect_uri=redirect_uri,
            token_manager=token_manager
        )
        
        # Try to get flow from session (web-based sign-in) or create new one
        flow = request.session.pop('gmail_auth_flow', None)
        if flow:
            # Use the stored flow from web sign-in
            gmail_oauth.flow = flow
        else:
            # Fallback for desktop app - create new flow
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path),
                scopes=scopes,
                redirect_uri=redirect_uri
            )
            gmail_oauth.flow = flow
        
        # Exchange code for tokens
        token_data = gmail_oauth.get_token_from_code(code)
        
        # Get user email to use as user_id
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        creds = Credentials(
            token=token_data['token'],
            refresh_token=token_data.get('refresh_token'),
            token_uri=token_data['token_uri'],
            client_id=token_data['client_id'],
            client_secret=token_data.get('client_secret'),
            scopes=token_data['scopes']
        )
        service = build('gmail', 'v1', credentials=creds)
        profile = service.users().getProfile(userId='me').execute()
        user_email = profile.get('emailAddress', 'gmail_user')
        
        # Store tokens using TokenManager
        token_manager.store_token(user_email, token_data)
        
        # Also store user info
        user_info = {'emailAddress': user_email}
        token_manager.store_user_info(user_email, user_info)
        
        # Store user in session for web-based authentication
        request.session['user'] = {
            'is_authenticated': True,
            'name': user_email,
            'email': user_email,
            'emailAddress': user_email
        }
        
        # Write status file to signal completion to desktop app
        status_file = app_dir / 'email_server' / '.gmail_auth_status.json'
        status_data = {
            'status': 'success',
            'user_email': user_email,
            'provider': 'gmail',
            'timestamp': str(datetime.now())
        }
        with open(status_file, 'w') as f:
            json.dump(status_data, f)
        
        # Redirect to home page if this was a web sign-in (user is now in session), otherwise show success page
        if request.session.get('user', {}).get('is_authenticated'):
            return HttpResponseRedirect(reverse('django_app.home:home'))
        
        return _success_response("Authentication Successful!", "Gmail authentication completed successfully.")
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"Gmail callback error: {error_details}")
        
        # Write error status file
        try:
            app_dir = _get_app_dir()
            status_file = app_dir / 'email_server' / '.gmail_auth_status.json'
            status_data = {
                'status': 'error',
                'error': str(e),
                'error_details': error_details,
                'provider': 'gmail',
                'timestamp': str(datetime.now())
            }
            with open(status_file, 'w') as f:
                json.dump(status_data, f)
        except Exception as save_error:
            print(f"Failed to save error status: {save_error}")
        
        return _error_response("Authentication Failed", str(e), "Error details have been logged. Please restart BriefKorb if you just updated the code.")
