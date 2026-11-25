package com.retium.field;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.location.LocationRequest;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

final class LocationUpdateController implements LocationListener {
    private static final String TAG = "ReticomLocation";
    private static final String PREFERENCES = "retium-location";
    private static final String ENABLED_KEY = "automatic-location";
    private static final String JOINED_KEY = "team-joined";
    private static final String COMMS_ONLY_KEY = "comms-only";
    private static final String STATE_URL = "http://127.0.0.1:8781/api/state";
    private static final String SEND_URL = "http://127.0.0.1:8781/api/send";
    private static final long MIN_UPDATE_MS = 15_000L;
    private static final long STATIONARY_HEARTBEAT_MS = 60_000L;
    private static final float MIN_MOVEMENT_METERS = 10f;
    private static final float MAX_ACCURACY_METERS = 100f;
    private static final float MAX_UNCONFIRMED_SPEED_METERS_PER_SECOND = 35f;
    private static final float MAX_CONFIRMED_SPEED_METERS_PER_SECOND = 250f;

    private final Context context;
    private final LocationManager locationManager;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final AtomicBoolean sending = new AtomicBoolean(false);
    private final Runnable finishFresh = () -> finishFreshRequest(this.freshFix);
    private final Runnable checkGps = this::checkGpsFallback;
    private final Runnable stopProbe = this::stopGpsProbe;
    private final LocationListener gpsProbe = new LocationListener() {
        @Override public void onLocationChanged(Location location) { LocationUpdateController.this.onLocationChanged(location); }
        @Override public void onStatusChanged(String provider, int status, Bundle extras) {}
        @Override public void onProviderEnabled(String provider) {}
        @Override public void onProviderDisabled(String provider) {}
    };
    private Consumer<Location> uiListener;
    private Consumer<Location> freshListener;
    private String uiMode = "idle";
    private Location movementAnchor;
    private volatile Location bestRecent;
    private volatile Location latestFix;
    private Location freshFix;
    private long freshAfter;
    private long lastMovement;
    private long lastUsableFix;
    private long interval;
    private boolean highAccuracy;
    private boolean precise;
    private boolean gpsProbing;
    private boolean joined;
    private boolean active;
    private volatile int shareGeneration;
    private volatile boolean sharing;
    private Location lastSent;
    private Location pendingRelocation;
    private long lastSentAt;

