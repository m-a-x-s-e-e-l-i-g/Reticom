#include <jni.h>
#include <string>
#include <algorithm>
#include "whisper.h"

extern "C" JNIEXPORT jstring JNICALL
Java_com_retium_field_WhisperTranscriber_transcribeNative(
        JNIEnv *env, jclass, jstring model, jfloatArray samples, jstring language, jint threads) {
    const char *path = env->GetStringUTFChars(model, nullptr);
    auto options = whisper_context_default_params();
    options.use_gpu = false;
    auto *ctx = whisper_init_from_file_with_params(path, options);
    env->ReleaseStringUTFChars(model, path);
    if (!ctx) {
        env->ThrowNew(env->FindClass("java/lang/IllegalStateException"), "Bundled Whisper model could not be loaded");
        return nullptr;
    }
    auto params = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    const char *lang = env->GetStringUTFChars(language, nullptr);
    params.language = lang;
    params.n_threads = std::clamp((int) threads, 1, 4);
    params.no_context = true;
    params.no_timestamps = true;
    params.print_realtime = false;
    params.print_progress = false;
    params.print_timestamps = false;
    params.print_special = false;
    float *pcm = env->GetFloatArrayElements(samples, nullptr);
    int result = whisper_full(ctx, params, pcm, env->GetArrayLength(samples));
    env->ReleaseFloatArrayElements(samples, pcm, JNI_ABORT);
    env->ReleaseStringUTFChars(language, lang);
    std::string text;
    if (result == 0) {
        for (int i = 0; i < whisper_full_n_segments(ctx); ++i)
            text += whisper_full_get_segment_text(ctx, i);
    }
    whisper_free(ctx);
    if (result != 0) {
        env->ThrowNew(env->FindClass("java/lang/IllegalStateException"), "Whisper inference failed");
        return nullptr;
    }
    // JNI's modified UTF-8 is unsuitable for arbitrary transcript text.
    jbyteArray bytes = env->NewByteArray(text.size());
    env->SetByteArrayRegion(bytes, 0, text.size(), reinterpret_cast<const jbyte *>(text.data()));
    jclass stringClass = env->FindClass("java/lang/String");
    return (jstring) env->NewObject(stringClass,
        env->GetMethodID(stringClass, "<init>", "([BLjava/lang/String;)V"), bytes, env->NewStringUTF("UTF-8"));
}
