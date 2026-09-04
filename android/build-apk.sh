#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RETICOM_TRANSPORT_HOST="${1:-${RETICOM_TRANSPORT_HOST:-}}"
RETICOM_TRANSPORT_PORT="${2:-${RETICOM_TRANSPORT_PORT:-4242}}"
TOOLCHAIN_DIR="${RETIUM_ANDROID_TOOLCHAIN:-/var/tmp/retium-android-toolchain}"
DOWNLOAD_DIR="$TOOLCHAIN_DIR/downloads"
JDK_ROOT="$TOOLCHAIN_DIR/jdk"
SDK_ROOT="$TOOLCHAIN_DIR/sdk"
GRADLE_ROOT="$TOOLCHAIN_DIR/gradle"
UV_ROOT="$TOOLCHAIN_DIR/uv"

mkdir -p "$DOWNLOAD_DIR" "$JDK_ROOT" "$SDK_ROOT" "$GRADLE_ROOT" "$UV_ROOT"

if ! find "$JDK_ROOT" -type f -path '*/bin/java' -print -quit | grep -q .; then
    JDK_ARCHIVE="$DOWNLOAD_DIR/temurin-jdk17.tar.gz"
    echo "Downloading local Java 17 toolchain..."
    curl -fL --retry 3 \
        'https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse' \
        -o "$JDK_ARCHIVE"
    tar -xzf "$JDK_ARCHIVE" -C "$JDK_ROOT"
fi
JAVA_HOME="$(dirname "$(dirname "$(find "$JDK_ROOT" -type f -path '*/bin/java' -print -quit)")")"
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

if [ ! -x "$UV_ROOT/bin/uv" ]; then
    echo "Downloading local Python toolchain manager..."
    curl -LsSf https://astral.sh/uv/install.sh | \
        env UV_INSTALL_DIR="$UV_ROOT/bin" UV_NO_MODIFY_PATH=1 sh
fi
if ! find "$TOOLCHAIN_DIR/python" -type f -name python3.11 -print -quit 2>/dev/null | grep -q .; then
    echo "Downloading local Python 3.11 build interpreter..."
    "$UV_ROOT/bin/uv" python install 3.11 --install-dir "$TOOLCHAIN_DIR/python"
fi
RETIUM_BUILD_PYTHON="$(find "$TOOLCHAIN_DIR/python" -type f -name python3.11 -print -quit)"
export RETIUM_BUILD_PYTHON

SDK_MANAGER="$SDK_ROOT/cmdline-tools/latest/bin/sdkmanager"
if [ ! -x "$SDK_MANAGER" ]; then
    SDK_ARCHIVE="$DOWNLOAD_DIR/android-commandlinetools.zip"
    SDK_EXTRACT="$TOOLCHAIN_DIR/sdk-extract"
    if [ ! -f "$SDK_ARCHIVE" ]; then
        echo "Downloading Android command-line tools..."
        curl -fL --retry 3 \
            'https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip' \
            -o "$SDK_ARCHIVE"
    fi
    echo '4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583  '"$SDK_ARCHIVE" | sha256sum -c -
    mkdir -p "$SDK_EXTRACT" "$SDK_ROOT/cmdline-tools/latest"
    python3 -m zipfile -e "$SDK_ARCHIVE" "$SDK_EXTRACT"
    cp -R "$SDK_EXTRACT/cmdline-tools/." "$SDK_ROOT/cmdline-tools/latest/"
    chmod +x "$SDK_ROOT/cmdline-tools/latest/bin/"*
fi
export ANDROID_HOME="$SDK_ROOT"
export ANDROID_SDK_ROOT="$SDK_ROOT"
export PATH="$SDK_ROOT/platform-tools:$SDK_ROOT/cmdline-tools/latest/bin:$PATH"

yes | "$SDK_MANAGER" --sdk_root="$SDK_ROOT" --licenses >/dev/null || true
"$SDK_MANAGER" --sdk_root="$SDK_ROOT" \
    'platform-tools' 'platforms;android-35' 'build-tools;35.0.0'

GRADLE_BIN="$GRADLE_ROOT/gradle-8.9/bin/gradle"
if [ ! -x "$GRADLE_BIN" ]; then
    GRADLE_ARCHIVE="$DOWNLOAD_DIR/gradle-8.9-bin.zip"
    if [ ! -f "$GRADLE_ARCHIVE" ]; then
        echo "Downloading Gradle 8.9..."
        curl -fL --retry 3 \
            'https://services.gradle.org/distributions/gradle-8.9-bin.zip' \
            -o "$GRADLE_ARCHIVE"
    fi
    python3 -m zipfile -e "$GRADLE_ARCHIVE" "$GRADLE_ROOT"
    chmod +x "$GRADLE_BIN"
fi

printf 'sdk.dir=%s\n' "$SDK_ROOT" > "$SCRIPT_DIR/local.properties"

cd "$SCRIPT_DIR"
if [ ! -x "$SCRIPT_DIR/gradlew" ]; then
    "$GRADLE_BIN" wrapper --gradle-version 8.9
fi
GRADLE_ARGS=(--no-daemon :app:assembleDebug "-PreticomTransportPort=$RETICOM_TRANSPORT_PORT")
if [ -n "$RETICOM_TRANSPORT_HOST" ]; then
    GRADLE_ARGS+=("-PreticomTransportHost=$RETICOM_TRANSPORT_HOST")
fi
./gradlew "${GRADLE_ARGS[@]}"

APK_SOURCE="$SCRIPT_DIR/app/build/outputs/apk/debug/app-debug.apk"
APK_TARGET="$PROJECT_ROOT/Reticom-Field-0.1.0-debug.apk"
cp "$APK_SOURCE" "$APK_TARGET"
sha256sum "$APK_TARGET" > "$APK_TARGET.sha256"
echo "APK: $APK_TARGET"
