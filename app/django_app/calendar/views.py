"""
Calendar views for BriefKorb web interface
"""

from django.shortcuts import render, redirect
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.contrib import messages
from datetime import datetime, timedelta
from dateutil import parser
from dateutil import tz as dateutil_tz

from .services import CalendarService, get_iana_from_windows


def _get_authenticated_user_id(request):
    """Get authenticated user ID from session or request"""
    # Try to get from session first (legacy UI compatibility)
    user = request.session.get('user', {})
    if user and user.get('is_authenticated'):
        return user.get('email') or user.get('userPrincipalName')
    
    # Try to get from email_server token manager
    # For now, we'll use the first available Microsoft user
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    
    from email_server.config import EmailServerConfig
    from email_server.auth import TokenManager
    
    app_dir = Path(__file__).parent.parent.parent
    config_path = app_dir / 'email_server' / 'config.yaml'
    
    if config_path.exists():
        config = EmailServerConfig.from_file(str(config_path))
        token_manager = TokenManager(storage_path=config.token_storage_path)
        
        # Get all user IDs and find a Microsoft-authenticated one
        all_user_ids = token_manager.get_all_user_ids()
        for user_id in all_user_ids:
            token_data = token_manager.get_token(user_id)
            if token_data and 'access_token' in token_data:
                # Check if it's a Microsoft token (has access_token, not token)
                if 'access_token' in token_data:
                    return user_id
    
    return None


def calendar_view(request):
    """Display calendar events for the current week"""
    user_id = _get_authenticated_user_id(request)
    
    if not user_id:
        messages.error(request, "Please authenticate with Microsoft first.")
        # Redirect to a simple message page or back to calendar
        return render(request, 'django_app/calendar/calendar.html', {
            'events': [],
            'error': 'Please authenticate with Microsoft first. Use the BriefKorb desktop app to authenticate.',
            'is_authenticated': False,
        })
    
    try:
        calendar_service = CalendarService(user_id)
        user_info = calendar_service.get_user_info()
        
        # Get user's timezone
        user_timezone = user_info.get('mailboxSettings', {}).get('timeZone') or 'UTC'
        # Convert Windows timezone to IANA if needed
        iana_timezone = get_iana_from_windows(user_timezone)
        tz_info = dateutil_tz.gettz(iana_timezone)
        
        # Get midnight today in user's timezone
        today = datetime.now(tz_info).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )
        
        # Get start of week (Sunday)
        if today.weekday() != 6:  # Sunday is 6
            start = today - timedelta(days=today.isoweekday())
        else:
            start = today
        
        end = start + timedelta(days=7)
        
        # Get calendar events
        events_response = calendar_service.get_calendar_events(start, end, iana_timezone)
        
        events = []
        if events_response and 'value' in events_response:
            # Convert ISO 8601 date times to datetime objects for template
            for event in events_response['value']:
                if 'start' in event and 'dateTime' in event['start']:
                    event['start']['dateTime'] = parser.parse(event['start']['dateTime'])
                if 'end' in event and 'dateTime' in event['end']:
                    event['end']['dateTime'] = parser.parse(event['end']['dateTime'])
                events.append(event)
        
        context = {
            'events': events,
            'user': user_info,
            'timezone': iana_timezone,
            'week_start': start,
            'week_end': end,
            'is_authenticated': True,
        }
        
        return render(request, 'django_app/calendar/calendar.html', context)
        
    except Exception as e:
        messages.error(request, f"Error loading calendar: {str(e)}")
        return render(request, 'django_app/calendar/calendar.html', {
            'events': [],
            'error': str(e),
            'is_authenticated': False,
        })


def new_event_view(request):
    """Create a new calendar event"""
    user_id = _get_authenticated_user_id(request)
    
    if not user_id:
        messages.error(request, "Please authenticate with Microsoft first.")
        return redirect('django_app.calendar:calendar')
    
    if request.method == 'POST':
        try:
            calendar_service = CalendarService(user_id)
            user_info = calendar_service.get_user_info()
            
            # Validate required fields
            subject = request.POST.get('ev-subject', '').strip()
            start_str = request.POST.get('ev-start', '').strip()
            end_str = request.POST.get('ev-end', '').strip()
            
            if not subject or not start_str or not end_str:
                messages.error(request, "Subject, start time, and end time are required.")
                return render(request, 'django_app/calendar/newevent.html', {
                    'user': user_info,
                    'is_authenticated': True,
                })
            
            # Parse datetime strings (from datetime-local input)
            start = datetime.fromisoformat(start_str.replace('T', ' '))
            end = datetime.fromisoformat(end_str.replace('T', ' '))
            
            # Get user's timezone
            user_timezone = user_info.get('mailboxSettings', {}).get('timeZone') or 'UTC'
            iana_timezone = get_iana_from_windows(user_timezone)
            
            # Parse attendees
            attendees = None
            attendees_str = request.POST.get('ev-attendees', '').strip()
            if attendees_str:
                attendees = [email.strip() for email in attendees_str.split(';') if email.strip()]
            
            # Get body
            body = request.POST.get('ev-body', '').strip() or None
            
            # Create event
            calendar_service.create_event(
                subject=subject,
                start=start,
                end=end,
                timezone=iana_timezone,
                attendees=attendees,
                body=body
            )
            
            messages.success(request, "Event created successfully!")
            return redirect('django_app.calendar:calendar')
            
        except Exception as e:
            messages.error(request, f"Error creating event: {str(e)}")
            return render(request, 'django_app/calendar/newevent.html', {
                'user': user_info if 'user_info' in locals() else {},
                'is_authenticated': True,
            })
    else:
        # GET request - show form
        try:
            calendar_service = CalendarService(user_id)
            user_info = calendar_service.get_user_info()
            
            return render(request, 'django_app/calendar/newevent.html', {
                'user': user_info,
                'is_authenticated': True,
            })
        except Exception as e:
            messages.error(request, f"Error loading form: {str(e)}")
            return redirect('django_app.calendar:calendar')
