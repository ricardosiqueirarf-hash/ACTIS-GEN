package com.actisgen.mobile;

import android.os.Bundle;
import android.util.Log;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    private static final String TAG = "ACTIS-Core";
    private static volatile boolean coreThreadStarted = false;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ActisTermuxPlugin.class); // optional advanced runtime only
        startEmbeddedCore();
        super.onCreate(savedInstanceState);
    }

    private void startEmbeddedCore() {
        if (coreThreadStarted) return;
        synchronized (MainActivity.class) {
            if (coreThreadStarted) return;
            coreThreadStarted = true;
        }

        Thread coreThread = new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                Python py = Python.getInstance();
                PyObject module = py.getModule("android_embedded_core");
                module.callAttr("start", getFilesDir().getAbsolutePath());
            } catch (Throwable error) {
                coreThreadStarted = false;
                Log.e(TAG, "Embedded ACTIS Core failed", error);
            }
        }, "actis-embedded-core");
        coreThread.setDaemon(true);
        coreThread.start();
    }
}
