[app]
title = ULTRA-Sonic Demonstrator
package.name = ultrasonics
package.domain = org.sssskyds

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt,ttf

version = 1.0

requirements = python3,kivy==2.3.0,pyjnius

orientation = portrait

android.permissions = INTERNET,BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_SCAN,BLUETOOTH_CONNECT,ACCESS_FINE_LOCATION
android.minapi = 21
android.api = 33
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.release_artifact = apk

[buildozer]
log_level = 2
warn_on_root = 1
