"""
Unified Email Server Interface

This module provides a unified interface for interacting with different email providers
(Microsoft Graph API and Gmail API) through a consistent API.
"""

from abc import ABC, abstractmethod
from email.utils import parseaddr
from typing import Any, List, Dict, Optional, Union, TYPE_CHECKING
from datetime import datetime
from dataclasses import dataclass
from .config import EmailServerConfig, create_default_config
from .utils.logger import setup_logger
from .utils.datetime_compat import normalize_received_at_utc
from .auth import TokenManager

# Set up logger
logger = setup_logger('email_server')

if TYPE_CHECKING:
    from .providers.base import EmailProvider


@dataclass
class AuthenticatedProvider:
    """Represents an authenticated provider instance with a specific user"""
    provider: 'EmailProvider'
    provider_name: str
    user_id: str
    user_info: Optional[Dict] = None
    
    def get_user_email(self) -> str:
        """Get the user's email address from user_info or user_id"""
        if self.user_info:
            # Microsoft returns 'mail' or 'userPrincipalName'
            email = self.user_info.get('mail') or self.user_info.get('userPrincipalName')
            if email:
                return email
            # Gmail returns 'emailAddress'
            email = self.user_info.get('emailAddress')
            if email:
                return email
        # Fallback to user_id (which should be the email)
        return self.user_id

class EmailMessage:
    """Unified email message representation"""
    def __init__(self, 
                 id: str,
                 subject: str,
                 sender: str,
                 recipients: List[str],
                 received_date: datetime,
                 body: str,
                 is_read: bool,
                 provider: str):
        self.id = id
        self.subject = subject
        self.sender = sender
        self.recipients = recipients
        self.received_date = received_date
        self.body = body
        self.is_read = is_read
        self.provider = provider

class EmailProvider(ABC):
    """Abstract base class for email providers"""
    
    @abstractmethod
    def authenticate(self, user_id: str) -> bool:
        """Authenticate with the email provider for a specific user"""
        pass
    
    @abstractmethod
    def get_messages(self,
                    user_id: str,
                    folder: str = 'inbox',
                    max_messages: int = 100,
                    unread_only: bool = False) -> List[EmailMessage]:
        """Get messages from the specified folder for a user"""
        pass

    @abstractmethod
    def get_message(self, user_id: str, message_id: str) -> Optional[EmailMessage]:
        """Get a single message (including body) by id for a user.

        Returns None if the message doesn't exist or can't be fetched --
        callers shouldn't need to distinguish "deleted" from "transient
        error" here.
        """
        pass

    @abstractmethod
    def send_message(self,
                    user_id: str,
                    to: Union[str, List[str]],
                    subject: str,
                    body: str,
                    cc: Optional[List[str]] = None,
                    bcc: Optional[List[str]] = None) -> bool:
        """Send an email message for a user"""
        pass
    
    @abstractmethod
    def mark_as_read(self, user_id: str, message_ids: List[str]) -> bool:
        """Mark messages as read for a user"""
        pass
    
    @abstractmethod
    def delete_messages(self, user_id: str, message_ids: List[str]) -> bool:
        """Delete messages for a user"""
        pass

    @abstractmethod
    def block_senders(self, user_id: str, sender_names: List[str]) -> bool:
        """Block future mail from the given senders for a user, where the
        provider supports it server-side.

        Not every provider has an equivalent capability -- a provider
        without one returns False rather than raising, so callers can
        treat "not supported" the same as "failed" without special-casing
        providers themselves.
        """
        pass

