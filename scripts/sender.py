#!/usr/bin/env python3
"""TCP client that zips a file, base64-encodes it, and streams it over TCP."""

from __future__ import annotations

import argparse
import base64
import io
import select
import signal
import socket
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

RECONNECT_DELAY = 2.0
DEFAULT_START_DELAY = 1.0
DEFAULT_INTERVAL = 0.0
DEFAULT_BURST = 32
DEFAULT_RATE = 100.0
READY_POLL_INTERVAL = 0.1
READY_STATUS_INTERVAL = 2.0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
      description="Zip a file, base64-encode it, and stream it to a TCP receiver.",
      formatter_class=argparse.RawDescriptionHelpFormatter,
      epilog=(
          "Examples:\n"
          "  python scripts/sender.py scripts/email.txt --host 192.168.1.136\n"
          "  python scripts/sender.py scripts --host 192.168.1.136\n"
          "  python scripts/sender.py firmware.bin image.jpg --host 192.168.1.136 --port 9000\n"
          "  python scripts/sender.py payloads report.pdf --host esp32-airtype.local --rate 80 --burst 16\n"
      ),
  )
  parser.add_argument(
      "inputs",
      nargs="+",
      type=Path,
      help="One or more files or folders to stream.",
  )
  parser.add_argument(
      "--host",
      default="192.168.1.136",
      help="Receiver hostname or IP (default: %(default)s)",
  )
  parser.add_argument(
      "--port",
      type=int,
      default=9000,
      help="Receiver TCP port (default: %(default)s)",
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


def _build_payload(path: Path) -> bytes:
  archive_name = path.name
  with io.BytesIO() as buffer:
    with zipfile.ZipFile(
        buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zf:
      zf.writestr(archive_name, path.read_bytes())
    zipped = buffer.getvalue()
  payload = base64.b64encode(zipped)
  print(
      f"[sender] Loaded {path} ({path.stat().st_size} bytes -> zip {len(zipped)} bytes -> "
      f"{len(payload)} base64 bytes)"
  )
  return payload


def _build_end_marker(path: Path) -> bytes:
  marker = f"@@@SAVE-D:\\\\Temp\\\\{path.name}.zip###"
  return marker.encode("ascii")


def _expand_inputs(inputs: list[Path]) -> list[Path]:
  files: list[Path] = []
  for input_path in inputs:
    if not input_path.exists():
      sys.exit(f"[sender] Input path not found: {input_path}")
    if input_path.is_file():
      files.append(input_path)
      continue
    if input_path.is_dir():
      files.extend(sorted(path for path in input_path.rglob("*") if path.is_file()))
      continue
    sys.exit(f"[sender] Unsupported input path: {input_path}")
  if not files:
    sys.exit("[sender] No files found to send.")
  return files


def _drain_ready_state(
    sock: socket.socket,
    pending: str,
    ready: bool,
    saw_state: bool,
) -> tuple[str, bool, bool]:
  """Read newline-delimited readiness state from the receiver.

  `1` means ready to accept input, `0` means wait.
  """
  while True:
    readable, _, _ = select.select([sock], [], [], 0)
    if not readable:
      break
    data = sock.recv(128)
    if not data:
      raise ConnectionError("socket closed by remote host")
    pending += data.decode("ascii", errors="ignore")
    while "\n" in pending:
      line, pending = pending.split("\n", 1)
      line = line.strip()
      if line == "1":
        if not ready:
          print("[sender] Receiver ready. Resuming transfer.")
        ready = True
        saw_state = True
      elif line == "0":
        if ready:
          print("[sender] Receiver not in input mode. Pausing transfer.")
        else:
          print("[sender] Receiver not in input mode. Waiting...")
        ready = False
        saw_state = True
  return pending, ready, saw_state


def stream_forever(
    host: str,
    port: int,
    interval: float,
    connect_timeout: float,
    payload: bytes,
    label: str,
    burst: int,
    start_delay: float,
    rate: float,
    stop_flag: "StopFlag",
) -> None:
  """Open a TCP connection and stream payload bytes."""
  total_chars = len(payload)
  while not stop_flag.is_set:
    sock: Optional[socket.socket] = None
    try:
      print(f"[sender] Connecting to {host}:{port} ...")
      sock = socket.create_connection((host, port), timeout=connect_timeout)
      sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
      print(f"[sender] Connected to {host}:{port} for {label}")
      if start_delay > 0:
        print(f"[sender] Waiting {start_delay:.1f}s before transmitting...")
        _sleep_with_stop(start_delay, stop_flag)

      next_send = time.monotonic()
      index = 0
      pending_state = ""
      receiver_ready = False
      saw_ready_state = False
      last_waiting_status = 0.0
      while not stop_flag.is_set:
        pending_state, receiver_ready, saw_ready_state = _drain_ready_state(
            sock, pending_state, receiver_ready, saw_ready_state)
        if index >= total_chars:
          sys.stdout.write(f"\r[sender] {label}: 100.00%\n")
          sys.stdout.flush()
          print(f"[sender] Finished sending {label}.")
          return
        if not receiver_ready:
          now = time.monotonic()
          if not saw_ready_state and now - last_waiting_status >= READY_STATUS_INTERVAL:
            print("[sender] No readiness state from receiver yet. Waiting...")
            last_waiting_status = now
          elif saw_ready_state and now - last_waiting_status >= READY_STATUS_INTERVAL:
            print("[sender] Receiver not in input mode. Waiting...")
            last_waiting_status = now
          _sleep_with_stop(READY_POLL_INTERVAL, stop_flag)
          continue
        now = time.monotonic()
        if now < next_send:
          _sleep_with_stop(min(0.01, next_send - now), stop_flag)
          continue
        send_count = min(burst, total_chars - index)
        chunk = payload[index:index + send_count]
        index += send_count
        sock.sendall(chunk)
        percent = (index / total_chars) * 100.0
        sys.stdout.write(f"\r[sender] {label}: {percent:6.2f}%")
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
  files_to_send = _expand_inputs(args.inputs)

  stop_flag = StopFlag()

  def handle_stop(signum: int, _frame: Optional[object]) -> None:
    print(f"[sender] Received signal {signum}, shutting down...")
    stop_flag.set()

  for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, handle_stop)

  try:
    for index, input_path in enumerate(files_to_send, start=1):
      if stop_flag.is_set:
        break
      label = f"{input_path.name} ({index}/{len(files_to_send)})"
      print(f"[sender] Preparing {label}")
      payload = _build_payload(input_path) + _build_end_marker(input_path)
      stream_forever(
          host=args.host,
          port=args.port,
          interval=args.interval,
          connect_timeout=args.connect_timeout,
          payload=payload,
          label=label,
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

