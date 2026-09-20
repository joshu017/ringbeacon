# RingBeacon

**Companion site: [ringbeacon.net](https://ringbeacon.net/)** — control your beacon and build animation commands in your browser.

[![Open Source Hardware](https://img.shields.io/badge/Open_Source-Hardware-00979D)](https://oshwa.org/definition/)

## Hardware

- **MCU:** ESP32-C3 (any dev board)
- **LEDs:** Multiple WS2812B in a ring, connected to GPIO 8 (the default in the sketch is 8 LEDs)
- **Protocol:** Bluetooth Low Energy (BLE 5.0)

## Project layout and quick start

```text
Makefile                  ESP32-C3 build, USB upload, firmware checks
ringbeacon/ringbeacon.ino  Firmware (Arduino folder and filename must match)
ringbeacon.py             BLE CLI and optional Tkinter GUI
web/public/index.html     Website source, served by Cloudflare Workers
web/wrangler.jsonc        Worker and ringbeacon.net custom domain configuration
web/package-lock.json     Locked Wrangler dependency tree
README.md                 Setup and complete protocol reference
AGENTS.md                 Contributor instructions and architectural constraints
tests/                    Hardware-free protocol regression checks
```

Install `arduino-cli`, Python 3.11+, and Node.js supported by the locked Wrangler version.

```sh
make setup
make build
make ports
make flash UPLOAD_PORT=/dev/cu.usbmodem1101  # use your actual port
make monitor UPLOAD_PORT=/dev/cu.usbmodem1101
python3 ringbeacon.py --scan
python3 ringbeacon.py --gui
make test           # firmware timing; requires Python and a C++ compiler
```

`make setup` installs ESP32 core 3.3.10, NimBLE-Arduino 2.3.8, NeoPixelBus 2.8.4,
and ArduinoJson 7.4.3. The Python tool runs directly as a single file using your
existing Python installation. Tkinter is optional and must
be supplied by your Python installation for the GUI. See `make help` for targets
and overrides. The USB port is explicit so uploads never use a stale saved port.

### Companion website: ringbeacon.net

The website is an independent npm project in `web/`, following Cloudflare's
[static-site scaffold](https://developers.cloudflare.com/workers/static-assets/get-started/).
The checked-in scaffold is ready to use; no generator needs to be rerun.
The root Makefile handles firmware only. Use Node.js 22 or newer, as required
by the locked Wrangler version.

```sh
cd web
npm ci             # install locked dependencies
npm run dev        # local Workers runtime; open the printed localhost URL
# Stop the server with Ctrl+C before continuing.
npm test           # browser command-generation regression tests
npm run build      # validate/package with Wrangler; does not publish
npm run check      # tests + build
npx wrangler login
npx wrangler whoami # verify the account owning ringbeacon.net
npm run deploy     # publish the existing ringbeacon Worker
```

Edit `web/public/index.html`, save, and reload the local page. `public/` contains
only deployable files. This is plain HTML/CSS/JavaScript; no frontend bundler is
needed. `npm run build` runs Wrangler's deployment dry run and writes generated
Worker output under `.wrangler/build`; static assets remain in `public/`.
`npm run deploy` builds directly from the current source and uploads the assets.
A dry run validates local packaging; it does not verify Cloudflare authentication,
remote domain ownership, deployment success, or a physical BLE connection.

This assets-only configuration intentionally omits `main` and an `ASSETS` binding,
as specified in [Cloudflare's migration guidance](https://developers.cloudflare.com/workers/static-assets/migration-guides/migrate-from-pages/).
There is no server-side application code requiring Node.js compatibility or
binding types. Browser console errors and BLE behavior must be checked in the
browser; Workers invocation logs and traces do not instrument browser JavaScript.

The Worker uses [Cloudflare Workers Static Assets](https://developers.cloudflare.com/workers/static-assets/)
and the custom domains [ringbeacon.net](https://ringbeacon.net/) and
[www.ringbeacon.net](https://www.ringbeacon.net/). Both serve the same page directly,
without a redirect. Each hostname is explicitly bound with `custom_domain: true`
in `web/wrangler.jsonc`; a DNS CNAME alone does not bind a Worker custom domain.
Keep `workers_dev` false and omit `account_id` from the configuration.
The configured Worker name, `ringbeacon`,
matches the existing Cloudflare dashboard service. Authenticate to the account
that owns it. For Cloudflare Git builds, set the root directory to `web`, use
`npm ci && npm run check` as the build command and `npm run deploy` as the deploy
command. Keep credentials out of the repository.

If adding `www` fails because of an externally managed DNS record, remove only
the conflicting `www.ringbeacon.net` record in Cloudflare DNS and deploy again.
Preserve the working apex custom domain and all unrelated DNS records. Cloudflare
manages DNS and TLS for both custom domains. After deployment, verify that both
HTTPS URLs return the expected RingBeacon page with status 200 and no redirect.

For full firmware/Python/browser protocol compatibility checks, run
`python3 -m unittest discover -s tests -v` from the repository root (requires
Node.js and a C++ compiler). Web development itself requires only Node.js/npm.

The page communicates directly with the beacon using Web Bluetooth. Use a
browser with Web Bluetooth support in a secure context (HTTPS or localhost).
The Python client is available when the browser does not expose Bluetooth.

### Timing migration

`duration` now means **milliseconds per cycle**, as shown in both interfaces.
`cycles` is the repeat count; zero means unlimited. Optional `timeout` is a total
run-time cap. Legacy `speed` is accepted as an alias when `duration` is absent;
when both appear, `duration` wins. Values of zero for cycle duration are clamped
to 1 ms; they do not freeze an animation. New UI payloads always include duration.

```json
{"cmd":"blink","color":[0,255,255],"duration":1000,"cycles":3}
```

This produces three cyan blinks over three seconds, then turns off. With the
standard duty ratio, each cycle is 500 ms on and 500 ms off. Flash the updated
firmware before using the new website or GUI. For old commands that used
`duration` as a total limit, rename that field to `timeout`, and rename `speed`
to `duration` (or retain it as the legacy alias).

## Firmware Dependencies

Install via Arduino Library Manager:

| Library | Version | Purpose |
|---|---|---|
| NimBLE-Arduino | 2.x | BLE stack |
| NeoPixelBus by Makuna | 2.8+ | LED driver (BitBang), color spaces, easing curves |
| ArduinoJson | 7.x | JSON parsing |

Board: **ESP32-C3** under Espressif ESP32 Arduino core 3.x.

## Python Dependencies

```
python3 -m pip install bleak
```

Requires Python 3.11+. Uses only `bleak` as an external dependency; install it
once in the Python installation you use to run the script, if it is not already
available. No project environment or requirements file is needed.

---

## BLE Interface

### Service

| Field | Value |
|---|---|
| Service UUID | `c0de1234-beef-cafe-1234-000000000001` |

### Characteristics

| Name | UUID | Properties | Description |
|---|---|---|---|
| **ID** | `c0de1234-beef-cafe-1234-000000000002` | READ, WRITE | Persistent ring identifier (UTF-8 string, max 32 bytes). Stored in NVS flash; separate from the BLE advertised name. |
| **Control** | `c0de1234-beef-cafe-1234-000000000003` | WRITE | JSON animation command payload. Supports accumulation across multiple BLE writes for payloads exceeding MTU. |

### Device Discovery

Each ring advertises with:
- **Local name:** `RingBeacon` (shared by all units)
- **Ring ID:** stored separately, default `ring-XXXX` from the last four hex digits of the BT MAC
- **Service UUID:** included in the advertisement packet

Discover by service UUID. The Python client reads the ID characteristic to target a specific ring; `--id` is not an advertised-name filter.

### MTU and Large Payloads

The firmware negotiates MTU up to 512 bytes. For JSON payloads that exceed a single BLE write, the firmware accumulates incoming bytes in a 1KB buffer and attempts to parse after each write. Partial JSON (detected via ArduinoJson's `IncompleteInput` error) is held until the rest arrives. A 5-second timeout clears stale partial buffers.

The Python client automatically chunks payloads at 500 bytes.

---

## JSON Command Format

Every command is a JSON object written to the **Control** characteristic. The only required field is `cmd`.

### Common Parameters

These parameters are accepted by all animation commands:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `cmd` | string | `"off"` | Animation command name (see below) |
| `color` | [R, G, B] | [255, 255, 255] | Primary color, each channel 0-255 |
| `color2` | [R, G, B] | [0, 0, 0] | Secondary color (used by blink, chase, wipe, gradient) |
| `hsv` | [H, S, V] | — | Alternative to `color`: hue 0-360, saturation 0-100, value 0-100. If both `color` and `hsv` are present, `color` takes precedence. |
| `hsv2` | [H, S, V] | — | Alternative to `color2`: same format as `hsv`. If both `color2` and `hsv2` are present, `color2` takes precedence. |
| `duration` | int | 1000 | Milliseconds per animation cycle; takes precedence over legacy `speed` |
| `speed` | int | 1000 | Legacy alias, used only when `duration` is absent |
| `timeout` | int | 0 | Total animation timeout in ms. 0 = no time limit. LEDs turn off when expired. |
| `cycles` | int | 0 | Stop after N complete animation cycles. 0 = no cycle limit. LEDs turn off when complete. |
| `brightness` | int | 255 | Global brightness, 0-255 |
| `easing` | string | `"linear"` | Easing curve applied to cycle progress (see Easing Curves) |
| `reverse` | bool | false | Reverse the animation direction |

### Timeout vs Cycles

Both `timeout` and `cycles` are upper bounds — **whichever fires first wins**. When either limit is reached, the LEDs turn off and the ring waits for the next command.

| `timeout` | `cycles` | Behavior |
|---|---|---|
| 0 | 0 | Run forever (default) |
| 5000 | 0 | Stop after 5 seconds |
| 0 | 3 | Stop after 3 complete cycles |
| 5000 | 3 | Stop at 5s OR 3 cycles, whichever comes first |

```json
{"cmd": "rotate", "color": [255, 0, 0], "duration": 800, "cycles": 3}
```
Rotate exactly 3 times (2.4 seconds total at 800ms/cycle), then turn off.

```json
{"cmd": "blink", "color": [255, 0, 0], "duration": 500, "timeout": 5000, "cycles": 20}
```
Blink for up to 5 seconds or 20 blinks — timeout will fire first at 5s (10 blinks completed).

### Cycle-Driven vs Continuous Animations

Most animations are **cycle-driven** — the visual progresses from start to finish over `duration` ms, then repeats. For these, `cycles` counts meaningful visual events (rotations, blinks, bounces, etc.). See each command's **Cycle** definition below.

Two animations are **continuous** and frame-based: `sparkle` and `fire`. They don't have a visual start/end point — they run as ongoing simulations. For these, `cycles` still works but uses `duration` as a time unit: `cycles: 3` at `duration: 1000` runs for 3 seconds. `timeout` is usually more intuitive for limiting continuous animations.

`solid` and `off` are static — they don't animate. `timeout` can be used with `solid` for timed display.

### Color Input

Both primary and secondary colors can be specified as RGB or HSV:

**RGB** (0-255 per channel):
```json
{"cmd": "solid", "color": [255, 0, 0]}
```

**HSV** (hue 0-360, saturation 0-100, value 0-100):
```json
{"cmd": "solid", "hsv": [0, 100, 100]}
```

Both produce red. The same applies to the secondary color with `color2` and `hsv2`:

```json
{"cmd": "gradient", "hsv": [0, 100, 100], "hsv2": [240, 100, 100]}
```

Red-to-blue gradient specified entirely in HSV.

**Precedence:** `color` takes precedence over `hsv`, and `color2` takes precedence over `hsv2`. You can mix freely — e.g. `color` (RGB) with `hsv2` (HSV).

---

## Animation Commands

### `off`

Turns all LEDs off immediately.

**Cycle:** not applicable — LEDs turn off instantly. The `cycles` and `timeout` parameters have no effect.

```json
{"cmd": "off"}
```

No additional parameters.

---

### `solid`

Sets all LEDs to a static color.

**Cycle:** not applicable — static display with no visual progression. Use `timeout` for timed display (e.g. `"timeout": 5000` to show for 5 seconds then turn off).

```json
{"cmd": "solid", "color": [0, 255, 0], "brightness": 128}
```

No command-specific parameters. Uses `color` and `brightness`.

---

### `blink`

Alternates between `color` (on) and off (or `color2` if set).

**Cycle:** one complete on→off (or color→color2) transition, lasting `duration` ms.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `duty` | float | 0.5 | On-time ratio, 0.0 to 1.0. At 0.5, on and off phases are equal. At 0.8, LEDs are on for 80% of each cycle. |

```json
{"cmd": "blink", "color": [255, 0, 0], "duration": 500}
```

Blink red at 2 Hz (500ms cycle).

```json
{"cmd": "blink", "color": [255, 0, 0], "color2": [0, 0, 255], "duration": 1000, "duty": 0.3}
```

Alternate between red (30% of cycle) and blue (70% of cycle) at 1 Hz.

---

### `breathe`

Smooth sinusoidal brightness pulsing. The brightness follows a `sin()` curve within the configured range. If `color2`/`hsv2` is provided, breathes between the two colors instead of pulsing brightness.

**Cycle:** one full dim→bright→dim pulse (half-sine wave), lasting `duration` ms.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `min_bright` | int | 0 | Minimum brightness (0-255) at the bottom of the breath. Ignored when `color2` is set. |
| `max_bright` | int | 255 | Maximum brightness (0-255) at the peak of the breath. Ignored when `color2` is set. |

```json
{"cmd": "breathe", "color": [0, 100, 255], "duration": 2000}
```

Blue pulse, full cycle every 2 seconds.

```json
{"cmd": "breathe", "hsv": [180, 100, 100], "duration": 3000, "min_bright": 20, "max_bright": 200, "easing": "sinusoidalInOut"}
```

Cyan breathe with a narrow brightness range and smoother easing.

```json
{"cmd": "breathe", "color": [255, 0, 0], "color2": [0, 0, 255], "duration": 2000}
```

Smooth transition between blue (at rest) and red (at peak), repeating every 2 seconds.

---

### `rotate`

One or more beams of light rotate around the ring with optional trailing fade. Beams are evenly spaced. Multiple beams blend additively.

**Cycle:** all beams complete one full revolution around the ring, lasting `duration` ms.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `width` | int | 3 | Beam width in pixels |
| `count` | int | 1 | Number of beams (evenly spaced around ring) |
| `trail` | int | 4 | Trail fade length in pixels (quadratic falloff) |
| `trail_dir` | string | `"back"` | Trail direction: `"back"` (behind beam), `"front"` (ahead of beam), `"both"` (extends in both directions) |

```json
{"cmd": "rotate", "color": [255, 0, 0], "duration": 800, "width": 2, "count": 2, "trail": 5}
```

Two red beams, opposite sides, rotating with 5-pixel trails behind — police light effect.

```json
{"cmd": "rotate", "color": [0, 0, 255], "duration": 2000, "width": 1, "count": 1, "trail": 8, "easing": "sinusoidalInOut"}
```

Single blue beam with long trail, variable speed via sinusoidal easing.

```json
{"cmd": "rotate", "color": [0, 255, 0], "duration": 1000, "width": 2, "trail": 4, "trail_dir": "both"}
```

Green beam with symmetric trail extending in both directions — wider, softer appearance.

```json
{"cmd": "rotate", "color": [255, 100, 0], "duration": 600, "width": 1, "trail": 6, "trail_dir": "front"}
```

Orange beam with trail leading ahead of the direction of travel.

---

### `chase`

Theater chase / marquee pattern. Groups of lit pixels scroll around the ring.

**Cycle:** the pattern advances by one full repeat (`spacing` pixels of movement), lasting `duration` ms.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `spacing` | int | 3 | Distance between lit groups (in pixels) |
| `width` | int | 3 | Width of each lit group |

```json
{"cmd": "chase", "color": [255, 255, 0], "duration": 500, "spacing": 4, "width": 2}
```

Yellow chase with 2 lit / 2 dark pattern.

```json
{"cmd": "chase", "color": [255, 0, 0], "color2": [0, 255, 0], "duration": 800, "spacing": 3, "width": 1}
```

Alternating red and green chase (Christmas lights).

---

### `rainbow`

Full HSV rainbow cycle rotating around the ring.

**Cycle:** the rainbow completes one full rotation around the ring, lasting `duration` ms.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `saturation` | int | 255 | Color saturation, 0 (white) to 255 (vivid) |
| `spread` | int | 1 | Number of full rainbow cycles that fit around the ring. 1 = one complete rainbow across all 16 LEDs. 2 = two rainbows, etc. |

```json
{"cmd": "rainbow", "duration": 3000}
```

Classic smooth rainbow rotation, one full cycle every 3 seconds.

```json
{"cmd": "rainbow", "duration": 5000, "spread": 2, "saturation": 180, "brightness": 150}
```

Two compressed rainbows, slightly desaturated and dimmed.

---

### `wipe`

Progressive color fill — pixels light up one by one from pixel 0 to pixel 15, then the cycle repeats.

**Cycle:** all pixels fill sequentially from pixel 0 to pixel 15, then reset. Lasts `duration` ms.

```json
{"cmd": "wipe", "color": [0, 255, 0], "duration": 2000}
```

Green wipe completing in 2 seconds.

```json
{"cmd": "wipe", "color": [255, 0, 0], "color2": [0, 0, 50], "duration": 1500}
```

Red wipe over a dim blue background.

---

### `sparkle`

Random pixels ignite to full brightness and decay. Frame-rate independent — runs on wall-clock time, not cycle progress.

**Cycle:** continuous — no visual cycle. The sparkle simulation runs independently of `duration`. The `cycles` parameter uses `duration` as a time unit: `cycles: 3` at `duration: 1000` runs for 3 seconds. Use `timeout` for more intuitive timed sparkle.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `density` | float | 0.3 | Probability of igniting each pixel per frame interval, 0.0 to 1.0. Higher = more simultaneous sparkles. |
| `fade_speed` | int | 20 | Decay rate. Higher = sparkles fade faster. |

```json
{"cmd": "sparkle", "color": [255, 255, 255], "density": 0.5, "fade_speed": 15}
```

Dense white twinkle with medium fade.

```json
{"cmd": "sparkle", "color": [255, 200, 50], "density": 0.1, "fade_speed": 30, "brightness": 200}
```

Sparse warm sparkle with fast fade.

---

### `comet`

A bright head travels around the ring with an exponentially decaying tail behind it.

**Cycle:** the comet head completes one full lap around the ring, lasting `duration` ms.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tail_length` | int | 6 | Number of pixels in the tail |
| `trail_decay` | float | 0.65 | Brightness multiplier per tail pixel. 0.5 = halves each pixel. 0.9 = long gentle tail. |

```json
{"cmd": "comet", "color": [0, 200, 255], "duration": 600, "tail_length": 8, "trail_decay": 0.7}
```

Cyan comet with a long, gradually fading tail.

```json
{"cmd": "comet", "color": [255, 100, 0], "duration": 400, "tail_length": 4, "trail_decay": 0.4}
```

Short sharp orange comet.

---

### `bounce`

KITT / Cylon scanner — a lit segment bounces back and forth across the ring (does not wrap around).

**Cycle:** one complete out-and-back sweep (the head travels to the far end and returns), lasting `duration` ms.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `width` | int | 3 | Width of the scanning head in pixels |
| `trail` | int | 4 | Trail fade length on both sides of the head (quadratic falloff) |

```json
{"cmd": "bounce", "color": [255, 0, 0], "duration": 1200, "width": 2, "trail": 5}
```

Classic red KITT scanner.

```json
{"cmd": "bounce", "color": [0, 255, 0], "duration": 800, "width": 4, "trail": 3, "easing": "cubicInOut"}
```

Wide green scanner with cubic easing for acceleration/deceleration at the edges.

---

### `fire`

Heat-based fire simulation. Each pixel maintains a "heat" value that cools, rises, and randomly sparks. The heat maps to a black-red-orange-yellow-white color palette. Frame-based — runs as fast as the main loop, independently of cycle progress.

**Cycle:** continuous — no visual cycle. The fire simulation steps every frame regardless of `duration`. The `cycles` parameter uses `duration` as a time unit: `cycles: 3` at `duration: 1000` runs for 3 seconds. Use `timeout` for more intuitive timed fire.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `cooling` | int | 55 | How quickly pixels cool down. Higher = shorter flames, more flickering. Range: 20-100. |
| `sparking` | int | 120 | Probability of new sparks igniting (0-255). Higher = more active fire. |

```json
{"cmd": "fire", "duration": 50, "cooling": 55, "sparking": 120}
```

Classic campfire. The simulation runs at full frame rate; `duration` only defines the time unit for the `cycles` counter.

```json
{"cmd": "fire", "cooling": 80, "sparking": 200, "brightness": 200}
```

Aggressive, bright fire with high spark rate.

```json
{"cmd": "fire", "cooling": 30, "sparking": 80}
```

Slow, smoldering embers (low spark rate, slow cooling).

---

### `gradient`

Smooth gradient between `color` and `color2` distributed around the ring. The gradient ping-pongs (color -> color2 -> color) for seamless wrapping, and rotates over time.

**Cycle:** the gradient completes one full rotation around the ring, lasting `duration` ms.

```json
{"cmd": "gradient", "color": [255, 0, 0], "color2": [0, 0, 255], "duration": 4000}
```

Red-to-blue gradient, rotating every 4 seconds.

```json
{"cmd": "gradient", "color": [255, 100, 0], "color2": [0, 50, 255], "duration": 60000}
```

A zero cycle duration is clamped to 1 ms; use a long duration for a slow gradient. Timeout turns LEDs off; it does not freeze them.

---

### `strobe`

Rapid flash bursts followed by a dark pause. Each cycle consists of N rapid on/off flashes, then a pause.

**Cycle:** one complete burst of N rapid flashes followed by a dark pause, lasting `duration` ms.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `flashes` | int | 3 | Number of rapid flashes per burst |
| `pause` | int | 200 | Pause timeout in ms between bursts (encoded as ratio within the cycle) |

```json
{"cmd": "strobe", "color": [255, 255, 255], "duration": 500, "flashes": 3}
```

White triple-flash strobe, 2 bursts per second.

```json
{"cmd": "strobe", "color": [255, 0, 0], "duration": 300, "flashes": 1}
```

Single red flash strobe at ~3.3 Hz.

---

### `pulse`

An expanding ring of light emanates from a specific pixel, spreading outward in both directions around the ring. Intensity fades with distance and as the pulse expands.

**Cycle:** the pulse expands from the origin to full radius and fades out, lasting `duration` ms.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `origin` | int | 0 | Starting pixel index (0-15) |

```json
{"cmd": "pulse", "color": [0, 255, 100], "duration": 1000, "origin": 0}
```

Green pulse expanding from pixel 0.

```json
{"cmd": "pulse", "color": [255, 255, 0], "duration": 600, "origin": 8, "easing": "exponentialOut"}
```

Yellow pulse from the opposite side of the ring with exponential easing (fast start, slow end).

---

## Easing Curves

Easing curves shape how the animation progresses through each cycle. Instead of linear (constant speed), easing can add acceleration, deceleration, or bounce.

The `easing` parameter accepts any of the following string values:

### Available Curves

| Name | Behavior |
|---|---|
| `linear` | Constant speed (default) |
| `quadraticIn` | Slow start, accelerates |
| `quadraticOut` | Fast start, decelerates |
| `quadraticInOut` | Slow start and end, fast middle |
| `quadraticCenter` | Fast start and end, slow middle |
| `cubicIn` | Stronger slow start |
| `cubicOut` | Stronger fast start |
| `cubicInOut` | Stronger ease in and out |
| `cubicCenter` | Stronger fast edges, slow middle |
| `quarticIn` | Even stronger slow start |
| `quarticOut` | Even stronger fast start |
| `quarticInOut` | Even stronger ease in and out |
| `quinticIn` | Maximum polynomial slow start |
| `quinticOut` | Maximum polynomial fast start |
| `quinticInOut` | Maximum polynomial ease in and out |
| `sinusoidalIn` | Sine-based slow start |
| `sinusoidalOut` | Sine-based fast start |
| `sinusoidalInOut` | Sine-based smooth ease in and out |
| `exponentialIn` | Exponential acceleration |
| `exponentialOut` | Exponential deceleration |
| `exponentialInOut` | Exponential ease in and out |
| `circularIn` | Circular curve slow start |
| `circularOut` | Circular curve fast start |
| `circularInOut` | Circular curve ease in and out |
| `gamma` | Gamma correction curve (perceptual brightness) |

### Easing Tips

- **`sinusoidalInOut`** is excellent for `breathe` and `bounce` — gives natural-feeling acceleration at direction changes.
- **`cubicInOut`** or **`exponentialInOut`** work well for `rotate` to simulate inertia.
- **`exponentialOut`** on `pulse` creates a fast burst that slows as it expands.
- **`gamma`** is useful for perceptually uniform brightness transitions.
- The **`*Center`** variants invert the typical curve: fast at edges, slow in the middle. Good for pendulum-like effects on `bounce`.

---

## CLI Tool Reference

### Synopsis

```
python3.11 ringbeacon.py [OPTIONS] [JSON_PAYLOAD]
```

### Arguments

| Argument | Description |
|---|---|
| `JSON_PAYLOAD` | JSON command string. If omitted (and no `--file`, `--set-id`, or `--read-id`), enters interactive mode. |

### Options

| Option | Description |
|---|---|
| `--gui` | Launch the Tkinter GUI. |
| `--id ID` | Target a specific ring by its ID (e.g. `--id ring-ab12`). Without this, the ring with the strongest signal is used. |
| `--file FILE`, `-f FILE` | Read JSON command from a file instead of the command line. The file contents are validated, minified, and sent. |
| `--scan` | Scan for all rings in range and print a table with ID, BLE address, and RSSI. Connects briefly to read each ring ID. |
| `--set-id NEW_ID` | Write a new persistent ID to the ring. **Requires `--id`** to identify which ring to rename. Max 32 UTF-8 bytes. |
| `--read-id` | Read and print the ring's current ID. |
| `--timeout SECONDS` | BLE scan timeout (default: 5.0). Increase in noisy environments. |

### Interactive Mode

When invoked with no JSON payload and no `--file`, the CLI connects to a ring and enters an interactive REPL where you can type JSON commands and send them on Enter:

```bash
python3.11 ringbeacon.py
python3.11 ringbeacon.py --id ring-ab12
```

```
Found: ring-ab12 (AA:BB:CC:DD:EE:FF)


Interactive mode — enter JSON commands (q to quit):
  Example: {"cmd":"rotate","color":[255,0,0],"duration":800}
  Shortcut: just a cmd name sends {"cmd":"<name>"}

> {"cmd":"rotate","color":[255,0,0],"duration":800}
  Sent: {"cmd":"rotate","color":[255,0,0],"duration":800}
> off
  Sent: {"cmd":"off"}
> q
Disconnected.
```

The shortcut feature lets you type a bare command name (e.g. `off`, `rainbow`, `fire`) and it wraps it as `{"cmd":"<name>"}` automatically.

### Usage Examples

**Send a command to the first ring found:**
```bash
python3.11 ringbeacon.py '{"cmd":"solid","color":[255,0,0]}'
```

**Target a specific ring:**
```bash
python3.11 ringbeacon.py --id ring-ab12 '{"cmd":"rotate","color":[0,0,255],"duration":600,"count":2}'
```

**Load command from a file:**
```bash
python3.11 ringbeacon.py --file effects/party.json
python3.11 ringbeacon.py --id ring-ab12 -f effects/alert.json
```

**Discover all rings:**
```bash
python3.11 ringbeacon.py --scan
```

Output:
```
Scanning for rings (5.0s)...

Ring ID                Address              RSSI
------------------------------------------------------
  ring-ab12                AA:BB:CC:DD:EE:FF    -45
  ring-f3e1                11:22:33:44:55:66    -62

2 ring(s) found.
```

**Read current ID:**
```bash
python3.11 ringbeacon.py --read-id
python3.11 ringbeacon.py --id ring-ab12 --read-id
```

**Naming a fleet of rings:**

The `--set-id` flag requires `--id` so you never accidentally rename the wrong ring:

```bash
# Step 1: power on all rings, scan to see their default IDs
python3.11 ringbeacon.py --scan

# Step 2: rename each ring using its current default ID
python3.11 ringbeacon.py --id ring-ab12 --set-id stage-left
python3.11 ringbeacon.py --id ring-f3e1 --set-id stage-right

# Step 3: verify
python3.11 ringbeacon.py --scan
```

**Combine operations:**
```bash
python3.11 ringbeacon.py --id stage-left --read-id '{"cmd":"breathe","color":[0,255,0]}'
```

Reads the ID, then sends the animation command.

### JSON File Format

JSON files should contain a single JSON object. Whitespace and formatting are stripped before sending. Example file `effects/alert.json`:

```json
{
    "cmd": "blink",
    "color": [255, 0, 0],
    "duration": 250,
    "duty": 0.7,
    "brightness": 255
}
```

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | No ring found within scan timeout |
| 2 | Argument error (invalid JSON, missing required args) |

### Troubleshooting

| Symptom | Fix |
|---|---|
| "No ring found" | Ensure the ESP32-C3 is powered and not connected to another client. BLE allows only one connection at a time. |
| Timeout during scan | Increase `--timeout`. On macOS, Bluetooth permissions must be granted to Terminal. |
| JSON parse error on device (see serial monitor) | Validate JSON with `python3 -c "import json; json.loads('...')"` before sending. |
| Animation doesn't change | Send `{"cmd":"off"}` first to reset, then send the new command. |
| Beacon not found by `--id` | The `--id` value must exactly match the persistent ring ID. Use `--scan` to check. |

---

## Cookbook

Quick-reference recipes for common scenarios.

### Status Indicators

```bash
# Green = OK
python3.11 ringbeacon.py '{"cmd":"solid","color":[0,255,0],"brightness":80}'

# Pulsing yellow = warning
python3.11 ringbeacon.py '{"cmd":"breathe","color":[255,200,0],"duration":1500}'

# Fast red blink = error
python3.11 ringbeacon.py '{"cmd":"blink","color":[255,0,0],"duration":250}'

# Turn off
python3.11 ringbeacon.py '{"cmd":"off"}'
```

### Ambient / Decorative

```bash
# Slow rainbow
python3.11 ringbeacon.py '{"cmd":"rainbow","duration":8000,"brightness":100}'

# Warm candlelight
python3.11 ringbeacon.py '{"cmd":"fire","duration":60,"cooling":40,"sparking":100,"brightness":150}'

# Gentle blue-purple gradient rotation
python3.11 ringbeacon.py '{"cmd":"gradient","color":[0,50,255],"color2":[150,0,255],"duration":6000,"brightness":120}'

# Starfield twinkle
python3.11 ringbeacon.py '{"cmd":"sparkle","color":[255,255,255],"density":0.15,"fade_speed":10,"brightness":180}'
```

### Attention / Alerts

```bash
# Police lights (red + blue rotating beams)
python3.11 ringbeacon.py '{"cmd":"rotate","color":[255,0,0],"duration":400,"width":3,"count":2,"trail":3}'

# Emergency strobe
python3.11 ringbeacon.py '{"cmd":"strobe","color":[255,255,255],"duration":300,"flashes":2}'

# Notification pulse
python3.11 ringbeacon.py '{"cmd":"pulse","color":[0,200,255],"duration":800,"origin":0,"easing":"exponentialOut"}'

# Timed alert (5 seconds then stops)
python3.11 ringbeacon.py '{"cmd":"blink","color":[255,0,0],"duration":200,"timeout":5000}'
```

### Multi-Beacon Setup

```bash
# Name your rings (--set-id requires --id to avoid renaming the wrong one)
python3.11 ringbeacon.py --id ring-ab12 --set-id stage-left
python3.11 ringbeacon.py --id ring-f3e1 --set-id stage-right

# Coordinated effects
python3.11 ringbeacon.py --id stage-left '{"cmd":"rotate","color":[255,0,0],"duration":1000}'
python3.11 ringbeacon.py --id stage-right '{"cmd":"rotate","color":[0,0,255],"duration":1000,"reverse":true}'
```

---

## Firmware Customization

### Changing the LED Pin

Edit `LED_PIN` in the sketch:

```cpp
#define LED_PIN   2   // change from default GPIO 8
```

### Changing LED Count

Edit `LED_COUNT`. The ring topology (wrapping, spacing calculations) adapts automatically:

```cpp
#define LED_COUNT 24  // 24-LED ring
```

### Enabling Debug Output

The sketch currently enables verbose serial logging with flush-after-print:

```cpp
#define DEBUG
```

### BLE UUIDs

If running multiple different projects on the same BLE network, change the service UUID to avoid collisions:

```cpp
#define SERVICE_UUID   "your-custom-uuid-here-000000000001"
#define CHAR_ID_UUID   "your-custom-uuid-here-000000000002"
#define CHAR_CTRL_UUID "your-custom-uuid-here-000000000003"
```

Update the corresponding constants in `ringbeacon.py` and `web/public/index.html`.
