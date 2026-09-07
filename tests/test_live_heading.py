import time
from types import SimpleNamespace

import pytest
import RNS

from retium.live_heading import ACK, HELLO, SAMPLE, RELAY, TTL_MS, HeadingHub, HeadingClient, encode, decode, interval


def test_compact_wire_roundtrip_and_stop():
    assert len(encode(SAMPLE, 359.9, 1000)) == 13
    assert len(encode(RELAY, 0, 1000, b"x" * 16)) == 29
    assert decode(encode(SAMPLE, 359.9, 1000), 1000)["heading"] == 359.9
    assert decode(encode(SAMPLE, None, 1000), 1000)["heading"] is None


@pytest.mark.parametrize("heading", [-1, 360, True, float("nan"), float("inf"), "90"])
def test_bad_values_rejected(heading):
    with pytest.raises(ValueError): encode(SAMPLE, heading)


def test_late_frames_bad_clocks_and_malformed_frames_rejected():
    for raw, now in [(encode(SAMPLE, 10, 1000), 1000 + TTL_MS + 1),
                     (encode(SAMPLE, 10, 9000), 0), (b"bad", 0),
                     (encode(SAMPLE, 10, 1000) + b"x" * 16, 1000)]:
        with pytest.raises(ValueError): decode(raw, now)


def test_heading_link_rate_is_bounded_and_adaptive():
    assert interval(SimpleNamespace(get_establishment_rate=lambda: 1e6)) == .1
    assert interval(SimpleNamespace(get_establishment_rate=lambda: 100)) == 2
    assert interval(SimpleNamespace(get_establishment_rate=lambda: None)) == .25


def test_identity_is_bound_to_identified_link_and_unsubscribed_samples_are_ignored(monkeypatch):
    frames, packets = [], []
    hub = HeadingHub(frames.append)
    identity = RNS.Identity()
    class Link:
        status = RNS.Link.ACTIVE
        def get_remote_identity(self): return identity
        def get_establishment_rate(self): return 1e6
    link = Link()
    monkeypatch.setattr("retium.live_heading.transmit", lambda link, raw: packets.append(raw))
    hub.receive(link, encode(SAMPLE, 25))
    assert not frames
    hub.receive(link, encode(HELLO))
    assert decode(packets[0])["kind"] == ACK
    hub.receive(link, encode(SAMPLE, 25))
    assert frames[-1]["sender_hash"] == identity.hash.hex()
    assert frames[-1]["heading"] == 25
    assert not hasattr(hub, "outbox")


def test_disconnected_samples_are_dropped_without_opening_links():
    client = HeadingClient(None, lambda timeout: pytest.fail("must not connect from send"), lambda frame: None)
    assert client.send(90) is False
    assert not hasattr(client, "outbox")


def test_out_of_order_frames_and_stops_do_not_resurrect_old_headings():
    frames = []
    client = HeadingClient(None, None, frames.append)
    link = client.link = object()
    client.clock_offset = 0
    now = int(time.time() * 1000)
    for at, value in [(now, 90), (now + 1, None), (now, 80)]:
        client.receive(link, encode(RELAY, value, at, b"x" * 16))
    assert len(frames) == 2
    assert frames[-1]["heading"] is None


@pytest.mark.parametrize("offset", [6000, -6000, 60000])
def test_heading_clock_handshake_corrects_device_skew_and_rejects_stale_samples(monkeypatch, offset):
    from retium.live_heading import CLOCK_HELLO, CLOCK_ACK
    monkeypatch.setattr("retium.live_heading.clock_ms", lambda: 100000)
    monkeypatch.setattr("retium.live_heading.time.monotonic", lambda: 100.)
    frames, packets = [], []
    client = HeadingClient(None, None, frames.append)
    client.link = SimpleNamespace(status=RNS.Link.ACTIVE, get_establishment_rate=lambda: 1e6)
    monkeypatch.setattr("retium.live_heading.transmit", lambda link, raw: packets.append(raw))
    client.probe()
    assert decode(packets[-1])["kind"] == CLOCK_HELLO
    client.receive(client.link, encode(CLOCK_ACK, at=100000+offset, probe_at=99999))
    assert not client.ready()
    client.receive(client.link, encode(CLOCK_ACK, at=100000+offset, probe_at=100000))
    assert client.ready()
    assert client.clock_offset == offset
    client.receive(client.link, encode(RELAY, 45, 100000+offset, b"x"*16))
    assert frames[-1]["at"] == 100000
    client.receive(client.link, encode(RELAY, 80, 100000+offset-TTL_MS-1, b"y"*16))
    assert len(frames) == 1
    client.send(90)
    assert decode(packets[-1], now=100000+offset)["at"] == 100000+offset


def test_clock_ack_with_excessive_roundtrip_is_ignored(monkeypatch):
    from retium.live_heading import CLOCK_ACK
    client = HeadingClient(None, None, lambda frame: None)
    client.link = object()
    client.probes[1000] = 1.
    monkeypatch.setattr("retium.live_heading.time.monotonic", lambda: 4.)
    client.receive(client.link, encode(CLOCK_ACK, at=9000, probe_at=1000))
    assert client.clock_offset is None
