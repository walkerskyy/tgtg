package org.tgtg;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.util.Log;

public class ServiceStarter extends Service {
    private static final String TAG = "TGTGServiceStarter";

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Log.i(TAG, "Starting TGTG background service");
        
        // Start as foreground service with notification
        String channelId = "tgtg_scanner";
        String channelName = "TGTG Scanner";
        
        // Create notification channel for Android 8+
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            android.app.NotificationChannel channel = new android.app.NotificationChannel(
                channelId, channelName,
                android.app.NotificationManager.IMPORTANCE_LOW
            );
            android.app.NotificationManager nm = 
                (android.app.NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            nm.createNotificationChannel(channel);
        }
        
        // Build foreground notification
        android.app.Notification notification = new android.app.Notification.Builder(this, channelId)
            .setContentTitle("TGTG Scanner")
            .setContentText("Monitoring for available bags")
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .build();
        
        startForeground(1, notification);
        
        return START_STICKY;
    }
}
