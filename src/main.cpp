#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Adafruit_NeoPixel.h>
#include <ArduinoOTA.h>
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define ENABLE_KEYBOARD 1

#include <USB.h>
#include <USBHIDMouse.h>
#if ENABLE_KEYBOARD
#include <USBHIDKeyboard.h>
#endif

constexpr uint8_t PIXEL_COUNT = 1;
constexpr uint8_t NEOPIXEL_PIN = 21;
constexpr uint16_t WIFI_RETRY_INTERVAL_MS = 5000;
constexpr uint16_t ALERT_BLINK_INTERVAL_MS = 400;
constexpr uint8_t BUTTON_PIN = 8;
constexpr uint8_t PIXEL_BRIGHTNESS = 255;
constexpr uint16_t TCP_CONSOLE_PORT = 9000;
constexpr uint16_t LOOP_DELAY_CONNECTED_MS = 2;
constexpr uint16_t LOOP_DELAY_DISCONNECTED_MS = 15;
constexpr uint32_t MOUSE_WAKE_INTERVAL_MS = 3 * 60 * 1000;
constexpr uint32_t BREATH_PERIOD_MS = 2000;
constexpr uint32_t BREATH_UPDATE_INTERVAL_MS = 30;
constexpr uint8_t BREATH_MIN_BRIGHTNESS = 10;
constexpr uint8_t BREATH_MAX_BRIGHTNESS = 255;
constexpr uint8_t RAINBOW_STEP = 2;
constexpr uint32_t KEYBOARD_MIN_INTERVAL_MS = 10;

// TODO: set these to your Wi-Fi credentials.
constexpr char WIFI_SSID[] = "WIFI_SSID";
constexpr char WIFI_PASS[] = "xxxxxx";
constexpr char OTA_HOSTNAME[] = "esp32-airtype";

Adafruit_NeoPixel pixel(PIXEL_COUNT, NEOPIXEL_PIN, NEO_RGB + NEO_KHZ800);
WiFiServer consoleServer(TCP_CONSOLE_PORT);
WiFiClient consoleClient;
int lastConsoleButtonValue = -1;
USBHIDMouse mouse;
#if ENABLE_KEYBOARD
USBHIDKeyboard keyboard;
#endif
uint32_t lastWifiAttempt = 0;
uint32_t lastAlertToggle = 0;
bool alertState = false;
bool wasConnected = false;
bool otaConfigured = false;
bool otaActive = false;
uint32_t lastIpLog = 0;
bool keyboardTypingActive = false;
uint32_t lastMouseWake = 0;
uint32_t lastBreathUpdate = 0;
uint8_t breathHue = 0;
bool breathInvertColors = false;
uint32_t lastKeyboardSendMs = 0;
volatile bool buttonPressed = false;
volatile bool buttonStateDirty = false;
volatile uint32_t buttonChangeTick = 0;
constexpr uint32_t BUTTON_DEBOUNCE_MS = 15;
static portMUX_TYPE buttonMux = portMUX_INITIALIZER_UNLOCKED;

void IRAM_ATTR handleButtonInterrupt() {
  const TickType_t now = xTaskGetTickCountFromISR();
  int level = digitalRead(BUTTON_PIN);
  if (now - buttonChangeTick < pdMS_TO_TICKS(BUTTON_DEBOUNCE_MS)) {
    return;
  }
  buttonChangeTick = now;
  const bool newState = level == LOW;
  if (newState == buttonPressed) {
    return;
  }
  buttonPressed = newState;
  buttonStateDirty = true;
}

static void beginWifiConnect() {
  Serial.printf("Connecting to Wi-Fi SSID: %s\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  lastWifiAttempt = millis();
}

static inline void taskDelayMs(uint32_t ms) {
  vTaskDelay(pdMS_TO_TICKS(ms));
}

static void maintainWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  uint32_t now = millis();
  if (now - lastWifiAttempt >= WIFI_RETRY_INTERVAL_MS) {
    Serial.println("Wi-Fi not connected, retrying...");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    lastWifiAttempt = now;
  }
}

