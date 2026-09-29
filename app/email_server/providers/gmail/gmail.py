"""
Gmail API provider implementation
"""

from typing import Collection, Dict, List, Optional, Union
from datetime import datetime, timezone
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parsedate_to_datetime
import httplib2
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from ... import EmailProvider, EmailMessage, MessagePage
from ...auth.gmail import GmailOAuth
from ...auth import TokenManager
from ...utils.logger import setup_logger
from ...utils.datetime_compat import normalize_received_at_utc

# Set up logger
logger = setup_logger('email_server.providers.gmail')

# googleapiclient's build() has no timeout= of its own; this is applied
# to the underlying httplib2 transport via AuthorizedHttp so a stalled
# Gmail call cannot hang get_messages() indefinitely.
GMAIL_REQUEST_TIMEOUT_SECONDS = 30
# Gmail's batch endpoint rejects more than 100 calls in one request.
GMAIL_BATCH_SIZE = 100
# Metadata-only batches stay smaller -- Gmail still treats each batched
# .get() as a concurrent request, and 100-wide full fetches 429'd.
GMAIL_METADATA_BATCH_SIZE = 20
GMAIL_METADATA_HEADERS = ['Subject', 'From', 'To', 'Date']

class GmailProvider(EmailProvider):
    """Gmail API provider implementation"""

    # Gmail's search-operator name for the sent-mail folder -- distinct from
    # Microsoft's Graph well-known folder name ('sentitems'), since folder
    # naming isn't unified across providers the way "inbox" happens to be.
    SENT_FOLDER = 'sent'

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
            
            # Build credentials for Gmail service. build() cannot take both
            # credentials= and http=, so wrap creds in an AuthorizedHttp
            # with an explicit socket timeout instead of leaving the
            # transport unbounded.
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
            authorized_http = AuthorizedHttp(
                creds, http=httplib2.Http(timeout=GMAIL_REQUEST_TIMEOUT_SECONDS),
            )
            self._service = build('gmail', 'v1', http=authorized_http)
            logger.debug(f"Successfully authenticated user {user_id}")
            return True
        except Exception as e:
            logger.debug(f"Authentication check for user {user_id}: {str(e)}")
            return False
    
    def _parse_message_response(self, message: Dict) -> EmailMessage:
        """Parse a Gmail API message resource into an EmailMessage. Pure
        parsing, no API call -- shared by get_messages()'s batch callback
        (format='full' or format='metadata') and get_message()'s
        single-message fetch. Metadata resources have headers but no body
        parts, so body is ''.
        """
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
                received_date = datetime.now(timezone.utc)
        else:
            received_date = datetime.now(timezone.utc)
        received_date = normalize_received_at_utc(received_date)

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

        return EmailMessage(
            id=message['id'],
            subject=subject,
            sender=sender,
            recipients=to.split(',') if to else [],
            received_date=received_date,
            body=body,
            is_read='UNREAD' not in message['labelIds'],
            provider='gmail'
        )

    def get_messages(self,
                    user_id: str,
                    folder: str = 'inbox',
                    max_messages: int = 100,
                    unread_only: bool = False,
                    include_body: bool = True) -> List[EmailMessage]:
        """Get messages from the specified folder"""
        if not self._service:
            if not self.authenticate(user_id):
                logger.error(f"Failed to authenticate user {user_id} for message retrieval")
                return []

        query = f'in:{folder}'
        if unread_only:
            query += ' is:unread'

        try:
            results = self._service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_messages
            ).execute()

            message_ids = [msg['id'] for msg in results.get('messages', [])]
            messages = self._fetch_messages_by_id(message_ids, include_body)
            logger.info(f"Retrieved {len(messages)} messages from {folder} for user {user_id}")
            return messages
        except Exception as e:
            logger.error(f"Failed to get messages for user {user_id}: {str(e)}")
            return []

    def _fetch_messages_by_id(self, message_ids: List[str], include_body: bool) -> List[EmailMessage]:
        """Fetch and parse listed message ids. A message that fails to
        fetch or parse is skipped."""
        # Gmail's list endpoint returns ids only, so each message still
        # needs a .get() -- but format='metadata' skips the body, which
        # is what get_message_digest() actually wants. Don't thread the
        # shared httplib2 transport; batch instead.
        messages: List[EmailMessage] = []

        def _collect(request_id: str, response: Dict, exception: Optional[Exception]) -> None:
            if exception is not None:
                logger.warning(f"Failed to fetch message {request_id}: {exception}")
                return
            try:
                messages.append(self._parse_message_response(response))
            except Exception as parse_error:
                logger.warning(f"Failed to parse message {request_id}: {parse_error}")

        batch_size = GMAIL_BATCH_SIZE if include_body else GMAIL_METADATA_BATCH_SIZE
        for i in range(0, len(message_ids), batch_size):
            chunk = message_ids[i:i + batch_size]
            batch = self._service.new_batch_http_request(callback=_collect)
            for msg_id in chunk:
                if include_body:
                    request = self._service.users().messages().get(
                        userId='me', id=msg_id, format='full',
                    )
                else:
                    request = self._service.users().messages().get(
                        userId='me', id=msg_id, format='metadata',
                        metadataHeaders=GMAIL_METADATA_HEADERS,
                    )
                batch.add(request, request_id=msg_id)
            batch.execute()
        return messages

    def get_messages_page(self,
                          user_id: str,
                          folder: str = 'inbox',
                          page_size: int = 100,
                          unread_only: bool = False,
                          include_body: bool = True,
                          page_token: Optional[str] = None,
                          oldest_first: bool = False,
                          skip_ids: Collection[str] = ()) -> MessagePage:
        """See EmailProvider.get_messages_page. `messages.list` has no sort
        parameter and lists newest-first, so `oldest_first` is ignored
        (SUPPORTS_OLDEST_FIRST is False)."""
        if not self._service and not self.authenticate(user_id):
            raise RuntimeError(f"Failed to authenticate Gmail user {user_id}")

        query = f'in:{folder}'
        if unread_only:
            query += ' is:unread'
        list_kwargs = {'userId': 'me', 'q': query, 'maxResults': page_size}
        if page_token:
            list_kwargs['pageToken'] = page_token
        results = self._service.users().messages().list(**list_kwargs).execute()

        listed_ids = [msg['id'] for msg in results.get('messages', [])]
        wanted = [msg_id for msg_id in listed_ids if msg_id not in skip_ids]
        return MessagePage(
            messages=self._fetch_messages_by_id(wanted, include_body),
            next_page_token=results.get('nextPageToken'),
            scanned=len(listed_ids),
        )

    def get_message(self, user_id: str, message_id: str) -> Optional[EmailMessage]:
        """Get a single message (including body) by id."""
        if not self._service:
            if not self.authenticate(user_id):
                logger.error(f"Failed to authenticate user {user_id} for message retrieval")
                return None

        try:
            message = self._service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            return self._parse_message_response(message)
        except Exception as e:
            logger.error(f"Failed to get message {message_id} for user {user_id}: {str(e)}")
            return None

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

    def block_senders(self, user_id: str, sender_names: List[str]) -> List[str]:
        """Create a Gmail filter per sender that auto-trashes their future
        mail -- the Gmail-API equivalent of MicrosoftGraphProvider's inbox
        rule. Returns the subset of `sender_names` a filter was actually
        created for.
        """
        if not sender_names:
            return []

        if not self._service:
            if not self.authenticate(user_id):
                logger.error(f"Failed to authenticate user {user_id} for blocking senders")
                return []

        successful_senders: List[str] = []
        for sender_name in sender_names:
            filter_body = {
                'criteria': {'from': sender_name},
                'action': {'addLabelIds': ['TRASH'], 'removeLabelIds': ['INBOX', 'UNREAD']},
            }
            try:
                self._service.users().settings().filters().create(userId='me', body=filter_body).execute()
                successful_senders.append(sender_name)
            except Exception as e:
                logger.warning(f"Failed to create block filter for {sender_name}: {str(e)}")

        if len(successful_senders) == len(sender_names):
            logger.info(f"Successfully created block filters for {len(sender_names)} sender(s) for user {user_id}")
        else:
            logger.warning(f"Created block filters for {len(successful_senders)}/{len(sender_names)} sender(s) for user {user_id}")
        return successful_senders 