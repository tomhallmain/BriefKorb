# BriefKorb

A unified email client for managing multiple email accounts (Microsoft Graph API and Gmail API) through a single desktop interface. BriefKorb groups messages by sender, helps filter valuable communications from spam, and provides a modern dark-themed UI for managing emails across multiple providers.

## Features

- Unified API interface for multiple email providers
- Support for Microsoft Graph API (Outlook/Office 365)
- Support for Gmail API
- RESTful API endpoints for email operations
- Authentication and authorization handling
- **BriefKorb Desktop Application** - Modern PySide6 GUI for managing emails across multiple providers with message bundling and spam filtering

## Technical Stack

- **Python 3.10**
  - ⚠️ **Note**: Python 3.10 will lose Google API support after October 2026. Plan to upgrade to Python 3.11+ with Django 4.x/5.x
- **Gmail API**: Uses recommended `google-auth-oauthlib` library
- **Microsoft Graph API**: Uses MSAL (Microsoft Authentication Library) for automatic token refresh and caching
- Django 3.2 (legacy web components)
- **PySide6** - Modern cross-platform GUI framework
- Microsoft Graph API (OAuth 2.0 with MSAL - automatic token refresh)
- Gmail API (OAuth 2.0)
- SQLite database

## Project Structure

```
app/
├── assets/          # Static assets
├── email_server/    # Unified email server backend
│   ├── providers/   # Email provider implementations (Gmail, Microsoft)
│   ├── auth/        # OAuth authentication handlers (shared by desktop & web)
│   └── utils/       # Utility functions and logging
├── email_client/    # PySide6 desktop GUI application
│   ├── main.py      # Application entry point
│   ├── ui/          # UI components and windows
│   └── widgets/     # Custom widgets
├── django_app/      # Django web application
│   ├── oauth/       # OAuth callbacks and web authentication
│   ├── home/        # Home page
│   ├── calendar/  # Calendar web interface
│   └── messages/    # Messages web interface
├── manage.py        # Django management script
└── requirements.txt # Project dependencies
```

## Setup Instructions

### Installation

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r app/requirements.txt
   ```
4. Copy `email_server/config.example.yaml` to `email_server/config.yaml` and fill in your API credentials:
   ```bash
   cp app/email_server/config.example.yaml app/email_server/config.yaml
   ```
5. Run migrations:
   ```bash
   python app/manage.py migrate
   ```
6. Start the development server (optional, for legacy Django components):
   ```bash
   python app/manage.py runserver
   ```

7. Launch BriefKorb:
   ```bash
   python app/email_client/main.py
   ```

## BriefKorb Applications

### Desktop Application

The PySide6 desktop application provides a modern, user-friendly interface for managing emails across multiple providers. The application includes:

### Features

- **Multi-Provider Support**: View and manage emails from Microsoft Outlook/Office 365 and Gmail in a single interface
- **Message Bundling**: Messages are automatically grouped by sender, showing one entry per sender with message count
- **Unified Message List**: See all message groups from all connected providers in one place, sorted by date
- **Provider Filtering**: Filter messages by specific provider (Microsoft, Gmail, or All)
- **Message Viewing**: View full message details including sender, recipients, date, and body with full HTML rendering support
- **Message Navigation**: Navigate between messages from the same sender using Previous/Next buttons
- **Unread Filter**: Toggle to show only unread messages
- **Message Actions**:
  - Mark messages as read
  - Delete messages
  - Compose and send new emails
- **Thread-Safe Loading**: Messages load in background threads to keep the UI responsive
- **Modern UI**: Clean, intuitive interface built with PySide6

### Usage

1. **Configure Email Server**: Ensure `app/email_server/config.yaml` is properly configured with your API credentials
2. **Launch BriefKorb**: Run `python app/email_client/main.py`
3. **Authenticate Providers**: Open Settings and authenticate at least one email provider (Microsoft or Gmail)
4. **Select Provider**: Choose "All" to see messages from all providers, or select a specific provider
5. **Load Messages**: Click "Refresh" to fetch messages from your email accounts
6. **View Messages**: Click on any message group in the list to view messages from that sender
7. **Navigate Messages**: Use Previous/Next buttons to navigate between messages in a group
8. **Compose Email**: Click "Compose" to send a new email through any configured provider

### Web Application

The Django web application (`django_app/`) provides:
- **OAuth Callbacks**: Handles authentication for both desktop and web apps
- **Web Authentication**: Sign in/out with Microsoft or Gmail directly from the web interface
- **Calendar View**: View and create Microsoft Calendar events
- **Messages View**: View and manage emails with sender aggregation, filtering, and blocking

**Usage**: Start Django server (`python app/manage.py runserver`) and visit `http://localhost:8000/`

## API Credentials

You'll need to set up API credentials for both Microsoft Graph API and Gmail API. All credentials are stored in `app/email_server/config.yaml` (copy from `config.example.yaml`).

### Microsoft Graph API

1. Register an application in the [Azure Portal](https://portal.azure.com) → Azure Active Directory → App registrations
2. Add a redirect URI: `http://localhost:8000/auth/microsoft/callback` (Web platform)
3. Create a client secret under Certificates & secrets
4. Note the Application (client) ID, client secret value, and your Directory (tenant) ID
5. Under API permissions, add the following Microsoft Graph delegated permissions:
   - `Mail.Read`
   - `Mail.Send`
   - `offline_access` (required for token refresh — without this users must re-authenticate every hour)
6. Fill in `client_id`, `client_secret`, `tenant_id`, and `redirect_uri` in `config.yaml`

### Gmail API

1. Create a project in [Google Cloud Console](https://console.cloud.google.com)
2. Enable the Gmail API
3. Create OAuth 2.0 credentials (Desktop app type), download `credentials.json`
4. Add `http://localhost:8000/auth/gmail/callback` as an authorised redirect URI
5. Set `credentials_path` in `config.yaml` to the path of your downloaded `credentials.json`

## Architecture

The application is structured in two main components:

1. **Backend (`app/email_server/`)**: Unified email server providing a consistent API for multiple email providers
   - Provider implementations for Microsoft Graph API and Gmail API
   - OAuth authentication handling
   - Token management
   - Configuration management

2. **Frontend (`app/email_client/`)**: PySide6 desktop GUI application
   - Main window with message list and detail view
   - Compose dialog for sending emails
   - Custom widgets for message display
   - Thread-based message loading for responsive UI

## Next Steps

Future improvements may include:
- Python 3.11+ upgrade (required before October 2026)
- Content analysis and classification for spam filtering
- Security enhancements
- Feature additions (search, attachments, threading, etc.)
