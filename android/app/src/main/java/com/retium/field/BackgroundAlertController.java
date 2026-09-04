package com.retium.field;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.Bundle;
import android.os.PowerManager;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

final class BackgroundAlertController {
    static final String ACTION_APP_VISIBILITY = "com.retium.field.APP_VISIBILITY";
    static final String ACTION_ALERTS_ENABLED = "com.retium.field.ALERTS_ENABLED";
    static final String ACTION_TTS_VOICE_CHANGED = "com.retium.field.TTS_VOICE_CHANGED";
    static final String EXTRA_APP_FOREGROUND = "app_foreground";
    static final String EXTRA_ALERTS_ENABLED = "alerts_enabled";

    private static final String TAG = "ReticomBackground";
    private static final String LOCAL_BASE_URL = "http://127.0.0.1:8781/";
    private static final String ALERT_CHANNEL_ID = "reticom_incoming_alerts";
    private static final String ALERT_PREFERENCES = "retium-background-alerts";
    private static final String ALERTS_ENABLED_KEY = "enabled";
    private static final String SEEN_IDS_KEY = "seen-event-ids";
    private static final String SEEN_INITIALIZED_KEY = "seen-initialized";
    private static final String TEAM_DESTINATION_KEY = "team-destination";
    private static final String TTS_PREFERENCES = "retium-tts";
    private static final String TTS_VOICE_KEY = "voice-name";
    private static final int MAX_SEEN_EVENTS = 200;

    private final Context context;
    private final NotificationManager notificationManager;
    private final ScheduledExecutorService executor = Executors.newSingleThreadScheduledExecutor();
    private final CountDownLatch ttsReadyLatch = new CountDownLatch(1);
    private volatile boolean appForeground;
    private volatile boolean stopped;
    private volatile boolean ttsReady;
    private volatile TextToSpeech textToSpeech;
    private volatile MediaPlayer activePlayer;
    private volatile CountDownLatch activePlaybackLatch;
    private volatile CountDownLatch activeSpeechLatch;

    BackgroundAlertController(Context context) {
        this.context = context.getApplicationContext();
        this.notificationManager = context.getSystemService(NotificationManager.class);
        createAlertChannel();
        initializeTextToSpeech();
    }

    void start() {
        executor.scheduleWithFixedDelay(this::pollSafely, 4, 15, TimeUnit.SECONDS);
    }

    void setAppForeground(boolean foreground) {
        appForeground = foreground;
        if (!foreground && !stopped) executor.execute(this::pollSafely);
    }

    void setAlertsEnabled(boolean enabled) {
        setAlertsEnabled(context, enabled);
        if (!enabled) stopPlayback();
    }

    void refreshTtsVoice() {
        TextToSpeech engine = textToSpeech;
        if (engine != null && ttsReady) executor.execute(() -> restoreTtsVoice(engine));
    }

    void stop() {
        stopped = true;
        executor.shutdownNow();
        stopPlayback();
        TextToSpeech engine = textToSpeech;
        textToSpeech = null;
        ttsReady = false;
        if (engine != null) engine.shutdown();
    }

    static void setAlertsEnabled(Context context, boolean enabled) {
        SharedPreferences preferences = context.getSharedPreferences(
                ALERT_PREFERENCES,
                Context.MODE_PRIVATE
        );
        boolean wasEnabled = preferences.getBoolean(ALERTS_ENABLED_KEY, true);
        SharedPreferences.Editor edit = preferences.edit().putBoolean(ALERTS_ENABLED_KEY, enabled);
        if (enabled && !wasEnabled) edit.putBoolean(SEEN_INITIALIZED_KEY, false);
        edit.apply();
    }

    static boolean alertsEnabled(Context context) {
        return context.getSharedPreferences(ALERT_PREFERENCES, Context.MODE_PRIVATE)
                .getBoolean(ALERTS_ENABLED_KEY, true);
    }

