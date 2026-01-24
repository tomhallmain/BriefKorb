# Calendar App

A clean implementation of calendar and event management functionality, ported from the legacy tutorial UI to use the modern `email_server` system.

## Features

- **View Calendar Events**: Display events for the current week
- **Create Events**: Create new calendar events with attendees and descriptions
- **Timezone Support**: Automatically handles Windows to IANA timezone conversion
- **Modern Architecture**: Uses `email_server` system for authentication and API calls

## Architecture

### Components

- **`services.py`**: `CalendarService` class that handles all Microsoft Graph API calendar operations
  - Uses `email_server.auth.MicrosoftOAuth` for authentication
  - Uses `email_server.config.EmailServerConfig` for configuration
  - Provides clean API for calendar operations

- **`views.py`**: Django views for calendar display and event creation
  - `calendar_view()`: Displays events for the current week
  - `new_event_view()`: Handles event creation form

- **Templates**: Clean Bootstrap-based templates
  - `calendar.html`: Event list view
  - `newevent.html`: Event creation form

## Configuration

The calendar functionality requires Microsoft Graph API scopes:
- `https://graph.microsoft.com/Calendars.ReadWrite` - Read and write calendar events
- `offline_access` - Refresh tokens for persistent access

Add these to your `email_server/config.yaml`:

```yaml
microsoft:
  scopes:
    - "https://graph.microsoft.com/Mail.Read"
    - "https://graph.microsoft.com/Mail.Send"
    - "https://graph.microsoft.com/Calendars.ReadWrite"
    - "offline_access"
```

## Usage

1. Authenticate with Microsoft using BriefKorb desktop app
2. Visit `http://localhost:8000/calendar` to view events
3. Click "New Event" to create a calendar event
4. Events are created in your Microsoft Calendar

## Differences from Legacy Implementation

- **Clean Service Layer**: All API calls are in `CalendarService` class
- **Modern Authentication**: Uses `email_server` system instead of legacy helpers
- **Better Error Handling**: Proper exception handling and user feedback
- **Cleaner Templates**: Modern Bootstrap 4 styling
- **No Session Dependencies**: Works with token-based authentication from `email_server`
