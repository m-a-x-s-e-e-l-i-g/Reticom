package com.retium.field;

/** Horizontal bearing: top edge when flat, rear camera when held upright. */
final class CompassBearing {
    private boolean useCamera;

    float heading(float[] rotation) {
        // Android rotation matrix maps device axes into east/north/up.
        // Hysteresis avoids switching axes repeatedly near the tilt threshold.
        float verticalTop = Math.abs(rotation[7]);
        if (verticalTop > .8f) useCamera = true;
        else if (verticalTop < .65f) useCamera = false;
        float east = useCamera ? -rotation[2] : rotation[1];
        float north = useCamera ? -rotation[5] : rotation[4];
        if (!Float.isFinite(east) || !Float.isFinite(north) || east * east + north * north < .04f) {
            return Float.NaN;
        }
        return (float) ((Math.toDegrees(Math.atan2(east, north)) + 360) % 360);
    }
}
