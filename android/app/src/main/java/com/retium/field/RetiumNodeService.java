package com.retium.field;

import android.app.Notification;
import android.app.ActivityManager;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.net.wifi.WifiManager;
import android.os.IBinder;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.util.Log;

import com.chaquo.python.Python;

public final class RetiumNodeService extends Service {
    private static final String ACTION_SHUT_DOWN = "com.retium.field.SHUT_DOWN";
    private static volatile boolean shuttingDown;
    public static final String ACTION_AUTOMATIC_LOCATION = "com.retium.field.AUTOMATIC_LOCATION";
    public static final String EXTRA_AUTOMATIC_LOCATION = "automatic_location";
    private static final String CHANNEL_ID = "retium_link";
    private static final int NOTIFICATION_ID = 8142;
    private WifiManager.MulticastLock multicastLock;
    private WifiManager.WifiLock wifiLock;
    private BackgroundAlertController backgroundAlerts;
    private LocationUpdateController locationUpdates;

    public static boolean isShuttingDown() { return shuttingDown; }

    @Override
    public void onCreate() {
        super.onCreate();
        WhisperTranscriber.initialize(this);
        if (shuttingDown) return;
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
        if (intent != null && ACTION_SHUT_DOWN.equals(intent.getAction())) {
            shutDown();
            return START_NOT_STICKY;
        }
        if (shuttingDown) {
            stopSelf();
            return START_NOT_STICKY;
        }
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
        stopControllersAndLocks();
        super.onDestroy();
    }

    private void stopControllersAndLocks() {
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
    }

    private void shutDown() {
        if (shuttingDown) return;
        // Set this before closing activities: their onStop callbacks must not
        // start the sticky node service again while shutdown is in progress.
        shuttingDown = true;
        Handler main = new Handler(Looper.getMainLooper());
        Runnable finishProcess = () -> Process.killProcess(Process.myPid());
        // Reticulum owns process-lifetime Python threads. Allow graceful cache
        // persistence first, then terminate only our own process so no hidden
        // node survives and a later launcher tap starts a clean instance.
        main.postDelayed(finishProcess, 10000);
        stopControllersAndLocks();
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
        ActivityManager activities = getSystemService(ActivityManager.class);
        if (activities != null) {
            for (ActivityManager.AppTask task : activities.getAppTasks()) task.finishAndRemoveTask();
        }
        new Thread(() -> {
            try {
                Python.getInstance().getModule("mobile_main").callAttr("stop");
            } catch (Exception error) {
                Log.w("ReticomShutdown", "Node shutdown did not complete gracefully", error);
            } finally {
                main.post(finishProcess);
            }
        }, "reticom-shutdown").start();
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
        PendingIntent shutdown = PendingIntent.getService(
                this, 1, new Intent(this, RetiumNodeService.class).setAction(ACTION_SHUT_DOWN),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_retium)
                .setContentTitle("Reticom Field")
                .setContentText(text)
                .setContentIntent(pending)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(
                        android.R.drawable.ic_menu_close_clear_cancel, "Shut down", shutdown
                ).build())
                .setCategory(Notification.CATEGORY_SERVICE)
                .build();
    }
}
