# ESP32 AirType

An ESP32-S3 Mini sketch that turns the board into a USB HID keyboard/mouse and
accepts text or base64 payloads over a TCP console (`9000`) to type on the host.

## How it works

- Wi-Fi STA connects to the configured SSID and listens for a TCP client.
- When the side button is held, incoming TCP bytes are injected via HID
  keyboard; otherwise the console is read-only for logging.
- LED states describe status (see below for details).
- OTA is enabled after Wi-Fi connects (hostname `esp32-airtype.local`).
- Mouse joggle runs every 3 minutes to keep the host awake.

## When to use it

This is meant for highly locked-down or offline hosts that still accept a USB
keyboard. You can drip small payloads (short scripts, config snippets, secrets)
from a separate machine over Wi-Fi; the ESP32 replays them as keystrokes on the
restricted machine. Typing speed is intentionally limited (~100 chars/second), so
it is suitable for small transfers, not bulk data exfiltration.
- Use it only on systems you own or are authorized to access; do not deploy it
  for illegal or unauthorized activity.

## Hardware requirements

- ESP32-S3 Mini (USB-capable) with NeoPixel on pin 21 (or wire a single RGB LED
  to GPIO 21 with power/ground as per NeoPixel specs).
- Momentary button wired to GPIO 8 and ground; the firmware enables the internal
  pull-up, so no external resistor is needed. Press = LOW = typing enabled.
- USB connection to the target computer (HID keyboard/mouse) and Wi-Fi available
  to the sender host.

## LED behavior

- Breathing rainbow: idle/connected, ready for commands.
- Solid blue: button held and typing active.
- Red/blue blinking: Wi-Fi disconnected.

## Typing speed / throttling

- The host helper `scripts/sender.py` defaults to 32-character bursts with a
  100 chars/second cap (`--rate` overrides; `0` disables).
- On-device, `KEYBOARD_MIN_INTERVAL_MS` enforces a ~100 chars/second floor
  between HID key events to match the sender cap.

## Usage

1. Update Wi-Fi credentials in `src/main.cpp` (`WIFI_SSID` / `WIFI_PASS`). Avoid
   committing real credentials. Build/flash with PlatformIO (`platformio run -t upload`).  
2. Start the board; it will log Wi-Fi/OTA info over USB CDC.  
3. Stream a payload from your host:
   ```bash
   python scripts/sender.py payload.txt text --host <board-ip> --port 9000
   # or base64 firmware/data
   python scripts/sender.py firmware.bin base64 --rate 100 --burst 32
   ```
4. Hold the button to enable typing; release to stop.

For sender options and decoding helpers, see `scripts/README.md`.
