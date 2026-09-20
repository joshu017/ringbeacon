#!/usr/bin/env python3
"""RingBeacon — LED Ring Controller.

A combined CLI and GUI tool for controlling RingBeacon LED rings over BLE.

CLI usage:
    python3.11 ringbeacon.py '{"cmd":"blink","color":[255,0,0],"duration":500}'
    python3.11 ringbeacon.py --id ring-ab12 '{"cmd":"rotate","color":[0,255,0]}'
    python3.11 ringbeacon.py --file animation.json
    python3.11 ringbeacon.py                          # interactive mode
    python3.11 ringbeacon.py --scan
    python3.11 ringbeacon.py --id ring-ab12 --set-id stage-left
    python3.11 ringbeacon.py --read-id

GUI usage:
    python3.11 ringbeacon.py --gui
"""

import argparse
import asyncio
import json
import sys
import threading
from pathlib import Path

try:
    import readline
except ImportError:
    readline = None

try:
    from bleak import BleakScanner, BleakClient
    HAS_BLEAK = True
except ImportError:
    HAS_BLEAK = False

try:
    import tkinter as tk
    from tkinter import ttk, colorchooser, messagebox
    HAS_TK = True
except ImportError:
    HAS_TK = False

# ── BLE Constants ────────────────────────────────────────────────────────────

SERVICE_UUID   = "c0de1234-beef-cafe-1234-000000000001"
CHAR_ID_UUID   = "c0de1234-beef-cafe-1234-000000000002"
CHAR_CTRL_UUID = "c0de1234-beef-cafe-1234-000000000003"

SCAN_TIMEOUT = 5.0
CHUNK_SIZE   = 500
HISTORY_FILE = Path.home() / ".ringbeacon_history"


# ── CLI Functions ────────────────────────────────────────────────────────────

def _has_ring_service(adv):
    """Check if advertising data includes the ring service UUID."""
    return SERVICE_UUID.lower() in [u.lower() for u in (adv.service_uuids or [])]


async def _read_ring_id(device):
    """Connect briefly to read the ring's ID characteristic."""
    try:
        async with BleakClient(device, timeout=5) as client:
            raw = await client.read_gatt_char(CHAR_ID_UUID)
            return raw.decode("utf-8")
    except Exception:
        return None


async def scan_and_print(timeout=SCAN_TIMEOUT):
    """Scan for all rings and print a table."""
    print(f"Scanning for rings ({timeout}s)...")
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)

    rings = [(d, adv) for d, adv in discovered.values() if _has_ring_service(adv)]

    if not rings:
        print("No rings found.")
        return

    print(f"\n{'Ring ID':<24} {'BLE Name':<16} {'Address':<20} {'RSSI'}")
    print("-" * 70)
    for d, adv in sorted(rings, key=lambda x: x[1].rssi, reverse=True):
        ble_name = adv.local_name or d.name or "?"
        rssi = adv.rssi
        ring_id = await _read_ring_id(d) or "(unreadable)"
        print(f"  {ring_id:<22} {ble_name:<16} {d.address:<20} {rssi}")
    print(f"\n{len(rings)} ring(s) found.")


async def find_device(target_id=None, timeout=SCAN_TIMEOUT):
    """Find a ring by service UUID, optionally matching by ring ID."""
    label = target_id if target_id else "any ring"
    print(f"Scanning for {label}...")

    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    rings = [(d, adv) for d, adv in discovered.values() if _has_ring_service(adv)]

    if not rings:
        return None

    if not target_id:
        # Return the strongest signal ring
        rings.sort(key=lambda x: x[1].rssi, reverse=True)
        return rings[0][0]

    # Match by ring ID (requires connecting to each)
    for d, adv in rings:
        ring_id = await _read_ring_id(d)
        if ring_id == target_id:
            return d

    return None


async def send_json(client, payload_str):
    """Send a JSON payload to the control characteristic, chunked if needed."""
    data = payload_str.encode("utf-8")
    for i in range(0, len(data), CHUNK_SIZE):
        await client.write_gatt_char(CHAR_CTRL_UUID, data[i:i + CHUNK_SIZE], response=True)


async def interactive_mode(client):
    """Interactive REPL: type JSON commands, send on Enter."""
    # Set up command history (up/down arrow support)
    if readline is not None:
        try:
            readline.read_history_file(HISTORY_FILE)
        except FileNotFoundError:
            pass
        readline.set_history_length(500)

    print("\nInteractive mode — enter JSON commands (q to quit):")
    print('  Example: {"cmd":"rotate","color":[255,0,0],"duration":800}')
    print('  Shortcut: just a cmd name sends {"cmd":"<name>"}')
    print('  Up/Down arrows scroll through command history\n')

    try:
        while True:
            try:
                raw = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not raw:
                continue
            if raw.lower() == "q":
                break

            # Shortcut: bare command name without braces
            if not raw.startswith("{"):
                raw = json.dumps({"cmd": raw})

            try:
                parsed = json.loads(raw)
                minified = json.dumps(parsed, separators=(",", ":"))
            except json.JSONDecodeError as e:
                print(f"  Invalid JSON: {e}")
                continue

            try:
                await send_json(client, minified)
                print(f"  Sent: {minified}")
            except Exception as e:
                print(f"  BLE write error: {e}")
                break
    finally:
        # Save command history for next session
        if readline is not None:
            try:
                readline.write_history_file(HISTORY_FILE)
            except OSError:
                pass


