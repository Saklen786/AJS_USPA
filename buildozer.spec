[app]

# (str) Title of your application
title = ULTRA-Sonic Demonstrator

# (str) Package name
package.name = ultrasonics

# (str) Package domain (needed for android/ios packaging)
package.domain = org.sssskyds

# (str) Source code where the main.py lives
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,kv,atlas,json,txt,ttf

# (str) Application versioning
version = 1.0

# (list) Application requirements
# CRUCIAL: We lock python3 and hostpython3 to 3.11.9 to prevent the server 404 crash.
# Added 'android' so you can request permissions in main.py.
# (Removed Cython pinning here, as our GitHub workflow handles it safely via pip).
requirements = python3==3.11.9,hostpython3==3.11.9,kivy==2.3.0,pyjnius,android

# (str) Supported orientations (landscape, sensorPortrait or all)
orientation = portrait

# --- Android specific ---

# (list) Permissions
android.permissions = INTERNET,BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_SCAN,BLUETOOTH_CONNECT,ACCESS_FINE_LOCATION

# (int) Target Android API, should be as high as possible.
android.api = 33

# (int) Minimum API your APK / AAB will support. (Bumped to 24 for modern Android stability)
android.minapi = 24

# (str) Android NDK version to use
android.ndk = 25b

# (bool) If True, then skip trying to update the Android sdk
android.skip_update = False

# (bool) If True, then automatically accept SDK license agreements.
android.accept_sdk_license = True

# (bool) Enable AndroidX support (CRUCIAL for API 33+)
android.enable_androidx = True

# (list) The Android archs to build for
# CRUCIAL: Limit to arm64-v8a to prevent GitHub RAM crashes
android.archs = arm64-v8a

# (str) The format used to package the app for debug mode
android.debug_artifact = apk

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug)
log_level = 2

# (int) Display warning if buildozer is run as root
warn_on_root = 1
