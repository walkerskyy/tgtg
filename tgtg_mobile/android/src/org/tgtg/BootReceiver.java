package org.tgtg;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.util.Log;

import java.io.File;
import java.io.FileReader;
import java.io.BufferedReader;

public class BootReceiver extends BroadcastReceiver {
    private static final String TAG = "TGTGBootReceiver";
    private static final String SETTINGS_FILE = "settings.json";
    private static final String KEY_FOREGROUND_SERVICE = "foreground_service_enabled";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) {
            return;
        }
        
        String action = intent.getAction();
        Log.i(TAG, "Received broadcast: " + action);

        if (Intent.ACTION_BOOT_COMPLETED.equals(action)
                || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            
            boolean serviceEnabled = readSettingsFromJson(context);

            if (serviceEnabled) {
                Log.i(TAG, "Foreground service enabled, starting service");
                startService(context);
            } else {
                Log.i(TAG, "Foreground service not enabled, skipping");
            }
        }
    }

    /**
     * Reads settings.json directly since Python/Kivy uses JsonStore,
     * not Android SharedPreferences.
     */
    private boolean readSettingsFromJson(Context context) {
        try {
            // Try multiple possible locations for settings.json
            String[] possiblePaths = {
                context.getFilesDir().getAbsolutePath() + "/" + SETTINGS_FILE,
                context.getExternalFilesDir(null).getAbsolutePath() + "/" + SETTINGS_FILE,
                context.getApplicationInfo().dataDir + "/" + SETTINGS_FILE,
            };

            for (String path : possiblePaths) {
                File file = new File(path);
                if (file.exists()) {
                    Log.i(TAG, "Found settings file at: " + path);
                    return parseSettingsFile(file);
                }
            }
            
            Log.i(TAG, "Settings file not found in any expected location");
        } catch (Exception e) {
            Log.e(TAG, "Failed to read settings: " + e.getMessage());
        }
        return false;
    }

    private boolean parseSettingsFile(File file) {
        try (BufferedReader reader = new BufferedReader(new FileReader(file))) {
            String line;
            StringBuilder content = new StringBuilder();
            while ((line = reader.readLine()) != null) {
                content.append(line);
            }
            
            String json = content.toString();
            // Simple string search for the key - avoids JSON parsing dependency
            return json.contains("\"foreground_service_enabled\": true") 
                || json.contains("\"foreground_service_enabled\":true");
        } catch (Exception e) {
            Log.e(TAG, "Failed to parse settings file: " + e.getMessage());
            return false;
        }
    }

    private void startService(Context context) {
        try {
            Intent serviceIntent = new Intent(context, ServiceStarter.class);
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                context.startForegroundService(serviceIntent);
            } else {
                context.startService(serviceIntent);
            }
        } catch (Exception e) {
            Log.e(TAG, "Failed to start service: " + e.getMessage());
        }
    }
}