    static synchronized void mergeSeenEventIds(Context context, String encodedIds) {
        try {
            LinkedHashSet<String> seen = readSeenEventIds(context);
            JSONArray incoming = new JSONArray(encodedIds == null ? "[]" : encodedIds);
            for (int index = 0; index < incoming.length(); index++) {
                String eventId = incoming.optString(index, "");
                if (!eventId.isEmpty()) {
                    seen.remove(eventId);
                    seen.add(eventId);
                }
            }
            writeSeenEventIds(context, seen, true);
        } catch (Exception error) {
            Log.w(TAG, "Could not merge foreground event IDs", error);
        }
    }

    static synchronized String seenEventIdsJson(Context context) {
        JSONArray result = new JSONArray();
        for (String eventId : readSeenEventIds(context)) result.put(eventId);
        return result.toString();
    }

    private void pollSafely() {
        if (stopped || appForeground || !alertsEnabled(context)) return;
        try {
            pollIncomingEvents();
        } catch (Exception error) {
            Log.w(TAG, "Background Reticulum feed sync failed", error);
        }
    }

    private void pollIncomingEvents() throws Exception {
        JSONObject state = requestJson("api/state", 3_000);
        JSONObject team = state.optJSONObject("team");
        if (team == null || !team.optBoolean("joined", false)) return;
        String destination = team.optString("destination", "");
        JSONObject user = state.optJSONObject("user");
        String localCallsign = user == null ? "Field" : user.optString("callsign", "Field");
        JSONObject network = state.optJSONObject("network");
        String localIdentity = network == null ? "" : network.optString("identity", "");
        JSONObject feed = requestJson("api/feed", 40_000);
        JSONArray rawEvents = feed.optJSONArray("events");
        if (rawEvents == null) return;
        JSONArray privateEvents = feed.optJSONArray("private_events");

        List<JSONObject> alertable = new ArrayList<>();
        for (int index = 0; index < rawEvents.length(); index++) {
            JSONObject event = rawEvents.optJSONObject(index);
            if (event != null && isAlertable(event.optString("type", ""))) {
                alertable.add(event);
            }
        }
        if (privateEvents != null) {
            for (int index = 0; index < privateEvents.length(); index++) {
                JSONObject event = privateEvents.optJSONObject(index);
                if (event != null && isAlertable(event.optString("type", ""))) {
                    event.put("_local_callsign", localCallsign);
                    alertable.add(event);
                }
            }
        }
        alertable.sort(Comparator.comparingLong(event -> event.optLong("created_at", 0)));

        SharedPreferences preferences = context.getSharedPreferences(
                ALERT_PREFERENCES,
                Context.MODE_PRIVATE
        );
        boolean initialized = preferences.getBoolean(SEEN_INITIALIZED_KEY, false);
        String previousDestination = preferences.getString(TEAM_DESTINATION_KEY, "");
        LinkedHashSet<String> seen = readSeenEventIds(context);
        if (!initialized || !destination.equals(previousDestination)) {
            for (JSONObject event : alertable) addSeen(seen, event.optString("id", ""));
            writeSeenEventIds(context, seen, true);
            preferences.edit().putString(TEAM_DESTINATION_KEY, destination).apply();
            return;
        }

        for (JSONObject event : alertable) {
            if (stopped || appForeground || !alertsEnabled(context)) return;
            String eventId = event.optString("id", "");
            if (eventId.isEmpty() || seen.contains(eventId)) continue;
            addSeen(seen, eventId);
            writeSeenEventIds(context, seen, true);
            JSONObject eventNetwork = event.optJSONObject("network");
            String sender = eventNetwork == null ? "" : eventNetwork.optString("sender_hash", "");
            if (!localIdentity.isEmpty() && localIdentity.equals(sender)) continue;
            deliver(event);
        }
    }

    private void deliver(JSONObject event) {
        showIncomingNotification(event);
        if (appForeground || !alertsEnabled(context)) return;
        String type = event.optString("type", "");
        if ("ptt.broadcast".equals(type) || "private.ptt".equals(type)) {
            playIncomingVoice(event.optString("clip_id", ""));
        } else {
            playIncomingSpeech(speechText(event));
        }
    }

