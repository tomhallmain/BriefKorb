"""
Messages service for Microsoft Graph API operations
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
import requests
import time
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor, wait, Future

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from email_server.config import EmailServerConfig
from email_server.auth import MicrosoftOAuth, TokenManager
from email_server.providers.microsoft.microsoft import MicrosoftGraphProvider


class MessagesService:
    """Service for Microsoft Messages operations"""
    
    def __init__(self, user_id: str):
        """Initialize messages service for a user"""
        self.user_id = user_id
        self.base_url = "https://graph.microsoft.com/v1.0"
        
        # Load config
        app_dir = Path(__file__).parent.parent.parent
        config_path = app_dir / 'email_server' / 'config.yaml'
        
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found at {config_path}")
        
        self.config = EmailServerConfig.from_file(str(config_path))
        
        if not self.config.microsoft.enabled:
            raise ValueError("Microsoft Graph is not configured")
        
        # Initialize token manager and OAuth
        self.token_manager = TokenManager(storage_path=self.config.token_storage_path)
        self.microsoft_oauth = MicrosoftOAuth(
            client_id=self.config.microsoft.client_id,
            client_secret=self.config.microsoft.client_secret,
            tenant_id=self.config.microsoft.tenant_id or 'common',
            redirect_uri=self.config.microsoft.redirect_uri,
            token_manager=self.token_manager,
            scopes=self.config.microsoft.scopes
        )
        
        # Also initialize provider for message operations
        self.provider = MicrosoftGraphProvider(
            client_id=self.config.microsoft.client_id,
            client_secret=self.config.microsoft.client_secret,
            tenant_id=self.config.microsoft.tenant_id or 'common',
            redirect_uri=self.config.microsoft.redirect_uri,
            scopes=self.config.microsoft.scopes
        )
    
    def _get_headers(self, timezone: Optional[str] = None) -> Dict[str, str]:
        """Get request headers with authentication token"""
        token_data = self.microsoft_oauth.get_valid_token(self.user_id)
        if not token_data:
            raise ValueError(f"No valid token found for user {self.user_id}")
        
        access_token = token_data.get('access_token') or token_data.get('token')
        if not access_token:
            raise ValueError(f"No access token found for user {self.user_id}")
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        if timezone:
            headers['Prefer'] = f'outlook.timezone="{timezone}"'
        
        return headers
    
    def get_user_info(self) -> Dict[str, Any]:
        """Get user information including timezone"""
        token_data = self.microsoft_oauth.get_valid_token(self.user_id)
        if not token_data:
            raise ValueError(f"No valid token found for user {self.user_id}")
        
        access_token = token_data.get('access_token') or token_data.get('token')
        user = self.microsoft_oauth.get_user_info(access_token)
        return user
    
    def get_messages(self, mailbox: str = 'inbox', exclude_read: bool = True, 
                    max_messages: int = 1000, timezone: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get messages from a mailbox
        
        Args:
            mailbox: Mailbox name (e.g., 'inbox', 'junkemail')
            exclude_read: If True, only return unread messages
            max_messages: Maximum number of messages to retrieve
            timezone: User's timezone (IANA format)
            
        Returns:
            List of message dictionaries
        """
        headers = self._get_headers(timezone)
        
        # Build query parameters
        query_params = {
            '$select': 'subject,from,receivedDateTime',
            '$orderby': 'receivedDateTime DESC',
            '$top': str(min(max_messages, 1000))  # Graph API limit is 1000 per request
        }
        
        if exclude_read:
            query_params['$filter'] = 'receivedDateTime ge 1970-01-01 and isRead eq false'
        
        # Get messages with paging if needed
        messages = []
        url = f'{self.base_url}/me/mailfolders/{mailbox}/messages'
        
        while len(messages) < max_messages:
            response = requests.get(url, headers=headers, params=query_params)
            response.raise_for_status()
            
            data = response.json()
            if 'value' not in data:
                break
            
            messages.extend(data['value'])
            
            # Check if there are more pages
            if len(messages) >= max_messages:
                break
            
            next_link = data.get('@odata.nextLink')
            if not next_link:
                break
            
            # For next page, use the nextLink URL directly
            url = next_link
            query_params = {}  # Next link already has params
        
        return messages[:max_messages]
    
    def aggregate_messages_by_sender(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Aggregate messages by sender name
        
        Args:
            messages: List of message dictionaries
            
        Returns:
            List of aggregated message info dictionaries
        """
        message_data = {}
        
        for message in messages:
            try:
                from_info = message.get('from', {}).get('emailAddress', {})
                from_name = from_info.get('name', 'Unknown')
                from_address = from_info.get('address', '')
                subject = message.get('subject', '(No subject)')
                received_date = message.get('receivedDateTime', '')
                
                if from_name in message_data:
                    message_info = message_data[from_name]
                    message_info['count'] += 1
                else:
                    message_info = {
                        'fromName': from_name,
                        'fromAddress': from_address,
                        'subject': subject,
                        'lastReceivedDateTime': received_date,
                        'count': 1
                    }
                    message_data[from_name] = message_info
            except Exception as e:
                # Skip messages with invalid structure
                continue
        
        # Sort by count (descending), then by name
        message_data_list = sorted(
            sorted(message_data.values(), key=lambda m: m['fromName']),
            key=lambda m: m['count'],
            reverse=True
        )
        
        return message_data_list
    
    def mark_messages_as_read(self, sender_names: List[str], mailbox: str = 'inbox') -> bool:
        """Mark messages from specific senders as read
        
        Args:
            sender_names: List of sender names to mark as read
            mailbox: Mailbox name
            
        Returns:
            True if successful
        """
        try:
            # Get all messages from these senders
            messages = self.get_messages(mailbox=mailbox, exclude_read=False, max_messages=10000)
            
            # Filter messages by sender names
            message_ids = []
            for message in messages:
                from_name = message.get('from', {}).get('emailAddress', {}).get('name', '')
                if from_name in sender_names:
                    message_ids.append(message.get('id'))
            
            # Mark as read using provider
            if message_ids:
                return self.provider.mark_as_read(self.user_id, message_ids)
            
            return True
        except Exception as e:
            return False
    
    def delete_messages(self, sender_names: List[str], mailbox: str = 'inbox') -> bool:
        """Delete messages from specific senders
        
        Args:
            sender_names: List of sender names to delete messages from
            mailbox: Mailbox name
            
        Returns:
            True if successful
        """
        try:
            # Get all messages from these senders
            messages = self.get_messages(mailbox=mailbox, exclude_read=False, max_messages=10000)
            
            # Filter messages by sender names
            message_ids = []
            for message in messages:
                from_name = message.get('from', {}).get('emailAddress', {}).get('name', '')
                if from_name in sender_names:
                    message_ids.append(message.get('id'))
            
            # Delete using provider
            if message_ids:
                return self.provider.delete_messages(self.user_id, message_ids)
            
            return True
        except Exception as e:
            return False
    
    def block_senders(self, sender_names: List[str]) -> bool:
        """Create inbox rules to block messages from specific senders
        
        This creates Microsoft Graph inbox rules that automatically delete
        messages from the specified senders. Uses parallel processing for efficiency.
        
        Args:
            sender_names: List of sender names to block
            
        Returns:
            True if successful
        """
        if not sender_names:
            return True
        
        try:
            headers = self._get_headers()
            failed_count = 0
            
            def create_block_rule(sender_name: str) -> bool:
                """Create a single block rule with retry logic"""
                rule_data = {
                    'displayName': f'Delete messages from "{sender_name}"',
                    'sequence': '2',
                    'isEnabled': True,
                    'conditions': {
                        'senderContains': [sender_name]
                    },
                    'actions': {
                        'delete': True,
                        'stopProcessingRules': True
                    }
                }
                
                def make_request():
                    return requests.post(
                        f'{self.base_url}/me/mailFolders/inbox/messageRules',
                        headers=headers,
                        json=rule_data
                    )
                
                # Retry logic (similar to provider methods)
                response = None
                retries = 0
                max_retries = 3
                retry_delay = 2.0
                
                while retries < max_retries:
                    try:
                        response = make_request()
                        if response is not None and response.status_code < 400:
                            return True
                    except Exception as e:
                        pass
                    
                    retries += 1
                    if retries < max_retries:
                        time.sleep(retry_delay * (2 ** (retries - 1)))
                
                return False
            
            # Process blocking rules in parallel using ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(10, len(sender_names))) as executor:
                futures: List[Future[bool]] = [
                    executor.submit(create_block_rule, sender_name)
                    for sender_name in sender_names
                ]
                
                # Wait for all operations to complete
                wait(futures)
                
                # Check results
                for future in futures:
                    if not future.result():
                        failed_count += 1
            
            return failed_count == 0
        except Exception as e:
            return False
