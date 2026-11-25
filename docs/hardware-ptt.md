# Hardware PTT

Reticom routes physical PTT input through the same Field and Command PTT
pipeline as the on-screen hold button.

## Laptop and Command

Command accepts `F9` and `F13` through `F24`. Dedicated USB/Bluetooth PTT
switches that present themselves as one of those keyboard keys work without a
special driver. Pressing starts the current public team PTT; releasing sends
the live stream or the existing recorded fallback. Modifier combinations are
ignored, and text fields keep their normal keyboard behavior.

The WebView must have keyboard focus for laptop key events. The PTT is stopped
when the window loses focus or the page is unloaded, preventing a stuck
transmission.

## Android and Field

Field accepts:

- wired/Bluetooth headset hook buttons;
- Bluetooth PTT devices that emit `MEDIA_PLAY_PAUSE`;
- USB/Bluetooth devices mapped to `F9` or `F13` through `F24`.

The foreground Reticom service owns a native Android `MediaSession` so headset
media-button events can reach Field while the app is backgrounded. The native
activity also handles function-key events while visible. Android sends only a
`down` and `up` signal to the WebView; microphone capture, live voice, clip
fallback, delivery and privacy behavior remain in the existing PTT pipeline.

Leaving Field or pausing the activity sends a defensive `up` signal.

## Verification

The keyboard mapping rules are covered by `tests/ptt-input.test.mjs`. A full
Android compile requires a local JDK and Android SDK; the repository's Gradle
wrapper is the authoritative build command:

```powershell
cd android
.\gradlew.bat :app:assembleDebug
```
