package com.retium.field;

import android.speech.tts.TextToSpeech;
import android.speech.tts.Voice;

import java.util.Locale;
import java.util.Set;

final class TtsVoicePolicy {
    private static final String DEFAULT_VOICE_NAME = "en-us-x-tpf-network";
    private static final String DEFAULT_VOICE_SUFFIX = "tpf-network";

    private TtsVoicePolicy() {
    }

    static Voice apply(TextToSpeech engine, String savedVoiceName) {
        Set<Voice> voices = engine.getVoices();
        if (voices == null || voices.isEmpty()) return null;

        Voice savedVoice = findByName(voices, savedVoiceName);
        if (savedVoice != null && engine.setVoice(savedVoice) == TextToSpeech.SUCCESS) {
            return savedVoice;
        }

        Voice defaultVoice = findDefault(voices);
        if (defaultVoice != null
                && engine.setVoice(defaultVoice) == TextToSpeech.SUCCESS) {
            return defaultVoice;
        }
        return null;
    }

    private static Voice findByName(Set<Voice> voices, String voiceName) {
        if (voiceName == null || voiceName.trim().isEmpty()) return null;
        for (Voice voice : voices) {
            if (voiceName.equals(voice.getName())) return voice;
        }
        return null;
    }

    private static Voice findDefault(Set<Voice> voices) {
        Voice best = null;
        int bestScore = 0;
        for (Voice voice : voices) {
            int score = defaultScore(voice);
            if (score > bestScore
                    || (score == bestScore && score > 0 && comesBefore(voice, best))) {
                best = voice;
                bestScore = score;
            }
        }
        return best;
    }

    private static int defaultScore(Voice voice) {
        Locale locale = voice.getLocale();
        if (locale == null
                || !"en".equalsIgnoreCase(locale.getLanguage())
                || !"US".equalsIgnoreCase(locale.getCountry())) {
            return 0;
        }

        String normalizedName = voice.getName() == null
                ? ""
                : voice.getName().trim().toLowerCase(Locale.ROOT).replace('_', '-');
        if (DEFAULT_VOICE_NAME.equals(normalizedName)
                && voice.isNetworkConnectionRequired()) {
            return 5;
        }
        if (normalizedName.endsWith(DEFAULT_VOICE_SUFFIX)
                && voice.isNetworkConnectionRequired()) {
            return 4;
        }
        if (normalizedName.endsWith(DEFAULT_VOICE_SUFFIX)) return 3;
        if (voice.isNetworkConnectionRequired()) return 2;
        return 1;
    }

    private static boolean comesBefore(Voice candidate, Voice current) {
        if (current == null) return true;
        String candidateName = candidate.getName() == null ? "" : candidate.getName();
        String currentName = current.getName() == null ? "" : current.getName();
        return candidateName.compareToIgnoreCase(currentName) < 0;
    }
}
