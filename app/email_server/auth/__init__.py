"""
Authentication module for email providers
"""

from typing import Dict, Any, Optional, Union
import json
from pathlib import Path
from abc import ABC, abstractmethod
from ..utils.logger import setup_logger

# Set up logger
logger = setup_logger('email_server.auth')

class BaseToken(ABC):
    """Abstract base class for OAuth tokens with provider type verification"""
    
    @classmethod
    @abstractmethod
    def verify_for_provider_type(cls, token_data: Dict[str, Any]) -> bool:
        """Verify if a token dictionary belongs to this provider type
        
        Args:
            token_data: Token dictionary to verify
            
        Returns:
            True if the token belongs to this provider type, False otherwise
        """
        pass
    
    @classmethod
    def get_provider_name(cls) -> str:
        """Get the name of the provider this token type belongs to"""
        return cls.__name__.replace('Token', '').lower()


class MicrosoftToken(BaseToken):
    """Microsoft OAuth token representation"""
    
    @classmethod
    def verify_for_provider_type(cls, token_data: Dict[str, Any]) -> bool:
        """Verify if a token dictionary is a Microsoft token"""
        if not isinstance(token_data, dict):
            return False
        
        # Microsoft tokens have 'access_token' key and may have 'msal_cache'
        # They should NOT have Google-specific keys like 'token' and 'token_uri' together
        has_access_token = 'access_token' in token_data
        has_msal_cache = 'msal_cache' in token_data
        has_google_structure = 'token' in token_data and 'token_uri' in token_data
        
        # It's a Microsoft token if it has access_token and doesn't have Google structure
        return has_access_token and not has_google_structure


class GmailToken(BaseToken):
    """Gmail OAuth token representation"""
    
    @classmethod
    def verify_for_provider_type(cls, token_data: Dict[str, Any]) -> bool:
        """Verify if a token dictionary is a Gmail/Google token"""
        if not isinstance(token_data, dict):
            return False
        
        # Gmail tokens have 'token' and 'token_uri' keys
        # They should NOT have Microsoft-specific keys like 'access_token' with 'msal_cache'
        has_token = 'token' in token_data
        has_token_uri = 'token_uri' in token_data
        has_microsoft_structure = 'access_token' in token_data and 'msal_cache' in token_data
        
        # It's a Gmail token if it has token/token_uri and doesn't have Microsoft structure
        return has_token and has_token_uri and not has_microsoft_structure


