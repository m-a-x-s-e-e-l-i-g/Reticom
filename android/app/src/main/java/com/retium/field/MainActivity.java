package com.retium.field;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.media.AudioAttributes;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import android.util.Log;
import android.view.Surface;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import com.google.mlkit.vision.barcode.common.Barcode;
import com.google.mlkit.vision.codescanner.GmsBarcodeScanner;
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions;
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Set;

public final class MainActivity extends Activity {
    private static final String LOCAL_URL = "http://127.0.0.1:8781/";
    private static final String TTS_TAG = "ReticomTTS";
    private static final String TTS_PREFERENCES = "retium-tts";
    private static final String TTS_VOICE_KEY = "voice-name";
    private static final int PERMISSIONS_REQUEST = 8143;
    private static final int MICROPHONE_PERMISSION_REQUEST = 8144;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private WebView webView;
    private GmsBarcodeScanner qrScanner;
    private TextToSpeech textToSpeech;
    private volatile boolean textToSpeechReady;
    private PermissionRequest pendingAudioPermissionRequest;
    private SensorManager sensorManager;
    private Sensor rotationSensor;
    private SensorEventListener compassListener;
    private float lastCompassHeading = Float.NaN;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(9, 13, 10));
        getWindow().setNavigationBarColor(Color.rgb(9, 13, 10));
        startNodeService();
        requestFieldPermissions();
        configureQrScanner();
        configureWebView();
        configureCompass();
        configureTextToSpeech();
        waitForNode(0);
    }

    @Override
    protected void onStart() {
        super.onStart();
        startCompass();
        updateNodeServiceVisibility(true);
    }

    @Override
    protected void onStop() {
        stopCompass();
        updateNodeServiceVisibility(false);
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        if (pendingAudioPermissionRequest != null) {
            pendingAudioPermissionRequest.deny();
            pendingAudioPermissionRequest = null;
        }
        textToSpeechReady = false;
        if (textToSpeech != null) {
            textToSpeech.stop();
            textToSpeech.shutdown();
            textToSpeech = null;
        }
        if (webView != null) {
            webView.removeJavascriptInterface("RetiumAndroid");
            webView.destroy();
        }
        super.onDestroy();
    }

    private void startNodeService() {
        Intent service = new Intent(this, RetiumNodeService.class)
                .setAction(BackgroundAlertController.ACTION_APP_VISIBILITY)
                .putExtra(BackgroundAlertController.EXTRA_APP_FOREGROUND, true);
        startForegroundService(service);
    }

    private void configureCompass() {
        sensorManager = (SensorManager) getSystemService(SENSOR_SERVICE);
        rotationSensor = sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR);
        compassListener = new SensorEventListener() {
            @Override
            public void onSensorChanged(SensorEvent event) {
                float[] rotation = new float[9];
                float[] adjusted = new float[9];
                SensorManager.getRotationMatrixFromVector(rotation, event.values);
                int displayRotation = getWindowManager().getDefaultDisplay().getRotation();
                switch (displayRotation) {
                    case Surface.ROTATION_90:
                        SensorManager.remapCoordinateSystem(rotation, SensorManager.AXIS_Y, SensorManager.AXIS_MINUS_X, adjusted);
                        break;
                    case Surface.ROTATION_180:
                        SensorManager.remapCoordinateSystem(rotation, SensorManager.AXIS_MINUS_X, SensorManager.AXIS_MINUS_Y, adjusted);
                        break;
                    case Surface.ROTATION_270:
                        SensorManager.remapCoordinateSystem(rotation, SensorManager.AXIS_MINUS_Y, SensorManager.AXIS_X, adjusted);
                        break;
                    default:
                        System.arraycopy(rotation, 0, adjusted, 0, rotation.length);
                }
                float[] orientation = new float[3];
                SensorManager.getOrientation(adjusted, orientation);
                float heading = (float) ((Math.toDegrees(orientation[0]) + 360.0) % 360.0);
                float change = Float.isNaN(lastCompassHeading)
                        ? 360f
                        : Math.abs(((heading - lastCompassHeading + 540f) % 360f) - 180f);
                if (change < 0.35f) return;
                lastCompassHeading = heading;
                emitCompassHeading();
            }

            @Override
            public void onAccuracyChanged(Sensor sensor, int accuracy) {
                // Keep the tape available while Android recalibrates magnetic accuracy.
            }
        };
    }

    private void startCompass() {
        if (sensorManager != null && rotationSensor != null && compassListener != null) {
            sensorManager.registerListener(compassListener, rotationSensor, SensorManager.SENSOR_DELAY_UI);
        }
    }

    private void emitCompassHeading() {
        if (webView == null || Float.isNaN(lastCompassHeading)) return;
        webView.evaluateJavascript(
                "window.retiumAndroidHeading&&window.retiumAndroidHeading(" + lastCompassHeading + ")",
                null
        );
    }

    private void stopCompass() {
        if (sensorManager != null && compassListener != null) {
            sensorManager.unregisterListener(compassListener);
        }
        lastCompassHeading = Float.NaN;
    }

    private void updateNodeServiceVisibility(boolean foreground) {
        Intent service = new Intent(this, RetiumNodeService.class)
                .setAction(BackgroundAlertController.ACTION_APP_VISIBILITY)
                .putExtra(BackgroundAlertController.EXTRA_APP_FOREGROUND, foreground);
        startService(service);
    }

    private void updateBackgroundAlertPreference(boolean enabled) {
        BackgroundAlertController.setAlertsEnabled(this, enabled);
        Intent service = new Intent(this, RetiumNodeService.class)
                .setAction(BackgroundAlertController.ACTION_ALERTS_ENABLED)
                .putExtra(BackgroundAlertController.EXTRA_ALERTS_ENABLED, enabled);
        startService(service);
    }

    private void notifyBackgroundTtsVoiceChanged() {
        Intent service = new Intent(this, RetiumNodeService.class)
                .setAction(BackgroundAlertController.ACTION_TTS_VOICE_CHANGED);
        startService(service);
    }

    private void updateAutomaticLocationPreference(boolean enabled) {
        LocationUpdateController.setEnabled(this, enabled);
        Intent service = new Intent(this, RetiumNodeService.class)
                .setAction(RetiumNodeService.ACTION_AUTOMATIC_LOCATION)
                .putExtra(RetiumNodeService.EXTRA_AUTOMATIC_LOCATION, enabled);
        startService(service);
    }

    private void requestFieldPermissions() {
        List<String> needed = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.RECORD_AUDIO);
        }
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.ACCESS_FINE_LOCATION);
        }
        if (android.os.Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!needed.isEmpty()) {
            requestPermissions(needed.toArray(new String[0]), PERMISSIONS_REQUEST);
        }
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode,
            String[] permissions,
            int[] grantResults
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSIONS_REQUEST) {
            boolean granted = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                    == PackageManager.PERMISSION_GRANTED;
            updateAutomaticLocationPreference(
                    granted && LocationUpdateController.isEnabled(this)
            );
        }
        if (requestCode == PERMISSIONS_REQUEST || requestCode == MICROPHONE_PERMISSION_REQUEST) {
            resolvePendingAudioPermissionRequest();
        }
    }

    private void configureQrScanner() {
        GmsBarcodeScannerOptions options = new GmsBarcodeScannerOptions.Builder()
                .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                .enableAutoZoom()
                .build();
        qrScanner = GmsBarcodeScanning.getClient(this, options);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView() {
        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(9, 13, 10));
        clearWebCacheAfterUpgrade();
        FrameLayout container = new FrameLayout(this);
        container.setBackgroundColor(Color.rgb(9, 13, 10));
        container.setOnApplyWindowInsetsListener((view, windowInsets) -> {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                Insets bars = windowInsets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else {
                view.setPadding(
                        windowInsets.getSystemWindowInsetLeft(),
                        windowInsets.getSystemWindowInsetTop(),
                        windowInsets.getSystemWindowInsetRight(),
                        windowInsets.getSystemWindowInsetBottom()
                );
            }
            return windowInsets;
        });
        container.addView(
                webView,
                new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT
                )
        );
        setContentView(container);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        webView.addJavascriptInterface(new AndroidBridge(), "RetiumAndroid");
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                if (url.startsWith(LOCAL_URL)) {
                    view.clearHistory();
                    emitCompassHeading();
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if ("127.0.0.1".equals(uri.getHost()) || "localhost".equals(uri.getHost())) {
                    return false;
                }
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
                return true;
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> {
                    if (!isLocalOrigin(request.getOrigin()) || !requestsAudioCapture(request)) {
                        request.deny();
                        return;
                    }
                    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                            == PackageManager.PERMISSION_GRANTED) {
                        request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
                        return;
                    }
                    if (pendingAudioPermissionRequest != null) pendingAudioPermissionRequest.deny();
                    pendingAudioPermissionRequest = request;
                    requestPermissions(
                            new String[]{Manifest.permission.RECORD_AUDIO},
                            MICROPHONE_PERMISSION_REQUEST
                    );
                });
            }

            @Override
            public void onPermissionRequestCanceled(PermissionRequest request) {
                if (pendingAudioPermissionRequest == request) {
                    pendingAudioPermissionRequest = null;
                }
            }

            @Override
            public void onGeolocationPermissionsShowPrompt(
                    String origin,
                    GeolocationPermissions.Callback callback
            ) {
                callback.invoke(
                        origin,
                        checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                                == PackageManager.PERMISSION_GRANTED,
                        false
                );
            }
        });
        webView.loadDataWithBaseURL(
                null,
                "<html><body style='margin:0;background:#090d0a;color:#82936f;display:grid;place-items:center;height:100vh;font:12px monospace;letter-spacing:.12em'>STARTING RETICULUM…</body></html>",
                "text/html",
                "UTF-8",
                null
        );
    }

    private boolean isLocalOrigin(Uri origin) {
        return origin != null
                && "http".equals(origin.getScheme())
                && "127.0.0.1".equals(origin.getHost())
                && origin.getPort() == 8781;
    }

    private boolean requestsAudioCapture(PermissionRequest request) {
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) return true;
        }
        return false;
    }

    private void resolvePendingAudioPermissionRequest() {
        if (pendingAudioPermissionRequest == null) return;
        PermissionRequest request = pendingAudioPermissionRequest;
        pendingAudioPermissionRequest = null;
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED) {
            request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
        } else {
            request.deny();
        }
    }

    private void clearWebCacheAfterUpgrade() {
        try {
            long updateTime = getPackageManager()
                    .getPackageInfo(getPackageName(), 0)
                    .lastUpdateTime;
            SharedPreferences preferences = getSharedPreferences(
                    "retium-shell",
                    MODE_PRIVATE
            );
            if (preferences.getLong("cached-update-time", -1) != updateTime) {
                webView.clearCache(true);
                preferences.edit().putLong("cached-update-time", updateTime).apply();
            }
        } catch (PackageManager.NameNotFoundException error) {
            webView.clearCache(true);
        }
    }

    private void configureTextToSpeech() {
        textToSpeech = new TextToSpeech(getApplicationContext(), status -> {
            if (status != TextToSpeech.SUCCESS || textToSpeech == null) {
                Log.e(TTS_TAG, "Android TTS initialisation failed: " + status);
                textToSpeechReady = false;
                return;
            }
            int languageResult = textToSpeech.setLanguage(Locale.US);
            if (languageResult == TextToSpeech.LANG_MISSING_DATA
                    || languageResult == TextToSpeech.LANG_NOT_SUPPORTED) {
                languageResult = textToSpeech.setLanguage(Locale.getDefault());
            }
            if (languageResult == TextToSpeech.LANG_MISSING_DATA
                    || languageResult == TextToSpeech.LANG_NOT_SUPPORTED) {
                Log.e(TTS_TAG, "No usable Android TTS language is installed");
                textToSpeechReady = false;
                return;
            }
            textToSpeech.setSpeechRate(0.96f);
            textToSpeech.setPitch(0.92f);
            restoreTtsVoice();
            textToSpeech.setAudioAttributes(
                    new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build()
            );
            textToSpeech.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override
                public void onStart(String utteranceId) {
                    Log.i(TTS_TAG, "started " + utteranceId);
                    emitTtsEvent(utteranceId, "started");
                }

                @Override
                public void onDone(String utteranceId) {
                    Log.i(TTS_TAG, "done " + utteranceId);
                    emitTtsEvent(utteranceId, "done");
                }

                @Override
                public void onError(String utteranceId) {
                    Log.e(TTS_TAG, "failed " + utteranceId);
                    emitTtsEvent(utteranceId, "error");
                }

                @Override
                public void onError(String utteranceId, int errorCode) {
                    Log.e(TTS_TAG, "failed " + utteranceId + ": " + errorCode);
                    emitTtsEvent(utteranceId, "error");
                }

                @Override
                public void onStop(String utteranceId, boolean interrupted) {
                    emitTtsEvent(utteranceId, "stopped");
                }
            });
            textToSpeechReady = true;
            Log.i(TTS_TAG, "Android TTS ready");
        });
    }

    private Voice findTtsVoice(String voiceName) {
        if (textToSpeech == null || voiceName == null || voiceName.isEmpty()) return null;
        Set<Voice> voices = textToSpeech.getVoices();
        if (voices == null) return null;
        for (Voice voice : voices) {
            if (voiceName.equals(voice.getName())) return voice;
        }
        return null;
    }

    private void restoreTtsVoice() {
        String voiceName = getSharedPreferences(TTS_PREFERENCES, MODE_PRIVATE)
                .getString(TTS_VOICE_KEY, "");
        Voice voice = TtsVoicePolicy.apply(textToSpeech, voiceName);
        if (voice != null) {
            Log.i(TTS_TAG, (voiceName == null || voiceName.isEmpty()
                    ? "Applied default voice "
                    : "Restored voice ") + voice.getName());
        }
    }

    private boolean selectTtsVoice(String voiceName, boolean persist) {
        if (!textToSpeechReady || textToSpeech == null) return false;
        Voice voice = findTtsVoice(voiceName);
        if (voice == null || textToSpeech.setVoice(voice) != TextToSpeech.SUCCESS) return false;
        if (persist) {
            getSharedPreferences(TTS_PREFERENCES, MODE_PRIVATE)
                    .edit()
                    .putString(TTS_VOICE_KEY, voiceName)
                    .apply();
        }
        return true;
    }

    private void emitTtsEvent(String utteranceId, String state) {
        runOnUiThread(() -> {
            if (webView == null) return;
            webView.evaluateJavascript(
                    "window.retiumAndroidTtsEvent?.("
                            + JSONObject.quote(utteranceId) + ","
                            + JSONObject.quote(state) + ")",
                    null
            );
        });
    }

    private void waitForNode(int attempt) {
        new Thread(() -> {
            boolean ready = false;
            try {
                HttpURLConnection connection = (HttpURLConnection)
                        new URL(LOCAL_URL + "api/state").openConnection();
                connection.setConnectTimeout(500);
                connection.setReadTimeout(500);
                ready = connection.getResponseCode() == 200;
                connection.disconnect();
            } catch (Exception ignored) {
                // The Python runtime and Reticulum need a moment on first launch.
            }
            boolean nodeReady = ready;
            handler.post(() -> {
                if (nodeReady) {
                    webView.loadUrl(LOCAL_URL);
                } else if (attempt < 120) {
                    handler.postDelayed(() -> waitForNode(attempt + 1), 500);
                } else {
                    webView.loadDataWithBaseURL(
                            null,
                            "<html><body style='margin:0;background:#090d0a;color:#b5816d;padding:32px;font:14px sans-serif'>Reticulum did not start. Close and reopen Reticom, then inspect Android system logs if this repeats.</body></html>",
                            "text/html",
                            "UTF-8",
                            null
                    );
                }
            });
        }, "retium-health").start();
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    private final class AndroidBridge {
        @JavascriptInterface
        public boolean isTtsReady() {
            return textToSpeechReady && textToSpeech != null;
        }

        @JavascriptInterface
        public boolean speakText(String utteranceId, String text) {
            if (!isTtsReady() || text == null || text.trim().isEmpty()) return false;
            Bundle parameters = new Bundle();
            parameters.putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 0.9f);
            return textToSpeech.speak(
                    text,
                    TextToSpeech.QUEUE_FLUSH,
                    parameters,
                    utteranceId
            ) == TextToSpeech.SUCCESS;
        }

        @JavascriptInterface
        public String getTtsVoices() {
            JSONObject payload = new JSONObject();
            JSONArray available = new JSONArray();
            try {
                if (isTtsReady()) {
                    Set<Voice> voiceSet = textToSpeech.getVoices();
                    List<Voice> voices = voiceSet == null
                            ? new ArrayList<>()
                            : new ArrayList<>(voiceSet);
                    voices.sort(Comparator
                            .comparing((Voice voice) -> voice.getLocale().getDisplayName(Locale.ENGLISH))
                            .thenComparing(Voice::getName));
                    for (Voice voice : voices) {
                        JSONObject item = new JSONObject();
                        item.put("id", voice.getName());
                        item.put("locale", voice.getLocale().toLanguageTag());
                        item.put("language", voice.getLocale().getDisplayName(Locale.ENGLISH));
                        item.put("network", voice.isNetworkConnectionRequired());
                        available.put(item);
                    }
                    Voice selected = textToSpeech.getVoice();
                    payload.put("selected", selected == null ? "" : selected.getName());
                } else {
                    payload.put("selected", "");
                }
                payload.put("voices", available);
                return payload.toString();
            } catch (Exception error) {
                Log.e(TTS_TAG, "Could not list Android TTS voices", error);
                return "{\"selected\":\"\",\"voices\":[]}";
            }
        }

        @JavascriptInterface
        public boolean setTtsVoice(String voiceName) {
            boolean selected = selectTtsVoice(voiceName, true);
            if (selected) notifyBackgroundTtsVoiceChanged();
            return selected;
        }

        @JavascriptInterface
        public boolean previewTtsVoice(String voiceName) {
            if (!selectTtsVoice(voiceName, true)) return false;
            notifyBackgroundTtsVoiceChanged();
            Bundle parameters = new Bundle();
            parameters.putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 0.9f);
            textToSpeech.stop();
            return textToSpeech.speak(
                    "Reticom voice check. Incoming message ready.",
                    TextToSpeech.QUEUE_FLUSH,
                    parameters,
                    "retium-voice-preview-" + System.currentTimeMillis()
            ) == TextToSpeech.SUCCESS;
        }

        @JavascriptInterface
        public void stopTts() {
            if (textToSpeech != null) textToSpeech.stop();
        }

        @JavascriptInterface
        public void setIncomingAlertsEnabled(boolean enabled) {
            updateBackgroundAlertPreference(enabled);
        }

        @JavascriptInterface
        public boolean isAutomaticLocationEnabled() {
            return LocationUpdateController.isEnabled(MainActivity.this);
        }

        @JavascriptInterface
        public void setAutomaticLocationEnabled(boolean enabled) {
            updateAutomaticLocationPreference(enabled);
        }

        @JavascriptInterface
        public void syncSeenIncomingEventIds(String encodedIds) {
            BackgroundAlertController.mergeSeenEventIds(MainActivity.this, encodedIds);
        }

        @JavascriptInterface
        public String getSeenIncomingEventIds() {
            return BackgroundAlertController.seenEventIdsJson(MainActivity.this);
        }

        @JavascriptInterface
        public void scanQr() {
            runOnUiThread(() -> qrScanner.startScan()
                    .addOnSuccessListener(barcode -> {
                        String value = barcode.getRawValue();
                        if (value == null) value = "";
                        webView.evaluateJavascript(
                                "window.retiumAndroidQrResult(" + JSONObject.quote(value) + ")",
                                null
                        );
                    })
                    .addOnCanceledListener(() -> webView.evaluateJavascript(
                            "window.retiumAndroidQrError('')",
                            null
                    ))
                    .addOnFailureListener(error -> webView.evaluateJavascript(
                            "window.retiumAndroidQrError(" + JSONObject.quote(
                                    "QR scanner unavailable: " + error.getMessage()
                            ) + ")",
                            null
                    )));
        }
    }
}
