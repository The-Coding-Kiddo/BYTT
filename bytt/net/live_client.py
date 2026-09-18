"""Threaded TCP client for the towfish/SatCenter live data port (16129).
Reassembles multi-packet frames, verifies checksums, and emits Qt signals
for 3101 (ping) and 166 (status) packets."""
import socket
import struct
import threading
import time
from PySide6.QtCore import QObject, Signal
from bytt.protocol import constants as pc
from bytt.protocol.packets import checksum_ok, parse_3101_body


class LiveClient(QObject):
    ping_received = Signal(object, object, dict)  # port_raw, stbd_raw, meta
    status_changed = Signal(str)
    raw_packet_received = Signal(bytes)

    def __init__(self, host, port, parent=None):
        super().__init__(parent)
        self.host, self.port = host, port
        self._sock   = None
        self._stop   = threading.Event()
        self._thread = None
        self._partial_frames = {}
        self._last_ping_number = None
        self._checksum_failures = 0

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        self.status_changed.emit('connecting')
        try:
            sock = socket.create_connection((self.host, self.port), timeout=3.0)
        except Exception as e:
            self.status_changed.emit(f'not connected ({e})')
            return
        self._sock = sock
        sock.settimeout(1.0)
        self.status_changed.emit('connected')
        buf = bytearray()
        try:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf.extend(chunk)
                self._drain(buf)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
            if not self._stop.is_set():
                self.status_changed.emit('disconnected (connection lost)')

    def _drain(self, buf):
        while True:
            if len(buf) < pc.PACKET_HEADER_SIZE:
                return
            idx = buf.find(pc.PACKET_IDENTIFIER_BYTES)
            if idx == -1:
                buf.clear()
                return
            if idx > 0:
                del buf[:idx]
            if len(buf) < pc.PACKET_HEADER_SIZE:
                return
            packet_size = struct.unpack_from('<I', buf, pc.PACKET_SIZE_OFFSET)[0]
            if packet_size < pc.PACKET_HEADER_SIZE or packet_size > 8_000_000:
                del buf[:4]
                continue
            if len(buf) < packet_size:
                return
            packet = bytes(buf[:packet_size])
            del buf[:packet_size]
            self._handle_packet(packet)

    def _prune_partial_frames(self, now):
        stale = [k for k, v in self._partial_frames.items()
                 if now - v['first_seen'] > pc.MULTI_PACKET_FRAME_TIMEOUT_S]
        for k in stale:
            del self._partial_frames[k]

    def _handle_packet(self, packet):
        packet_size = len(packet)
        try:
            packet_type = struct.unpack_from('<I', packet, pc.PACKET_TYPE_OFFSET)[0]
            flag        = struct.unpack_from('<H', packet, pc.PACKET_FLAG_OFFSET)[0]
        except struct.error:
            return
        if not checksum_ok(packet, packet_size):
            self._checksum_failures += 1
            self.status_changed.emit(f'checksum FAILED (packet dropped, '
                           f'{self._checksum_failures} total)')
            return
        self.raw_packet_received.emit(packet)
        is_multi = bool(flag & pc.PACKET_FLAG_MULTI_BIT)
        body_and_data = packet[pc.PACKET_HEADER_SIZE: packet_size - 4]
        if is_multi:
            body_and_data = self._reassemble(packet, packet_type, body_and_data)
            if body_and_data is None:
                return
        if packet_type == pc.PACKET_TYPE_3101:
            self._handle_3101(body_and_data)
        elif packet_type == pc.PACKET_TYPE_166:
            self._handle_166(body_and_data)

    def _reassemble(self, packet, packet_type, body_and_data):
        now = time.time()
        self._prune_partial_frames(now)
        try:
            frame_id     = struct.unpack_from('<H', packet, pc.PACKET_FRAME_ID_OFFSET)[0]
            total_packet = struct.unpack_from('<H', packet, pc.PACKET_TOTAL_OFFSET)[0]
            packet_num   = struct.unpack_from('<H', packet, pc.PACKET_NUMBER_OFFSET)[0]
        except struct.error:
            return None
        if total_packet <= 1 or packet_num < 1:
            return body_and_data
        if packet_num > total_packet:
            # Out-of-range fragment number — can't belong to a well-formed
            # frame of this size. Drop it rather than corrupting/growing the
            # partial-frame entry with a key the final join won't expect.
            return None
        key = (packet_type, frame_id)
        entry = self._partial_frames.get(key)
        if entry is None or entry['total'] != total_packet:
            entry = {'total': total_packet, 'parts': {}, 'first_seen': now}
            self._partial_frames[key] = entry
        entry['parts'][packet_num] = body_and_data
        if len(entry['parts']) < total_packet:
            return None
        del self._partial_frames[key]
        if set(entry['parts'].keys()) != set(range(1, total_packet + 1)):
            # Defensive: state got confused somehow (e.g. duplicate packet
            # numbers padding out the count). Drop the stale/corrupt entry
            # instead of KeyError-ing on the join below.
            return None
        return b''.join(entry['parts'][i] for i in range(1, total_packet + 1))

    def _handle_3101(self, body_and_data):
        parsed = parse_3101_body(None, body_and_data)
        if not parsed:
            return
        port_raw, stbd_raw, meta = parsed
        pn = meta.get('ping_number')
        if pn is not None and self._last_ping_number is not None:
            delta = pn - self._last_ping_number
            if delta > 1:
                meta['ping_gap'] = delta - 1
            elif delta < 0:
                meta['ping_gap'] = 0
        self._last_ping_number = pn if pn is not None else self._last_ping_number
        self.ping_received.emit(port_raw, stbd_raw, meta)

    def _handle_166(self, body_and_data):
        if len(body_and_data) < 1:
            return
        state = body_and_data[0]
        data_up = bool(state & 0x01)
        cmd_up  = bool(state & 0x10)
        if data_up and cmd_up:
            desc = 'connected (data+cmd)'
        elif data_up:
            desc = 'connected (data only)'
        elif cmd_up:
            desc = 'connected (cmd only)'
        else:
            desc = 'sonar link down'
        self.status_changed.emit(f'connected — hw: {desc}')
