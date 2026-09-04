package com.retium.field;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.Looper;
import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;

final class LocationUpdateController implements LocationListener {
    private static final String TAG = "ReticomLocation";
    private static final String PREFERENCES = "retium-location";
    private static final String ENABLED_KEY = "automatic-location";
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
    private final AtomicBoolean sending = new AtomicBoolean(false);
    private Location lastSent;
    private Location pendingRelocation;
    private long lastSentAt;
    private boolean started;

    LocationUpdateController(Context context) {
        this.context = context.getApplicationContext();
        this.locationManager = (LocationManager) context.getSystemService(Context.LOCATION_SERVICE);
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

    void start() {
        if (started || !isEnabled(context) || locationManager == null) return;
        if (context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            Log.i(TAG, "Waiting for location permission");
            return;
        }
        started = true;
        requestProvider(LocationManager.GPS_PROVIDER);
        requestProvider(LocationManager.NETWORK_PROVIDER);
        Log.i(TAG, "Automatic Reticulum location updates active");
    }

    private void requestProvider(String provider) {
        try {
            if (locationManager.isProviderEnabled(provider)) {
                locationManager.requestLocationUpdates(
                        provider,
                        MIN_UPDATE_MS,
                        MIN_MOVEMENT_METERS,
                        this,
                        Looper.getMainLooper()
                );
            }
        } catch (SecurityException error) {
            Log.e(TAG, "Location permission unavailable", error);
        }
    }

    void stop() {
        if (locationManager != null && started) {
            try {
                locationManager.removeUpdates(this);
            } catch (SecurityException ignored) {
                // Permission can be revoked while the service is running.
            }
        }
        started = false;
        lastSent = null;
        pendingRelocation = null;
        lastSentAt = 0;
    }

    @Override
    public void onLocationChanged(Location location) {
        if (!started || location == null || !location.hasAccuracy()
                || location.getAccuracy() <= 0 || location.getAccuracy() > MAX_ACCURACY_METERS) return;
        long now = System.currentTimeMillis();
        if (lastSent != null) {
            long elapsed = now - lastSentAt;
            float moved = location.distanceTo(lastSent);
            float movementThreshold = Math.max(
                    MIN_MOVEMENT_METERS,
                    Math.min(50f, (lastSent.getAccuracy() + location.getAccuracy()) / 2f)
            );
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
        new Thread(() -> transmit(fix), "reticom-location-send").start();
    }

    private void transmit(Location fix) {
        try {
            if (!fieldIsJoined()) return;
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
                lastSent = fix;
                lastSentAt = System.currentTimeMillis();
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
            return false;
        }
        StringBuilder body = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) body.append(line);
        }
        connection.disconnect();
        JSONObject team = new JSONObject(body.toString()).optJSONObject("team");
        return team != null && team.optBoolean("joined", false);
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
