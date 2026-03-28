# TGTG Scanner Mobile App

A mobile version of the TGTG Scanner built with Kivy for Android.

## Features

- Login with email + PIN (like the desktop version)
- View your favorite TGTG stores
- See item availability, prices, and pickup times
- Toggle favorites directly from the app
- Background scanning for availability changes
- Push notifications when items become available

## Prerequisites

- Python 3.10+
- Android SDK (for building APK)
- [Buildozer](https://buildozer.readthedocs.io/) for building

## Setup

### Local Development (Desktop)

```bash
cd tgtg_mobile
pip install -r requirements.txt
python main.py
```

### Building for Android

1. Install buildozer:
```bash
pip install buildozer
```

2. Install Android SDK dependencies (Ubuntu/Debian):
```bash
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev
```

3. Build the debug APK:
```bash
cd tgtg_mobile
buildozer android debug
```

The APK will be in `bin/tgtgscanner-*-debug.apk`

## Project Structure

```
tgtg_mobile/
├── main.py                 # App entry point
├── buildozer.spec          # Build configuration
├── requirements.txt        # Python dependencies
├── tgtg_api/
│   └── client.py          # TGTG API client (adapted for mobile)
├── models/
│   └── item.py            # Item data model
├── screens/
│   ├── login.py           # Email + PIN login
│   ├── favorites.py       # Favorite items list
│   ├── item_detail.py     # Item details view
│   └── settings.py        # App settings
├── services/
│   ├── token_storage.py   # Secure token storage
│   └── android_service.py # Background scanning & notifications
└── errors.py               # Custom exceptions
```

## Configuration

Settings are stored locally:
- `tokens.json`: API tokens (access, refresh, datadome)
- `settings.json`: User preferences

## Background Scanning

When enabled, the app polls for item availability every 60 seconds (configurable) and sends notifications when items become available.

## Notes

- The app requires internet connectivity
- Background scanning may impact battery life
- On Android 13+, notification permission is required