static void applySolidColor(uint8_t r, uint8_t g, uint8_t b) {
  pixel.setPixelColor(0, r, g, b);
  pixel.show();
}

static uint32_t wheelColor(uint8_t pos) {
  pos = 255 - pos;
  uint8_t r, g, b;
  if (pos < 85) {
    r = 255 - pos * 3;
    g = 0;
    b = pos * 3;
  } else if (pos < 170) {
    pos -= 85;
    r = 0;
    g = pos * 3;
    b = 255 - pos * 3;
  } else {
    pos -= 170;
    r = pos * 3;
    g = 255 - pos * 3;
    b = 0;
  }
  return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

static void updateBreathingLed() {
  uint32_t now = millis();
  if (now - lastBreathUpdate < BREATH_UPDATE_INTERVAL_MS) {
    return;
  }
  lastBreathUpdate = now;
  breathHue = static_cast<uint8_t>(breathHue + RAINBOW_STEP);
  uint32_t pos = now % BREATH_PERIOD_MS;
  uint32_t half = BREATH_PERIOD_MS / 2;
  uint8_t brightness;
  if (pos < half) {
    brightness = BREATH_MIN_BRIGHTNESS +
                 ((BREATH_MAX_BRIGHTNESS - BREATH_MIN_BRIGHTNESS) * pos) / half;
  } else {
    uint32_t desc = pos - half;
    brightness =
        BREATH_MAX_BRIGHTNESS -
        ((BREATH_MAX_BRIGHTNESS - BREATH_MIN_BRIGHTNESS) * desc) / half;
  }
  uint32_t color = wheelColor(breathHue);
  uint8_t r = (color >> 16) & 0xFF;
  uint8_t g = (color >> 8) & 0xFF;
  uint8_t b = color & 0xFF;
  r = (r * brightness) / 255;
  g = (g * brightness) / 255;
  b = (b * brightness) / 255;
  if (breathInvertColors) {
    r = 255 - r;
    g = 255 - g;
    b = 255 - b;
  }
  applySolidColor(r, g, b);
}

static void showAlertPattern() {
  uint32_t now = millis();
  if (now - lastAlertToggle >= ALERT_BLINK_INTERVAL_MS) {
    lastAlertToggle = now;
    alertState = !alertState;
    if (alertState) {
      applySolidColor(255, 0, 0);
    } else {
      applySolidColor(0, 0, 255);
    }
  }
}

static void logIpAndMac(const char* prefix) {
  String ip = WiFi.localIP().toString();
  String mac = WiFi.macAddress();
  Serial.printf("%s IP: %s, MAC: %s\n", prefix, ip.c_str(), mac.c_str());
}

static void sendConsoleButtonState(bool buttonDown, bool force) {
  if (!consoleClient || !consoleClient.connected()) {
    return;
  }
  int currentValue = buttonDown ? 1 : 0;
  if (!force && currentValue == lastConsoleButtonValue) {
    return;
  }
  if (consoleClient.availableForWrite() <= 0) {
    return;
  }
  consoleClient.printf("%d\n", currentValue);
  lastConsoleButtonValue = currentValue;
}

static void closeConsoleClient(const char* reason) {
  if (!consoleClient) {
    return;
  }
  Serial.printf("Console client %s\n", reason);
  consoleClient.stop();
  lastConsoleButtonValue = -1;
}

static void handleTcpConsoleInput(bool buttonDown) {
  if (WiFi.status() != WL_CONNECTED) {
    if (consoleClient) {
      closeConsoleClient("dropped (Wi-Fi lost)");
    }
    return;
  }

  if (!consoleClient || !consoleClient.connected()) {
    if (consoleClient) {
      closeConsoleClient("disconnected");
    }
    WiFiClient candidate = consoleServer.available();
    if (candidate) {
      candidate.setNoDelay(true);
      consoleClient = candidate;
      Serial.printf("Console client connected from %s:%u\n",
                    consoleClient.remoteIP().toString().c_str(),
                    consoleClient.remotePort());
      lastConsoleButtonValue = -1;
      sendConsoleButtonState(buttonDown, true);
    }
    return;
  }

  while (consoleClient.available()) {
    char raw = static_cast<char>(consoleClient.read());
    char c = raw == '\r' ? '\n' : raw;
    if (c == '\n') {
      Serial.println("Console RX: <newline>");
    } else if (c >= 32 && c <= 126) {
      Serial.printf("Console RX: %c\n", c);
    } else {
      Serial.printf("Console RX: 0x%02X\n", static_cast<unsigned char>(c));
    }
#if ENABLE_KEYBOARD
    if (buttonDown && keyboardTypingActive) {
      uint32_t now = millis();
      uint32_t elapsed = now - lastKeyboardSendMs;
      if (elapsed < KEYBOARD_MIN_INTERVAL_MS) {
        taskDelayMs(KEYBOARD_MIN_INTERVAL_MS - elapsed);
        now = millis();
      }
      keyboard.write(static_cast<uint8_t>(c));
      lastKeyboardSendMs = now;
    }
#endif
  }

  if (!consoleClient.connected()) {
    closeConsoleClient("disconnected");
    return;
  }

  sendConsoleButtonState(buttonDown, false);
}

static void ensureOtaConfigured() {
  if (otaConfigured) {
    return;
  }
  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA.onStart([]() {
    Serial.printf("OTA start (%s)\n",
                  ArduinoOTA.getCommand() == U_FLASH ? "sketch" : "filesystem");
  });
  ArduinoOTA.onEnd([]() { Serial.println("\nOTA end"); });
  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    Serial.printf("OTA progress: %u%%\n", (progress * 100) / total);
  });
  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("OTA Error[%u]: ", error);
    switch (error) {
      case OTA_AUTH_ERROR:
        Serial.println("Auth Failed");
        break;
      case OTA_BEGIN_ERROR:
        Serial.println("Begin Failed");
        break;
      case OTA_CONNECT_ERROR:
        Serial.println("Connect Failed");
        break;
      case OTA_RECEIVE_ERROR:
        Serial.println("Receive Failed");
        break;
      case OTA_END_ERROR:
        Serial.println("End Failed");
        break;
      default:
        Serial.println("Unknown error");
        break;
    }
  });
  otaConfigured = true;
}

