"""
Gmail API provider implementation
"""

from typing import List, Optional, Union, Dict
from datetime import datetime
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parsedate_to_datetime
from googleapiclient.discovery import build
from ... import EmailProvider, EmailMessage
from ...auth.gmail import GmailOAuth
from ...auth import TokenManager
from ...utils.logger import setup_logger

# Set up logger
logger = setup_logger('email_server.providers.gmail')

class GmailProvider(EmailProvider):
    """Gmail API provider implementation"""
    
    def __init__(self, credentials_path: str, redirect_uri: str, token_manager: Optional[TokenManager] = None):
        self.credentials_path = credentials_path
        self.redirect_uri = redirect_uri
        
        # Initialize auth components
        # Use provided token_manager or create new one (but should always be provided)
        self.token_manager = token_manager if token_manager is not None else TokenManager()
        logger.debug(f"GmailProvider using TokenManager with storage path: {self.token_manager.storage_path}")
        # Pass token_manager to avoid creating duplicate
        self.oauth = GmailOAuth(credentials_path, redirect_uri, self.token_manager)
        self._service = None
        self._user_info = None
        logger.info("Initialized Gmail provider")
    
    def authenticate(self, user_id: str) -> bool:
        """Authenticate with Gmail API and retrieve/cache user info if needed"""
        try:
            # get_valid_token expects user_id, not token_data
            token_data = self.oauth.get_valid_token(user_id)
            if not token_data:
                # Don't log error - this user_id might not have tokens for this provider
                return False
            
            # Extract access token from token_data
            access_token = token_data.get('token')
            if not access_token:
                logger.debug(f"No access token in token data for user {user_id}")
                return False
            
            # Check if we have user_info cached, if not retrieve it
            user_info = self.token_manager.get_user_info(user_id)
            if not user_info:
                try:
                    # Get user info using the token_data (get_user_info can handle dict)
                    user_info = self.oauth.get_user_info(token_data)
                    if user_info:
                        # Cache it
                        self.token_manager.store_user_info(user_id, user_info)
                        logger.debug(f"Retrieved and cached user info for {user_id}")
                except Exception as e:
                    logger.warning(f"Could not retrieve user info for {user_id}: {e}")
                    # Continue anyway - authentication succeeded even if user_info fetch failed
            
            # Store in instance for backward compatibility
            self._user_info = user_info
            
            # Build credentials for Gmail service
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            creds = Credentials(
                token=token_data['token'],
                refresh_token=token_data.get('refresh_token'),
                token_uri=token_data['token_uri'],
                client_id=token_data['client_id'],
                client_secret=token_data.get('client_secret'),
                scopes=token_data['scopes']
            )
            self._service = build('gmail', 'v1', credentials=creds)
            logger.debug(f"Successfully authenticated user {user_id}")
            return True
        except Exception as e:
            logger.debug(f"Authentication check for user {user_id}: {str(e)}")
            return False
    
    def get_messages(self, 
                    user_id: str,
                    folder: str = 'inbox',
                    max_messages: int = 100,
                    unread_only: bool = False) -> List[EmailMessage]:
        """Get messages from the specified folder"""
        if not self._service:
            if not self.authenticate(user_id):
                logger.error(f"Failed to authenticate user {user_id} for message retrieval")
                return []
        
        query = 'in:inbox'
        if unread_only:
            query += ' is:unread'
        
        try:
            results = self._service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_messages
            ).execute()
            
            messages = []
            for msg in results.get('messages', []):
                message = self._service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='full'
                ).execute()
                
                headers = message['payload']['headers']
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '(No Subject)')
                sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
                date_str = next((h['value'] for h in headers if h['name'] == 'Date'), None)
                to = next((h['value'] for h in headers if h['name'] == 'To'), '')
                
                # Parse date - use email.utils.parsedate_to_datetime which handles various formats including GMT
                received_date = None
                if date_str:
                    try:
                        # parsedate_to_datetime handles RFC 2822 dates including GMT, EST, etc.
                        received_date = parsedate_to_datetime(date_str)
                    except (ValueError, TypeError) as e:
                        # Fallback to current time if parsing fails
                        logger.warning(f"Could not parse date '{date_str}': {e}, using current time")
                        received_date = datetime.now()
                else:
                    received_date = datetime.now()
                
                # Get message body - prefer HTML over plain text
                body = ''
                plain_text_body = ''
                if 'parts' in message['payload']:
                    for part in message['payload']['parts']:
                        if part['mimeType'] == 'text/html' and 'body' in part and 'data' in part['body']:
                            # Prefer HTML content
                            body = base64.urlsafe_b64decode(
                                part['body']['data']
                            ).decode('utf-8')
                        elif part['mimeType'] == 'text/plain' and 'body' in part and 'data' in part['body']:
                            # Store plain text as fallback
                            if not body:
                                plain_text_body = base64.urlsafe_b64decode(
                                    part['body']['data']
                                ).decode('utf-8')
                elif 'body' in message['payload'] and 'data' in message['payload']['body']:
                    mime_type = message['payload'].get('mimeType', 'text/plain')
                    body_data = base64.urlsafe_b64decode(
                        message['payload']['body']['data']
                    ).decode('utf-8')
                    if mime_type == 'text/html':
                        body = body_data
                    else:
                        plain_text_body = body_data
                
                # Use plain text as fallback if no HTML found
                if not body and plain_text_body:
                    body = plain_text_body
                
                messages.append(EmailMessage(
                    id=message['id'],
                    subject=subject,
                    sender=sender,
                    recipients=to.split(',') if to else [],
                    received_date=received_date,
                    body=body,
                    is_read='UNREAD' not in message['labelIds'],
                    provider='gmail'
                ))
            
            logger.info(f"Retrieved {len(messages)} messages from {folder} for user {user_id}")
            return messages
        except Exception as e:
            logger.error(f"Failed to get messages for user {user_id}: {str(e)}")
            return []
    
    def send_message(self,
                    user_id: str,
                    to: Union[str, List[str]],
                    subject: str,
                    body: str,
                    cc: Optional[List[str]] = None,
                    bcc: Optional[List[str]] = None) -> bool:
        """Send an email message"""
        if not self._service:
            if not self.authenticate(user_id):
                logger.error(f"Failed to authenticate user {user_id} for sending message")
                return False
        
        try:
            message = MIMEMultipart()
            message['to'] = to if isinstance(to, str) else ', '.join(to)
            message['subject'] = subject
            
            if cc:
                message['cc'] = ', '.join(cc)
            if bcc:
                message['bcc'] = ', '.join(bcc)
            
            msg = MIMEText(body, 'html')
            message.attach(msg)
            
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            self._service.users().messages().send(
                userId='me',
                body={'raw': raw}
            ).execute()
            
            logger.info(f"Successfully sent message to {to} from user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message for user {user_id}: {str(e)}")
            return False
    
    def mark_as_read(self, user_id: str, message_ids: List[str]) -> bool:
        """Mark messages as read"""
        if not self._service:
            if not self.authenticate(user_id):
                logger.error(f"Failed to authenticate user {user_id} for marking messages as read")
                return False
        
        try:
            for msg_id in message_ids:
                self._service.users().messages().modify(
                    userId='me',
                    id=msg_id,
                    body={'removeLabelIds': ['UNREAD']}
                ).execute()
            
            logger.info(f"Successfully marked {len(message_ids)} messages as read for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark messages as read for user {user_id}: {str(e)}")
            return False
    
    def delete_messages(self, user_id: str, message_ids: List[str]) -> bool:
        """Delete messages"""
        if not self._service:
            if not self.authenticate(user_id):
                logger.error(f"Failed to authenticate user {user_id} for deleting messages")
                return False
        
        try:
            for msg_id in message_ids:
                self._service.users().messages().trash(
                    userId='me',
                    id=msg_id
                ).execute()
            
            logger.info(f"Successfully deleted {len(message_ids)} messages for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete messages for user {user_id}: {str(e)}")
            return False 