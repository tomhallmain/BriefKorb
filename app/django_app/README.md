# Django App - BriefKorb Web Interface

## Overview

The `django_app/` directory contains the Django web application that provides:
- OAuth callback handlers for BriefKorb desktop app
- Web-based authentication (sign in/out with Microsoft or Gmail)
- Web interface for calendar and message management

## Structure

```
django_app/
├── oauth/          # OAuth callbacks and web authentication
├── home/           # Home page
├── calendar/       # Calendar web interface
├── messages/       # Messages web interface
└── templates/      # HTML templates
```

## Features

### OAuth Callbacks
- Handles OAuth redirects from Microsoft and Gmail
- Exchanges authorization codes for tokens
- Stores tokens using `email_server.auth.TokenManager`
- Works for both desktop app and web authentication

### Web Authentication
- Sign in with Microsoft or Gmail directly from web browser
- Django session management for web users
- Shared token storage (web and desktop use same tokens)

### Calendar Interface
- View Microsoft Calendar events for current week
- Create new calendar events
- Timezone-aware event display

### Messages Interface
- View messages aggregated by sender
- Filter by mailbox (inbox/junk) and unread status
- Mark messages as read
- Delete messages
- Block senders (creates Microsoft Graph inbox rules)

## Authentication

The web app supports two authentication methods, both of which route through the same Django sign-in endpoints:

1. **Web-based**: Users visit `/auth/microsoft/signin` or `/auth/gmail/signin` directly in a browser. Django initiates the OAuth flow (using MSAL for Microsoft), stores the provider-specific flow state in the Django session, and redirects to the provider's authorization page. On return, `/auth/microsoft/callback` or `/auth/gmail/callback` completes the exchange and redirects to the home page.

2. **Desktop app**: The PySide6 app opens a browser to the same `/auth/microsoft/signin` or `/auth/gmail/signin` URL. The Django server handles the full OAuth flow identically to the web-based path. When the callback completes it also writes a status file (`.microsoft_auth_status.json` / `.gmail_auth_status.json` in `email_server/`) that the desktop app polls for to detect completion.

Both methods use the same `TokenManager` for token storage, so tokens are shared between desktop and web.

> **Note for Microsoft**: The `offline_access` scope must be included in `config.yaml` for MSAL to receive a refresh token. Without it, silent token refresh will fail and users will need to re-authenticate every hour.

## Running

```bash
python app/manage.py runserver
```

Then visit `http://localhost:8000/`

## Dependencies

- Uses `email_server` backend for all API operations
- OAuth classes in `email_server/auth/` (shared with desktop app)
- Token storage via `email_server.auth.TokenManager`
