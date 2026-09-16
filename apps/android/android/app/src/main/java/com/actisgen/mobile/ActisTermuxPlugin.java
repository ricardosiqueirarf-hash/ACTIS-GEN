package com.actisgen.mobile;

import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.provider.Settings;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

@CapacitorPlugin(
    name = "ActisTermux",
    permissions = {
        @Permission(alias = "runCommand", strings = { "com.termux.permission.RUN_COMMAND" })
    }
)
public class ActisTermuxPlugin extends Plugin {
    private static final String TERMUX_PACKAGE = "com.termux";
    private static final String RUN_SERVICE = "com.termux.app.RunCommandService";
    private static final String RUN_ACTION = "com.termux.RUN_COMMAND";
    private static final String TERMUX_HOME = "/data/data/com.termux/files/home";
    private static final String BASH = "/data/data/com.termux/files/usr/bin/bash";

    private boolean isTermuxInstalled() {
        try {
            getContext().getPackageManager().getPackageInfo(TERMUX_PACKAGE, 0);
            return true;
        } catch (PackageManager.NameNotFoundException error) {
            return false;
        }
    }

    @PluginMethod
    public void status(PluginCall call) {
        JSObject result = new JSObject();
        result.put("installed", isTermuxInstalled());
        result.put("permission", String.valueOf(getPermissionState("runCommand")));
        call.resolve(result);
    }

    @PluginMethod
    public void requestRunPermission(PluginCall call) {
        if (!isTermuxInstalled()) {
            call.reject("Termux não está instalado.");
            return;
        }
        if (getPermissionState("runCommand") == PermissionState.GRANTED) {
            JSObject result = new JSObject();
            result.put("granted", true);
            call.resolve(result);
            return;
        }
        requestPermissionForAlias("runCommand", call, "runPermissionCallback");
    }

    @PermissionCallback
    private void runPermissionCallback(PluginCall call) {
        JSObject result = new JSObject();
        result.put("granted", getPermissionState("runCommand") == PermissionState.GRANTED);
        call.resolve(result);
    }

    @PluginMethod
    public void runCommand(PluginCall call) {
        if (!isTermuxInstalled()) {
            call.reject("Termux não está instalado.");
            return;
        }
        if (getPermissionState("runCommand") != PermissionState.GRANTED) {
            requestPermissionForAlias("runCommand", call, "runCommandPermissionCallback");
            return;
        }
        dispatchCommand(call);
    }

    @PermissionCallback
    private void runCommandPermissionCallback(PluginCall call) {
        if (getPermissionState("runCommand") != PermissionState.GRANTED) {
            call.reject("Permissão 'Executar comandos no Termux' não concedida.");
            return;
        }
        dispatchCommand(call);
    }

    private void dispatchCommand(PluginCall call) {
        String script = call.getString("script", "").trim();
        if (script.isEmpty()) {
            call.reject("Comando vazio.");
            return;
        }
        boolean background = Boolean.TRUE.equals(call.getBoolean("background", true));

        Intent intent = new Intent();
        intent.setComponent(new ComponentName(TERMUX_PACKAGE, RUN_SERVICE));
        intent.setAction(RUN_ACTION);
        intent.putExtra("com.termux.RUN_COMMAND_PATH", BASH);
        intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[] { "-lc", script });
        intent.putExtra("com.termux.RUN_COMMAND_WORKDIR", TERMUX_HOME);
        intent.putExtra("com.termux.RUN_COMMAND_BACKGROUND", background);
        intent.putExtra("com.termux.RUN_COMMAND_SESSION_ACTION", "0");
        intent.putExtra("com.termux.RUN_COMMAND_LABEL", "ACTIS Core");
        intent.putExtra("com.termux.RUN_COMMAND_DESCRIPTION", "Executa o ACTIS Core local no Termux.");

        try {
            getContext().startService(intent);
            JSObject result = new JSObject();
            result.put("accepted", true);
            result.put("background", background);
            call.resolve(result);
        } catch (SecurityException error) {
            call.reject("Termux bloqueou o comando. Ative allow-external-apps=true e conceda a permissão RUN_COMMAND ao ACTIS.", error);
        } catch (Exception error) {
            call.reject("Não foi possível iniciar o comando no Termux: " + error.getMessage(), error);
        }
    }

    @PluginMethod
    public void openTermux(PluginCall call) {
        Intent launch = getContext().getPackageManager().getLaunchIntentForPackage(TERMUX_PACKAGE);
        if (launch == null) {
            call.reject("Termux não está instalado.");
            return;
        }
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(launch);
        call.resolve();
    }

    @PluginMethod
    public void openAppSettings(PluginCall call) {
        Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
        intent.setData(Uri.parse("package:" + getContext().getPackageName()));
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(intent);
        call.resolve();
    }
}
