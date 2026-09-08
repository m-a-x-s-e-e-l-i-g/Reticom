# Local PTT transcription

Each device transcribes PTT audio locally, including Command workspaces joined to a team hosted on another device. Audio not already stored locally is fetched through the existing authenticated Reticulum audio route. Recognition does not use a cloud service.

## Android

The APK includes whisper.cpp v1.7.6 (commit a8d002cfd879315632a579e73f0148d06959de36), its ARM64 CPU library, and the multilingual Whisper base model (147,951,465 bytes). CMake verifies the source archive SHA-256; Gradle verifies the model SHA-256. The first transcription extracts the packaged model into private app storage. There is no model download at runtime. Model and engine licenses are included under assets/licenses.

Android MediaExtractor and MediaCodec decode the recorded WebM, Ogg or MP4 into PCM, mix channels to mono and resample to 16 kHz before native recognition. Inference uses up to four CPU threads and releases the model context after each clip. The default language is English, consistent with desktop; RETIUM_TRANSCRIPTION_LANGUAGE=auto enables detection.

Build through build-android.ps1. The build installs pinned NDK 27.0.12077973 and CMake 3.22.1. Native sources: https://github.com/ggml-org/whisper.cpp/tree/v1.7.6 . Model: https://huggingface.co/ggerganov/whisper.cpp .

## Command

Docker includes faster-whisper and uses CPU int8 inference. Its first transcription downloads the configured model. Model loading and inference run in a background worker. Joined teams use the local worker instead of requesting a transcript from the remote host.

Results are cached by clip ID, audio hash, model and requested language. Failed jobs retain a readable error, and tapping audio retries them. Errors are logged without recording transcript text. Clear speech does not guarantee an exact transcript; verify recognized place names and commands.

## Device verification

Use Windows-native ADB only, never WSL ADB. If port 5037 conflicts, use adb.exe -P 5038. Read the installed versionCode before building, install with -r, and do not uninstall or clear app data to resolve an upgrade issue.
