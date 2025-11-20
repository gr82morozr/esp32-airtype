# Python Client

Utilities for interacting with the ESP32 firmware from a host computer.

## TCP console helper (`sender.py`)

`sender.py` only uses Python's standard library. Provide the path to an input
file and choose a mode:

- `base64`: read the file as bytes, base64-encode it in memory, wait
  (configurable, default 1 second) after connecting to the ESP32 (default
  `192.168.1.136:9000`), and then stream the encoded characters in bursts
  (default 32 chars) with a rate cap (default 100 chars/second).
- `text`: read the file as text, send each line as-is but normalize line endings
  to Windows style (CRLF) before streaming so the HID keyboard injects the
  expected carriage returns.

```bash
# base64 mode (gentle defaults: 32-char bursts capped to ~100 cps)
python scripts/sender.py firmware.bin base64 --host 192.168.1.136 --port 9000

# tuned example (e.g., 64-char bursts every 1 ms, still capped by --rate)
python scripts/sender.py firmware.bin base64 --interval 0.001 --burst 64 --rate 200

# text mode
python scripts/sender.py payload.txt text --host 192.168.1.136 --port 9000
```

Use `--rate` to cap characters per second (set to `0` to disable), and combine it
with `--interval`, `--burst`, `--start-delay`, and `--connect-timeout` to tune
throughput and connection behavior. Press `Ctrl+C` to stop; the script
automatically reconnects whenever the ESP32 drops the socket.

## Base64 decoder (`decode_base64.py`)

`decode_base64.py` reads a base64-encoded file and writes the decoded bytes to a
new file.

```bash
python scripts/decode_base64.py encoded.txt decoded.bin
```

The same workflow is available as a PowerShell script (`decode_base64.ps1`) for
environments where Python is unavailable or constrained.

```powershell
pwsh -File scripts/decode_base64.ps1 -InputPath encoded.txt -OutputPath decoded.bin
```
