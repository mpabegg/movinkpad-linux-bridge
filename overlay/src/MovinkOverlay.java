import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.PorterDuff;
import android.graphics.Rect;
import android.view.Surface;
import android.view.SurfaceControl;
import java.io.BufferedReader;
import java.io.InputStreamReader;

/**
 * Marks the pen area on the tablet's screen, from an adb shell and without an installed app.
 * Reads "width height left top right bottom" lines on stdin; "hide" clears, EOF exits.
 * The layer has no input window, so touch passes straight through it.
 */
public final class MovinkOverlay {
    private static final int LAYER = 1_000_000;
    private static final int DIM = Color.argb(150, 0, 0, 0);
    private static final int BORDER = Color.argb(255, 0, 200, 255);
    private static final int BORDER_PX = 3;

    private SurfaceControl layer;
    private Surface surface;

    private void hide() {
        if (layer == null) {
            return;
        }
        try (SurfaceControl.Transaction transaction = new SurfaceControl.Transaction()) {
            transaction.reparent(layer, null).apply();
        }
        surface.release();
        layer.release();
        layer = null;
    }

    private void show(int width, int height, Rect area) throws ReflectiveOperationException {
        hide();
        layer = new SurfaceControl.Builder()
                .setName("MovinkOverlay")
                .setBufferSize(width, height)
                .setFormat(PixelFormat.TRANSLUCENT)
                .build();
        surface = new Surface(layer);

        Canvas canvas = surface.lockCanvas(null);
        canvas.drawColor(Color.TRANSPARENT, PorterDuff.Mode.CLEAR);
        canvas.save();
        canvas.clipOutRect(area);
        canvas.drawColor(DIM);
        canvas.restore();
        Paint border = new Paint();
        border.setColor(BORDER);
        border.setStyle(Paint.Style.STROKE);
        border.setStrokeWidth(BORDER_PX);
        canvas.drawRect(area, border);
        surface.unlockCanvasAndPost(canvas);

        try (SurfaceControl.Transaction transaction = new SurfaceControl.Transaction()) {
            // A root layer belongs to no display until it joins the default layer stack.
            SurfaceControl.Transaction.class
                    .getMethod("setLayerStack", SurfaceControl.class, int.class)
                    .invoke(transaction, layer, 0);
            transaction.setLayer(layer, LAYER).setVisibility(layer, true).apply();
        }
    }

    public static void main(String[] args) throws Exception {
        MovinkOverlay overlay = new MovinkOverlay();
        BufferedReader input = new BufferedReader(new InputStreamReader(System.in));
        for (String line = input.readLine(); line != null; line = input.readLine()) {
            String[] fields = line.trim().split(" ");
            if (fields.length == 6) {
                int[] v = new int[6];
                for (int i = 0; i < 6; i++) {
                    v[i] = Integer.parseInt(fields[i]);
                }
                overlay.show(v[0], v[1], new Rect(v[2], v[3], v[4], v[5]));
            } else {
                overlay.hide();
            }
        }
        overlay.hide();
    }
}
