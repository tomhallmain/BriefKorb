"""
Configuration management for the email server
"""

import os
from typing import Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass
class ProviderConfig:
    """Configuration for a specific provider"""
    enabled: bool = True
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None
    tenant_id: Optional[str] = None  # For Microsoft
    credentials_path: Optional[str] = None  # For Gmail
    scopes: Optional[list[str]] = None
    additional_settings: Dict[str, Any] = None

    def __post_init__(self):
        if self.additional_settings is None:
            self.additional_settings = {}

@dataclass
class EmailServerConfig:
    """Main configuration for the email server"""
    microsoft: ProviderConfig
    gmail: ProviderConfig
    token_storage_path: str = "tokens"
    log_level: str = "INFO"
    
    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'EmailServerConfig':
        """Create config from dictionary"""
        return cls(
            microsoft=ProviderConfig(**config_dict.get('microsoft', {})),
            gmail=ProviderConfig(**config_dict.get('gmail', {})),
            token_storage_path=config_dict.get('token_storage_path', 'tokens'),
            log_level=config_dict.get('log_level', 'INFO')
        )
    
    @classmethod
    def from_file(cls, config_path: str) -> 'EmailServerConfig':
        """Load configuration from a YAML file"""
        config_file = Path(config_path).resolve()
        with open(config_file, 'r') as f:
            config_dict = yaml.safe_load(f)
        
        # Resolve token_storage_path relative to config file's parent directory if it's relative
        # The config file is typically at app/email_server/config.yaml, so tokens should be at app/tokens
        token_storage_path = config_dict.get('token_storage_path', 'tokens')
        if token_storage_path and not Path(token_storage_path).is_absolute():
            # Make it relative to the config file's parent directory (app/)
            token_storage_path = str(config_file.parent.parent / token_storage_path)
        config_dict['token_storage_path'] = token_storage_path
        
        return cls.from_dict(config_dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary"""
        return {
            'microsoft': {
                'enabled': self.microsoft.enabled,
                'client_id': self.microsoft.client_id,
                'client_secret': self.microsoft.client_secret,
                'redirect_uri': self.microsoft.redirect_uri,
                'tenant_id': self.microsoft.tenant_id,
                'scopes': self.microsoft.scopes,
                'additional_settings': self.microsoft.additional_settings
            },
            'gmail': {
                'enabled': self.gmail.enabled,
                'client_id': self.gmail.client_id,
                'client_secret': self.gmail.client_secret,
                'redirect_uri': self.gmail.redirect_uri,
                'credentials_path': self.gmail.credentials_path,
                'scopes': self.gmail.scopes,
                'additional_settings': self.gmail.additional_settings
            },
            'token_storage_path': self.token_storage_path,
            'log_level': self.log_level
        }
    
    def save(self, config_path: str) -> None:
        """Save configuration to a YAML file"""
        with open(config_path, 'w') as f:
            yaml.safe_dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)
    
    def validate(self) -> bool:
        """Validate the configuration"""
        if not self.microsoft.enabled and not self.gmail.enabled:
            raise ValueError("At least one provider must be enabled")
        
        if self.microsoft.enabled:
            if not all([self.microsoft.client_id, self.microsoft.client_secret, 
                       self.microsoft.redirect_uri, self.microsoft.tenant_id]):
                raise ValueError("Microsoft provider requires client_id, client_secret, redirect_uri, and tenant_id")
        
        if self.gmail.enabled:
            if not all([self.gmail.credentials_path, self.gmail.redirect_uri]):
                raise ValueError("Gmail provider requires credentials_path and redirect_uri")
        
        # Ensure token storage directory exists
        Path(self.token_storage_path).mkdir(parents=True, exist_ok=True)
        
        return True

def create_default_config(config_path: str) -> EmailServerConfig:
    """Create a default configuration file
    Note: token_storage_path will be resolved relative to the config file's directory
    """
    config = EmailServerConfig(
        microsoft=ProviderConfig(
            enabled=True,
            redirect_uri="http://localhost:8000/auth/microsoft/callback",
            scopes=["https://graph.microsoft.com/Mail.ReadWrite",
                   "https://graph.microsoft.com/Mail.Send"]
        ),
        gmail=ProviderConfig(
            enabled=True,
            redirect_uri="http://localhost:8000/auth/gmail/callback",
            scopes=["https://www.googleapis.com/auth/gmail.readonly",
                   "https://www.googleapis.com/auth/gmail.send"]
        )
    )
    config.save(config_path)
    return config 