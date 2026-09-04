package com.retium.field;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.net.wifi.WifiManager;
import android.os.IBinder;

import com.chaquo.python.Python;

public final class RetiumNodeService extends Service {
    public static final String ACTION_AUTOMATIC_LOCATION = "com.retium.field.AUTOMATIC_LOCATION";
    public static final String EXTRA_AUTOMATIC_LOCATION = "automatic_location";
    private static final String CHANNEL_ID = "retium_link";
    private static final int NOTIFICATION_ID = 8142;
    private WifiManager.MulticastLock multicastLock;
    private WifiManager.WifiLock wifiLock;
    private BackgroundAlertController backgroundAlerts;
    private LocationUpdateController locationUpdates;

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
        startForeground(NOTIFICATION_ID, buildNotification("Starting Reticulum…"));
        acquireNetworkLocks();
        Python.getInstance()
                .getModule("mobile_main")
                .callAttr(
                        "start",
                        getFilesDir().getAbsolutePath(),
                        BuildConfig.RETICOM_TRANSPORT_HOST,
                        BuildConfig.RETICOM_TRANSPORT_PORT
                );
        backgroundAlerts = new BackgroundAlertController(this);
        backgroundAlerts.start();
        locationUpdates = new LocationUpdateController(this);
        locationUpdates.start();
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.notify(NOTIFICATION_ID, buildNotification(
                LocationUpdateController.isEnabled(this)
                        ? "Reticulum + automatic location active"
                        : "Reticulum Field node active"
        ));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && backgroundAlerts != null) {
            String action = intent.getAction();
            if (BackgroundAlertController.ACTION_APP_VISIBILITY.equals(action)) {
                backgroundAlerts.setAppForeground(intent.getBooleanExtra(
                        BackgroundAlertController.EXTRA_APP_FOREGROUND,
                        true
                ));
            } else if (BackgroundAlertController.ACTION_ALERTS_ENABLED.equals(action)) {
                backgroundAlerts.setAlertsEnabled(intent.getBooleanExtra(
                        BackgroundAlertController.EXTRA_ALERTS_ENABLED,
                        true
                ));
            } else if (BackgroundAlertController.ACTION_TTS_VOICE_CHANGED.equals(action)) {
                backgroundAlerts.refreshTtsVoice();
            } else if (ACTION_AUTOMATIC_LOCATION.equals(action) && locationUpdates != null) {
                boolean enabled = intent.getBooleanExtra(EXTRA_AUTOMATIC_LOCATION, true);
                LocationUpdateController.setEnabled(this, enabled);
                if (enabled) locationUpdates.start();
                else locationUpdates.stop();
                NotificationManager manager = getSystemService(NotificationManager.class);
                manager.notify(NOTIFICATION_ID, buildNotification(
                        enabled
                                ? "Reticulum + automatic location active"
                                : "Reticulum Field node active"
                ));
            }
        }
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        if (backgroundAlerts != null) {
            backgroundAlerts.stop();
            backgroundAlerts = null;
        }
        if (locationUpdates != null) {
            locationUpdates.stop();
            locationUpdates = null;
        }
        if (multicastLock != null && multicastLock.isHeld()) multicastLock.release();
        if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
        super.onDestroy();
    }

    private void acquireNetworkLocks() {
        WifiManager wifi = (WifiManager) getApplicationContext()
                .getSystemService(Context.WIFI_SERVICE);
        if (wifi == null) return;
        multicastLock = wifi.createMulticastLock("retium-multicast");
        multicastLock.setReferenceCounted(false);
        multicastLock.acquire();
        wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "retium-wifi");
        wifiLock.setReferenceCounted(false);
        wifiLock.acquire();
    }

    private void createNotificationChannel() {
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Reticulum link",
                NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("Keeps the local Reticulum Field node available");
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    private Notification buildNotification(String text) {
        Intent launch = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(
                this,
                0,
                launch,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_retium)
                .setContentTitle("Reticom Field")
                .setContentText(text)
                .setContentIntent(pending)
                .setOngoing(true)
                .setCategory(Notification.CATEGORY_SERVICE)
                .build();
    }
}
