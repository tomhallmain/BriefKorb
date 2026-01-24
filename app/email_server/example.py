"""
Example usage of the unified email server
"""

from email_server import UnifiedEmailServer
from pathlib import Path
from .utils.logger import setup_logger

# Set up logger
logger = setup_logger('email_server.example')

def main():
    # Initialize server with configuration
    config_path = Path(__file__).parent / 'config.yaml'
    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        logger.info("Please copy config.example.yaml to config.yaml and update with your settings")
        return
        
    server = UnifiedEmailServer(config_path=str(config_path))
    user_id = 'user123'
    
    # Get messages from all providers
    messages = server.get_user_messages(user_id)
    logger.info(f"Found {len(messages)} unread messages")
    
    for msg in messages:
        logger.info(f"\nFrom: {msg.sender}")
        logger.info(f"Subject: {msg.subject}")
        logger.info(f"Provider: {msg.provider}")
        logger.info(f"Date: {msg.received_date}")
        logger.info("-" * 50)
    
    # Get messages from specific provider
    gmail_messages = server.get_user_messages(user_id, provider='gmail')
    logger.info(f"\nFound {len(gmail_messages)} messages in Gmail")
    
    # Send a message
    success = server.send_message(
        user_id,
        'gmail',
        'recipient@example.com',
        'Test Email',
        'This is a test email sent through the unified email server.'
    )
    logger.info(f"Message sent: {success}")
    
    # Mark messages as read
    if gmail_messages:
        message_ids = [msg.id for msg in gmail_messages[:5]]  # Mark first 5 messages as read
        success = server.mark_messages_as_read(user_id, 'gmail', message_ids)
        logger.info(f"Marked messages as read: {success}")
    
    # Delete messages
    if gmail_messages:
        message_ids = [msg.id for msg in gmail_messages[5:10]]  # Delete next 5 messages
        success = server.delete_user_messages(user_id, 'gmail', message_ids)
        logger.info(f"Deleted messages: {success}")

if __name__ == '__main__':
    main() 