    LocationUpdateController(Context context) {
        this.context = context.getApplicationContext();
        this.locationManager = (LocationManager) context.getSystemService(Context.LOCATION_SERVICE);
        this.joined = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).getBoolean(JOINED_KEY, false);
    }

    static boolean isEnabled(Context context) {
        return context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .getBoolean(ENABLED_KEY, true);
    }

    static void setEnabled(Context context, boolean enabled) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .edit()
                .putBoolean(ENABLED_KEY, enabled)
                .apply();
    }

    static boolean isCommsOnly(Context context) {
        return context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).getBoolean(COMMS_ONLY_KEY, false);
    }

    static void setCommsOnly(Context context, boolean enabled) {
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit().putBoolean(COMMS_ONLY_KEY, enabled).apply();
    }

    void start() {
        active = true;
        refreshRequests();
    }

    static boolean hasPermission(Context context) {
        return hasPrecisePermission(context) || context.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION)
                == PackageManager.PERMISSION_GRANTED;
    }

    static boolean hasPrecisePermission(Context context) {
        return context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    void setTeamJoined(boolean value) {
        if (joined == value) return;
        joined = value;
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit().putBoolean(JOINED_KEY, value).apply();
        refreshRequests();
    }

    void setUi(String mode, Consumer<Location> listener) {
        uiMode = mode;
        uiListener = listener;
        if (listener == null && freshListener != null) finishFreshRequest(null);
        refreshRequests();
    }

    void requestFresh(Consumer<Location> listener) {
        if (!active || !hasPermission(context) || isCommsOnly(context)) { listener.accept(null); return; }
        if (freshListener != null) finishFreshRequest(null);
        freshListener = listener;
        freshFix = null;
        freshAfter = SystemClock.elapsedRealtime();
        refreshRequests();
        handler.postDelayed(finishFresh, 12_000L);
    }

    private void finishFreshRequest(Location location) {
        handler.removeCallbacks(finishFresh);
        Consumer<Location> listener = freshListener;
        freshListener = null;
        freshFix = null;
        refreshRequests();
        if (listener != null) listener.accept(location);
    }

    void refreshRequests() {
        boolean permitted = active && hasPermission(context);
        boolean fine = permitted && hasPrecisePermission(context);
        boolean allowed = permitted && !isCommsOnly(context);
        if (!allowed && freshListener != null) { finishFreshRequest(null); return; }
        boolean send = allowed && fine && joined && isEnabled(context);
        if (sharing != send) {
            sharing = send;
            shareGeneration++;
            lastSent = null;
            pendingRelocation = null;
        }
        boolean high = allowed && fine && (freshListener != null || "navigation".equals(uiMode));
        boolean foreground = allowed && (freshListener != null || !"idle".equals(uiMode));
        boolean stationary = movementAnchor != null && SystemClock.elapsedRealtime() - lastMovement >= LocationPolicy.STATIONARY_AFTER_MS;
        long nextInterval = freshListener != null && allowed ? 1_000L
                : LocationPolicy.interval(high, foreground, send, stationary);
        if (interval == nextInterval && highAccuracy == high && precise == fine) return;
        clearRequests();
        if (!permitted || (precise && !fine)) {
            bestRecent = null;
            latestFix = null;
        }
        interval = nextInterval;
        highAccuracy = high;
        precise = fine;
        if (locationManager == null || interval == 0L) return;
        boolean fused = Build.VERSION.SDK_INT >= 31 && requestProvider(LocationManager.FUSED_PROVIDER, interval, high, this);
        if (!fused) {
            requestProvider(LocationManager.NETWORK_PROVIDER, interval, false, this);
            if (high) requestProvider(LocationManager.GPS_PROVIDER, interval, true, this);
        }
        if (fine && !high) handler.postDelayed(checkGps, Math.min(interval, 15_000L));
    }

    private boolean requestProvider(String provider, long cadence, boolean high, LocationListener listener) {
        try {
            if (!locationManager.isProviderEnabled(provider)) return false;
            if (Build.VERSION.SDK_INT >= 31) {
                LocationRequest request = new LocationRequest.Builder(cadence)
                        .setQuality(high ? LocationRequest.QUALITY_HIGH_ACCURACY : LocationRequest.QUALITY_BALANCED_POWER_ACCURACY)
                        .setMinUpdateIntervalMillis(cadence)
                        .build();
                locationManager.requestLocationUpdates(provider, request, context.getMainExecutor(), listener);
            } else {
                locationManager.requestLocationUpdates(provider, cadence, 0, listener, Looper.getMainLooper());
            }
            return true;
        } catch (IllegalArgumentException | SecurityException error) {
            return false;
        }
    }

    private void checkGpsFallback() {
        if (interval == 0 || highAccuracy || !hasPrecisePermission(context)) return;
        long now = SystemClock.elapsedRealtime();
        if ((lastUsableFix == 0 || now - lastUsableFix >= interval) && requestProvider(LocationManager.GPS_PROVIDER, 2_000L, true, gpsProbe)) {
            gpsProbing = true;
            handler.postDelayed(stopProbe, 12_000L);
        }
        handler.postDelayed(checkGps, interval);
    }

    private void stopGpsProbe() {
        handler.removeCallbacks(stopProbe);
        if (gpsProbing) {
            gpsProbing = false;
            locationManager.removeUpdates(gpsProbe);
        }
    }

    private void clearRequests() {
        handler.removeCallbacks(checkGps);
        stopGpsProbe();
        if (locationManager != null) locationManager.removeUpdates(this);
    }

    void stop() {
        active = false;
        setUi("idle", null);
    }

    private static long fixTime(Location location) {
        return location.getElapsedRealtimeNanos() / 1_000_000L;
    }

    private static double score(Location location, long now) {
        return location == null || !location.hasAccuracy() ? Double.POSITIVE_INFINITY
                : LocationPolicy.score(now - fixTime(location), location.getAccuracy());
    }

    Location getBestRecentLocation() {
        return getCachedLocation(false);
    }

    Location getLatestCachedLocation() {
        return getCachedLocation(true);
    }

    private Location getCachedLocation(boolean latest) {
        if (locationManager == null || !hasPermission(context)) return null;
        boolean fine = hasPrecisePermission(context);
        long now = SystemClock.elapsedRealtime();
        Location best = fine ? latest ? latestFix : bestRecent : null;
        for (String provider : new String[]{"fused", LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER, LocationManager.PASSIVE_PROVIDER}) {
            try {
                Location candidate = locationManager.getLastKnownLocation(provider);
                if (Double.isFinite(score(candidate, now)) && (!Double.isFinite(score(best, now))
                        || (latest ? fixTime(candidate) > fixTime(best) : score(candidate, now) < score(best, now)))) best = candidate;
            } catch (IllegalArgumentException | SecurityException ignored) {
                // Providers and permissions vary by Android device.
            }
        }
        return Double.isFinite(score(best, now)) ? best : null;
    }

    @Override
    public void onLocationChanged(Location location) {
        long now = SystemClock.elapsedRealtime();
        if (!active || interval == 0L || !hasPermission(context) || isCommsOnly(context) || !Double.isFinite(score(location, now))) return;
        if (latestFix == null || fixTime(location) >= fixTime(latestFix)) latestFix = new Location(location);
        if (freshListener != null && fixTime(location) >= freshAfter && score(location, now) < score(freshFix, now)) {
            freshFix = new Location(location);
            if (location.getAccuracy() <= LocationPolicy.PRECISE_FIX_METERS || !precise) finishFreshRequest(freshFix);
        }
        if (now - fixTime(location) > 15_000L) return;
        if (score(location, now) <= score(bestRecent, now)) {
            bestRecent = new Location(location);
            if (uiListener != null) uiListener.accept(location);
        }
        if (location.getAccuracy() > MAX_ACCURACY_METERS) return;
        lastUsableFix = now;
        stopGpsProbe();
        if (movementAnchor == null || location.distanceTo(movementAnchor) >= movementThreshold(movementAnchor, location)) {
            movementAnchor = new Location(location);
            lastMovement = now;
        }
        refreshRequests();
        if (!sharing) return;
        if (lastSent != null) {
            long elapsed = now - lastSentAt;
            float moved = location.distanceTo(lastSent);
            float movementThreshold = movementThreshold(lastSent, location);
            if (elapsed < MIN_UPDATE_MS
                    || (moved < movementThreshold
                    && elapsed < STATIONARY_HEARTBEAT_MS)) {
                return;
            }
            if (isImplausible(lastSent, location, elapsed,
                    MAX_UNCONFIRMED_SPEED_METERS_PER_SECOND)) {
                if (pendingRelocation == null) {
                    pendingRelocation = new Location(location);
                    return;
                }
                long confirmationElapsed = Math.max(
                        1L,
                        (location.getElapsedRealtimeNanos()
                                - pendingRelocation.getElapsedRealtimeNanos()) / 1_000_000L
                );
                if (isImplausible(pendingRelocation, location, confirmationElapsed,
                        MAX_CONFIRMED_SPEED_METERS_PER_SECOND)) {
                    pendingRelocation = new Location(location);
                    return;
                }
            }
        }
        pendingRelocation = null;
        if (!sending.compareAndSet(false, true)) return;
        Location fix = new Location(location);
        int generation = shareGeneration;
        new Thread(() -> transmit(fix, generation), "reticom-location-send").start();
    }

    private static float movementThreshold(Location previous, Location current) {
        return Math.max(MIN_MOVEMENT_METERS, Math.min(50f, (previous.getAccuracy() + current.getAccuracy()) / 2f));
    }

    private void transmit(Location fix, int generation) {
        try {
            if (!fieldIsJoined()) {
                handler.post(() -> { if (generation == shareGeneration) setTeamJoined(false); });
                return;
            }
            if (!sharing || generation != shareGeneration || isCommsOnly(context)) return;
            JSONObject payload = new JSONObject();
            payload.put("type", "position.updated");
            payload.put("lat", round(fix.getLatitude(), 6));
            payload.put("lon", round(fix.getLongitude(), 6));
            payload.put("accuracy", round(fix.getAccuracy(), 1));
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
            HttpURLConnection connection = (HttpURLConnection) new URL(SEND_URL).openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(4_000);
            connection.setReadTimeout(12_000);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(body.length);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(body);
            }
            int status = connection.getResponseCode();
            connection.disconnect();
            if (status >= 200 && status < 300) {
                handler.post(() -> {
                    if (generation != shareGeneration) return;
                    lastSent = fix;
                    lastSentAt = SystemClock.elapsedRealtime();
                });
                Log.i(TAG, "Signed location update delivered over Reticulum");
            } else {
                Log.w(TAG, "Location update rejected with HTTP " + status);
            }
        } catch (Exception error) {
            Log.w(TAG, "Location update deferred", error);
        } finally {
            sending.set(false);
        }
    }

    private boolean fieldIsJoined() throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(STATE_URL).openConnection();
        connection.setConnectTimeout(2_000);
        connection.setReadTimeout(2_000);
        int status = connection.getResponseCode();
        if (status != 200) {
            connection.disconnect();
            throw new IOException("Reticom state is unavailable");
        }
        StringBuilder body = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) body.append(line);
        }
        connection.disconnect();
        return new JSONObject(body.toString()).getJSONObject("team").getBoolean("joined");
    }

    private static double round(double value, int decimals) {
        double scale = Math.pow(10, decimals);
        return Math.round(value * scale) / scale;
    }

    private static boolean isImplausible(
            Location origin,
            Location candidate,
            long elapsedMs,
            float maximumSpeedMetersPerSecond
    ) {
        float uncertainty = 2f * (origin.getAccuracy() + candidate.getAccuracy());
        float travelledLimit = maximumSpeedMetersPerSecond * Math.max(1f, elapsedMs / 1000f);
        float plausibleDistance = Math.max(75f, Math.max(uncertainty, travelledLimit));
        return origin.distanceTo(candidate) > plausibleDistance;
    }

    @Override
    public void onStatusChanged(String provider, int status, Bundle extras) {}

    @Override
    public void onProviderEnabled(String provider) {}

    @Override
    public void onProviderDisabled(String provider) {}
}
