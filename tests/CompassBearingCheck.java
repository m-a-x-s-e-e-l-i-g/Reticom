package com.retium.field;

/** Plain JVM checks: no Android runtime or extra test dependency required. */
public final class CompassBearingCheck {
    private static void check(float expected, float[] rotation) {
        float actual = new CompassBearing().heading(rotation);
        if (!Float.isFinite(actual) || Math.abs(actual - expected) > .01f) throw new AssertionError(expected + " != " + actual);
    }
    public static void main(String[] args) {
        check(0, new float[]{1,0,0, 0,1,0, 0,0,1}); // flat, top north
        check(90, new float[]{0,1,0, -1,0,0, 0,0,1}); // flat, top east
        check(0, new float[]{1,0,0, 0,0,-1, 0,1,0}); // upright, camera north
        check(90, new float[]{0,0,-1, -1,0,0, 0,1,0}); // upright, camera east
        check(270, new float[]{0,0,1, 1,0,0, 0,1,0}); // upright, camera west
        check(180, new float[]{-1,0,0, 0,0,1, 0,1,0}); // upright, camera south
        if (!Float.isNaN(new CompassBearing().heading(new float[9]))) throw new AssertionError("degenerate orientation");
        System.out.println("Compass bearing: flat and upright cardinal checks passed");
    }
}