async def cli_main(args):
    """Run CLI mode."""
    # --scan: list all rings and exit
    if args.scan:
        await scan_and_print(timeout=args.timeout)
        return

    # Resolve JSON payload
    payload = None
    if args.file:
        raw = Path(args.file).read_text()
        payload = json.dumps(json.loads(raw), separators=(",", ":"))
    elif args.json_payload:
        payload = json.dumps(json.loads(args.json_payload), separators=(",", ":"))

    # Determine mode: if no payload, no file, no set-id, no read-id -> interactive
    interactive = (payload is None and not args.set_id and not args.read_id)

    # Find device
    device = await find_device(target_id=args.id, timeout=args.timeout)
    if not device:
        label = args.id if args.id else "any ring"
        print(f"No ring found ({label}). Is it powered and advertising?")
        sys.exit(1)

    name = device.name or device.address
    print(f"Found: {name} ({device.address})")

    try:
        async with BleakClient(device) as client:
            # Read ID
            if args.read_id:
                raw = await client.read_gatt_char(CHAR_ID_UUID)
                print(f"Ring ID: {raw.decode('utf-8')}")

            # Set ID
            if args.set_id:
                await client.write_gatt_char(
                    CHAR_ID_UUID, args.set_id.encode("utf-8"), response=True
                )
                print(f"Ring ID set to: {args.set_id}")

            # Send single animation command
            if payload:
                await send_json(client, payload)
                print(f"Sent: {payload}")

            # Interactive REPL
            elif interactive:
                await interactive_mode(client)

    except KeyboardInterrupt:
        pass

    print("Disconnected.")


# ── Command Definitions ─────────────────────────────────────────────────────
# Used by the GUI for building parameter controls.

COMMANDS = [
    "off", "solid", "blink", "breathe", "rotate", "chase", "rainbow",
    "wipe", "sparkle", "comet", "bounce", "fire", "gradient", "strobe", "pulse",
]

COMMAND_DESCRIPTIONS = {
    "off":      "Turn all LEDs off",
    "solid":    "Static color on all LEDs",
    "blink":    "Alternate on/off (or color/color2)",
    "breathe":  "Sinusoidal brightness pulse (or color blend)",
    "rotate":   "Beam(s) rotating around the ring",
    "chase":    "Marquee / theater chase pattern",
    "rainbow":  "Full HSV rainbow rotation",
    "wipe":     "Progressive color fill pixel by pixel",
    "sparkle":  "Random pixels ignite and decay",
    "comet":    "Single head with exponential tail",
    "bounce":   "KITT / Cylon scanner, bounces back and forth",
    "fire":     "Heat-based fire simulation",
    "gradient": "Two-color gradient rotating around ring",
    "strobe":   "Rapid flash bursts with dark pause",
    "pulse":    "Expanding ring of light from origin pixel",
}

# Command-specific parameters: (label, json_key, widget_type, default, extra)
COMMAND_PARAMS = {
    "off":      [],
    "solid":    [],
    "blink":    [
        ("Duty cycle", "duty", "float", 0.5, {"from_": 0.0, "to": 1.0, "resolution": 0.05}),
    ],
    "breathe":  [
        ("Min brightness", "min_bright", "int", 0, {"from_": 0, "to": 255}),
        ("Max brightness", "max_bright", "int", 255, {"from_": 0, "to": 255}),
    ],
    "rotate":   [
        ("Beam width", "width", "int", 3, {"from_": 1, "to": 16}),
        ("Beam count", "count", "int", 1, {"from_": 1, "to": 8}),
        ("Trail length", "trail", "int", 4, {"from_": 0, "to": 16}),
        ("Trail direction", "trail_dir", "choice", "back", {"choices": ["back", "front", "both"]}),
    ],
    "chase":    [
        ("Spacing", "spacing", "int", 3, {"from_": 1, "to": 16}),
        ("Width", "width", "int", 3, {"from_": 1, "to": 16}),
    ],
    "rainbow":  [
        ("Saturation", "saturation", "int", 255, {"from_": 0, "to": 255}),
        ("Spread", "spread", "int", 1, {"from_": 1, "to": 8}),
    ],
    "wipe":     [],
    "sparkle":  [
        ("Density", "density", "float", 0.3, {"from_": 0.0, "to": 1.0, "resolution": 0.05}),
        ("Fade speed", "fade_speed", "int", 20, {"from_": 1, "to": 100}),
    ],
    "comet":    [
        ("Tail length", "tail_length", "int", 6, {"from_": 1, "to": 16}),
        ("Trail decay", "trail_decay", "float", 0.65, {"from_": 0.1, "to": 0.99, "resolution": 0.05}),
    ],
    "bounce":   [
        ("Width", "width", "int", 3, {"from_": 1, "to": 16}),
        ("Trail", "trail", "int", 4, {"from_": 0, "to": 16}),
    ],
    "fire":     [
        ("Cooling", "cooling", "int", 55, {"from_": 10, "to": 200}),
        ("Sparking", "sparking", "int", 120, {"from_": 0, "to": 255}),
    ],
    "gradient": [],
    "strobe":   [
        ("Flashes per burst", "flashes", "int", 3, {"from_": 1, "to": 20}),
        ("Pause (ms)", "pause", "int", 200, {"from_": 10, "to": 5000}),
    ],
    "pulse":    [
        ("Origin pixel", "origin", "int", 0, {"from_": 0, "to": 15}),
    ],
}

