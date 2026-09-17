package com.actisgen.mobile;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

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
    private static final String EXTRA_EXECUTION_ID = "actis_execution_id";
    private static final String EXTRA_PENDING_INTENT = "pendingIntent";
    private static final String RESULT_BUNDLE = "result";

    private static final AtomicInteger NEXT_EXECUTION_ID = new AtomicInteger(1000);
    private static final ConcurrentHashMap<Integer, PluginCall> PENDING_CALLS = new ConcurrentHashMap<>();

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
        PermissionState permission = getPermissionState("runCommand");
        result.put("installed", isTermuxInstalled());
        result.put("permission", String.valueOf(permission));
        result.put("permissionGranted", permission == PermissionState.GRANTED);
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
            call.reject("Permissão 'Executar comandos no Termux' não concedida. Abra Informações do app > Permissões > Permissões adicionais e autorize o ACTIS.");
            return;
        }
        dispatchCommand(call);
    }

    private String withActisDiagnostics(String script) {
        if (!script.contains("9router")) return script;
        return script
            + "\n__actis_exit=$?"
            + "\nif [ \"$__actis_exit\" -eq 0 ]; then"
            + "\n  sleep 3"
            + "\n  if ! (exec 9<>/dev/tcp/127.0.0.1/20128) 2>/dev/null; then"
            + "\n    echo 'ACTIS_DIAG: 9Router não abriu a porta 20128.' >&2"
            + "\n    echo 'ACTIS_DIAG: node='$(command -v node 2>/dev/null || echo ausente) >&2"
            + "\n    echo 'ACTIS_DIAG: 9router='$(command -v 9router 2>/dev/null || echo ausente) >&2"
            + "\n    if [ -f \"$HOME/actis-9router.log\" ]; then echo '--- actis-9router.log ---' >&2; tail -n 50 \"$HOME/actis-9router.log\" >&2; fi"
            + "\n    exit 86"
            + "\n  fi"
            + "\n  exec 9>&-"
            + "\nfi"
            + "\nexit \"$__actis_exit\"";
    }

    private void dispatchCommand(PluginCall call) {
        String script = call.getString("script", "").trim();
        if (script.isEmpty()) {
            call.reject("Comando vazio.");
            return;
        }
        boolean background = Boolean.TRUE.equals(call.getBoolean("background", true));
        int executionId = NEXT_EXECUTION_ID.getAndIncrement();

        Intent resultIntent = new Intent(getContext(), TermuxResultService.class);
        resultIntent.putExtra(EXTRA_EXECUTION_ID, executionId);
        int pendingFlags = PendingIntent.FLAG_ONE_SHOT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) pendingFlags |= PendingIntent.FLAG_MUTABLE;
        PendingIntent pendingIntent = PendingIntent.getService(getContext(), executionId, resultIntent, pendingFlags);

        Intent intent = new Intent();
        intent.setComponent(new ComponentName(TERMUX_PACKAGE, RUN_SERVICE));
        intent.setAction(RUN_ACTION);
        intent.putExtra("com.termux.RUN_COMMAND_PATH", BASH);
        intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[] { "-lc", withActisDiagnostics(script) });
        intent.putExtra("com.termux.RUN_COMMAND_WORKDIR", TERMUX_HOME);
        intent.putExtra("com.termux.RUN_COMMAND_BACKGROUND", background);
        intent.putExtra("com.termux.RUN_COMMAND_SESSION_ACTION", "0");
        intent.putExtra("com.termux.RUN_COMMAND_LABEL", "ACTIS · 9Router");
        intent.putExtra("com.termux.RUN_COMMAND_DESCRIPTION", "Prepara e inicia o gateway local 9Router para os agentes do ACTIS.");
        intent.putExtra(EXTRA_PENDING_INTENT, pendingIntent);

        try {
            PENDING_CALLS.put(executionId, call);
            getContext().startService(intent);
        } catch (SecurityException error) {
            PENDING_CALLS.remove(executionId);
            call.reject("Termux bloqueou o comando. Ative allow-external-apps=true e conceda a permissão RUN_COMMAND ao ACTIS.", error);
        } catch (Exception error) {
            PENDING_CALLS.remove(executionId);
            call.reject("Não foi possível iniciar o comando no Termux: " + error.getMessage(), error);
        }
    }

    static void resolveExecutionResult(Intent intent) {
        if (intent == null) return;
        int executionId = intent.getIntExtra(EXTRA_EXECUTION_ID, 0);
        PluginCall call = PENDING_CALLS.remove(executionId);
        if (call == null) return;

        Bundle bundle = intent.getBundleExtra(RESULT_BUNDLE);
        if (bundle == null) {
            call.reject("Termux não devolveu o resultado do comando. Atualize o Termux para uma versão com suporte ao RUN_COMMAND result (>= 0.109).");
            return;
        }

        String stdout = bundle.getString("stdout", "");
        String stderr = bundle.getString("stderr", "");
        String errmsg = bundle.getString("errmsg", "");
        int exitCode = bundle.getInt("exitCode", -999);
        int errCode = bundle.getInt("err", Activity.RESULT_OK);

        JSObject result = new JSObject();
        result.put("executionId", executionId);
        result.put("stdout", stdout);
        result.put("stderr", stderr);
        result.put("exitCode", exitCode);
        result.put("errCode", errCode);
        result.put("errmsg", errmsg);

        if (errCode != Activity.RESULT_OK || exitCode != 0) {
            String detail = !stderr.trim().isEmpty() ? stderr.trim() : (!errmsg.trim().isEmpty() ? errmsg.trim() : stdout.trim());
            if (detail.length() > 7000) detail = detail.substring(detail.length() - 7000);
            call.reject("Falha no Termux (exit " + exitCode + "): " + detail);
            return;
        }
        call.resolve(result);
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
