"""Ephemeral pointing telemetry. Encrypted RNS Links, no application retry/outbox.

13 byte sample payloads (29 on relay) are separate from voice Channels. Absolute
sample time rejects delayed packets; a bounded clock handshake corrects skew.
"""
import math
import struct
import threading
import time

import RNS

MAGIC = b"RH"
HELLO, ACK, SAMPLE, RELAY = range(4)
CLOCK_HELLO, CLOCK_ACK = 4, 5
HEADER = struct.Struct("!2sBQH")
STOP = 65535
TTL_MS = 4000


def clock_ms():
    return time.time_ns() // 1_000_000


def encode(kind, heading=None, at=None, sender=None, probe_at=None):
    if heading is not None and (type(heading) not in (int, float) or not math.isfinite(heading) or not 0 <= heading < 360):
        raise ValueError("heading must be 0-359.9 degrees or null")
    payload = HEADER.pack(MAGIC, kind, clock_ms() if at is None else at,
                          STOP if heading is None else round(heading * 10) % 3600)
    if kind == RELAY:
        if not isinstance(sender, bytes) or len(sender) != 16:
            raise ValueError("invalid heading sender")
        payload += sender
    if kind == CLOCK_ACK:
        payload += struct.pack("!Q", probe_at)
    return payload


def decode(raw, now=None):
    if not isinstance(raw, bytes) or len(raw) not in (HEADER.size, HEADER.size + 8, HEADER.size + 16):
        raise ValueError("invalid heading packet size")
    magic, kind, at, heading = HEADER.unpack(raw[:HEADER.size])
    if magic != MAGIC or kind not in (HELLO, ACK, SAMPLE, RELAY, CLOCK_HELLO, CLOCK_ACK) or len(raw) != HEADER.size + (16 if kind == RELAY else 8 if kind == CLOCK_ACK else 0):
        raise ValueError("invalid heading packet")
    age = (clock_ms() if now is None else now) - at
    if kind not in (CLOCK_HELLO, CLOCK_ACK) and (age > TTL_MS or age < -TTL_MS):
        raise ValueError("stale heading or unsynchronized clock")
    if heading != STOP and heading >= 3600:
        raise ValueError("invalid heading")
    return {"type": "heading.sample", "heading": None if heading == STOP else heading / 10,
            "at": at, "kind": kind, "sender_hash": raw[HEADER.size:].hex() if kind == RELAY else "", "ttl_ms": TTL_MS,
            "probe_at": struct.unpack("!Q", raw[HEADER.size:])[0] if kind == CLOCK_ACK else None}


def interval(link):
    """Cap at 10 Hz; budget roughly 15% of measured link rate, at most 2s."""
    rate = link.get_establishment_rate()
    return max(.1, min(2., 1024 / (.15 * rate))) if rate and rate > 0 else .25


def transmit(link, raw):
    if link.status != RNS.Link.ACTIVE:
        return False
    # No application delivery tracking or retry backlog. RNS may still emit
    # link proofs according to the destination's configured proof policy.
    return RNS.Packet(link, raw, create_receipt=False).send() is not False


class HeadingHub:
    def __init__(self, callback, ready=lambda: True, authorized=lambda identity: True):
        self.callback, self.ready, self.authorized = callback, ready, authorized
        self.links, self.latest, self.sent = {}, {}, {}
        self.lock = threading.RLock()

    def attach(self, link):
        link.set_packet_callback(lambda raw, packet: self.receive(link, raw))

    def receive(self, link, raw):
        identity = link.get_remote_identity()
        if identity is None or not self.ready() or not self.authorized(identity):
            return
        try:
            frame = decode(raw)
            sender = identity.hash
            if frame["kind"] in (HELLO, CLOCK_HELLO):
                with self.lock:
                    if link not in self.links and len(self.links) >= 128:
                        return
                    self.links[link] = sender
                transmit(link, encode(CLOCK_ACK, probe_at=frame["at"]) if frame["kind"] == CLOCK_HELLO else encode(ACK))
            elif frame["kind"] == SAMPLE and link in self.links:
                self.publish(frame["heading"], sender, at=frame["at"], excluded=link)
        except (ValueError, OSError, RuntimeError):
            return

    def publish(self, heading, sender, at=None, excluded=None):
        if not self.ready():
            return False
        at = clock_ms() if at is None else at
        raw = encode(RELAY, heading, at, sender)
        with self.lock:
            previous = self.latest.get(sender)
            if previous and (at <= previous[0] or (heading is not None and at - previous[0] < 95)):
                return False
            self.latest = {key: value for key, value in self.latest.items() if at - value[0] <= TTL_MS}
            self.latest[sender] = (at, heading)
            links = list(self.links)
        frame = decode(raw)
        self.callback(frame)
        for link in links:
            if link is excluded or not self.authorized(link.get_remote_identity()):
                continue
            key = (link, sender)
            now = time.monotonic()
            with self.lock:
                if heading is not None and now - self.sent.get(key, 0) < interval(link):
                    continue
                self.sent[key] = now
            try:
                transmit(link, raw)
            except (OSError, RuntimeError):
                pass
        return True

    def disconnected(self, link):
        with self.lock:
            sender = self.links.pop(link, None)
            self.sent = {key: value for key, value in self.sent.items() if key[0] is not link}
        if sender:
            self.publish(None, sender)


