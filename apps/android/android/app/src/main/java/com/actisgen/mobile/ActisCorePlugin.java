package com.actisgen.mobile;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "ActisCore")
public class ActisCorePlugin extends Plugin {
    @PluginMethod
    public void status(PluginCall call) {
        JSObject result = new JSObject();
        result.put("state", MainActivity.getCoreState());
        result.put("error", MainActivity.getCoreError());
        call.resolve(result);
    }

    @PluginMethod
    public void ensureStarted(PluginCall call) {
        if (getActivity() instanceof MainActivity) {
            ((MainActivity) getActivity()).ensureEmbeddedCore();
        }
        JSObject result = new JSObject();
        result.put("state", MainActivity.getCoreState());
        call.resolve(result);
    }
}
