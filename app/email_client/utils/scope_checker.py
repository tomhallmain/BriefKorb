"""
Utility functions for checking OAuth scopes and permissions
"""

from typing import List, Optional, Set


class ScopeChecker:
    """Utility class for checking OAuth scopes"""
    
    # Microsoft Graph API scopes
    MS_READ = "https://graph.microsoft.com/Mail.Read"
    MS_SEND = "https://graph.microsoft.com/Mail.Send"
    MS_MODIFY = "https://graph.microsoft.com/Mail.ReadWrite"
    
    # Gmail API scopes
    GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
    GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
    GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
    
    @staticmethod
    def has_read_permission(scopes: Optional[List[str]], provider: str) -> bool:
        """Check if scopes include read permission"""
        if not scopes:
            return False
        
        scope_set = set(scopes)
        if provider.lower() == 'microsoft':
            return ScopeChecker.MS_READ in scope_set or ScopeChecker.MS_MODIFY in scope_set
        elif provider.lower() == 'gmail':
            return ScopeChecker.GMAIL_READONLY in scope_set or ScopeChecker.GMAIL_MODIFY in scope_set
        return False
    
    @staticmethod
    def has_send_permission(scopes: Optional[List[str]], provider: str) -> bool:
        """Check if scopes include send permission"""
        if not scopes:
            return False
        
        scope_set = set(scopes)
        if provider.lower() == 'microsoft':
            return ScopeChecker.MS_SEND in scope_set or ScopeChecker.MS_MODIFY in scope_set
        elif provider.lower() == 'gmail':
            return ScopeChecker.GMAIL_SEND in scope_set or ScopeChecker.GMAIL_MODIFY in scope_set
        return False
    
    @staticmethod
    def has_delete_permission(scopes: Optional[List[str]], provider: str) -> bool:
        """Check if scopes include delete/modify permission"""
        if not scopes:
            return False
        
        scope_set = set(scopes)
        if provider.lower() == 'microsoft':
            # Microsoft delete is part of Mail.ReadWrite
            return ScopeChecker.MS_MODIFY in scope_set
        elif provider.lower() == 'gmail':
            # Gmail delete is part of modify scope
            return ScopeChecker.GMAIL_MODIFY in scope_set
        return False
    
    @staticmethod
    def get_available_scopes(provider: str) -> List[dict]:
        """Get list of available scopes for a provider with descriptions"""
        if provider.lower() == 'microsoft':
            return [
                {
                    'value': ScopeChecker.MS_READ,
                    'label': 'Mail.Read - Read mail',
                    'permissions': ['read']
                },
                {
                    'value': ScopeChecker.MS_SEND,
                    'label': 'Mail.Send - Send mail',
                    'permissions': ['send']
                },
                {
                    'value': ScopeChecker.MS_MODIFY,
                    'label': 'Mail.ReadWrite - Read, send, and modify mail',
                    'permissions': ['read', 'send', 'delete']
                }
            ]
        elif provider.lower() == 'gmail':
            return [
                {
                    'value': ScopeChecker.GMAIL_READONLY,
                    'label': 'gmail.readonly - Read mail',
                    'permissions': ['read']
                },
                {
                    'value': ScopeChecker.GMAIL_SEND,
                    'label': 'gmail.send - Send mail',
                    'permissions': ['send']
                },
                {
                    'value': ScopeChecker.GMAIL_MODIFY,
                    'label': 'gmail.modify - Read, send, and modify mail',
                    'permissions': ['read', 'send', 'delete']
                }
            ]
        return []