COLOR2_COMMANDS = {"blink", "breathe", "chase", "wipe", "gradient"}

EASING_CURVES = [
    "linear",
    "quadraticIn", "quadraticOut", "quadraticInOut", "quadraticCenter",
    "cubicIn", "cubicOut", "cubicInOut", "cubicCenter",
    "quarticIn", "quarticOut", "quarticInOut",
    "quinticIn", "quinticOut", "quinticInOut",
    "sinusoidalIn", "sinusoidalOut", "sinusoidalInOut",
    "exponentialIn", "exponentialOut", "exponentialInOut",
    "circularIn", "circularOut", "circularInOut",
    "gamma",
]


# ── GUI Classes ──────────────────────────────────────────────────────────────
# BLEBridge is always defined (no tk dependency). ColorSwatch and RingGUI
# use fallback base classes so they can be defined at module level without
# requiring tkinter at import time.

class BLEBridge:
    """Runs bleak operations on a background asyncio loop (for GUI use)."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._client = None

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro):
        """Submit a coroutine to the BLE loop and return a concurrent.futures.Future."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    @property
    def connected(self):
        return self._client is not None and self._client.is_connected

    async def scan(self, timeout=SCAN_TIMEOUT):
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
        rings = []
        for d, adv in discovered.values():
            if SERVICE_UUID.lower() not in [u.lower() for u in (adv.service_uuids or [])]:
                continue
            rssi = adv.rssi if hasattr(adv, "rssi") else None
            ring_id = None
            try:
                async with BleakClient(d, timeout=5) as client:
                    raw = await client.read_gatt_char(CHAR_ID_UUID)
                    ring_id = raw.decode("utf-8")
            except Exception:
                pass
            ring_id = ring_id or "(unknown)"
            rings.append((ring_id, d.address, rssi, d))
        return sorted(rings, key=lambda x: x[0])

    async def connect(self, device):
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = BleakClient(device)
        await self._client.connect()

    async def disconnect(self):
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    async def send_json(self, payload_str):
        if not self._client or not self._client.is_connected:
            raise RuntimeError("Not connected")
        data = payload_str.encode("utf-8")
        for i in range(0, len(data), CHUNK_SIZE):
            await self._client.write_gatt_char(
                CHAR_CTRL_UUID, data[i:i + CHUNK_SIZE], response=True
            )

    async def set_id(self, new_id):
        if not self._client or not self._client.is_connected:
            raise RuntimeError("Not connected")
        await self._client.write_gatt_char(
            CHAR_ID_UUID, new_id.encode("utf-8"), response=True
        )


# Fallback base classes: allows GUI classes to be defined at module level
# even when tkinter is not installed. The classes simply can't be instantiated.
if HAS_TK:
    _TkBase = tk.Tk
    _FrameBase = tk.Frame
else:
    _TkBase = object
    _FrameBase = object


class ColorSwatch(_FrameBase):
    """A clickable color swatch with RGB display and color picker."""

    def __init__(self, parent, label_text, initial=(255, 255, 255), on_change=None, **kw):
        super().__init__(parent, **kw)
        self._color = list(initial)
        self._on_change = on_change
        self._updating = False  # guard against trace re-entrancy

        lbl = ttk.Label(self, text=label_text, width=8)
        lbl.pack(side=tk.LEFT, padx=(0, 4))

        self._swatch = tk.Canvas(self, width=28, height=28, cursor="hand2",
                                 highlightthickness=1, highlightbackground="#999")
        self._swatch.pack(side=tk.LEFT, padx=(0, 6))
        self._swatch.bind("<Button-1>", self._pick_color)

        # RGB spin boxes
        self._vars = []
        for i, ch in enumerate("RGB"):
            var = tk.IntVar(value=self._color[i])
            var.trace_add("write", self._on_spin_change)
            self._vars.append(var)
            ttk.Label(self, text=ch, width=1).pack(side=tk.LEFT)
            sb = ttk.Spinbox(self, from_=0, to=255, width=4, textvariable=var)
            sb.pack(side=tk.LEFT, padx=(0, 4))

        self._update_swatch()

    def _hex_color(self):
        r, g, b = self._color
        return f"#{r:02x}{g:02x}{b:02x}"

    def _update_swatch(self):
        self._swatch.configure(bg=self._hex_color())

    def _pick_color(self, _event=None):
        # On macOS, a grayscale initial color (R==G==B) causes the native
        # color panel to open in grayscale-only mode.  Skip initialcolor
        # for grayscale values so the panel keeps its last-used mode.
        r, g, b = self._color
        kw = {"title": "Choose color"}
        if not (r == g == b):
            kw["color"] = self._hex_color()
        result = colorchooser.askcolor(**kw)
        if result and result[0]:
            rgb = tuple(int(c) for c in result[0])
            self.set_color(rgb)

    def _on_spin_change(self, *_args):
        if self._updating:
            return
        try:
            new = [max(0, min(255, v.get())) for v in self._vars]
        except tk.TclError:
            return
        if new != self._color:
            self._color = new
            self._update_swatch()
            if self._on_change:
                self._on_change()

    def get_color(self):
        return list(self._color)

    def set_color(self, rgb):
        self._updating = True
        self._color = list(rgb)
        for i, v in enumerate(self._vars):
            v.set(self._color[i])
        self._updating = False
        self._update_swatch()
        if self._on_change:
            self._on_change()


