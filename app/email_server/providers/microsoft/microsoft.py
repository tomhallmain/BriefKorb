"""
Microsoft Graph API email provider implementation
"""

from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
import requests
import html as html_escape
import time
from concurrent.futures import ThreadPoolExecutor, wait, Future
from ...auth import MicrosoftOAuth, TokenManager
from ... import EmailProvider, EmailMessage
from ...utils.logger import setup_logger

# Set up logger
logger = setup_logger('email_server.providers.microsoft')

class MicrosoftGraphProvider(EmailProvider):
    """Microsoft Graph API email provider implementation"""
    
    def __init__(self, client_id: str, client_secret: str, tenant_id: str, redirect_uri: str, scopes: Optional[List[str]] = None, token_manager: Optional[TokenManager] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.redirect_uri = redirect_uri
        self.base_url = "https://graph.microsoft.com/v1.0"
        # Use provided token_manager or create new one (but should always be provided)
        self.token_manager = token_manager if token_manager is not None else TokenManager()
        logger.debug(f"MicrosoftGraphProvider using TokenManager with storage path: {self.token_manager.storage_path}")
        # Pass token_manager and scopes to avoid creating duplicate
        self.oauth = MicrosoftOAuth(client_id, client_secret, tenant_id, redirect_uri, self.token_manager, scopes=scopes)
        logger.info("Initialized Microsoft Graph provider")
    
    def authenticate(self, user_id: str) -> bool:
        """Authenticate with Microsoft Graph API and retrieve/cache user info if needed"""
        try:
            token_data = self.oauth.get_valid_token(user_id)
            if not token_data:
                # Don't log error - this user_id might not have tokens for this provider
                return False
            
            # Check if we have user_info cached, if not retrieve it
            user_info = self.token_manager.get_user_info(user_id)
            if not user_info:
                try:
                    # Get access token
                    access_token = token_data.get('access_token') or token_data.get('token')
                    if access_token:
                        # Retrieve user info from API
                        user_info = self.oauth.get_user_info(access_token)
                        if user_info:
                            # Cache it
                            self.token_manager.store_user_info(user_id, user_info)
                            logger.debug(f"Retrieved and cached user info for {user_id}")
                except Exception as e:
                    logger.warning(f"Could not retrieve user info for {user_id}: {e}")
                    # Continue anyway - authentication succeeded even if user_info fetch failed
            
            logger.debug(f"Successfully authenticated user {user_id}")
            return True
        except Exception as e:
            # Only log if it's a real error, not just "no token for this user"
            logger.debug(f"Authentication check for user {user_id}: {str(e)}")
            return False
    
    def _get_headers(self, user_id: str) -> Dict[str, str]:
        """Get headers for API requests"""
        token_data = self.oauth.get_valid_token(user_id)
        if not token_data:
            logger.error(f"No valid token found for user {user_id}")
            raise RuntimeError("Not authenticated")
        # Microsoft token data uses 'access_token' key
        access_token = token_data.get('access_token') or token_data.get('token')
        if not access_token:
            logger.error(f"No access token in token data for user {user_id}")
            raise RuntimeError("Invalid token data")
        logger.info(f"Using access token for {user_id}: present={bool(access_token)}, prefix={access_token[:10] if access_token else None}")
        return {
            'Authorization': f"Bearer {access_token}",
            'Content-Type': 'application/json'
        }
    
    def _retry_request(self, request_func: Callable, max_retries: int = 3, retry_delay: float = 2.0) -> Optional[requests.Response]:
        """Execute a request with retry logic and exponential backoff
        
        Args:
            request_func: A callable that returns a requests.Response
            max_retries: Maximum number of retry attempts
            retry_delay: Initial delay between retries in seconds
            
        Returns:
            Response object if successful, None if all retries failed
        """
        response = None
        retries = 0
        
        while retries < max_retries:
            try:
                response = request_func()
                # Check if request was successful (status code < 400)
                if response is not None and response.status_code < 400:
                    return response
            except Exception as e:
                logger.debug(f"Request failed (attempt {retries + 1}/{max_retries}): {e}")
            
            retries += 1
            if retries < max_retries:
                # Exponential backoff: delay increases with each retry
                delay = retry_delay * (2 ** (retries - 1))
                logger.debug(f"Retrying in {delay:.1f} seconds...")
                time.sleep(delay)
        
        return response
    
    def get_messages(self, user_id: str, folder: str = 'inbox', unread_only: bool = False, max_messages: int = 10) -> List[EmailMessage]:
        """Get messages from specified folder
        
        Note: user_id is used to retrieve the token, but the API call uses /me
        which automatically resolves to the authenticated user for that token.
        """
        try:
            headers = self._get_headers(user_id)
            filter_query = "isRead eq false" if unread_only else None
            
            # First, get message list with basic info
            response = requests.get(
                f"{self.base_url}/me/mailFolders/{folder}/messages",
                headers=headers,
                params={
                    '$top': max_messages,
                    '$filter': filter_query,
                    '$select': 'id,subject,from,receivedDateTime,isRead',
                    '$orderby': 'receivedDateTime desc'
                }
            )
            if not response.ok:
                logger.error(f"Graph API {response.status_code} response body: {response.text[:500]}")
            response.raise_for_status()

            message_list = response.json().get('value', [])
            messages = []
            
            # Fetch full message details including body for each message
            for msg in message_list:
                try:
                    # Get full message with body content
                    msg_response = requests.get(
                        f"{self.base_url}/me/messages/{msg['id']}",
                        headers=headers,
                        params={
                            '$select': 'id,subject,from,toRecipients,receivedDateTime,isRead,body,bodyPreview'
                        }
                    )
                    msg_response.raise_for_status()
                    full_msg = msg_response.json()
                    
                    # Extract body - prefer HTML, fallback to bodyPreview (plain text)
                    body = ''
                    if 'body' in full_msg:
                        body_content = full_msg['body']
                        body = body_content.get('content', '')
                        content_type = body_content.get('contentType', 'text')
                        # If it's plain text, convert to HTML by escaping and preserving newlines
                        if content_type == 'text':
                            body = html_escape.escape(body).replace('\n', '<br>')
                    elif 'bodyPreview' in full_msg:
                        # Fallback to plain text preview
                        body = html_escape.escape(full_msg['bodyPreview']).replace('\n', '<br>')
                    
                    # Extract sender
                    sender_info = full_msg.get('from', {})
                    sender = sender_info.get('emailAddress', {}).get('address', 'Unknown') if sender_info else 'Unknown'
                    
                    # Extract recipients
                    recipients = []
                    if 'toRecipients' in full_msg:
                        recipients = [r.get('emailAddress', {}).get('address', '') for r in full_msg['toRecipients']]
                    
                    # Parse date
                    received_date = datetime.fromisoformat(full_msg['receivedDateTime'].replace('Z', '+00:00'))
                    
                    messages.append(EmailMessage(
                        id=full_msg['id'],
                        subject=full_msg.get('subject', '(No Subject)'),
                        sender=sender,
                        recipients=recipients,
                        received_date=received_date,
                        body=body,
                        is_read=full_msg.get('isRead', False),
                        provider='microsoft'
                    ))
                except Exception as e:
                    logger.warning(f"Failed to get full message details for {msg.get('id', 'unknown')}: {e}")
                    # Skip this message if we can't get full details
                    continue
            
            logger.info(f"Retrieved {len(messages)} messages from {folder} for user {user_id}")
            return messages
        except Exception as e:
            logger.error(f"Failed to get messages for user {user_id}: {str(e)}")
            return []
    
    def send_message(self, user_id: str, to: str, subject: str, body: str, cc: Optional[str] = None, bcc: Optional[str] = None) -> bool:
        """Send an email message"""
        try:
            headers = self._get_headers(user_id)
            message = {
                'message': {
                    'subject': subject,
                    'body': {
                        'contentType': 'HTML',
                        'content': body
                    },
                    'toRecipients': [{'emailAddress': {'address': to}}]
                }
            }
            
            if cc:
                message['message']['ccRecipients'] = [{'emailAddress': {'address': cc}}]
            if bcc:
                message['message']['bccRecipients'] = [{'emailAddress': {'address': bcc}}]
            
            # Use /me endpoint which resolves to the authenticated user for the token
            response = requests.post(
                f"{self.base_url}/me/sendMail",
                headers=headers,
                json=message
            )
            response.raise_for_status()
            
            logger.info(f"Successfully sent message to {to} from user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message for user {user_id}: {str(e)}")
            return False
    
    def mark_as_read(self, user_id: str, message_ids: List[str]) -> bool:
        """Mark messages as read with retry logic and parallel processing"""
        if not message_ids:
            return True
        
        try:
            headers = self._get_headers(user_id)
            failed_count = 0
            
            def mark_single_message(message_id: str) -> bool:
                """Mark a single message as read with retry logic"""
                def make_request():
                    return requests.patch(
                        f"{self.base_url}/me/messages/{message_id}",
                        headers=headers,
                        json={'isRead': True}
                    )
                
                response = self._retry_request(make_request)
                if response is None or response.status_code >= 400:
                    logger.warning(f"Failed to mark message {message_id} as read after retries")
                    return False
                return True
            
            # Process messages in parallel using ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(10, len(message_ids))) as executor:
                futures: List[Future[bool]] = [
                    executor.submit(mark_single_message, msg_id)
                    for msg_id in message_ids
                ]
                
                # Wait for all operations to complete
                wait(futures)
                
                # Check results
                for future in futures:
                    if not future.result():
                        failed_count += 1
            
            if failed_count == 0:
                logger.info(f"Successfully marked {len(message_ids)} messages as read for user {user_id}")
                return True
            else:
                logger.warning(f"Marked {len(message_ids) - failed_count}/{len(message_ids)} messages as read for user {user_id} ({failed_count} failed)")
                return failed_count < len(message_ids)  # Return True if at least some succeeded
        except Exception as e:
            logger.error(f"Failed to mark messages as read for user {user_id}: {str(e)}")
            return False
    
    def delete_messages(self, user_id: str, message_ids: List[str]) -> bool:
        """Delete messages with retry logic and parallel processing"""
        if not message_ids:
            return True
        
        try:
            headers = self._get_headers(user_id)
            failed_count = 0
            
            def delete_single_message(message_id: str) -> bool:
                """Delete a single message with retry logic"""
                def make_request():
                    return requests.delete(
                        f"{self.base_url}/me/messages/{message_id}",
                        headers=headers
                    )
                
                response = self._retry_request(make_request)
                if response is None or response.status_code >= 400:
                    logger.warning(f"Failed to delete message {message_id} after retries")
                    return False
                return True
            
            # Process messages in parallel using ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(10, len(message_ids))) as executor:
                futures: List[Future[bool]] = [
                    executor.submit(delete_single_message, msg_id)
                    for msg_id in message_ids
                ]
                
                # Wait for all operations to complete
                wait(futures)
                
                # Check results
                for future in futures:
                    if not future.result():
                        failed_count += 1
            
            if failed_count == 0:
                logger.info(f"Successfully deleted {len(message_ids)} messages for user {user_id}")
                return True
            else:
                logger.warning(f"Deleted {len(message_ids) - failed_count}/{len(message_ids)} messages for user {user_id} ({failed_count} failed)")
                return failed_count < len(message_ids)  # Return True if at least some succeeded
        except Exception as e:
            logger.error(f"Failed to delete messages for user {user_id}: {str(e)}")
            return False 