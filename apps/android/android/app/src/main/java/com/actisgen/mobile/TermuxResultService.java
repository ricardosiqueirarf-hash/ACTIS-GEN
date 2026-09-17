package com.actisgen.mobile;

import android.app.IntentService;
import android.content.Intent;

@SuppressWarnings("deprecation")
public class TermuxResultService extends IntentService {
    public TermuxResultService() {
        super("ACTIS-TermuxResultService");
    }

    @Override
    protected void onHandleIntent(Intent intent) {
        ActisTermuxPlugin.resolveExecutionResult(intent);
    }
}
