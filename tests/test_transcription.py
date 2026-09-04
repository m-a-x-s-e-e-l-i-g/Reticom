from types import SimpleNamespace

import pytest

from retium.transcription import LocalTranscriber, TranscriptionError


class FakeSegment:
    def __init__(self, text: str):
        self.text = text


class FakeModel:
    def __init__(self):
        self.calls = 0

    def transcribe(self, path, **kwargs):
        del path, kwargs
        self.calls += 1
        return [FakeSegment(" Unit moving."), FakeSegment(" Hold position. ")], SimpleNamespace(
            language="en", language_probability=0.94
        )


def test_local_transcription_is_cached_by_clip_and_audio_hash(tmp_path):
    model = FakeModel()
    transcriber = LocalTranscriber(
        tmp_path / "transcripts",
        model_name="test",
        language="en",
        model_factory=lambda *args, **kwargs: model,
    )
    clip_id = "00112233-4455-6677-8899-aabbccddeeff"

    first = transcriber.transcribe(clip_id, b"encoded voice", "audio/webm")
    second = transcriber.transcribe(clip_id, b"encoded voice", "audio/webm")

    assert first["text"] == "Unit moving. Hold position."
    assert first["status"] == "ready"
    assert first["requested_language"] == "en"
    assert second == first
    assert model.calls == 1
    assert transcriber.status(clip_id)["language"] == "en"
    transcriber.close()


def test_local_transcription_rejects_invalid_clip_ids(tmp_path):
    transcriber = LocalTranscriber(tmp_path / "transcripts", model_name="test")

    with pytest.raises(TranscriptionError, match="clip id"):
        transcriber.transcribe("../escape", b"voice", "audio/webm")

    transcriber.close()


def test_local_transcription_cache_can_be_deleted(tmp_path):
    model = FakeModel()
    transcriber = LocalTranscriber(
        tmp_path / "transcripts",
        model_name="test",
        language="en",
        model_factory=lambda *args, **kwargs: model,
    )
    clip_id = "00112233-4455-6677-8899-aabbccddeeff"
    transcriber.transcribe(clip_id, b"encoded voice", "audio/webm")

    assert transcriber.delete(clip_id) is True
    assert transcriber.cached(clip_id) is None
    assert transcriber.status(clip_id)["status"] == "unavailable"
    assert transcriber.schedule(clip_id, b"encoded voice", "audio/webm") is False
    transcriber.close()
