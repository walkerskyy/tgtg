# TGTG Mobile App Development Session Notes

## Date: March 28, 2026

## Summary

Fixed the TGTG Scanner mobile app to work on Android. The app now:
- Shows login screen properly
- Handles TGTG API authentication with PIN
- Displays favorites list
- Supports notifications for available items

## Issues Fixed

### 1. Black Screen on Startup
- **Problem:** App showed black screen after loading
- **Root Cause:** KV file loading happening before Kivy was imported
- **Fix:** Moved KV file loading inside Kivy import block in main.py

### 2. ActivityIndicator Not Available
- **Problem:** `kivy.factory.FactoryException: Unknown class <ActivityIndicator>`
- **Root Cause:** ActivityIndicator is iOS-only, not available on Android
- **Fix:** Replaced with a simple Label

### 3. Invalid Color Format
- **Problem:** `ValueError: Label.color has an invalid format`
- **Root Cause:** Conditional color syntax in KV file was invalid
- **Fix:** Removed conditional color, used simple color value

### 4. Invalid input_type
- **Problem:** `TextInput.input_type is set to an invalid option 'email'`
- **Root Cause:** 'email' is not valid, should be 'mail'
- **Fix:** Changed to 'mail'

### 5. TGTG API 403 Errors
- **Problem:** Getting 403/geo.captcha errors when logging in
- **Root Cause:** Mobile API client had poor DataDome handling
- **Fix:** Rewrote client to match main scanner:
  - Switched from urllib3 to requests.Session
  - Added APK version fetching from Google Play
  - Added 403 retry logic with DataDome cache invalidation
  - Improved DataDome cookie handling using cookie jar

### 6. SSL Certificate Errors
- **Problem:** DataDome cookie fetch failed with SSL verification error
- **Fix:** Added `verify=certifi.where()` to requests calls

## Files Modified

```
tgtg_mobile/
├── main.py                     # Fixed KV loading, imports
├── screens/
│   ├── splash.kv               # Added splash screen
│   ├── login.kv                # Fixed input_type, removed ActivityIndicator
│   ├── login.py                # Login flow
│   ├── favorites.kv            # Favorites UI
│   ├── favorites.py            # Favorites logic
│   ├── settings.kv             # Settings UI
│   ├── settings.py             # Settings logic
│   └── item_detail.kv          # Item detail UI
├── services/
│   ├── token_storage.py        # Token persistence
│   └── android_service.py      # Notifications (using plyer)
├── tgtg_api/
│   └── client.py               # Complete rewrite with robust API handling
├── models/
│   └── item.py                 # Item model
├── requirements.txt            # Added requests
└── buildozer.spec             # Build config
```

## Build Commands

```bash
# Create venv and install buildozer
cd tgtg_mobile
python3 -m venv venv
source venv/bin/activate
pip install buildozer
pip install 'cython<3'

# Build APK
buildozer android debug

# APK location
# bin/tgtgscanner-0.1.1-arm64-v8a-debug.apk
```

## Key Learnings

1. **Kivy Android Limitations:**
   - ActivityIndicator is iOS-only
   - input_type 'email' is invalid, use 'mail'
   - KV files must be loaded after Kivy imports

2. **TGTG API Anti-Bot Protection:**
   - Requires DataDome cookie for requests
   - Needs proper User-Agent rotation
   - 403 errors should trigger retry with new session
   - SSL verification must use certifi

3. **Build Issues:**
   - pyjnius causes Cython compatibility issues with newer versions
   - Use 'cython<3' to avoid build failures

## Git Commit History (mobile-ui branch)

- `fix: improve API client with robust 403 handling and DataDome support`
- `fix: add certifi SSL verification to DataDome and APK fetch requests`
- `fix: replace ActivityIndicator (iOS only) with Label`
- `fix: use valid input_type 'mail' instead of 'email'`
- `fix: remove invalid conditional color syntax in login.kv`
- `fix: load KV files only after Kivy is available`
- `fix: improve KV loading and add debug logging`
- `fix: properly initialize screens from screens module`
- `fix: mobile app with login, favorites, notifications`