class RingGUI(_TkBase):
    def __init__(self):
        super().__init__()
        self.title("RingBeacon")
        self.geometry("900x720")
        self.minsize(800, 600)

        self._ble = BLEBridge() if HAS_BLEAK else None
        self._connected_name = None
        self._connected_addr = None
        self._param_widgets = {}  # json_key -> variable

        self._build_ui()
        self._select_command("off")
        self._update_json()

        # Auto-scan on startup
        if self._ble:
            self.after(100, self._on_scan)

    # ── UI Construction ──────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar: connection
        self._build_connection_bar()

        # Vertical paned window: top = controls, bottom = JSON preview
        vpane = ttk.PanedWindow(self, orient=tk.VERTICAL)
        vpane.pack(fill=tk.BOTH, expand=True, padx=6, pady=(4, 6))

        # Top pane: sidebar + right panel
        main = ttk.PanedWindow(vpane, orient=tk.HORIZONTAL)
        vpane.add(main, weight=3)

        self._build_sidebar(main)
        self._build_right_panel(main)

        # Bottom pane: JSON preview + send
        self._build_json_panel(vpane)

    def _build_connection_bar(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill=tk.X)

        ttk.Label(bar, text="Ring:").pack(side=tk.LEFT)

        self._ring_combo = ttk.Combobox(bar, state="readonly", width=28)
        self._ring_combo.pack(side=tk.LEFT, padx=4)
        self._ring_combo.set("(not connected)")

        self._scan_btn = ttk.Button(bar, text="Scan", command=self._on_scan)
        self._scan_btn.pack(side=tk.LEFT, padx=2)

        self._connect_btn = ttk.Button(bar, text="Connect", command=self._on_connect)
        self._connect_btn.pack(side=tk.LEFT, padx=2)

        self._disconnect_btn = ttk.Button(bar, text="Disconnect", command=self._on_disconnect,
                                          state=tk.DISABLED)
        self._disconnect_btn.pack(side=tk.LEFT, padx=2)

        self._status_lbl = ttk.Label(bar, text="", foreground="gray")
        self._status_lbl.pack(side=tk.LEFT, padx=8)

        if not HAS_BLEAK:
            self._status_lbl.configure(text="⚠ bleak not installed — BLE disabled",
                                       foreground="red")

        # Storage for scan results
        self._scan_results = []

        # ID bar (second row)
        id_bar = ttk.Frame(self, padding=(6, 0, 6, 4))
        id_bar.pack(fill=tk.X)

        ttk.Label(id_bar, text="Ring ID:").pack(side=tk.LEFT)
        self._id_entry = ttk.Entry(id_bar, width=20)
        self._id_entry.pack(side=tk.LEFT, padx=4)
        self._set_id_btn = ttk.Button(id_bar, text="Set ID", command=self._on_set_id)
        self._set_id_btn.pack(side=tk.LEFT, padx=2)

    def _build_sidebar(self, parent):
        sidebar = ttk.Frame(parent, padding=4)
        parent.add(sidebar, weight=0)

        ttk.Label(sidebar, text="Effects", font=("", 12, "bold")).pack(anchor=tk.W, pady=(0, 6))

        # Scrollable command list
        list_frame = ttk.Frame(sidebar)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self._cmd_buttons = {}
        for cmd in COMMANDS:
            btn = ttk.Button(
                list_frame, text=cmd, width=14,
                command=lambda c=cmd: self._select_command(c),
            )
            btn.pack(fill=tk.X, pady=1)
            self._cmd_buttons[cmd] = btn

    def _build_right_panel(self, parent):
        right = ttk.Frame(parent, padding=4)
        parent.add(right, weight=1)

        # ─ Command description ───────────────────────────────────────────────
        self._desc_lbl = ttk.Label(right, text="", font=("", 10, "italic"),
                                   foreground="#555")
        self._desc_lbl.pack(anchor=tk.W, pady=(0, 6))

        # ─ Scrollable parameters area ────────────────────────────────────────
        param_canvas = tk.Canvas(right, highlightthickness=0)
        param_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=param_canvas.yview)
        self._param_container = ttk.Frame(param_canvas)

        self._param_container.bind(
            "<Configure>",
            lambda e: param_canvas.configure(scrollregion=param_canvas.bbox("all"))
        )
        param_canvas.create_window((0, 0), window=self._param_container, anchor=tk.NW)
        param_canvas.configure(yscrollcommand=param_scroll.set)

        param_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        param_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Build global + command-specific inside _param_container
        self._build_global_params(self._param_container)

        ttk.Separator(self._param_container, orient=tk.HORIZONTAL).pack(
            fill=tk.X, pady=8, padx=4
        )

        # Command-specific frame (rebuilt on command change)
        self._cmd_param_frame = ttk.LabelFrame(self._param_container,
                                                text="Command Parameters", padding=6)
        self._cmd_param_frame.pack(fill=tk.X, padx=4, pady=(0, 8))

    def _build_json_panel(self, parent):
        """JSON preview + send/copy buttons, added to the vertical PanedWindow."""
        bottom = ttk.Frame(parent, padding=(0, 4, 0, 0))
        parent.add(bottom, weight=1)

        header = ttk.Frame(bottom)
        header.pack(fill=tk.X)

        ttk.Label(header, text="JSON Preview:", font=("", 9, "bold")).pack(
            side=tk.LEFT, anchor=tk.W)

        btn_frame = ttk.Frame(header)
        btn_frame.pack(side=tk.RIGHT)

        self._send_btn = ttk.Button(btn_frame, text="Send", command=self._on_send)
        self._send_btn.pack(side=tk.LEFT, padx=2)

        ttk.Button(btn_frame, text="Copy", command=self._on_copy).pack(side=tk.LEFT, padx=2)

        self._json_text = tk.Text(bottom, wrap=tk.WORD,
                                  font=("Menlo", 11), bg="#1e1e1e", fg="#d4d4d4",
                                  insertbackground="#d4d4d4", padx=6, pady=4)
        self._json_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    def _build_global_params(self, parent):
        gf = ttk.LabelFrame(parent, text="Global Parameters", padding=6)
        gf.pack(fill=tk.X, padx=4, pady=(0, 4))

        # Color swatches
        self._color_swatch = ColorSwatch(gf, "Color", initial=(255, 255, 255),
                                         on_change=self._update_json)
        self._color_swatch.pack(fill=tk.X, pady=2)

        self._color2_swatch = ColorSwatch(gf, "Color 2", initial=(0, 0, 0),
                                          on_change=self._update_json)
        self._color2_swatch.pack(fill=tk.X, pady=2)

        self._color2_hint = ttk.Label(gf, text="", foreground="#888", font=("", 8))
        self._color2_hint.pack(anchor=tk.W, padx=(0, 0), pady=(0, 4))

        # Grid for numeric globals
        grid = ttk.Frame(gf)
        grid.pack(fill=tk.X, pady=4)

        row = 0

        # Speed
        ttk.Label(grid, text="Cycle duration (ms)").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._duration_var = tk.IntVar(value=1000)
        self._duration_var.trace_add("write", lambda *_: self._update_json())
        ttk.Spinbox(grid, from_=1, to=60000, width=8,
                     textvariable=self._duration_var).grid(row=row, column=1, sticky=tk.W, padx=4)
        row += 1

        # Brightness
        ttk.Label(grid, text="Brightness").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._bright_var = tk.IntVar(value=255)
        self._bright_var.trace_add("write", lambda *_: self._update_json())
        bright_frame = ttk.Frame(grid)
        bright_frame.grid(row=row, column=1, sticky=tk.EW, padx=4)
        self._bright_scale = ttk.Scale(bright_frame, from_=0, to=255, variable=self._bright_var,
                                       orient=tk.HORIZONTAL, length=120,
                                       command=lambda _: self._update_json())
        self._bright_scale.pack(side=tk.LEFT)
        self._bright_lbl = ttk.Label(bright_frame, textvariable=self._bright_var, width=4)
        self._bright_lbl.pack(side=tk.LEFT, padx=4)
        row += 1

        # Total timeout
        ttk.Label(grid, text="Total timeout (ms)").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._timeout_var = tk.IntVar(value=0)
        self._timeout_var.trace_add("write", lambda *_: self._update_json())
        ttk.Spinbox(grid, from_=0, to=600000, width=8,
                     textvariable=self._timeout_var).grid(row=row, column=1, sticky=tk.W, padx=4)
        ttk.Label(grid, text="0 = no limit", foreground="#888").grid(
            row=row, column=2, sticky=tk.W)
        row += 1

        # Cycles
        ttk.Label(grid, text="Cycles").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._cycles_var = tk.IntVar(value=0)
        self._cycles_var.trace_add("write", lambda *_: self._update_json())
        ttk.Spinbox(grid, from_=0, to=10000, width=8,
                     textvariable=self._cycles_var).grid(row=row, column=1, sticky=tk.W, padx=4)
        ttk.Label(grid, text="0 = no limit", foreground="#888").grid(
            row=row, column=2, sticky=tk.W)
        row += 1

        # Easing
        ttk.Label(grid, text="Easing").grid(row=row, column=0, sticky=tk.W, pady=2)
        self._easing_var = tk.StringVar(value="linear")
        self._easing_var.trace_add("write", lambda *_: self._update_json())
        ease_combo = ttk.Combobox(grid, textvariable=self._easing_var, values=EASING_CURVES,
                                  state="readonly", width=18)
        ease_combo.grid(row=row, column=1, sticky=tk.W, padx=4, columnspan=2)
        row += 1

        # Reverse
        self._reverse_var = tk.BooleanVar(value=False)
        self._reverse_var.trace_add("write", lambda *_: self._update_json())
        ttk.Checkbutton(grid, text="Reverse", variable=self._reverse_var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W, pady=2)

    # ── Command Selection ────────────────────────────────────────────────────

    def _select_command(self, cmd):
        self._current_cmd = cmd

        # Update button styles
        for name, btn in self._cmd_buttons.items():
            if name == cmd:
                btn.state(["pressed"])
            else:
                btn.state(["!pressed"])

        # Update description
        self._desc_lbl.configure(text=COMMAND_DESCRIPTIONS.get(cmd, ""))

        # Update color2 hint
        if cmd in COLOR2_COMMANDS:
            self._color2_hint.configure(text=f"✓ {cmd} uses color2")
        else:
            self._color2_hint.configure(text="color2 not used by this command")

        # Rebuild command-specific params
        self._rebuild_cmd_params(cmd)
        self._update_json()

    def _rebuild_cmd_params(self, cmd):
        # Clear existing widgets
        for w in self._cmd_param_frame.winfo_children():
            w.destroy()
        self._param_widgets.clear()

        params = COMMAND_PARAMS.get(cmd, [])
        if not params:
            ttk.Label(self._cmd_param_frame, text="No command-specific parameters",
                      foreground="#888").pack(anchor=tk.W)
            return

        for label, key, ptype, default, extra in params:
            row_frame = ttk.Frame(self._cmd_param_frame)
            row_frame.pack(fill=tk.X, pady=2)

            ttk.Label(row_frame, text=label, width=16).pack(side=tk.LEFT)

            if ptype == "int":
                var = tk.IntVar(value=default)
                var.trace_add("write", lambda *_: self._update_json())
                sb = ttk.Spinbox(row_frame, textvariable=var, width=8,
                                 from_=extra.get("from_", 0),
                                 to=extra.get("to", 999))
                sb.pack(side=tk.LEFT, padx=4)
                self._param_widgets[key] = var

            elif ptype == "float":
                var = tk.DoubleVar(value=default)
                var.trace_add("write", lambda *_: self._update_json())
                res = extra.get("resolution", 0.01)
                sb = ttk.Spinbox(row_frame, textvariable=var, width=8,
                                 from_=extra.get("from_", 0.0),
                                 to=extra.get("to", 1.0),
                                 increment=res)
                sb.pack(side=tk.LEFT, padx=4)
                self._param_widgets[key] = var

            elif ptype == "choice":
                var = tk.StringVar(value=default)
                var.trace_add("write", lambda *_: self._update_json())
                cb = ttk.Combobox(row_frame, textvariable=var,
                                  values=extra.get("choices", []),
                                  state="readonly", width=10)
                cb.pack(side=tk.LEFT, padx=4)
                self._param_widgets[key] = var

            elif ptype == "bool":
                var = tk.BooleanVar(value=default)
                var.trace_add("write", lambda *_: self._update_json())
                ttk.Checkbutton(row_frame, variable=var).pack(side=tk.LEFT, padx=4)
                self._param_widgets[key] = var

    # ── JSON Assembly ────────────────────────────────────────────────────────

    def _build_payload(self):
        """Assemble JSON dict from current UI state."""
        payload = {"cmd": self._current_cmd}

        # Color (omit if white default and cmd is off)
        color = self._color_swatch.get_color()
        if color != [255, 255, 255] or self._current_cmd not in ("off",):
            payload["color"] = color

        # Color2 (omit if black/default)
        color2 = self._color2_swatch.get_color()
        if color2 != [0, 0, 0]:
            payload["color2"] = color2

        # Cycle duration (always explicit)
        try:
            payload["duration"] = max(1, self._duration_var.get())
        except tk.TclError:
            pass

        # Brightness (omit if default 255)
        try:
            bright = self._bright_var.get()
            if bright != 255:
                payload["brightness"] = bright
        except tk.TclError:
            pass

        # Total timeout (omit if 0)
        try:
            timeout = self._timeout_var.get()
            if timeout > 0:
                payload["timeout"] = timeout
        except tk.TclError:
            pass

        # Cycles (omit if 0)
        try:
            cyc = self._cycles_var.get()
            if cyc > 0:
                payload["cycles"] = cyc
        except tk.TclError:
            pass

        # Easing (omit if linear)
        easing = self._easing_var.get()
        if easing and easing != "linear":
            payload["easing"] = easing

        # Reverse (omit if false)
        if self._reverse_var.get():
            payload["reverse"] = True

        # Command-specific params (omit if at default)
        cmd_params = COMMAND_PARAMS.get(self._current_cmd, [])
        for _label, key, ptype, default, _extra in cmd_params:
            var = self._param_widgets.get(key)
            if var is None:
                continue
            try:
                val = var.get()
            except tk.TclError:
                continue
            if val != default:
                payload[key] = val

        # Clean up: "off" only needs cmd
        if self._current_cmd == "off":
            payload = {"cmd": "off"}

        return payload

    def _update_json(self, *_args):
        """Refresh the JSON preview text."""
        try:
            payload = self._build_payload()
            text = json.dumps(payload, indent=2)
        except Exception:
            text = '{"cmd":"off"}'

        self._json_text.delete("1.0", tk.END)
        self._json_text.insert("1.0", text)

    # ── BLE Actions ──────────────────────────────────────────────────────────

    def _set_status(self, msg, color="gray"):
        self._status_lbl.configure(text=msg, foreground=color)

    def _on_scan(self):
        if not self._ble:
            messagebox.showwarning("BLE", "bleak is not installed.\npip install bleak")
            return

        self._scan_btn.configure(state=tk.DISABLED)
        self._set_status("Scanning...", "blue")
        self._ring_combo.set("(scanning...)")

        future = self._ble.submit(self._ble.scan(timeout=SCAN_TIMEOUT))

        def _on_done(fut):
            try:
                rings = fut.result()
                self._scan_results = rings
                names = [f"{r[0]}  ({r[2]} dBm)" if r[2] else r[0] for r in rings]
                self._ring_combo.configure(values=names)
                if names:
                    self._ring_combo.current(0)
                    self._set_status(f"{len(rings)} ring(s) found", "green")
                else:
                    self._ring_combo.set("(no rings found)")
                    self._set_status("No rings found", "orange")
            except Exception as e:
                self._set_status(f"Scan error: {e}", "red")
                self._ring_combo.set("(scan failed)")
            finally:
                self._scan_btn.configure(state=tk.NORMAL)

        self.after(100, lambda: self._poll_future(future, _on_done))

    def _on_connect(self):
        if not self._ble:
            return
        idx = self._ring_combo.current()
        if idx < 0 or idx >= len(self._scan_results):
            messagebox.showinfo("Connect", "Scan for rings first, then select one.")
            return

        name, addr, rssi, device = self._scan_results[idx]
        self._connect_btn.configure(state=tk.DISABLED)
        self._set_status(f"Connecting to {name}...", "blue")

        future = self._ble.submit(self._ble.connect(device))

        def _on_done(fut):
            try:
                fut.result()
                self._connected_name = name
                self._connected_addr = addr
                self._set_status(f"Connected: {name}", "green")
                self._disconnect_btn.configure(state=tk.NORMAL)
            except Exception as e:
                self._set_status(f"Connect failed: {e}", "red")
            finally:
                self._connect_btn.configure(state=tk.NORMAL)

        self.after(100, lambda: self._poll_future(future, _on_done))

    def _on_disconnect(self):
        if not self._ble:
            return
        self._disconnect_btn.configure(state=tk.DISABLED)
        self._set_status("Disconnecting...", "blue")

        future = self._ble.submit(self._ble.disconnect())

        def _on_done(fut):
            try:
                fut.result()
            except Exception:
                pass
            self._connected_name = None
            self._connected_addr = None
            self._set_status("Disconnected", "gray")
            self._connect_btn.configure(state=tk.NORMAL)

        self.after(100, lambda: self._poll_future(future, _on_done))

    def _on_set_id(self):
        new_id = self._id_entry.get().strip()
        if not new_id:
            messagebox.showinfo("Set ID", "Enter a ring ID first.")
            return
        if not self._ble:
            return

        # If not connected, auto-connect to selected ring first
        if not self._ble.connected:
            idx = self._ring_combo.current()
            if idx < 0 or idx >= len(self._scan_results):
                messagebox.showinfo("Set ID", "Scan for rings first, then select one.")
                return

            name, addr, rssi, device = self._scan_results[idx]
            self._set_id_btn.configure(state=tk.DISABLED)
            self._set_status(f"Connecting to {name}...", "blue")

            future = self._ble.submit(self._ble.connect(device))

            def _on_connected(fut):
                try:
                    fut.result()
                    self._connected_name = name
                    self._connected_addr = addr
                    self._disconnect_btn.configure(state=tk.NORMAL)
                    self._do_set_id(new_id)
                except Exception as e:
                    self._set_status(f"Connect failed: {e}", "red")
                    self._set_id_btn.configure(state=tk.NORMAL)

            self.after(100, lambda: self._poll_future(future, _on_connected))
            return

        self._do_set_id(new_id)

    def _do_set_id(self, new_id):
        """Write new ID to ring, disconnect, and re-scan."""
        self._set_id_btn.configure(state=tk.DISABLED)
        self._set_status(f"Setting ID to '{new_id}'...", "blue")

        future = self._ble.submit(self._ble.set_id(new_id))

        def _on_done(fut):
            try:
                fut.result()
                self._set_status(f"ID set to '{new_id}' — rescanning...", "green")
                self._id_entry.delete(0, tk.END)
                # Disconnect and re-scan to pick up the new name
                self._connected_name = None
                self._connected_addr = None
                disc_future = self._ble.submit(self._ble.disconnect())
                self.after(100, lambda: self._poll_future(disc_future,
                           lambda _f: self._after_set_id_disconnect()))
            except Exception as e:
                self._set_status(f"Set ID failed: {e}", "red")
            finally:
                self._set_id_btn.configure(state=tk.NORMAL)

        self.after(100, lambda: self._poll_future(future, _on_done))

    def _after_set_id_disconnect(self):
        """Post set-id: update button states and trigger a re-scan."""
        self._disconnect_btn.configure(state=tk.DISABLED)
        self._connect_btn.configure(state=tk.NORMAL)
        self._on_scan()

    def _on_send(self):
        payload = self._build_payload()
        json_str = json.dumps(payload, separators=(",", ":"))

        if not self._ble:
            self._set_status("BLE not available", "red")
            return

        idx = self._ring_combo.current()
        if idx < 0 or idx >= len(self._scan_results):
            messagebox.showinfo("Send", "Scan for rings first, then select one.")
            return

        name, addr, rssi, device = self._scan_results[idx]

        # Already connected to the SAME ring — just send
        if self._ble.connected and self._connected_addr == addr:
            self._do_send(json_str)
            return

        # Connected to a DIFFERENT ring — disconnect first, then reconnect
        if self._ble.connected and self._connected_addr != addr:
            self._send_btn.configure(state=tk.DISABLED)
            self._set_status(f"Switching to {name}...", "blue")

            disc_future = self._ble.submit(self._ble.disconnect())

            def _after_disc(fut):
                try:
                    fut.result()
                except Exception:
                    pass
                self._connected_name = None
                self._connected_addr = None
                self._disconnect_btn.configure(state=tk.DISABLED)
                self._connect_and_send(name, addr, device, json_str)

            self.after(100, lambda: self._poll_future(disc_future, _after_disc))
            return

        # Not connected — connect to selected ring, then send
        self._connect_and_send(name, addr, device, json_str)

    def _connect_and_send(self, name, addr, device, json_str):
        """Connect to a ring and send a command after connection succeeds."""
        self._send_btn.configure(state=tk.DISABLED)
        self._connect_btn.configure(state=tk.DISABLED)
        self._set_status(f"Connecting to {name}...", "blue")

        future = self._ble.submit(self._ble.connect(device))

        def _on_connected(fut):
            try:
                fut.result()
                self._connected_name = name
                self._connected_addr = addr
                self._disconnect_btn.configure(state=tk.NORMAL)
                self._do_send(json_str)
            except Exception as e:
                self._set_status(f"Connect failed: {e}", "red")
                self._send_btn.configure(state=tk.NORMAL)
            finally:
                self._connect_btn.configure(state=tk.NORMAL)

        self.after(100, lambda: self._poll_future(future, _on_connected))

    def _do_send(self, json_str):
        """Send a JSON string over BLE (assumes already connected)."""
        self._send_btn.configure(state=tk.DISABLED)
        self._set_status("Sending...", "blue")

        future = self._ble.submit(self._ble.send_json(json_str))

        def _on_done(fut):
            try:
                fut.result()
                self._set_status(f"Sent to {self._connected_name}", "green")
            except Exception as e:
                self._set_status(f"Send error: {e}", "red")
            finally:
                self._send_btn.configure(state=tk.NORMAL)

        self.after(100, lambda: self._poll_future(future, _on_done))

    def _on_copy(self):
        json_str = self._json_text.get("1.0", tk.END).strip()
        self.clipboard_clear()
        self.clipboard_append(json_str)
        self._set_status("Copied to clipboard", "green")

    # ── Async Polling ────────────────────────────────────────────────────────

    def _poll_future(self, future, callback, interval=100):
        """Poll a concurrent.futures.Future from the tkinter main loop."""
        if future.done():
            callback(future)
        else:
            self.after(interval, lambda: self._poll_future(future, callback, interval))