class OAuthProvider(ABC):
    """Base class for OAuth authentication"""
    
    @abstractmethod
    def get_auth_url(self) -> str:
        """Get the authorization URL for the OAuth flow"""
        pass
    
    @abstractmethod
    def get_token_from_code(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token"""
        pass
    
    @abstractmethod
    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh an expired access token"""
        pass
    
    @abstractmethod
    def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Get user information using the access token"""
        pass

class TokenManager:
    """Manages OAuth tokens and user sessions with disk persistence"""
    
    def __init__(self, storage_path: str = "tokens"):
        self.storage_path = Path(storage_path)
        self._tokens: Dict[str, Dict] = {}
        self._user_info: Dict[str, Dict] = {}
        
        # Create storage directory if it doesn't exist
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Load existing tokens from disk
        self._load_from_disk()
        
        logger.info(f"Initialized TokenManager with storage path: {storage_path}")
    
    def _get_tokens_file(self) -> Path:
        """Get path to tokens file"""
        return self.storage_path / "tokens.json"
    
    def _get_user_info_file(self) -> Path:
        """Get path to user info file"""
        return self.storage_path / "user_info.json"
    
    def _load_from_disk(self):
        """Load tokens and user info from disk"""
        try:
            tokens_file = self._get_tokens_file()
            if tokens_file.exists():
                with open(tokens_file, 'r') as f:
                    loaded_tokens = json.load(f)
                # Validate that all keys are strings (JSON should ensure this, but be safe)
                self._tokens = {str(k): v for k, v in loaded_tokens.items() if isinstance(v, dict)}
                logger.debug(f"Loaded {len(self._tokens)} tokens from disk. User IDs: {list(self._tokens.keys())}")
            else:
                logger.debug(f"Tokens file does not exist: {tokens_file}")
                self._tokens = {}
            
            user_info_file = self._get_user_info_file()
            if user_info_file.exists():
                with open(user_info_file, 'r') as f:
                    loaded_user_info = json.load(f)
                # Validate that all keys are strings
                self._user_info = {str(k): v for k, v in loaded_user_info.items() if isinstance(v, dict)}
                logger.debug(f"Loaded {len(self._user_info)} user info records from disk. User IDs: {list(self._user_info.keys())}")
            else:
                logger.debug(f"User info file does not exist: {user_info_file}")
                self._user_info = {}
        except Exception as e:
            logger.error(f"Failed to load tokens from disk: {e}", exc_info=True)
            # Start with empty dicts if loading fails
            self._tokens = {}
            self._user_info = {}
    
    def _save_to_disk(self):
        """Save tokens and user info to disk"""
        try:
            tokens_file = self._get_tokens_file()
            with open(tokens_file, 'w') as f:
                json.dump(self._tokens, f, indent=2)
            
            user_info_file = self._get_user_info_file()
            with open(user_info_file, 'w') as f:
                json.dump(self._user_info, f, indent=2)
            
            logger.debug(f"Saved {len(self._tokens)} tokens and {len(self._user_info)} user info records to disk")
        except Exception as e:
            logger.error(f"Failed to save tokens to disk: {e}")
    
    def get_valid_token(self, token_data: Dict[str, Any]) -> Optional[str]:
        """Get a valid access token from token data, checking expiration"""
        if not token_data:
            return None
        
        # Check if token is expired (simplified check)
        # In a real implementation, you'd check expires_at timestamp
        access_token = token_data.get('access_token') or token_data.get('token')
        return access_token
    
    def store_token(self, user_id: str, token_data: Dict[str, Any]) -> None:
        """Store OAuth token data for a user"""
        try:
            self._tokens[user_id] = token_data
            self._save_to_disk()  # Persist to disk
            logger.info(f"Stored token for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to store token for user {user_id}: {str(e)}")
            raise
    
    def get_token(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get stored token data for a user"""
        try:
            # Ensure user_id is a string
            if not isinstance(user_id, str):
                logger.error(f"Invalid user_id type: {type(user_id)}, expected str. Value: {user_id}")
                return None
            return self._tokens.get(user_id)
        except Exception as e:
            logger.error(f"Failed to get token for user {user_id}: {str(e)}")
            return None
    
    def store_user_info(self, user_id: str, user_info: Dict[str, Any]) -> None:
        """Store user information"""
        try:
            self._user_info[user_id] = user_info
            self._save_to_disk()  # Persist to disk
            logger.info(f"Stored user info for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to store user info for user {user_id}: {str(e)}")
            raise
    
    def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get stored user information"""
        try:
            return self._user_info.get(user_id)
        except Exception as e:
            logger.error(f"Failed to get user info for user {user_id}: {str(e)}")
            return None
    
    def clear_user_data(self, user_id: str) -> None:
        """Clear all stored data for a user"""
        try:
            self._tokens.pop(user_id, None)
            self._user_info.pop(user_id, None)
            self._save_to_disk()  # Persist to disk
            logger.info(f"Cleared all data for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to clear data for user {user_id}: {str(e)}")
            raise
    
    def get_all_user_ids(self) -> list[str]:
        """Get list of all user IDs that have stored tokens"""
        # Filter to ensure all keys are strings (JSON keys should always be strings, but be safe)
        return [uid for uid in self._tokens.keys() if isinstance(uid, str)]
    
    def has_token(self, user_id: str) -> bool:
        """Check if a user has a stored token"""
        return user_id in self._tokens and self._tokens[user_id] is not None

# Import OAuth providers at the end to avoid circular imports
from .microsoft import MicrosoftOAuth
from .gmail import GmailOAuth

# Export token classes
__all__ = ['OAuthProvider', 'TokenManager', 'BaseToken', 'MicrosoftToken', 'GmailToken', 'MicrosoftOAuth', 'GmailOAuth']

__all__ = ['OAuthProvider', 'TokenManager', 'MicrosoftOAuth', 'GmailOAuth'] 