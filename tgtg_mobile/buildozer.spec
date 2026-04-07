[app]

title = TGTG Scanner
package.name = tgtgscanner
package.domain = org.tgtg

source.dir = .
version = 0.2.0

requirements = python3,kivy==2.3.0,requests,urllib3,certifi,plyer,pyjnius

orientation = portrait

fullscreen = 0

android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,VIBRATE,POST_NOTIFICATIONS,FOREGROUND_SERVICE,RECEIVE_BOOT_COMPLETED,WAKE_LOCK

android.archs = arm64-v8a

android.minapi = 24
android.api = 34

android.allow_backup = False

android.add_src = android/src/

# Merge additional manifest entries (receiver, service, permissions)
android.add_android_manifest_entries = android/AndroidManifest.xml

log_level = 2

p4a.bootstrap = sdl2