static void beginOtaIfConnected() {
  if (otaActive || WiFi.status() != WL_CONNECTED) {
    return;
  }
  ensureOtaConfigured();
  ArduinoOTA.begin();
  otaActive = true;
  Serial.printf("OTA ready: %s.local (%s)\n", OTA_HOSTNAME,
                WiFi.localIP().toString().c_str());
}

#if ENABLE_KEYBOARD
static void updateKeyboardTypingState(bool enable) {
  if (enable == keyboardTypingActive) {
    return;
  }
  keyboardTypingActive = enable;
  if (keyboardTypingActive) {
    Serial.println("Keyboard typing ENABLED");
  } else {
    keyboard.releaseAll();
    Serial.println("Keyboard typing DISABLED");
  }
}

#endif

static void performMouseWakeJiggle() {
  constexpr int8_t STEP = 10;
  constexpr uint32_t STEP_DELAY_MS = 2;
  static const int8_t moves[4][2] = {
      {STEP, 0}, {0, STEP}, {-STEP, 0}, {0, -STEP},
  };
  uint32_t idx = esp_random() % 4;
  int8_t dx = moves[idx][0];
  int8_t dy = moves[idx][1];
  mouse.move(dx, dy, 0);
  taskDelayMs(STEP_DELAY_MS);
  mouse.move(-dx, -dy, 0);
  breathInvertColors = !breathInvertColors;
}

