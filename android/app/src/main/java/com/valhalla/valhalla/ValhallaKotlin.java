package com.valhalla.valhalla;

import java.nio.charset.StandardCharsets;

/**
 * JNI ABI adapter for Rallista valhalla-mobile 0.6.3 (MIT).
 * Class/method names match its exported JNI symbols; do not rename them.
 * No HTTP client is provided: all graph reads must come from device storage.
 */
public final class ValhallaKotlin implements AutoCloseable {
    static { System.loadLibrary("valhalla-wrapper"); }
    private long handle;

    public ValhallaKotlin(String configPath) {
        handle = createActor(configPath, null);
        if (handle == 0) throw new IllegalStateException("Offline routing engine could not start");
    }

    public synchronized String routeJson(String request) {
        if (handle == 0) throw new IllegalStateException("Offline routing engine is closed");
        return new String(route(handle, request.getBytes(StandardCharsets.UTF_8)), StandardCharsets.UTF_8);
    }

    @Override public synchronized void close() {
        if (handle != 0) { deleteActor(handle); handle = 0; }
    }

    private native long createActor(String configPath, Object unusedHttpClient);
    private native void deleteActor(long handle);
    private native byte[] route(long handle, byte[] request);
}
