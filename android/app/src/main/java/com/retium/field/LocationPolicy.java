package com.retium.field;

final class LocationPolicy {
    static final long MAX_CACHED_AGE_MS = 5 * 60_000L;
    static final long STATIONARY_AFTER_MS = 60_000L;
    static final float PRECISE_FIX_METERS = 25f;

    private LocationPolicy() {}

    static long interval(boolean highAccuracy, boolean mapVisible, boolean sharing, boolean stationary) {
        if (highAccuracy) return 3_000L;
        if (mapVisible) return stationary ? 60_000L : 15_000L;
        if (sharing) return stationary ? 120_000L : 45_000L;
        return 0L;
    }

    static double score(long ageMs, float accuracyMeters) {
        if (ageMs < 0 || ageMs > MAX_CACHED_AGE_MS || !Float.isFinite(accuracyMeters) || accuracyMeters <= 0) {
            return Double.POSITIVE_INFINITY;
        }
        return accuracyMeters + ageMs * .0015;
    }
}
