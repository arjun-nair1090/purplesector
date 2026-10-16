"""Background UDP receiver: one socket, one thread, clean stop/restart."""
from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable

from config.defaults import UDP_RECV_BUFFER_BYTES
from telemetry.connection import ConnectionMonitor
from telemetry.decoder import IgnoredPacketError, PacketError, UnsupportedFormatError, decode_packet
from telemetry.packet_models import Packet

log = logging.getLogger(__name__)


class UdpReceiver:
    def __init__(self, host: str, port: int, monitor: ConnectionMonitor, on_packet: Callable[[Packet], None],
                 supported_formats: tuple[int, ...] = (2022,)):
        self.host, self.port = host, port
        self.monitor = monitor
        self.on_packet = on_packet
        self.supported_formats = supported_formats
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Bind synchronously so port conflicts are reported immediately. Returns success."""
        if self.running:
            return True
        self._stop.clear()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RECV_BUFFER_BYTES)
            sock.bind((self.host, self.port))
        except OSError as exc:
            sock.close()
            if getattr(exc, "winerror", None) == 10048 or exc.errno in (98, 48, 10048):
                msg = (f"UDP port {self.port} is already in use by another program, such as another telemetry "
                       "app or a second PurpleSector window. Close it, then press Try again.")
            else:
                msg = f"Cannot listen on {self.host}:{self.port}: {exc.strerror or exc}."
            log.error(msg)
            self.monitor.set_error(msg)
            return False
        sock.settimeout(0.5)
        self._sock = sock
        self.monitor.set_running(True, f"{self.host}:{self.port}")
        self._thread = threading.Thread(target=self._run, name="udp-receiver", daemon=True)
        self._thread.start()
        log.info("UDP receiver listening on %s:%s", self.host, self.port)
        return True

    def _run(self) -> None:
        sock = self._sock
        while not self._stop.is_set():
            try:
                data = sock.recv(2048)
            except TimeoutError:
                continue
            except OSError as exc:
                if self._stop.is_set():
                    break
                # Windows reports ICMP port-unreachable on UDP sockets as WSAECONNRESET; harmless.
                if getattr(exc, "winerror", None) == 10054:
                    continue
                log.exception("UDP receive failed")
                self.monitor.set_error(f"Socket error: {exc}")
                break
            self.handle_datagram(data)
        self.monitor.set_running(False)

    def handle_datagram(self, data: bytes) -> None:
        try:
            packet = decode_packet(data, self.supported_formats)
        except UnsupportedFormatError as exc:
            self.monitor.record_unsupported(exc.packet_format)
            return
        except IgnoredPacketError as exc:
            self.monitor.record_ignored(str(exc))
            return
        except PacketError as exc:
            self.monitor.record_malformed(str(exc))
            return
        except Exception as exc:  # struct errors or anything unexpected: never kill the thread
            self.monitor.record_malformed(f"decoder error: {exc!r}")
            return
        self.monitor.record_valid(packet.packet_id.name)
        try:
            self.on_packet(packet)
        except Exception:
            log.exception("Packet handler failed for %s", packet.packet_id.name)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._sock is not None:
            self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                log.warning("UDP receiver thread did not stop within %.1fs", timeout)
        self._sock = None
        self._thread = None
        self.monitor.set_running(False)
