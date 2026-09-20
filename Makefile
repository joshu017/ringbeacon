SHELL := /bin/sh
.DEFAULT_GOAL := build
ARDUINO_CLI ?= arduino-cli
PYTHON ?= python3
BOARD_OPTIONS ?= PartitionScheme=min_spiffs,CDCOnBoot=cdc
FQBN := esp32:esp32:esp32c3:$(BOARD_OPTIONS)
BUILD_DIR := $(CURDIR)/build
UPLOAD_PORT ?=
UPLOAD_SPEED ?= 921600
ESP32_CORE_VERSION ?= 3.3.10

.PHONY: help setup build flash monitor ports test clean
help:
	@echo 'RingBeacon (ESP32-C3 only)'
	@echo '  setup        Install pinned Arduino core/libraries'
	@echo '  build        Compile firmware (default)'
	@echo '  ports        List USB ports'
	@echo '  flash        Build/upload; requires UPLOAD_PORT=/dev/cu.usbmodem...'
	@echo '  monitor      Serial monitor; requires UPLOAD_PORT=...'
	@echo '  test         Run firmware timing regression tests'
	@echo '  clean        Delete local build artifacts'
	@echo 'Overrides: ARDUINO_CLI, PYTHON, BOARD_OPTIONS, UPLOAD_PORT, UPLOAD_SPEED, ESP32_CORE_VERSION'
setup:
	$(ARDUINO_CLI) core update-index --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
	$(ARDUINO_CLI) core install esp32:esp32@$(ESP32_CORE_VERSION) --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
	$(ARDUINO_CLI) lib install "NimBLE-Arduino@2.3.8" "NeoPixelBus by Makuna@2.8.4" "ArduinoJson@7.4.3"
build:
	$(ARDUINO_CLI) compile --fqbn "$(FQBN)" --build-path "$(BUILD_DIR)" ringbeacon
flash:
	@test -n "$(UPLOAD_PORT)" || { echo 'Set UPLOAD_PORT (use make ports to list devices).'; exit 1; }
	$(MAKE) build
	$(ARDUINO_CLI) upload --fqbn "$(FQBN)" --port "$(UPLOAD_PORT)" --upload-property upload.speed=$(UPLOAD_SPEED) --input-dir "$(BUILD_DIR)" ringbeacon
monitor:
	@test -n "$(UPLOAD_PORT)" || { echo 'Set UPLOAD_PORT (use make ports to list devices).'; exit 1; }
	$(ARDUINO_CLI) monitor --port "$(UPLOAD_PORT)" --config baudrate=115200
ports:
	$(ARDUINO_CLI) board list
test:
	$(PYTHON) -m unittest discover -s tests -v -k firmware_timing
clean:
	rm -rf "$(CURDIR)/build"