# ── Entry Point ──────────────────────────────────────────────────────────────

def run_gui():
    """Launch the tkinter GUI."""
    if not HAS_TK:
        print("Error: tkinter is required for --gui mode.", file=sys.stderr)
        print("Install it with your system package manager.", file=sys.stderr)
        sys.exit(1)
    app = RingGUI()
    app.mainloop()


def main():
    parser = argparse.ArgumentParser(
        description="RingBeacon LED Ring Controller",
        epilog="Examples:\n"
               "  %(prog)s --gui                         # launch GUI\n"
               "  %(prog)s '{\"cmd\":\"blink\",\"color\":[255,0,0]}'\n"
               "  %(prog)s --id ring-ab12 '{\"cmd\":\"rotate\"}'\n"
               "  %(prog)s --file animation.json\n"
               "  %(prog)s --scan\n"
               "  %(prog)s                               # interactive mode\n"
               "  %(prog)s --id ring-ab12 --set-id stage-left\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("json_payload", nargs="?", help="JSON command string")
    parser.add_argument("--gui", action="store_true", help="Launch the GUI instead of CLI")
    parser.add_argument("--id", help="Target ring by ID (e.g. ring-ab12)")
    parser.add_argument("--file", "-f", help="Read JSON command from file")
    parser.add_argument("--scan", action="store_true", help="Scan and list all rings")
    parser.add_argument("--set-id", metavar="NEW_ID",
                        help="Set the ring's persistent ID (requires --id)")
    parser.add_argument("--read-id", action="store_true",
                        help="Read the ring's current ID")
    parser.add_argument("--timeout", type=float, default=SCAN_TIMEOUT,
                        help=f"BLE scan timeout in seconds (default: {SCAN_TIMEOUT})")
    args = parser.parse_args()

    # GUI mode
    if args.gui:
        run_gui()
        return

    # CLI mode requires bleak
    if not HAS_BLEAK:
        print("Error: bleak is required. Install with: pip install bleak", file=sys.stderr)
        sys.exit(1)

    # --set-id requires --id so you don't accidentally rename the wrong ring
    if args.set_id and not args.id:
        parser.error("--set-id requires --id to identify which ring to rename.\n"
                     "  Workflow: --scan to discover rings, then "
                     "--id <current-id> --set-id <new-id>")

    try:
        asyncio.run(cli_main(args))
    except KeyboardInterrupt:
        print("\nAborted.")


if __name__ == "__main__":
    main()
