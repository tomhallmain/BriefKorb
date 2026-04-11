"""
Microsoft OAuth implementation using MSAL (Microsoft Authentication Library)
"""

from typing import Dict, Any, Optional, List
import requests
import msal
import json
import time
from . import OAuthProvider, TokenManager, MicrosoftToken
from ..utils.logger import setup_logger

# Set up logger
logger = setup_logger('email_server.auth.microsoft')

class MicrosoftOAuth(OAuthProvider):
    """Microsoft OAuth implementation using MSAL"""
    
    def __init__(self, client_id: str, client_secret: str, tenant_id: str, redirect_uri: str, 
                 token_manager: Optional[TokenManager] = None, scopes: Optional[List[str]] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.redirect_uri = redirect_uri
        # Use provided token_manager or create new one (but provider should always provide one)
        self.token_manager = token_manager if token_manager is not None else TokenManager()
        
        # Default scopes if not provided
        self.scopes = scopes or [
            "https://graph.microsoft.com/Mail.ReadWrite",
            "https://graph.microsoft.com/Mail.Send",
        ]
        
        # Microsoft Graph API endpoints
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        self.graph_url = "https://graph.microsoft.com/v1.0"
        
        # Initialize MSAL app
        self._msal_app = None
        self._auth_flow_cache: Dict[str, Dict[str, Any]] = {}  # Store auth flows by user_id
        self._current_flow: Optional[Dict[str, Any]] = None  # Store current flow for get_token_from_code
        
        logger.info("Initialized Microsoft OAuth provider with MSAL")
    
    def _get_msal_app(self, cache: Optional[msal.SerializableTokenCache] = None) -> msal.ConfidentialClientApplication:
        """Get or create MSAL ConfidentialClientApplication instance"""
        if self._msal_app is None or cache is not None:
            # Create MSAL app with optional token cache
            self._msal_app = msal.ConfidentialClientApplication(
                self.client_id,
                authority=self.authority,
                client_credential=self.client_secret,
                token_cache=cache
            )
        return self._msal_app
    
    def _load_token_cache(self, user_id: str) -> msal.SerializableTokenCache:
        """Load MSAL token cache for a user from TokenManager"""
        cache = msal.SerializableTokenCache()
        token_data = self.token_manager.get_token(user_id)
        
        if token_data and 'msal_cache' in token_data:
            try:
                cache.deserialize(token_data['msal_cache'])
                logger.debug(f"Loaded MSAL token cache for user {user_id}")
            except Exception as e:
                logger.warning(f"Failed to deserialize MSAL cache for user {user_id}: {str(e)}")
        
        return cache
    
    def _save_token_cache(self, user_id: str, cache: msal.SerializableTokenCache) -> None:
        """Save MSAL token cache for a user to TokenManager"""
        if cache.has_state_changed:
            token_data = self.token_manager.get_token(user_id) or {}
            token_data['msal_cache'] = cache.serialize()
            self.token_manager.store_token(user_id, token_data)
            logger.debug(f"Saved MSAL token cache for user {user_id}")
    
    def get_auth_url(self, user_id: Optional[str] = None) -> str:
        """Get Microsoft OAuth authorization URL using MSAL
        
        Args:
            user_id: Optional user ID to associate with the auth flow
        """
        try:
            cache = self._load_token_cache(user_id) if user_id else None
            auth_app = self._get_msal_app(cache)
            
            # Initiate auth code flow
            flow = auth_app.initiate_auth_code_flow(
                scopes=self.scopes,
                redirect_uri=self.redirect_uri
            )
            
            # Store the flow for later use in get_token_from_code
            self._current_flow = flow  # Store as instance variable for base class compatibility
            if user_id:
                self._auth_flow_cache[user_id] = flow
            
            auth_url = flow.get('auth_uri')
            if not auth_url:
                raise ValueError("MSAL did not return an authorization URL")
            
            logger.debug(f"Generated auth URL for Microsoft OAuth using MSAL")
            return auth_url
        except Exception as e:
            logger.error(f"Failed to generate auth URL: {str(e)}")
            raise
    
    def get_token_from_code(self, code: str, user_id: Optional[str] = None, 
                           flow: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Exchange authorization code for access token using MSAL
        
        Args:
            code: Authorization code from OAuth callback
            user_id: Optional user ID (if not provided, will try to extract from token)
            flow: Optional flow dict (if not provided, uses stored flow from get_auth_url)
        """
        try:
            # Try to determine user_id from stored flows if not provided
            if not user_id and not flow:
                # Try to find user_id from flow cache
                for uid, cached_flow in self._auth_flow_cache.items():
                    if uid != "temp_flow":
                        user_id = uid
                        flow = cached_flow
                        break
            
            # Use provided flow, cached flow, or current flow
            if not flow:
                if user_id:
                    flow = self._auth_flow_cache.pop(user_id, None) or self._current_flow
                else:
                    flow = self._current_flow
            
            if not flow:
                raise ValueError("No auth flow found. Call get_auth_url first.")
            
            cache = self._load_token_cache(user_id) if user_id else msal.SerializableTokenCache()
            auth_app = self._get_msal_app(cache)
            
            # MSAL's acquire_token_by_auth_code_flow expects the flow dict and request parameters
            # The request should contain the authorization code and any other query parameters
            # For desktop apps, we construct a simple dict with the code
            request_dict = {'code': code}
            
            # Acquire token by auth code flow
            # Note: MSAL will handle the token exchange internally
            result = auth_app.acquire_token_by_auth_code_flow(flow, request_dict)
            
            if "error" in result:
                error_msg = result.get("error_description", result.get("error", "Unknown error"))
                logger.error(f"MSAL token acquisition failed: {error_msg}")
                raise RuntimeError(f"Token acquisition failed: {error_msg}")
            
            # Try to extract user_id from token if not provided
            if not user_id and 'id_token_claims' in result:
                # Extract user principal name or email from ID token
                claims = result.get('id_token_claims', {})
                user_id = claims.get('preferred_username') or claims.get('upn') or claims.get('email')
            
            # Save token cache
            if user_id:
                self._save_token_cache(user_id, cache)
                # Also store the token data in our format for compatibility
                token_data = {
                    'access_token': result.get('access_token'),
                    'refresh_token': result.get('refresh_token'),
                    'expires_in': result.get('expires_in', 3600),
                    'token_type': result.get('token_type', 'Bearer'),
                    'scope': result.get('scope'),
                    'id_token': result.get('id_token'),
                    'msal_cache': cache.serialize() if cache.has_state_changed else None
                }
                # Remove None values but preserve msal_cache
                token_data = {k: v for k, v in token_data.items() if v is not None or k == 'msal_cache'}
                if 'msal_cache' in token_data and token_data['msal_cache'] is None:
                    # If cache didn't change, try to get existing cache from stored token
                    existing_token = self.token_manager.get_token(user_id)
                    if existing_token and 'msal_cache' in existing_token:
                        token_data['msal_cache'] = existing_token['msal_cache']
                self.token_manager.store_token(user_id, token_data)
            
            # Clear current flow after use
            self._current_flow = None
            
            logger.info("Successfully obtained token from authorization code using MSAL")
            return result
        except Exception as e:
            logger.error(f"Failed to get token from code: {str(e)}")
            raise
    
    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired access token using MSAL"""
        # Note: With MSAL, we typically use acquire_token_silent instead
        # This method is kept for compatibility with the OAuthProvider interface
        try:
            cache = msal.SerializableTokenCache()
            auth_app = self._get_msal_app(cache)
            
            # MSAL doesn't have a direct refresh_token method for ConfidentialClientApplication
            # Instead, we use acquire_token_silent with accounts
            # For this compatibility method, we'll use the old approach
            # In practice, get_valid_token should be used instead
            logger.warning("refresh_token() called directly. Consider using get_valid_token() with MSAL.")
            
            # Fallback to manual refresh if needed
            token_url = f"{self.authority}/oauth2/v2.0/token"
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(self.scopes)
            }
            
            response = requests.post(token_url, data=data)
            response.raise_for_status()
            token_data = response.json()
            logger.info("Successfully refreshed access token")
            return token_data
        except Exception as e:
            logger.error(f"Failed to refresh token: {str(e)}")
            raise
    
    def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Get user information from Microsoft Graph API"""
        headers = {"Authorization": f"Bearer {access_token}"}
        
        try:
            response = requests.get(f"{self.graph_url}/me", headers=headers)
            response.raise_for_status()
            user_info = response.json()
            logger.info("Successfully retrieved user info from Microsoft Graph")
            return user_info
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get user info: {str(e)}")
            raise
    
    def get_valid_token(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a valid access token using MSAL's acquire_token_silent (automatically refreshes if needed)"""
        try:
            # First check if we have a token for this user
            token_data = self.token_manager.get_token(user_id)
            if not token_data:
                # No token at all - this user_id doesn't have tokens for this provider
                return None
            
            # Verify this token belongs to Microsoft provider
            if not MicrosoftToken.verify_for_provider_type(token_data):
                return None

            # If the stored token is still fresh, return it directly without calling
            # acquire_token_silent — which can overwrite a valid v1 compact token with
            # a JWT that has the wrong audience for Graph API.
            acquired_at = token_data.get('acquired_at', 0)
            expires_in = token_data.get('expires_in', 3600)
            if acquired_at and time.time() < acquired_at + expires_in - 300:
                if token_data.get('access_token'):
                    logger.info(f"Using fresh stored token for {user_id} (expires in {int(acquired_at + expires_in - time.time())}s)")
                    return token_data

            # Try to load MSAL cache
            cache = self._load_token_cache(user_id)
            auth_app = self._get_msal_app(cache)
            
            # Get accounts from cache
            accounts = auth_app.get_accounts()
            
            if accounts:
                # Try to acquire token silently (will refresh if needed)
                result = auth_app.acquire_token_silent(
                    scopes=self.scopes,
                    account=accounts[0]
                )
                silent_token = result.get('access_token') if result else None
                logger.info(f"acquire_token_silent for {user_id}: result={'error' if result and 'error' in result else ('token' if silent_token else 'None')}, prefix={silent_token[:20] if silent_token else None}")

                if result is not None and "error" not in result and silent_token:
                    # Save updated cache
                    self._save_token_cache(user_id, cache)

                    # Store token data in our format for compatibility
                    existing_msal_cache = token_data.get('msal_cache')
                    token_data = {
                        'access_token': silent_token,
                        'refresh_token': result.get('refresh_token'),
                        'expires_in': result.get('expires_in', 3600),
                        'token_type': result.get('token_type', 'Bearer'),
                        'scope': result.get('scope'),
                        'id_token': result.get('id_token'),
                        'msal_cache': cache.serialize() if cache.has_state_changed else existing_msal_cache,
                        'acquired_at': time.time(),
                    }
                    # Remove None values
                    token_data = {k: v for k, v in token_data.items() if v is not None}
                    self.token_manager.store_token(user_id, token_data)

                    logger.debug(f"Successfully acquired token silently for user {user_id}")
                    return token_data
                else:
                    if result and 'error' in result:
                        logger.info(f"acquire_token_silent error for {user_id}: {result.get('error_description', result.get('error'))}")
            else:
                logger.info(f"No MSAL accounts in cache for {user_id}, using stored token")
            
            # Fallback: try to use stored token if it has access_token
            if token_data and token_data.get('access_token'):
                logger.debug(f"Using stored token for user {user_id}")
                return token_data
            
            # No valid token for this provider
            return None
        except Exception as e:
            # Don't log as error - this might just be a token from a different provider
            logger.debug(f"Token check for user {user_id}: {str(e)}")
            return None 