class UnifiedEmailServer:
    """Main class for the unified email server"""

    def __init__(self, config: Optional[EmailServerConfig] = None, config_path: Optional[str] = None):
        """Initialize the email server with configuration"""
        self._providers: Dict[str, EmailProvider] = {}

        if config is None and config_path is None:
            config_path = "email_server_config.json"
            config = create_default_config(config_path)
            logger.info(f"Created default configuration at {config_path}")
        elif config_path is not None:
            config = EmailServerConfig.from_file(config_path)
            logger.info(f"Loaded configuration from {config_path}")

        config.validate()

        # Create shared TokenManager instance for all providers in this server instance
        self.token_manager = TokenManager(storage_path=config.token_storage_path)
        logger.info(f"Created shared TokenManager with storage path: {config.token_storage_path}")

        self.entity_graph_manager = self._init_entity_graph_manager(config.token_storage_path)

        self._initialize_providers(config)

    @staticmethod
    def _init_entity_graph_manager(token_storage_path: str):
        try:
            import os
            from entity_graph import EntityGraphManager
            storage_dir = os.path.join(token_storage_path, "entity_graph")
            mgr = EntityGraphManager(storage_dir)
            logger.info(f"Entity graph manager initialized at {storage_dir}")
            return mgr
        except Exception as e:
            logger.warning(f"Entity graph manager unavailable: {e}")
            return None
    
    def _initialize_providers(self, config: EmailServerConfig) -> None:
        """Initialize providers based on configuration"""
        if config.microsoft.enabled:
            logger.info("Initializing Microsoft Graph provider")
            self.register_provider('microsoft', MicrosoftGraphProvider(
                client_id=config.microsoft.client_id,
                client_secret=config.microsoft.client_secret,
                tenant_id=config.microsoft.tenant_id,
                redirect_uri=config.microsoft.redirect_uri,
                scopes=config.microsoft.scopes,
                token_manager=self.token_manager
            ))
        
        if config.gmail.enabled:
            logger.info("Initializing Gmail provider")
            self.register_provider('gmail', GmailProvider(
                credentials_path=config.gmail.credentials_path,
                redirect_uri=config.gmail.redirect_uri,
                token_manager=self.token_manager
            ))
    
    def register_provider(self, name: str, provider: EmailProvider) -> None:
        """Register an email provider"""
        self._providers[name] = provider
        logger.info(f"Registered provider: {name}")
    
    def get_provider(self, name: str) -> Optional[EmailProvider]:
        """Get a registered provider by name"""
        return self._providers.get(name)
    
    def handle_auth_callback(self, provider_name: str, user_id: str, auth_code: str) -> bool:
        """Handle OAuth callback and store tokens for a user"""
        provider = self.get_provider(provider_name)
        if not provider:
            logger.error(f"Provider not found: {provider_name}")
            return False
            
        try:
            # Get token from auth code
            token_data = provider.oauth.get_token_from_code(auth_code)
            # Store token for the user
            provider.token_manager.store_token(user_id, token_data)
            # Get and store user info. token_data's access-token key is
            # provider-shape-dependent (Microsoft: 'access_token', Gmail:
            # 'token'), so use TokenManager's provider-agnostic accessor
            # rather than assuming Microsoft's shape.
            access_token = provider.token_manager.get_valid_token(token_data)
            if not access_token:
                logger.error(f"No usable access token in auth response for user {user_id} with provider {provider_name}")
                return False
            user_info = provider.oauth.get_user_info(access_token)
            provider.token_manager.store_user_info(user_id, user_info)
            logger.info(f"Successfully handled auth callback for user {user_id} with provider {provider_name}")
            return True
        except Exception as e:
            logger.error(f"Error handling auth callback for user {user_id} with provider {provider_name}: {str(e)}")
            return False
    
    def get_authenticated_providers(self, provider_name: Optional[str] = None) -> List[AuthenticatedProvider]:
        """Get list of authenticated provider instances with their users
        
        Returns:
            List of AuthenticatedProvider instances representing provider+user combinations
        """
        authenticated = []
        
        providers_to_check = [provider_name] if provider_name else list(self._providers.keys())
        logger.debug(f"Checking authentication for providers: {providers_to_check}")
        
        for pname in providers_to_check:
            if pname not in self._providers:
                logger.debug(f"Provider {pname} not found in registered providers")
                continue
                
            provider_instance = self._providers[pname]
            # Get all user IDs that have tokens for this provider
            user_ids = provider_instance.token_manager.get_all_user_ids()
            logger.debug(f"Provider {pname}: Found {len(user_ids)} user IDs with tokens: {user_ids}")
            
            # Create AuthenticatedProvider for each valid user
            for user_id in user_ids:
                # Only check if token exists for this provider's token manager
                if not provider_instance.token_manager.has_token(user_id):
                    continue
                
                # Try to authenticate (this will also retrieve/cache user_info if needed)
                if provider_instance.authenticate(user_id):
                    logger.info(f"Successfully authenticated {pname} user {user_id}")
                    # Get user_info (should be cached now)
                    user_info = provider_instance.token_manager.get_user_info(user_id)
                    authenticated.append(AuthenticatedProvider(
                        provider=provider_instance,
                        provider_name=pname,
                        user_id=user_id,
                        user_info=user_info
                    ))
                else:
                    logger.debug(f"Authentication failed for {pname} user {user_id}")
        
        logger.debug(f"Total authenticated providers: {len(authenticated)}")
        return authenticated
    
    def get_authenticated_users(self, provider: Optional[str] = None) -> Dict[str, List[str]]:
        """Get list of authenticated user IDs for each provider (legacy method)
        
        Returns:
            Dict mapping provider names to lists of user IDs that have valid tokens
        """
        authenticated = {}
        auth_providers = self.get_authenticated_providers(provider)
        
        for auth_prov in auth_providers:
            if auth_prov.provider_name not in authenticated:
                authenticated[auth_prov.provider_name] = []
            authenticated[auth_prov.provider_name].append(auth_prov.user_id)
        
        return authenticated
    
    def get_user_messages(self,
                         providers: Optional[Union[EmailProvider, List[EmailProvider], List[AuthenticatedProvider]]] = None,
                         folder: str = 'inbox',
                         max_messages: int = 100,
                         unread_only: bool = False) -> List[EmailMessage]:
        """Get messages from specified providers or all authenticated providers
        
        Args:
            providers: Can be:
                - None: Use all authenticated providers
                - EmailProvider instance: Use this provider (must be authenticated)
                - List[EmailProvider]: Use these providers
                - List[AuthenticatedProvider]: Use these authenticated provider+user combinations
        """
        messages = []
        
        if providers is None:
            # Get all authenticated providers
            auth_providers = self.get_authenticated_providers()
        elif isinstance(providers, list):
            if len(providers) > 0 and isinstance(providers[0], AuthenticatedProvider):
                # List of AuthenticatedProvider
                auth_providers = providers
            else:
                # List of EmailProvider - convert to AuthenticatedProvider
                auth_providers = []
                for provider_instance in providers:
                    # Find which provider this is
                    provider_name = None
                    for pname, pinst in self._providers.items():
                        if pinst is provider_instance:
                            provider_name = pname
                            break
                    if not provider_name:
                        continue
                    
                    # Get authenticated users for this provider
                    user_ids = provider_instance.token_manager.get_all_user_ids()
                    for user_id in user_ids:
                        if provider_instance.token_manager.has_token(user_id) and provider_instance.authenticate(user_id):
                            user_info = provider_instance.token_manager.get_user_info(user_id)
                            auth_providers.append(AuthenticatedProvider(
                                provider=provider_instance,
                                provider_name=provider_name,
                                user_id=user_id,
                                user_info=user_info
                            ))
        elif isinstance(providers, EmailProvider):
            # Single provider - find authenticated users
            provider_name = None
            for pname, pinst in self._providers.items():
                if pinst is providers:
                    provider_name = pname
                    break
            if provider_name:
                auth_providers = self.get_authenticated_providers(provider_name)
            else:
                auth_providers = []
        else:
            # Legacy: try to treat as AuthenticatedProvider
            auth_providers = [providers] if isinstance(providers, AuthenticatedProvider) else []
        
        # Get messages from each authenticated provider
        for auth_prov in auth_providers:
            try:
                provider_messages = auth_prov.provider.get_messages(
                    user_id=auth_prov.user_id,
                    folder=folder,
                    max_messages=max_messages,
                    unread_only=unread_only
                )
                messages.extend(provider_messages)
                logger.info(f"Retrieved {len(provider_messages)} messages from {auth_prov.provider_name} for user {auth_prov.user_id}")
            except Exception as e:
                logger.error(f"Failed to get messages from {auth_prov.provider_name} for user {auth_prov.user_id}: {e}")

        for m in messages:
            m.received_date = normalize_received_at_utc(m.received_date)

        return sorted(messages, key=lambda x: x.received_date, reverse=True)

    def get_message_digest(
        self,
        folder: str = 'inbox',
        unread_only: bool = True,
        max_messages: int = 1000,
        messages: Optional[List[EmailMessage]] = None,
    ) -> List[Dict[str, Any]]:
        """Aggregate messages from every authenticated provider into one row
        per sender, tagged with the source provider.

        Built on get_user_messages(), so it's provider-agnostic *and*
        provider-count-agnostic: it covers however many providers are
        registered/authenticated with no per-provider branching here or in
        any caller -- adding a third provider means registering it (see
        _initialize_providers), not writing new aggregation code.

        Pass `messages` (e.g. a list already fetched via get_user_messages())
        to aggregate from it directly instead of fetching again -- useful for
        callers that also want the raw per-message list themselves (a
        message-reading view, entity extraction) and shouldn't pay for two
        live fetches in one request.

        Deliberately does not apply sender-impact/spam categorization --
        that lives in SenderCategorizationManager (email_client.utils.
        sender_categorization), a layer above this one. Callers that want it
        annotate this method's output themselves (see
        django_app.messages.services.annotate_sender_impact).
        """
        if messages is None:
            messages = self.get_user_messages(folder=folder, unread_only=unread_only, max_messages=max_messages)

        buckets: Dict[tuple, Dict[str, Any]] = {}
        for message in messages:
            # EmailMessage.sender isn't consistently shaped across providers
            # (Microsoft's provider strips it down to a bare address; Gmail's
            # keeps the raw, possibly-"Name <addr>" From header) -- parseaddr
            # handles both forms uniformly rather than needing a per-provider
            # extraction path.
            name, address = parseaddr(message.sender or '')
            name = name or 'Unknown'
            key = (message.provider, name)
            last_received = message.received_date.isoformat() if message.received_date else ''
            message_summary = {
                'id': message.id,
                'subject': message.subject,
                'lastReceivedDateTime': last_received,
                'isRead': message.is_read,
            }
            if key in buckets:
                buckets[key]['count'] += 1
                buckets[key]['messages'].append(message_summary)
            else:
                buckets[key] = {
                    'fromName': name,
                    'fromAddress': address,
                    'subject': message.subject,
                    'lastReceivedDateTime': last_received,
                    'count': 1,
                    'provider': message.provider,
                    'messages': [message_summary],
                }

        return sorted(
            sorted(buckets.values(), key=lambda m: m['fromName']),
            key=lambda m: m['count'],
            reverse=True,
        )

    def get_message(self, user_id: str, provider_name: str, message_id: str) -> Optional[EmailMessage]:
        """Get a single message (including body) for a user via the named provider."""
        provider = self.get_provider(provider_name)
        if not provider:
            logger.error(f"Provider not found: {provider_name}")
            return None

        if not provider.authenticate(user_id):
            logger.warning(f"Authentication failed for user {user_id} with provider {provider_name}")
            return None

        return provider.get_message(user_id, message_id)

    def send_message(self,
                    user_id: str,
                    provider_name: str,
                    to: Union[str, List[str]],
                    subject: str,
                    body: str,
                    cc: Optional[List[str]] = None,
                    bcc: Optional[List[str]] = None) -> bool:
        """Send a message using the specified provider for a user"""
        provider = self.get_provider(provider_name)
        if not provider:
            logger.error(f"Provider not found: {provider_name}")
            return False
            
        if not provider.authenticate(user_id):
            logger.warning(f"Authentication failed for user {user_id} with provider {provider_name}")
            return False
            
        success = provider.send_message(
            user_id=user_id,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc
        )
        
        if success:
            logger.info(f"Successfully sent message to {to} using {provider_name} for user {user_id}")
        else:
            logger.error(f"Failed to send message to {to} using {provider_name} for user {user_id}")
        
        return success
    
    def mark_messages_as_read(self,
                            user_id: str,
                            provider_name: str,
                            message_ids: List[str]) -> bool:
        """Mark messages as read for a user using the specified provider"""
        provider = self.get_provider(provider_name)
        if not provider:
            logger.error(f"Provider not found: {provider_name}")
            return False
            
        if not provider.authenticate(user_id):
            logger.warning(f"Authentication failed for user {user_id} with provider {provider_name}")
            return False
            
        success = provider.mark_as_read(user_id, message_ids)
        
        if success:
            logger.info(f"Successfully marked {len(message_ids)} messages as read for user {user_id} with provider {provider_name}")
        else:
            logger.error(f"Failed to mark messages as read for user {user_id} with provider {provider_name}")
        
        return success
    
    def extract_entities(self, messages: List[EmailMessage]) -> int:
        """Run entity extraction over a list of messages. Returns job posting count.

        Returns 0 silently if the entity graph manager is unavailable (e.g. rdflib
        not installed) so callers never need to guard against None.
        """
        if self.entity_graph_manager is None:
            return 0
        return self.entity_graph_manager.process_messages(messages)

    def delete_user_messages(self,
                           user_id: str,
                           provider_name: str,
                           message_ids: List[str]) -> bool:
        """Delete messages for a user using the specified provider"""
        provider = self.get_provider(provider_name)
        if not provider:
            logger.error(f"Provider not found: {provider_name}")
            return False
            
        if not provider.authenticate(user_id):
            logger.warning(f"Authentication failed for user {user_id} with provider {provider_name}")
            return False
            
        success = provider.delete_messages(user_id, message_ids)

        if success:
            logger.info(f"Successfully deleted {len(message_ids)} messages for user {user_id} with provider {provider_name}")
        else:
            logger.error(f"Failed to delete messages for user {user_id} with provider {provider_name}")

        return success

    def block_senders(self, user_id: str, provider_name: str, sender_names: List[str]) -> bool:
        """Block future mail from the given senders for a user using the
        specified provider, where that provider supports it (see
        EmailProvider.block_senders -- not every provider has an
        equivalent capability; an unsupported provider returns False here
        the same as any other failure)."""
        provider = self.get_provider(provider_name)
        if not provider:
            logger.error(f"Provider not found: {provider_name}")
            return False

        if not provider.authenticate(user_id):
            logger.warning(f"Authentication failed for user {user_id} with provider {provider_name}")
            return False

        success = provider.block_senders(user_id, sender_names)

        if success:
            logger.info(f"Successfully blocked {len(sender_names)} sender(s) for user {user_id} with provider {provider_name}")
        else:
            logger.error(f"Failed to block senders for user {user_id} with provider {provider_name}")

        return success

# Import providers at the end to avoid circular imports
# These imports must come after EmailProvider and EmailMessage are defined
from .providers.microsoft.microsoft import MicrosoftGraphProvider
from .providers.gmail.gmail import GmailProvider 