    private void playIncomingSpeech(String text) {
        if (text.isEmpty() || !awaitTtsReady() || appForeground || !alertsEnabled(context)) return;
        playUrlAndWait(LOCAL_BASE_URL + "audio/incoming-transmission-start.ogg", 15);
        if (appForeground || !alertsEnabled(context)) return;
        speakAndWait(text);
        if (!appForeground && alertsEnabled(context)) {
            playUrlAndWait(LOCAL_BASE_URL + "audio/incomming-transmission-end.ogg", 15);
        }
    }

    private void playIncomingVoice(String clipId) {
        if (clipId.isEmpty()) return;
        MediaPlayer voice = preparePlayer(LOCAL_BASE_URL + "api/audio/" + clipId);
        if (voice == null || appForeground || !alertsEnabled(context)) {
            if (voice != null) voice.release();
            return;
        }
        playUrlAndWait(LOCAL_BASE_URL + "audio/incoming-transmission-start.ogg", 15);
        if (appForeground || !alertsEnabled(context)) {
            voice.release();
            return;
        }
        playPreparedAndWait(voice, 45);
        if (!appForeground && alertsEnabled(context)) {
            playUrlAndWait(LOCAL_BASE_URL + "audio/incomming-transmission-end.ogg", 15);
        }
    }

