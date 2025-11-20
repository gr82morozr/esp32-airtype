#!/usr/bin/env python3
"""TCP client that streams file data (base64 or text) to the ESP32 console."""

from __future__ import annotations

import argparse
import base64
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Optional

RECONNECT_DELAY = 2.0
DEFAULT_START_DELAY = 1.0
DEFAULT_INTERVAL = 0.0
DEFAULT_BURST = 32
DEFAULT_RATE = 100.0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
      description="Stream file contents to the ESP32 TCP console.")
  parser.add_argument(
      "input",
      type=Path,
      help="Path to the file that should be streamed.",
  )
  parser.add_argument(
      "mode",
      choices=("base64", "text"),
      help="Transmission mode: base64-encode the file or send text directly.",
  )
  parser.add_argument(
      "--host",
      default="192.168.1.136",
      help="ESP32 hostname or IP (default: %(default)s)",
  )
  parser.add_argument(
      "--port",
      type=int,
      default=9000,
      help="ESP32 TCP console port (default: %(default)s)",
  )
  parser.add_argument(
      "--interval",
      type=float,
      default=DEFAULT_INTERVAL,
      help=(
          "Seconds between bursts (0 to disable, combined with --rate when set, "
          "default: %(default)s)"
      ),
  )
  parser.add_argument(
      "--burst",
      type=int,
      default=DEFAULT_BURST,
      help=(
          "Number of characters to send per interval (default: %(default)s). "
          "Smaller bursts smooth out typing."
      ),
  )
  parser.add_argument(
      "--rate",
      type=float,
      default=DEFAULT_RATE,
      help=(
          "Maximum characters per second (0 to disable rate cap, default: "
          "%(default)s)"
      ),
  )
  parser.add_argument(
      "--connect-timeout",
      type=float,
      default=5.0,
      help="Seconds to wait when opening the socket (default: %(default)s)",
  )
  parser.add_argument(
      "--start-delay",
      type=float,
      default=DEFAULT_START_DELAY,
      help="Seconds to wait after connecting before streaming (default: %(default)s)",
  )
  return parser


def _sleep_with_stop(duration: float, stop_flag: "StopFlag") -> None:
  """Sleep up to duration seconds, bailing out early if stop_flag is set."""
  end = time.monotonic() + max(0.0, duration)
  while not stop_flag.is_set:
    remaining = end - time.monotonic()
    if remaining <= 0:
      break
    time.sleep(min(0.1, remaining))


def _load_payload(path: Path, mode: str) -> str:
  if mode == "base64":
    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    print(
        f"[sender] Loaded {path} ({len(data)} bytes -> {len(encoded)} base64 chars)"
    )
    return encoded
  text = path.read_text()
  if not text:
    print(f"[sender] Loaded {path} (empty text)")
    return ""
  parts = []
  for chunk in text.splitlines(keepends=True):
    if chunk.endswith(("\r", "\n")):
      stripped = chunk.rstrip("\r\n")
      parts.append(stripped + "\r\n")
    else:
      parts.append(chunk)
  payload = "".join(parts)
  print(
      f"[sender] Loaded {path} ({len(text)} chars text -> {len(payload)} bytes after CRLF normalization)"
  )
  return payload


def stream_forever(
    host: str,
    port: int,
    interval: float,
    connect_timeout: float,
    payload: str,
    burst: int,
    start_delay: float,
    rate: float,
    stop_flag: "StopFlag",
) -> None:
  """Open a TCP connection and send base64 characters."""
  total_chars = len(payload)
  while not stop_flag.is_set:
    sock: Optional[socket.socket] = None
    try:
      print(f"[sender] Connecting to {host}:{port} ...")
      sock = socket.create_connection((host, port), timeout=connect_timeout)
      sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
      print(f"[sender] Connected to {host}:{port}")
      if start_delay > 0:
        print(f"[sender] Waiting {start_delay:.1f}s before transmitting...")
        _sleep_with_stop(start_delay, stop_flag)

      next_send = time.monotonic()
      index = 0
      while not stop_flag.is_set:
        if index >= total_chars:
          sys.stdout.write("\r[sender] Progress: 100.00%\n")
          sys.stdout.flush()
          print("[sender] Finished sending payload.")
          return
        now = time.monotonic()
        if now < next_send:
          _sleep_with_stop(min(0.01, next_send - now), stop_flag)
          continue
        send_count = min(burst, total_chars - index)
        chunk = payload[index:index + send_count]
        index += send_count
        sock.sendall(chunk.encode("ascii"))
        percent = (index / total_chars) * 100.0
        sys.stdout.write(f"\r[sender] Progress: {percent:6.2f}%")
        sys.stdout.flush()
        delay = interval if interval > 0 else 0.0
        if rate > 0:
          delay = max(delay, send_count / rate)
        if delay > 0:
          next_send = time.monotonic() + delay
    except KeyboardInterrupt:
      stop_flag.set()
    except (ConnectionError, OSError) as exc:
      if stop_flag.is_set:
        break
      print(
          f"[sender] Socket error: {exc!r}. Retrying in {RECONNECT_DELAY:.0f} seconds..."
      )
      _sleep_with_stop(RECONNECT_DELAY, stop_flag)
    finally:
      if sock:
        try:
          sock.close()
        except OSError:
          pass


class StopFlag:
  def __init__(self) -> None:
    self.is_set = False

  def set(self) -> None:
    self.is_set = True


def main() -> None:
  parser = build_parser()
  args = parser.parse_args()

  payload = _load_payload(args.input, args.mode)
  stop_flag = StopFlag()

  def handle_stop(signum: int, _frame: Optional[object]) -> None:
    print(f"[sender] Received signal {signum}, shutting down...")
    stop_flag.set()

  for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, handle_stop)

  try:
    stream_forever(
        host=args.host,
        port=args.port,
        interval=args.interval,
        connect_timeout=args.connect_timeout,
        payload=payload,
        burst=args.burst,
        start_delay=args.start_delay,
        rate=args.rate,
        stop_flag=stop_flag,
    )
  except KeyboardInterrupt:
    stop_flag.set()
  finally:
    print("[sender] Stopped.")


if __name__ == "__main__":
  if sys.version_info < (3, 8):
    sys.exit("Python 3.8+ is required.")
  main()

