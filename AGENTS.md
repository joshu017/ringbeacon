# RingBeacon contributor instructions

## Scope and layout

This repository is standalone and ESP32-C3 only. Its Makefile replaces the
ancestor framework build rules; do not restore multi-board detection,
board_config.mk, parent-directory dependencies, or OTA functionality.
README.md is the user and protocol reference. Keep project documentation here
and in README.md; the obsolete PLAN.md has been consolidated.

Firmware lives in ringbeacon/ringbeacon.ino; the directory and sketch name must
match. ringbeacon.py provides CLI/GUI controls as a standalone single file run
directly with python3. Do not add a required virtual environment or Python
packaging setup; bleak is installed separately in the user’s Python installation.
web/public/index.html is the sole
web page source. web/wrangler.jsonc and the locked npm dependencies configure
Cloudflare Workers hosting. Never recreate a copy/paste worker.txt. The root
Makefile is firmware-only; use npm scripts from web/ for all website work.
For Cloudflare changes, consult the official
[Workers best practices skill](https://github.com/cloudflare/skills/blob/main/skills/workers-best-practices/SKILL.md)
and [Wrangler skill](https://github.com/cloudflare/skills/blob/main/skills/wrangler/SKILL.md),
current documentation through Context7 when available, and the installed Wrangler
schema. This is an assets-only Worker: no main script or ASSETS binding is needed.
If server-side code is added, reassess binding types, compatibility flags, logs,
and traces. Keep the existing locked Wrangler version unless an upgrade is needed.

## Protocol contract

- duration: milliseconds per animation cycle, default 1000, minimum 1.
- speed: legacy alias used only when duration is absent.
- cycles: repeat count, zero means unlimited.
- timeout: optional total milliseconds, zero means no limit. The first of the
  cycle limit and timeout stops the animation and clears the LEDs.
- Preserve the service/characteristic UUIDs across firmware, Python, and website.
- BLE advertising name is RingBeacon, declared in the sketch, not build flags.
  Persistent ring IDs are stored in NVS and read via the ID characteristic.
- BLE callbacks and loop run in separate FreeRTOS tasks even on single-core C3;
  single-core execution is not a guarantee of atomic multi-field state transfer.
- Keep animation processing nonblocking, and yield on every loop path.
- Hardware defaults: 16 WS2812B GRB LEDs, GPIO 8, NeoPixelBus BitBang method.

## Validation and deployment

Run make test for firmware timing regressions and make build for ESP32-C3 compile
validation. In web/, run npm ci and npm run check for browser tests and Wrangler
build validation. Run python3 -m unittest discover -s tests -v from the root for
full cross-client protocol checks.
Keep help output and README commands in sync with the Makefile. Do not claim
hardware behavior is verified from compilation or host tests alone.

Use an explicit UPLOAD_PORT for USB flashing. Publishing uses npm run deploy from web/;
check the Cloudflare account and existing Worker name before the first migration.
Keep both ringbeacon.net and www.ringbeacon.net explicitly configured as custom
domains on the ringbeacon Worker. Both serve the page directly, without redirects.
Keep workers_dev false and omit account_id. A www CNAME to the apex does not
bind www to the Worker. If an existing www DNS record conflicts, ask the user to
remove only that record; preserve the working apex and unrelated DNS records.
After deployment, verify both HTTPS hostnames return the expected page with
status 200 and no redirect.
Do not commit .venv, build, node_modules, .wrangler, credentials, or local editor
settings. Preserve the license and existing GitHub history.