    private MediaPlayer preparePlayer(String source) {
        try {
            MediaPlayer player = new MediaPlayer();
            player.setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build());
            player.setWakeMode(context, PowerManager.PARTIAL_WAKE_LOCK);
            player.setDataSource(source);
            player.prepare();
            return player;
        } catch (Exception error) {
            Log.w(TAG, "Could not prepare background audio " + source, error);
            return null;
        }
    }

    private void playUrlAndWait(String source, int timeoutSeconds) {
        MediaPlayer player = preparePlayer(source);
        if (player != null) playPreparedAndWait(player, timeoutSeconds);
    }

    private void playPreparedAndWait(MediaPlayer player, int timeoutSeconds) {
        CountDownLatch finished = new CountDownLatch(1);
        activePlayer = player;
        activePlaybackLatch = finished;
        player.setOnCompletionListener(value -> finished.countDown());
        player.setOnErrorListener((value, what, extra) -> {
            finished.countDown();
            return true;
        });
        try {
            player.start();
            finished.await(timeoutSeconds, TimeUnit.SECONDS);
        } catch (Exception error) {
            Log.w(TAG, "Background audio playback failed", error);
        } finally {
            if (activePlayer == player) activePlayer = null;
            if (activePlaybackLatch == finished) activePlaybackLatch = null;
            try {
                player.release();
            } catch (Exception ignored) {
                // MediaPlayer may already have released its native resources.
            }
        }
    }

    private boolean awaitTtsReady() {
        try {
            return ttsReady || (ttsReadyLatch.await(8, TimeUnit.SECONDS) && ttsReady);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private void speakAndWait(String text) {
        TextToSpeech engine = textToSpeech;
        if (engine == null || !ttsReady) return;
        CountDownLatch finished = new CountDownLatch(1);
        activeSpeechLatch = finished;
        String utteranceId = "retium-background-" + System.currentTimeMillis();
        Bundle parameters = new Bundle();
        parameters.putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 0.9f);
        if (engine.speak(text, TextToSpeech.QUEUE_FLUSH, parameters, utteranceId)
                != TextToSpeech.SUCCESS) {
            finished.countDown();
        }
        try {
            finished.await(60, TimeUnit.SECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
        } finally {
            if (activeSpeechLatch == finished) activeSpeechLatch = null;
        }
    }

    private void initializeTextToSpeech() {
        textToSpeech = new TextToSpeech(context, status -> {
            TextToSpeech engine = textToSpeech;
            if (status != TextToSpeech.SUCCESS || engine == null) {
                Log.e(TAG, "Background Android TTS initialisation failed: " + status);
                ttsReadyLatch.countDown();
                return;
            }
            int language = engine.setLanguage(Locale.US);
            if (language == TextToSpeech.LANG_MISSING_DATA
                    || language == TextToSpeech.LANG_NOT_SUPPORTED) {
                language = engine.setLanguage(Locale.getDefault());
            }
            if (language == TextToSpeech.LANG_MISSING_DATA
                    || language == TextToSpeech.LANG_NOT_SUPPORTED) {
                Log.e(TAG, "No usable background TTS language is installed");
                ttsReadyLatch.countDown();
                return;
            }
            engine.setSpeechRate(0.96f);
            engine.setPitch(0.92f);
            engine.setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build());
            restoreTtsVoice(engine);
            engine.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override
                public void onStart(String utteranceId) {
                    Log.i(TAG, "Background TTS started " + utteranceId);
                }

                @Override
                public void onDone(String utteranceId) {
                    finishSpeech();
                }

                @Override
                public void onError(String utteranceId) {
                    finishSpeech();
                }

                @Override
                public void onError(String utteranceId, int errorCode) {
                    finishSpeech();
                }

                @Override
                public void onStop(String utteranceId, boolean interrupted) {
                    finishSpeech();
                }
            });
            ttsReady = true;
            ttsReadyLatch.countDown();
        });
    }

    private void restoreTtsVoice(TextToSpeech engine) {
        String voiceName = context.getSharedPreferences(TTS_PREFERENCES, Context.MODE_PRIVATE)
                .getString(TTS_VOICE_KEY, "");
        Voice voice = TtsVoicePolicy.apply(engine, voiceName);
        if (voice != null) {
            Log.i(TAG, (voiceName == null || voiceName.isEmpty()
                    ? "Applied default background voice "
                    : "Restored background voice ") + voice.getName());
        }
    }

    private void finishSpeech() {
        CountDownLatch latch = activeSpeechLatch;
        if (latch != null) latch.countDown();
    }

    private void stopPlayback() {
        MediaPlayer player = activePlayer;
        activePlayer = null;
        if (player != null) {
            try {
                player.stop();
            } catch (Exception ignored) {
                // It may still be preparing or already complete.
            }
        }
        CountDownLatch playbackLatch = activePlaybackLatch;
        if (playbackLatch != null) playbackLatch.countDown();
        TextToSpeech engine = textToSpeech;
        if (engine != null) engine.stop();
        finishSpeech();
    }

    private void createAlertChannel() {
        NotificationChannel channel = new NotificationChannel(
                ALERT_CHANNEL_ID,
                "Incoming Reticom traffic",
                NotificationManager.IMPORTANCE_HIGH
        );
        channel.setDescription("Team and private messages, assignments, waypoint arrivals, and voice transmissions received over Reticulum");
        channel.setSound(null, null);
        channel.enableVibration(true);
        notificationManager.createNotificationChannel(channel);
    }

    private void showIncomingNotification(JSONObject event) {
        Intent launch = new Intent(context, MainActivity.class);
        launch.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pending = PendingIntent.getActivity(
                context,
                1,
                launch,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        String type = event.optString("type", "");
        String callsign = event.optString("callsign", "Unknown");
        String title;
        String body;
        if ("task.created".equals(type)) {
            title = "New assignment · " + callsign;
            body = event.optString("title", "Open Reticom for details");
        } else if ("waypoint.arrived".equals(type)) {
            title = "Waypoint reached";
            body = event.optString("operator_callsign", "Operator") + " reached "
                    + event.optString("waypoint_label", "the waypoint");
        } else if ("private.ptt".equals(type)) {
            title = callsign + " to you · private voice";
            body = "Playing private Reticulum audio";
        } else if ("ptt.broadcast".equals(type)) {
            title = callsign + " · voice transmission";
            body = "Playing received Reticulum audio";
        } else if ("private.message".equals(type)) {
            title = callsign + " to you · private message";
            body = event.optString("message", "Open Reticom to read");
        } else {
            title = callsign + " · message";
            body = event.optString("message", "Open Reticom to read");
        }
        String eventId = event.optString("id", String.valueOf(System.currentTimeMillis()));
        int notificationId = 20_000 + Math.floorMod(eventId.hashCode(), 30_000);
        Notification notification = new Notification.Builder(context, ALERT_CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_retium)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setContentIntent(pending)
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_MESSAGE)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .build();
        notificationManager.notify(notificationId, notification);
    }

    private String speechText(JSONObject event) {
        String callsign = event.optString("callsign", "Unknown");
        if ("private.message".equals(event.optString("type", ""))) {
            return callsign + " to " + event.optString("_local_callsign", "Field")
                    + ": " + event.optString("message", "");
        }
        if ("waypoint.arrived".equals(event.optString("type", ""))) {
            return event.optString("operator_callsign", "Operator") + " reached "
                    + event.optString("waypoint_label", "the waypoint") + ".";
        }
        if ("task.created".equals(event.optString("type", ""))) {
            String assignment = event.optString("assignee", "").isEmpty()
                    ? "Open to the team."
                    : "Assigned to " + event.optString("assignee") + ".";
            return "New assignment from " + callsign + ". "
                    + event.optString("title", "") + ". " + assignment;
        }
        return "Message from " + callsign + ". " + event.optString("message", "");
    }

    private JSONObject requestJson(String path, int readTimeoutMs) throws Exception {
        HttpURLConnection connection = (HttpURLConnection)
                new URL(LOCAL_BASE_URL + path).openConnection();
        connection.setConnectTimeout(2_000);
        connection.setReadTimeout(readTimeoutMs);
        connection.setUseCaches(false);
        try {
            int status = connection.getResponseCode();
            InputStream stream = status >= 200 && status < 300
                    ? connection.getInputStream()
                    : connection.getErrorStream();
            String body = readUtf8(stream);
            if (status < 200 || status >= 300) {
                throw new IllegalStateException("Local API returned " + status + ": " + body);
            }
            return new JSONObject(body);
        } finally {
            connection.disconnect();
        }
    }

    private static String readUtf8(InputStream stream) throws Exception {
        if (stream == null) return "";
        try (InputStream input = stream; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8_192];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static boolean isAlertable(String type) {
        return "chat.message".equals(type)
                || "private.message".equals(type)
                || "private.ptt".equals(type)
                || "ptt.broadcast".equals(type)
                || "task.created".equals(type)
                || "waypoint.arrived".equals(type);
    }

    private static synchronized LinkedHashSet<String> readSeenEventIds(Context context) {
        LinkedHashSet<String> seen = new LinkedHashSet<>();
        String encoded = context.getSharedPreferences(ALERT_PREFERENCES, Context.MODE_PRIVATE)
                .getString(SEEN_IDS_KEY, "[]");
        try {
            JSONArray values = new JSONArray(encoded == null ? "[]" : encoded);
            for (int index = 0; index < values.length(); index++) {
                String eventId = values.optString(index, "");
                if (!eventId.isEmpty()) seen.add(eventId);
            }
        } catch (Exception error) {
            Log.w(TAG, "Could not read seen event IDs", error);
        }
        return seen;
    }

    private static synchronized void writeSeenEventIds(
            Context context,
            LinkedHashSet<String> seen,
            boolean initialized
    ) {
        while (seen.size() > MAX_SEEN_EVENTS) {
            String oldest = seen.iterator().next();
            seen.remove(oldest);
        }
        JSONArray values = new JSONArray();
        for (String eventId : seen) values.put(eventId);
        context.getSharedPreferences(ALERT_PREFERENCES, Context.MODE_PRIVATE)
                .edit()
                .putString(SEEN_IDS_KEY, values.toString())
                .putBoolean(SEEN_INITIALIZED_KEY, initialized)
                .apply();
    }

    private static void addSeen(LinkedHashSet<String> seen, String eventId) {
        if (eventId == null || eventId.isEmpty()) return;
        seen.remove(eventId);
        seen.add(eventId);
    }
}
