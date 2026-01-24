"""
Gmail OAuth implementation
"""

from typing import Dict, Any, Optional, Union
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from . import OAuthProvider, TokenManager, GmailToken
from ..utils.logger import setup_logger
from googleapiclient.discovery import build

# Set up logger
logger = setup_logger('email_server.auth.gmail')

class GmailOAuth(OAuthProvider):
    """Gmail OAuth implementation"""
    
    # Gmail API scopes
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.modify'
    ]
    
    def __init__(self, credentials_path: str, redirect_uri: str, token_manager: Optional[TokenManager] = None):
        self.credentials_path = credentials_path
        self.redirect_uri = redirect_uri
        # Use provided token_manager (provider should always provide one)
        self.token_manager = token_manager if token_manager is not None else TokenManager()
        self.flow = None
        logger.info("Initialized Gmail OAuth provider")
    
    def get_auth_url(self) -> str:
        """Get Gmail OAuth authorization URL"""
        self.flow = InstalledAppFlow.from_client_secrets_file(
            self.credentials_path,
            scopes=self.SCOPES,
            redirect_uri=self.redirect_uri
        )
        
        auth_url, _ = self.flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true'
        )
        
        logger.debug("Generated auth URL for Gmail OAuth")
        return auth_url
    
    def get_token_from_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token"""
        if not self.flow:
            logger.error("OAuth flow not initialized")
            raise RuntimeError("OAuth flow not initialized. Call get_auth_url first.")
        
        try:
            self.flow.fetch_token(code=code)
            credentials = self.flow.credentials
            token_data = {
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'client_id': credentials.client_id,
                'client_secret': credentials.client_secret,
                'scopes': credentials.scopes
            }
            logger.info("Successfully obtained token from authorization code")
            return token_data
        except Exception as e:
            logger.error(f"Failed to get token from code: {str(e)}")
            raise
    
    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired access token"""
        try:
            credentials = Credentials(
                None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.flow.client_config['installed']['client_id'],
                client_secret=self.flow.client_config['installed']['client_secret'],
                scopes=self.SCOPES
            )
            
            credentials.refresh(Request())
            token_data = {
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'client_id': credentials.client_id,
                'client_secret': credentials.client_secret,
                'scopes': credentials.scopes
            }
            logger.info("Successfully refreshed access token")
            return token_data
        except Exception as e:
            logger.error(f"Failed to refresh token: {str(e)}")
            raise
    
    def get_user_info(self, access_token: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Get user information from Gmail API
        
        Args:
            access_token: Either a string token or a full token_data dict
            
        Returns:
            User information dictionary from Gmail API
        """
        try:
            # Handle both string token and token_data dict
            if isinstance(access_token, dict):
                # If it's a dict, extract the token
                token_str = access_token.get('token') or access_token.get('access_token')
                if not token_str:
                    raise ValueError("No token found in token_data dict")
                credentials = Credentials(
                    token=token_str,
                    refresh_token=access_token.get('refresh_token'),
                    token_uri=access_token.get('token_uri', 'https://oauth2.googleapis.com/token'),
                    client_id=access_token.get('client_id'),
                    client_secret=access_token.get('client_secret'),
                    scopes=access_token.get('scopes', self.SCOPES)
                )
            else:
                # If it's a string, create credentials from it
                credentials = Credentials(access_token)
            
            service = build('gmail', 'v1', credentials=credentials)
            user_info = service.users().getProfile(userId='me').execute()
            logger.info("Successfully retrieved user info from Gmail API")
            return user_info
        except Exception as e:
            logger.error(f"Failed to get user info: {str(e)}")
            raise
    
    def get_valid_token(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a valid access token, refreshing if necessary"""
        token_data = self.token_manager.get_token(user_id)
        if not token_data:
            return None
        
        # Verify this token belongs to Gmail provider
        if not GmailToken.verify_for_provider_type(token_data):
            return None
        
        try:
            credentials = Credentials(
                token_data['token'],
                refresh_token=token_data.get('refresh_token'),
                token_uri=token_data['token_uri'],
                client_id=token_data['client_id'],
                client_secret=token_data.get('client_secret'),
                scopes=token_data['scopes']
            )
            
            if not credentials.valid:
                logger.info(f"Token expired for user {user_id}, refreshing...")
                credentials.refresh(Request())
                new_token_data = {
                    'token': credentials.token,
                    'refresh_token': credentials.refresh_token,
                    'token_uri': credentials.token_uri,
                    'client_id': credentials.client_id,
                    'client_secret': credentials.client_secret,
                    'scopes': credentials.scopes
                }
                self.token_manager.store_token(user_id, new_token_data)
                return new_token_data
            
            logger.debug(f"Using existing valid token for user {user_id}")
            return token_data
        except Exception as e:
            # Don't log as error - this might just be a token from a different provider
            logger.debug(f"Token check for user {user_id}: {str(e)}")
            return None
