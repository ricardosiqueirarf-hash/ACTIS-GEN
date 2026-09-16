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
    private static volatile String coreState = "idle";
    private static volatile String coreError = "";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ActisTermuxPlugin.class); // optional advanced runtime only
        registerPlugin(ActisCorePlugin.class);
        super.onCreate(savedInstanceState);
        ensureEmbeddedCore();
    }

    public static String getCoreState() {
        return coreState;
    }

    public static String getCoreError() {
        return coreError;
    }

    public void ensureEmbeddedCore() {
        if (coreThreadStarted) return;
        synchronized (MainActivity.class) {
            if (coreThreadStarted) return;
            coreThreadStarted = true;
            coreState = "starting";
            coreError = "";
        }

        Thread coreThread = new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                coreState = "python-ready";
                Python py = Python.getInstance();
                PyObject module = py.getModule("android_embedded_core");
                coreState = "core-loading";
                module.callAttr("start", getFilesDir().getAbsolutePath());
                coreState = "stopped";
            } catch (Throwable error) {
                coreError = Log.getStackTraceString(error);
                coreState = "failed";
                coreThreadStarted = false;
                Log.e(TAG, "Embedded ACTIS Core failed", error);
            }
        }, "actis-embedded-core");
        coreThread.setDaemon(true);
        coreThread.start();
    }
}
