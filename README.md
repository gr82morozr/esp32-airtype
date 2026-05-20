# ESP32 AirType

https://www.oceanlabz.in/getting-started-with-esp32-s3-mini-development-board/

`esp32-airtype` is an Arduino/PlatformIO firmware for the `LOLIN S3 Mini` that:

- exposes itself to a host computer as a USB HID mouse and keyboard
- joins Wi-Fi as a station
- listens for a TCP client on port `9000`
- types incoming bytes as keystrokes only while a hardware button is held
- performs Arduino OTA updates after Wi-Fi comes up
- nudges the mouse every 3 minutes to keep the host awake

The implementation lives in [`src/main.cpp`](src/main.cpp) and the host helpers live under [`scripts/`](scripts).

## Behavior

- USB: the board enumerates as a HID mouse and keyboard over native USB.
- Wi-Fi: station mode only, with reconnect attempts every 5 seconds.
- TCP console: a single client can connect to port `9000`.
- Keyboard injection: received bytes are forwarded to the HID keyboard only when the GPIO `8` button is pressed.
- Mouse wake: every `3` minutes the firmware moves the cursor briefly and returns it.
- OTA: `ArduinoOTA` starts once Wi-Fi is connected, using hostname `esp32-airtype`.

## LED states

The built-in NeoPixel on GPIO `21` is used as status output:

- breathing rainbow: Wi-Fi connected and idle
- solid blue: button held, keyboard typing enabled
- alternating red/blue blink: Wi-Fi disconnected

The mouse wake jiggle also toggles the LED color inversion so you can see that the wake task is still running.

## Hardware

Expected wiring from the current firmware:

- board: `LOLIN S3 Mini` / ESP32-S3
- status LED: 1 NeoPixel on GPIO `21`
- button: momentary switch between GPIO `8` and GND
- USB: connected to the target machine that should receive keyboard/mouse input

`GPIO 8` uses `INPUT_PULLUP`, so pressed means `LOW`.

Reference image: [`doc/esp32s3-mini.pins.png`](doc/esp32s3-mini.pins.png)

## Configuration

Before building, update the Wi-Fi constants in [`src/main.cpp`](src/main.cpp):

```cpp
constexpr char WIFI_SSID[] = "WIFI_SSID";
constexpr char WIFI_PASS[] = "xxxxxx";
constexpr char OTA_HOSTNAME[] = "esp32-airtype";
```

Other useful firmware constants in the same file:

- `TCP_CONSOLE_PORT = 9000`
- `MOUSE_WAKE_INTERVAL_MS = 3 * 60 * 1000`
- `KEYBOARD_MIN_INTERVAL_MS = 10`
- `BUTTON_DEBOUNCE_MS = 15`

## Build And Flash

PlatformIO environments are defined in [`platformio.ini`](platformio.ini):

- `env:esp32-s3-devkitm-1`: USB flash
- `env:esp32-s3-devkitm-1-ota`: OTA upload via `espota`

USB flash:

```bash
pio run -e esp32-s3-devkitm-1 -t upload
pio device monitor -b 115200
```

If the board does not enter the bootloader automatically, switch it to upload mode manually:

1. Hold the `BOOT` button.
2. Press and release `RESET`.
3. Release `BOOT`.
4. Run the upload command again.

After flashing, press `RESET` once if the sketch does not start on its own.

OTA upload:

```bash
pio run -e esp32-s3-devkitm-1-ota -t upload
```

The OTA environment currently hard-codes:

```ini
upload_port = 192.168.1.136
upload_flags = -p 3232
```

Adjust `upload_port` to the board's current IP before using OTA uploads.

## Host Helpers

[`scripts/sender.py`](scripts/sender.py) streams a file to the TCP console.

Modes:

- `text`: reads text and normalizes line endings to `CRLF`
- `base64`: reads bytes and base64-encodes them before sending

Examples:

```bash
python scripts/sender.py payload.txt text --host 192.168.1.136 --port 9000
python scripts/sender.py firmware.bin base64 --host 192.168.1.136 --port 9000
```

Useful sender defaults from the current script:

- `--burst 32`
- `--rate 100`
- `--start-delay 1.0`
- `--connect-timeout 5.0`

[`scripts/decode_base64.py`](scripts/decode_base64.py) and [`scripts/decode_base64.ps1`](scripts/decode_base64.ps1) decode captured base64 back into binary data.

## Typical Flow

1. Flash the firmware over USB.
2. Open the serial monitor and note the device IP address.
3. Connect the ESP32-S3 USB port to the machine that should receive keyboard/mouse input.
4. Start `scripts/sender.py` from another machine on the same network.
5. Hold the button on GPIO `8` when you want incoming TCP data to be typed.
6. Release the button to stop typing immediately.

## Serial Output

The firmware logs over USB CDC at `115200` baud, including:

- Wi-Fi connection attempts and current IP/MAC
- TCP client connect/disconnect events
- OTA progress
- button state changes
- chip and flash information at boot

## Storage Layout

[`partitions.csv`](partitions.csv) defines a 4 MB flash layout with:

- `app0` and `app1` OTA slots
- `spiffs`
- `nvs`
- `otadata`
- `coredump`

## Operational Notes

- The TCP console is plain text over the local network and does not implement authentication or encryption.
- OTA is enabled without an OTA password in the current firmware.
- Only one TCP console client is supported at a time.
- The firmware prints button state continuously in the main loop, so serial output is intentionally noisy.
- Typing is rate-limited in firmware to roughly 100 characters per second.
