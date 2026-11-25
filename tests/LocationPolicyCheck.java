package com.retium.field;

/** Plain JVM checks for the native location request policy. */
public final class LocationPolicyCheck {
    public static void main(String[] args) {
        if (LocationPolicy.interval(false, false, false, false) != 0) throw new AssertionError("idle requests a fix");
        long navigation = LocationPolicy.interval(true, true, true, true);
        long map = LocationPolicy.interval(false, true, true, false);
        long background = LocationPolicy.interval(false, false, true, false);
        long stationary = LocationPolicy.interval(false, false, true, true);
        if (!(0 < navigation && navigation < map && map < background && background < stationary)) {
            throw new AssertionError("location cadence does not follow demand");
        }
        if (!(LocationPolicy.score(20_000, 8) < LocationPolicy.score(0, 120))) throw new AssertionError("coarse fix won");
        if (!(LocationPolicy.score(0, 15) < LocationPolicy.score(80_000, 8))) throw new AssertionError("old fix won");
        if (Double.isFinite(LocationPolicy.score(301_000, 3))) throw new AssertionError("expired fix admitted");
        if (Double.isFinite(LocationPolicy.score(0, Float.NaN))) throw new AssertionError("unknown accuracy admitted");
        System.out.println("Location cadence and cached fix selection passed");
    }
}
