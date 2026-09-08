package com.retium.field;

import android.content.Context;
import android.media.AudioFormat;
import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;

/** Offline PTT recognition. Model and native engine ship inside the APK. */
public final class WhisperTranscriber {
    private static Context context;
    public static void initialize(Context value) { context = value.getApplicationContext(); }
    private static native String transcribeNative(String model, float[] samples, String language, int threads);

    public static synchronized String transcribe(String audioPath, String language) throws Exception {
        if (context == null) throw new IllegalStateException("Android transcription is not initialized");
        System.loadLibrary("reticom_whisper");
        File model = new File(context.getFilesDir(), "ggml-base-v1.bin");
        if (!model.isFile() || model.length() != 147951465L) {
            File staging = new File(context.getFilesDir(), "ggml-base-v1.tmp");
            try (InputStream input = context.getAssets().open("whisper/ggml-base.bin");
                 FileOutputStream output = new FileOutputStream(staging)) {
                byte[] buffer = new byte[65536];
                int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                output.getFD().sync();
            }
            if (staging.length() != 147951465L || !staging.renameTo(model))
                throw new IllegalStateException("Cannot prepare bundled Whisper model");
        }
        return transcribeNative(model.getAbsolutePath(), decode(audioPath), language,
                Math.min(4, Runtime.getRuntime().availableProcessors()));
    }

    private static float[] decode(String path) throws Exception {
        MediaExtractor extractor = new MediaExtractor();
        MediaCodec decoder = null;
        try {
            extractor.setDataSource(path);
            MediaFormat format = null;
            for (int i = 0; i < extractor.getTrackCount(); i++) {
                MediaFormat candidate = extractor.getTrackFormat(i);
                if (candidate.getString(MediaFormat.KEY_MIME).startsWith("audio/")) {
                    format = candidate; extractor.selectTrack(i); break;
                }
            }
            if (format == null) throw new IllegalArgumentException("Recording contains no audio track");
            int rate = format.getInteger(MediaFormat.KEY_SAMPLE_RATE);
            int channels = format.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
            int encoding = AudioFormat.ENCODING_PCM_16BIT;
            decoder = MediaCodec.createDecoderByType(format.getString(MediaFormat.KEY_MIME));
            decoder.configure(format, null, null, 0);
            decoder.start();
            float[] mono = new float[rate * 5];
            int size = 0;
            boolean inputDone = false, outputDone = false;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            long deadline = android.os.SystemClock.elapsedRealtime() + 60000;
            while (!outputDone) {
                if (android.os.SystemClock.elapsedRealtime() > deadline)
                    throw new IllegalStateException("Audio decoding timed out");
                if (!inputDone) {
                    int index = decoder.dequeueInputBuffer(10000);
                    if (index >= 0) {
                        ByteBuffer input = decoder.getInputBuffer(index);
                        int count = extractor.readSampleData(input, 0);
                        inputDone = count < 0;
                        decoder.queueInputBuffer(index, 0, Math.max(0, count),
                            inputDone ? 0 : extractor.getSampleTime(), inputDone ? MediaCodec.BUFFER_FLAG_END_OF_STREAM : 0);
                        if (!inputDone) extractor.advance();
                    }
                }
                int index = decoder.dequeueOutputBuffer(info, 10000);
                if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat output = decoder.getOutputFormat();
                    rate = output.getInteger(MediaFormat.KEY_SAMPLE_RATE);
                    channels = output.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                    if (output.containsKey(MediaFormat.KEY_PCM_ENCODING)) encoding = output.getInteger(MediaFormat.KEY_PCM_ENCODING);
                    if (encoding != AudioFormat.ENCODING_PCM_FLOAT && encoding != AudioFormat.ENCODING_PCM_16BIT)
                        throw new IllegalArgumentException("Unsupported decoded audio format");
                } else if (index >= 0) {
                    ByteBuffer output = decoder.getOutputBuffer(index).order(ByteOrder.LITTLE_ENDIAN);
                    output.position(info.offset); output.limit(info.offset + info.size);
                    int frames = info.size / (channels * (encoding == AudioFormat.ENCODING_PCM_FLOAT ? 4 : 2));
                    if (size + frames > rate * 120) throw new IllegalArgumentException("Recording exceeds two minutes");
                    if (size + frames > mono.length) mono = Arrays.copyOf(mono, Math.max(size + frames, mono.length * 2));
                    for (int i = 0; i < frames; i++) {
                        float value = 0;
                        for (int channel = 0; channel < channels; channel++)
                            value += encoding == AudioFormat.ENCODING_PCM_FLOAT ? output.getFloat() : output.getShort() / 32768f;
                        mono[size++] = value / channels;
                    }
                    outputDone = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                    decoder.releaseOutputBuffer(index, false);
                }
            }
            if (size == 0) throw new IllegalArgumentException("Recording decoded to empty audio");
            float[] samples = new float[(int) ((long) size * 16000 / rate)];
            for (int i = 0; i < samples.length; i++) {
                double source = i * (double) rate / 16000;
                int left = (int) source, right = Math.min(left + 1, size - 1);
                samples[i] = mono[left] + (mono[right] - mono[left]) * (float) (source - left);
            }
            return samples;
        } finally {
            if (decoder != null) decoder.release();
            extractor.release();
        }
    }
}
