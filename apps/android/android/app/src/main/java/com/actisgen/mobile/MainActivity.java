package com.actisgen.mobile;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ActisTermuxPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
