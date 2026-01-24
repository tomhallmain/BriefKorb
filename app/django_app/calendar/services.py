"""
Calendar service for Microsoft Graph API operations
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import requests
import json
from dateutil import tz
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from email_server.config import EmailServerConfig
from email_server.auth import MicrosoftOAuth, TokenManager


# Windows timezone to IANA timezone mappings
WINDOWS_TO_IANA_MAPPINGS = {
    'Dateline Standard Time': 'Etc/GMT+12',
    'UTC-11': 'Etc/GMT+11',
    'Hawaiian Standard Time': 'Pacific/Honolulu',
    'Alaskan Standard Time': 'America/Anchorage',
    'Pacific Standard Time': 'America/Los_Angeles',
    'US Mountain Standard Time': 'America/Phoenix',
    'Mountain Standard Time': 'America/Denver',
    'Central America Standard Time': 'America/Guatemala',
    'Central Standard Time': 'America/Chicago',
    'Mexico Standard Time': 'America/Mexico_City',
    'Canada Central Standard Time': 'America/Regina',
    'SA Pacific Standard Time': 'America/Bogota',
    'Eastern Standard Time': 'America/New_York',
    'US Eastern Standard Time': 'America/Indianapolis',
    'Venezuela Standard Time': 'America/Caracas',
    'Paraguay Standard Time': 'America/Asuncion',
    'Atlantic Standard Time': 'America/Halifax',
    'Central Brazilian Standard Time': 'America/Cuiaba',
    'SA Western Standard Time': 'America/La_Paz',
    'Pacific SA Standard Time': 'America/Santiago',
    'Newfoundland Standard Time': 'America/St_Johns',
    'E. South America Standard Time': 'America/Sao_Paulo',
    'Argentina Standard Time': 'America/Buenos_Aires',
    'SA Eastern Standard Time': 'America/Cayenne',
    'Greenland Standard Time': 'America/Godthab',
    'Montevideo Standard Time': 'America/Montevideo',
    'Magallanes Standard Time': 'America/Punta_Arenas',
    'Saint Pierre Standard Time': 'America/Miquelon',
    'Bahia Standard Time': 'America/Bahia',
    'UTC-02': 'Etc/GMT+2',
    'Mid-Atlantic Standard Time': 'Etc/GMT+2',
    'Azores Standard Time': 'Atlantic/Azores',
    'Cape Verde Standard Time': 'Atlantic/Cape_Verde',
    'UTC': 'UTC',
    'GMT Standard Time': 'Europe/London',
    'Greenwich Standard Time': 'Atlantic/Reykjavik',
    'W. Europe Standard Time': 'Europe/Berlin',
    'Central Europe Standard Time': 'Europe/Budapest',
    'Romance Standard Time': 'Europe/Paris',
    'Central European Standard Time': 'Europe/Warsaw',
    'W. Central Africa Standard Time': 'Africa/Lagos',
    'Jordan Standard Time': 'Asia/Amman',
    'GTB Standard Time': 'Europe/Bucharest',
    'Middle East Standard Time': 'Asia/Beirut',
    'Egypt Standard Time': 'Africa/Cairo',
    'E. Europe Standard Time': 'Europe/Chisinau',
    'Syria Standard Time': 'Asia/Damascus',
    'West Bank Standard Time': 'Asia/Hebron',
    'South Africa Standard Time': 'Africa/Johannesburg',
    'FLE Standard Time': 'Europe/Kiev',
    'Israel Standard Time': 'Asia/Jerusalem',
    'Kaliningrad Standard Time': 'Europe/Kaliningrad',
    'Sudan Standard Time': 'Africa/Khartoum',
    'Libya Standard Time': 'Africa/Tripoli',
    'Namibia Standard Time': 'Africa/Windhoek',
    'Arabic Standard Time': 'Asia/Baghdad',
    'Turkey Standard Time': 'Europe/Istanbul',
    'Arab Standard Time': 'Asia/Riyadh',
    'Belarus Standard Time': 'Europe/Minsk',
    'Russian Standard Time': 'Europe/Moscow',
    'E. Africa Standard Time': 'Africa/Nairobi',
    'Iran Standard Time': 'Asia/Tehran',
    'Arabian Standard Time': 'Asia/Dubai',
    'Astrakhan Standard Time': 'Europe/Astrakhan',
    'Azerbaijan Standard Time': 'Asia/Baku',
    'Russia Time Zone 3': 'Europe/Samara',
    'Mauritius Standard Time': 'Indian/Mauritius',
    'Saratov Standard Time': 'Europe/Saratov',
    'Georgian Standard Time': 'Asia/Tbilisi',
    'Volgograd Standard Time': 'Europe/Volgograd',
    'Caucasus Standard Time': 'Asia/Yerevan',
    'Afghanistan Standard Time': 'Asia/Kabul',
    'West Asia Standard Time': 'Asia/Tashkent',
    'Ekaterinburg Standard Time': 'Asia/Yekaterinburg',
    'Pakistan Standard Time': 'Asia/Karachi',
    'Qyzylorda Standard Time': 'Asia/Qyzylorda',
    'India Standard Time': 'Asia/Calcutta',
    'Sri Lanka Standard Time': 'Asia/Colombo',
    'Nepal Standard Time': 'Asia/Katmandu',
    'Central Asia Standard Time': 'Asia/Almaty',
    'Bangladesh Standard Time': 'Asia/Dhaka',
    'Omsk Standard Time': 'Asia/Omsk',
    'Myanmar Standard Time': 'Asia/Rangoon',
    'SE Asia Standard Time': 'Asia/Bangkok',
    'Altai Standard Time': 'Asia/Barnaul',
    'W. Mongolia Standard Time': 'Asia/Hovd',
    'North Asia Standard Time': 'Asia/Krasnoyarsk',
    'N. Central Asia Standard Time': 'Asia/Novosibirsk',
    'Tomsk Standard Time': 'Asia/Tomsk',
    'China Standard Time': 'Asia/Shanghai',
    'North Asia East Standard Time': 'Asia/Irkutsk',
    'Singapore Standard Time': 'Asia/Singapore',
    'W. Australia Standard Time': 'Australia/Perth',
    'Taipei Standard Time': 'Asia/Taipei',
    'Ulaanbaatar Standard Time': 'Asia/Ulaanbaatar',
    'Aus Central W. Standard Time': 'Australia/Eucla',
    'Transbaikal Standard Time': 'Asia/Chita',
    'Tokyo Standard Time': 'Asia/Tokyo',
    'North Korea Standard Time': 'Asia/Pyongyang',
    'Korea Standard Time': 'Asia/Seoul',
    'Yakutsk Standard Time': 'Asia/Yakutsk',
    'Cen. Australia Standard Time': 'Australia/Adelaide',
    'AUS Central Standard Time': 'Australia/Darwin',
    'E. Australia Standard Time': 'Australia/Brisbane',
    'AUS Eastern Standard Time': 'Australia/Sydney',
    'West Pacific Standard Time': 'Pacific/Port_Moresby',
    'Tasmania Standard Time': 'Australia/Hobart',
    'Vladivostok Standard Time': 'Asia/Vladivostok',
    'Lord Howe Standard Time': 'Australia/Lord_Howe',
    'Bougainville Standard Time': 'Pacific/Bougainville',
    'Russia Time Zone 10': 'Asia/Srednekolymsk',
    'Magadan Standard Time': 'Asia/Magadan',
    'Norfolk Standard Time': 'Pacific/Norfolk',
    'Sakhalin Standard Time': 'Asia/Sakhalin',
    'Central Pacific Standard Time': 'Pacific/Guadalcanal',
    'Russia Time Zone 11': 'Asia/Kamchatka',
    'New Zealand Standard Time': 'Pacific/Auckland',
    'UTC+12': 'Etc/GMT-12',
    'Fiji Standard Time': 'Pacific/Fiji',
    'Chatham Islands Standard Time': 'Pacific/Chatham',
    'UTC+13': 'Etc/GMT-13',
    'Tonga Standard Time': 'Pacific/Tongatapu',
    'Samoa Standard Time': 'Pacific/Apia',
    'Line Islands Standard Time': 'Pacific/Kiritimati'
}


def get_iana_from_windows(windows_tz_name: str) -> str:
    """Convert Windows timezone name to IANA timezone identifier
    
    Args:
        windows_tz_name: Windows timezone name (e.g., "Pacific Standard Time")
        
    Returns:
        IANA timezone identifier (e.g., "America/Los_Angeles")
    """
    if windows_tz_name in WINDOWS_TO_IANA_MAPPINGS:
        return WINDOWS_TO_IANA_MAPPINGS[windows_tz_name]
    
    # If not found, assume it's already an IANA name or return UTC as fallback
    if '/' in windows_tz_name:
        return windows_tz_name
    return 'UTC'


class CalendarService:
    """Service for Microsoft Calendar operations"""
    
    def __init__(self, user_id: str):
        """Initialize calendar service for a user"""
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
    
    def get_calendar_events(self, start: datetime, end: datetime, timezone: str) -> Dict[str, Any]:
        """Get calendar events for a date range
        
        Args:
            start: Start datetime
            end: End datetime
            timezone: User's timezone (IANA format)
            
        Returns:
            Dictionary with 'value' key containing list of events
        """
        headers = self._get_headers(timezone)
        
        query_params = {
            'startDateTime': start.isoformat(timespec='seconds'),
            'endDateTime': end.isoformat(timespec='seconds'),
            '$select': 'subject,organizer,start,end',
            '$orderby': 'start/dateTime',
            '$top': '50'
        }
        
        response = requests.get(
            f'{self.base_url}/me/calendarview',
            headers=headers,
            params=query_params
        )
        response.raise_for_status()
        
        return response.json()
    
    def create_event(self, subject: str, start: datetime, end: datetime, 
                    timezone: str, attendees: Optional[List[str]] = None, 
                    body: Optional[str] = None) -> Dict[str, Any]:
        """Create a new calendar event
        
        Args:
            subject: Event subject
            start: Start datetime
            end: End datetime
            timezone: User's timezone (IANA format)
            attendees: Optional list of attendee email addresses
            body: Optional event body/description
            
        Returns:
            Created event dictionary
        """
        headers = self._get_headers()
        
        # Create event object
        new_event = {
            'subject': subject,
            'start': {
                'dateTime': start.isoformat(timespec='seconds'),
                'timeZone': timezone
            },
            'end': {
                'dateTime': end.isoformat(timespec='seconds'),
                'timeZone': timezone
            }
        }
        
        if attendees:
            attendee_list = []
            for email in attendees:
                attendee_list.append({
                    'type': 'required',
                    'emailAddress': {'address': email.strip()}
                })
            new_event['attendees'] = attendee_list
        
        if body:
            new_event['body'] = {
                'contentType': 'text',
                'content': body
            }
        
        response = requests.post(
            f'{self.base_url}/me/events',
            headers=headers,
            json=new_event
        )
        response.raise_for_status()
        
        return response.json()