class HeadingClient:
    def __init__(self, identity, destination, callback, failed=lambda destination: None):
        self.identity, self.destination, self.callback = identity, destination, callback
        self.failed = failed
        self.link = None
        self.ack = threading.Event()
        self.last_ack = self.last_sent = 0.
        self.latest = {}
        self.connect_lock = threading.Lock()
        self.generation = 0
        self.clock_offset = None
        self.probes = {}

    def receive(self, link, raw):
        if link is not self.link:
            return
        try:
            local_now = clock_ms()
            frame = decode(raw, now=local_now + (self.clock_offset or 0))
            if frame["kind"] == CLOCK_ACK:
                started = self.probes.pop(frame["probe_at"], None)
                if started is None:
                    return
                elapsed = (time.monotonic() - started) * 1000
                if not 0 <= elapsed <= 2000:
                    return
                offset = frame["at"] - (frame["probe_at"] + elapsed / 2)
                if self.clock_offset is not None and abs(offset - self.clock_offset) > 1000:
                    self.latest.clear()
                self.clock_offset = offset
                self.last_ack = time.monotonic()
                self.ack.set()
            elif frame["kind"] == RELAY:
                if self.clock_offset is None:
                    return
                sender = frame["sender_hash"]
                if frame["at"] <= self.latest.get(sender, 0):
                    return
                self.latest = {key: value for key, value in self.latest.items() if local_now + self.clock_offset - value <= TTL_MS}
                self.latest[sender] = frame["at"]
                frame["at"] = round(frame["at"] - self.clock_offset)
                self.callback(frame)
        except ValueError:
            pass

    def probe(self):
        at, started = clock_ms(), time.monotonic()
        self.probes = {key: value for key, value in self.probes.items() if started - value < 2}
        self.probes[at] = started
        transmit(self.link, encode(CLOCK_HELLO, at=at))

    def ensure(self):
        with self.connect_lock:
            if self.link and self.link.status == RNS.Link.ACTIVE and time.monotonic() - self.last_ack < 6:
                self.probe()
                return self.ready()
            if self.link:
                self.failed(self.link.destination.hash.hex())
            self.close()
            generation = self.generation
            destination = self.destination(6)
            if generation != self.generation:
                return False
            established = threading.Event()
            link = RNS.Link(destination, established_callback=lambda _: established.set())
            self.link = link
            link.set_packet_callback(lambda raw, packet: self.receive(link, raw))
            if not established.wait(6) or link.status != RNS.Link.ACTIVE or link is not self.link:
                if link is self.link: self.failed(destination.hash.hex())
                if link is self.link: self.close()
                return False
            link.identify(self.identity)
            # Identification and subscription may reorder; retry only HELLO,
            # never a heading. No sensor sample waits for establishment.
            for _ in range(4):
                if link is not self.link: return False
                self.probe()
                if self.ack.wait(.5):
                    return True
            self.close()
            self.failed(destination.hash.hex())
            return False

    def ready(self):
        return bool(self.clock_offset is not None and self.link and self.link.status == RNS.Link.ACTIVE and time.monotonic() - self.last_ack < 6)

    def send(self, heading):
        if not self.ready():
            return False
        now = time.monotonic()
        if heading is not None and now - self.last_sent < interval(self.link):
            return False
        self.last_sent = now
        return transmit(self.link, encode(SAMPLE, heading, at=round(clock_ms() + self.clock_offset)))

    def close(self):
        self.generation += 1
        link, self.link = self.link, None
        self.ack.clear()
        self.last_ack = 0
        self.clock_offset = None
        self.probes.clear()
        self.latest.clear()
        if link is not None and link.status != RNS.Link.CLOSED:
            link.teardown()
