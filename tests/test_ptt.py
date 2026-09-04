import pytest

from retium.ptt import PTTError, PTTStore


def test_voice_clip_persists_with_its_media_type(tmp_path):
    store = PTTStore(tmp_path / "ptt")
    clip_id = "00112233-4455-6677-8899-aabbccddeeff"

    store.save(clip_id, b"real-audio-bytes", "audio/webm")

    assert store.read(clip_id) == (b"real-audio-bytes", "audio/webm")


def test_voice_clip_rejects_bad_ids_and_oversized_payloads(tmp_path):
    store = PTTStore(tmp_path / "ptt")

    with pytest.raises(PTTError, match="clip id"):
        store.save("../escape", b"audio", "audio/webm")
    with pytest.raises(PTTError, match="voice clip"):
        store.save(
            "00112233-4455-6677-8899-aabbccddeeff",
            b"",
            "audio/webm",
        )


def test_voice_clip_delete_removes_audio_and_metadata(tmp_path):
    store = PTTStore(tmp_path / "ptt")
    clip_id = "00112233-4455-6677-8899-aabbccddeeff"
    store.save(clip_id, b"real-audio-bytes", "audio/webm")

    assert store.delete(clip_id) is True
    assert store.delete(clip_id) is False
    with pytest.raises(FileNotFoundError):
        store.read(clip_id)