static void printChipInfo() {
  Serial.println("=== ESP32-S3 Mini Info ===");
  Serial.printf("Chip model: %s\n", ESP.getChipModel());
  Serial.printf("Chip revision: %d\n", ESP.getChipRevision());
  Serial.printf("CPU frequency: %d MHz\n", ESP.getCpuFreqMHz());
  Serial.printf("Flash size: %u MB\n", ESP.getFlashChipSize() / (1024 * 1024));
  Serial.printf("Sketch size / free: %u KB / %u KB\n",
                ESP.getSketchSize() / 1024, ESP.getFreeSketchSpace() / 1024);
  Serial.printf("Heap size / free: %u KB / %u KB\n",
                ESP.getHeapSize() / 1024, ESP.getFreeHeap() / 1024);
  Serial.println("==========================");
}

void setup() {
  Serial.begin(115200);
  // Wait briefly for the USB CDC serial port to come up (ESP32-S3 requirement).
  while (!Serial && millis() < 2000) {
    taskDelayMs(10);
  }
  Serial.println("ESP32-S3 Mini setup complete. Driving NeoPixel on pin 21.");
  pixel.begin();
  pixel.setBrightness(PIXEL_BRIGHTNESS);
  pixel.clear();
  pixel.show();
  USB.begin();
  mouse.begin();
#if ENABLE_KEYBOARD
  keyboard.begin();
#endif
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  bool initialPressed = digitalRead(BUTTON_PIN) == LOW;
  buttonPressed = initialPressed;
  attachInterrupt(BUTTON_PIN, handleButtonInterrupt, CHANGE);
#if ENABLE_KEYBOARD
  updateKeyboardTypingState(initialPressed);
#endif
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);
  beginWifiConnect();
  ensureOtaConfigured();

  consoleServer.begin();
  Serial.printf("TCP console listening on port %u\n", TCP_CONSOLE_PORT);
  printChipInfo();
  beginOtaIfConnected();
}

void loop() {
  maintainWifi();
  bool connected = WiFi.status() == WL_CONNECTED;
  bool stateChanged = false;
  portENTER_CRITICAL(&buttonMux);
  if (buttonStateDirty) {
    buttonStateDirty = false;
    stateChanged = true;
  }
  portEXIT_CRITICAL(&buttonMux);
  bool pressedSnapshot = digitalRead(BUTTON_PIN) == LOW;
#if ENABLE_KEYBOARD
  updateKeyboardTypingState(pressedSnapshot);
#endif
  uint32_t nowMs = millis();
  if (nowMs - lastMouseWake >= MOUSE_WAKE_INTERVAL_MS) {
    performMouseWakeJiggle();
    lastMouseWake = nowMs;
  }
  handleTcpConsoleInput(pressedSnapshot);

  if (connected && !wasConnected) {
    logIpAndMac("Wi-Fi connected,");
  } else if (!connected && wasConnected) {
    Serial.println("Wi-Fi disconnected, entering alert mode.");
    lastIpLog = 0;
  }
  wasConnected = connected;

  if (connected) {
    beginOtaIfConnected();
    if (pressedSnapshot) {
      applySolidColor(0, 0, 255);
    } else {
      updateBreathingLed();
    }
    if (millis() - lastIpLog >= 2000) {
      logIpAndMac("Wi-Fi connected,");
      lastIpLog = millis();
    }
  } else {
    otaActive = false;
    showAlertPattern();
  }

  Serial.printf("Button state: %s\n", pressedSnapshot ? "PRESSED" : "RELEASED");
  if (stateChanged) {
    Serial.println("(debounced change)");
  }
  if (otaActive) {
    ArduinoOTA.handle();
  }
  uint32_t loopDelay =
      wasConnected ? LOOP_DELAY_CONNECTED_MS : LOOP_DELAY_DISCONNECTED_MS;
  taskDelayMs(loopDelay);
}
