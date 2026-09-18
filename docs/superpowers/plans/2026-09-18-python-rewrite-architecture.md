# BYTT Python Rewrite — Architecture, GUI Framework & Networking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monolithic `interactive_bsf_viewer.py` (3664 lines, pygame) into a proper
`bytt/` package, replace the GUI layer with PySide6 + pyqtgraph, and add towfish network
integration (live client, command client, playback source) — producing a runnable skeleton app
that can connect to a towfish (or replay a `.bsf` file) and render a live waterfall.

**Architecture:** Pure-logic code (protocol parsing, enhancement, colormaps, GPS math, `.bsf` file
I/O) is moved verbatim into small single-responsibility modules with unit tests. Networking code
is ported from callback-based `threading` classes to `QObject`-based classes that emit Qt signals.
A new `CommandClient` and `PlaybackSource` are added following the same shape as the existing
`LiveClient`. The GUI layer is new: a `QMainWindow` skeleton wired to whichever ping source
(live or playback) is active via one shared interface.

**Tech Stack:** Python 3.11+, PySide6, pyqtgraph, numpy, opencv-python, pytest.

**Spec:** `docs/superpowers/specs/2026-09-18-python-rewrite-architecture-design.md`

## Global Constraints

- Cross-platform: must run on Windows, Linux, and macOS — no OS-specific code paths in anything
  built by this plan (existing Windows-only helpers in `interactive_bsf_viewer.py`, e.g.
  `_style_windows_titlebar`, are not ported).
- GUI framework is PySide6 + pyqtgraph. `pygame` is not a dependency of the new package.
- No SatCenter process management (no `taskkill`/`QProcess`/config-file rewriting) — connecting is
  always "open a socket to `TowfishIP`".
- Config file fields are exactly: `TowfishIP`, `CmdPort`, `DataPort`, `Source` (`playback` |
  `towfish`) — no `[SonarDevice]`/`[HydroSonar]`/`[UserPara]` sections.
- Docking panels, automatic reconnect, recording, and control-panel UI are explicitly out of scope
  for this plan (see spec's "Explicitly out of scope" section).
- Networking facts (ports, packet layouts) are the current best-known baseline from
  `BYTT docs/vendor/hytem-data-protocol-v1.0.9.md` and are provisional pending the user's follow-up
  with the implementing team — if that changes, `bytt/protocol/` and `bytt/net/` are the modules to
  revisit, not this plan.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `bytt/__init__.py`
- Create: `bytt/protocol/__init__.py`, `bytt/net/__init__.py`, `bytt/processing/__init__.py`,
  `bytt/nav/__init__.py`, `bytt/bsf/__init__.py`, `bytt/gui/__init__.py`
- Create: `tests/__init__.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: an installable `bytt` package (`pip install -e .`) and a `pytest` test runner other
  tasks add tests under `tests/`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "bytt"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "PySide6>=6.6",
    "pyqtgraph>=0.13",
    "numpy>=1.26",
    "opencv-python>=4.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["bytt*"]
```

- [ ] **Step 2: Create empty package `__init__.py` files**

```bash
mkdir -p bytt/protocol bytt/net bytt/processing bytt/nav bytt/bsf bytt/gui tests
touch bytt/__init__.py bytt/protocol/__init__.py bytt/net/__init__.py \
      bytt/processing/__init__.py bytt/nav/__init__.py bytt/bsf/__init__.py \
      bytt/gui/__init__.py tests/__init__.py
```

- [ ] **Step 3: Update `.gitignore` to exclude the dev venv and stray recordings**

Read the current `.gitignore` first, then add these lines if not already present:

```
venve/
*.bsf
!tests/fixtures/*.bsf
```

- [ ] **Step 4: Install in editable/dev mode and verify pytest runs**

Run: `pip install -e ".[dev]" && pytest --collect-only`
Expected: exits 0, reports "no tests ran" (no test files exist yet).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml bytt tests .gitignore
git commit -m "chore: scaffold bytt package structure"
```

---

## Task 2: `bytt/protocol/constants.py`

**Files:**
- Create: `bytt/protocol/constants.py`
- Test: `tests/protocol/test_constants.py`

**Interfaces:**
- Produces: all named constants below, importable as `from bytt.protocol import constants as pc`.
  Later tasks (3, 4, 5, 8, 10, 11, 13) import from this module — do not redefine any of these
  values elsewhere.

- [ ] **Step 1: Write the failing test**

```python
# tests/protocol/test_constants.py
from bytt.protocol import constants as pc

def test_packet_header_layout():
    assert pc.PACKET_IDENTIFIER == 0x004D5448
    assert pc.PACKET_HEADER_SIZE == 24
    assert pc.PACKET_TYPE_OFFSET == 20
    assert pc.PACKET_SIZE_OFFSET == 8
    assert pc.PACKET_TYPE_3101 == 3101
    assert pc.PACKET_TYPE_166 == 166
    assert pc.PACKET_TYPE_107 == 107

def test_3101_body_offsets():
    assert pc.B3101_BODY_SIZE == 104
    assert pc.B3101_DATA_OFF == pc.PACKET_HEADER_SIZE + pc.B3101_BODY_SIZE

def test_bsf_file_constants():
    assert pc.BSF_MAGIC == b'\x0fHSFV1.1.000'
    assert pc.BSF_FILE_HDR_SZ == 0x400
    assert pc.SAMPLE_OFFSET == 216
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mkdir -p tests/protocol && touch tests/protocol/__init__.py && pytest tests/protocol/test_constants.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.protocol.constants'`

- [ ] **Step 3: Create the module by moving constants out of `interactive_bsf_viewer.py`**

Copy these exact definitions from `interactive_bsf_viewer.py` (lines given for reference — do not
change any value):

```python
"""Hytem wire-protocol and .bsf file-format constants."""
import struct

# ── Wire packet header (lines 150-163 of interactive_bsf_viewer.py) ───────────
PACKET_IDENTIFIER        = 0x004D5448
PACKET_IDENTIFIER_BYTES  = struct.pack('<I', PACKET_IDENTIFIER)
PACKET_HEADER_SIZE       = 24
PACKET_TYPE_OFFSET       = 20
PACKET_SIZE_OFFSET       = 8
PACKET_TYPE_3101         = 3101
PACKET_TYPE_166          = 166
PACKET_TYPE_107          = 107  # NEW — not in the original file, needed for CommandClient (Task 10)
PACKET_FLAG_OFFSET       = 12
PACKET_FRAME_ID_OFFSET   = 14
PACKET_TOTAL_OFFSET      = 16
PACKET_NUMBER_OFFSET     = 18
PACKET_FLAG_MULTI_BIT    = 0x0001
PACKET_FLAG_CHECKSUM_BIT = 0x0002
PACKET_CHECKSUM_NONE     = 0x77EEEE77
PACKET_VERSION           = 0x0109  # V1.0.9, per BYTT docs/vendor/hytem-data-protocol-v1.0.9.md

MULTI_PACKET_FRAME_TIMEOUT_S = 2.0

# ── 3101 side-scan body offsets, relative to body_and_data (lines 165-174) ────
B3101_SYNTIME_OFF    = 16
B3101_PINGNUM_OFF    = 28
B3101_SONARRANGE_OFF = 32
B3101_CENTERFREQ_OFF = 52
B3101_SPREADING_OFF  = 60
B3101_ABSORPTION_OFF = 64
B3101_SAMPLERATE_OFF = 72
B3101_SAMPLELEN_OFF  = 76
B3101_BODY_SIZE      = 104
B3101_DATA_OFF       = PACKET_HEADER_SIZE + B3101_BODY_SIZE

# ── .bsf file format (lines 108-118, 140-145) ──────────────────────────────────
BSF_MAGIC        = b'\x0fHSFV1.1.000'
BSF_FILE_HDR_SZ  = 0x400
BSF_RECORD_TYPE  = 0x00101702
SAMPLE_OFFSET    = 216
CH_SR_OFFSET     = 84
CH_SR_SIZE       = 56

NAV_RECORD_SIZE = 156
NAV_TS_OFFSET   = 64
NAV_LAT_OFFSET  = 80
NAV_LON_OFFSET  = 88
NAV_ALT_OFFSET  = 96
NAV_FIELD_FMT   = '<d'

# ── Amplitude normalization defaults (lines 136-137) ───────────────────────────
AMP_NORM_P_LO_DEFAULT = 12.4
AMP_NORM_P_HI_DEFAULT = 18.9
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/protocol/test_constants.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/protocol/constants.py tests/protocol/
git commit -m "feat: add protocol constants module"
```

---

## Task 3: `bytt/protocol/packets.py` — parsing and checksum

**Files:**
- Create: `bytt/protocol/packets.py`
- Test: `tests/protocol/test_packets.py`

**Interfaces:**
- Consumes: `bytt.protocol.constants` (Task 2).
- Produces: `checksum_ok(packet: bytes, packet_size: int) -> bool`,
  `compute_checksum(data: bytes) -> int`, `parse_channel(ping: bytes, ch_idx: int) -> dict`,
  `get_ping_meta(ping: bytes) -> dict`, `extract_nav_fix(nav_bytes: bytes) -> tuple|None`,
  `extract_raw_channels(ping, p_lo=..., p_hi=...) -> (np.ndarray, np.ndarray)`,
  `calibrate_amplitude_range(data, ping_index, n_sample=400) -> (float, float)`,
  `parse_3101_body(header_bytes, body_and_data) -> (port, stbd, meta)|None`,
  `parse_3101_packet(packet: bytes) -> (port, stbd, meta)|None`. Task 4 (commands), Task 9
  (live_client), Task 11 (playback_source), and Task 8 (bsf/file_io) all import from here.

- [ ] **Step 1: Write the failing tests**

```python
# tests/protocol/test_packets.py
import struct
import numpy as np
from bytt.protocol import constants as pc
from bytt.protocol import packets

def _make_3101_body(n_samples=4, ping_number=7, sonar_range_cm=5000,
                     center_freq_hz=400_000, sample_rate=96000.0):
    body = bytearray(pc.B3101_BODY_SIZE + 2 * n_samples * 2)
    struct.pack_into('<H', body, pc.B3101_SYNTIME_OFF, 2026)       # year
    body[pc.B3101_SYNTIME_OFF + 2] = 9                              # month
    body[pc.B3101_SYNTIME_OFF + 3] = 18                             # day
    body[pc.B3101_SYNTIME_OFF + 4] = 12                             # hour
    body[pc.B3101_SYNTIME_OFF + 5] = 30                             # minute
    body[pc.B3101_SYNTIME_OFF + 6] = 0                              # second
    struct.pack_into('<I', body, pc.B3101_PINGNUM_OFF, ping_number)
    struct.pack_into('<I', body, pc.B3101_SONARRANGE_OFF, sonar_range_cm)
    struct.pack_into('<I', body, pc.B3101_CENTERFREQ_OFF, center_freq_hz)
    struct.pack_into('<f', body, pc.B3101_SAMPLERATE_OFF, sample_rate)
    struct.pack_into('<I', body, pc.B3101_SAMPLELEN_OFF, n_samples)
    port_vals = np.arange(n_samples, dtype='<u2') * 100
    stbd_vals = np.arange(n_samples, dtype='<u2') * 200
    struct.pack_into(f'<{n_samples}H', body, pc.B3101_BODY_SIZE, *port_vals.tolist())
    struct.pack_into(f'<{n_samples}H', body, pc.B3101_BODY_SIZE + n_samples * 2, *stbd_vals.tolist())
    return bytes(body)

def test_parse_3101_body_round_trip():
    body = _make_3101_body(n_samples=4)
    result = packets.parse_3101_body(None, body)
    assert result is not None
    port, stbd, meta = result
    assert meta['ping_number'] == 7
    assert meta['max_range_m'] == 50.0
    assert meta['freq_kHz'] == 400.0
    assert meta['n_samples'] == 4
    assert port.shape == (4,)
    assert stbd.shape == (4,)

def test_parse_3101_body_rejects_short_input():
    assert packets.parse_3101_body(None, b'\x00' * 10) is None

def test_compute_checksum_and_checksum_ok_round_trip():
    header = struct.pack('<IHHIHHHHI', pc.PACKET_IDENTIFIER, pc.PACKET_HEADER_SIZE,
                          pc.PACKET_VERSION, 0, pc.PACKET_FLAG_CHECKSUM_BIT, 0, 1, 1,
                          pc.PACKET_TYPE_166)
    content = b'\x01'  # sonarState = data+cmd connected
    packet_size = len(header) + len(content) + 4
    header = header[:pc.PACKET_SIZE_OFFSET] + struct.pack('<I', packet_size) + \
             header[pc.PACKET_SIZE_OFFSET + 4:]
    body_no_trailer = header + content
    trailer = packets.compute_checksum(body_no_trailer)
    packet = body_no_trailer + struct.pack('<I', trailer)
    assert packets.checksum_ok(packet, packet_size) is True
    corrupted = packet[:-1] + bytes([packet[-1] ^ 0xFF])
    assert packets.checksum_ok(corrupted, packet_size) is False

def test_extract_nav_fix_rejects_out_of_range():
    nav = bytearray(pc.NAV_LON_OFFSET + 8)
    struct.pack_into(pc.NAV_FIELD_FMT, nav, pc.NAV_LAT_OFFSET, 999.0)
    struct.pack_into(pc.NAV_FIELD_FMT, nav, pc.NAV_LON_OFFSET, 10.0)
    assert packets.extract_nav_fix(bytes(nav)) is None

def test_extract_nav_fix_accepts_valid_fix():
    nav = bytearray(pc.NAV_LON_OFFSET + 8)
    struct.pack_into(pc.NAV_FIELD_FMT, nav, pc.NAV_LAT_OFFSET, 41.3)
    struct.pack_into(pc.NAV_FIELD_FMT, nav, pc.NAV_LON_OFFSET, 36.3)
    result = packets.extract_nav_fix(bytes(nav))
    assert result == (41.3, 36.3, 0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/protocol/test_packets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.protocol.packets'`

- [ ] **Step 3: Create the module**

Move these functions from `interactive_bsf_viewer.py` **unchanged** except: (a) update imports to
`from bytt.protocol import constants as pc` and prefix constant references with `pc.`, (b) rename
`_checksum_ok` to `checksum_ok` (drop the leading underscore — it's now a public module function),
(c) add the new `compute_checksum` helper factored out of the checksum math:

- `parse_channel` (source lines 470-476)
- `get_ping_meta` (478-488)
- `extract_nav_fix` (490-505)
- `extract_raw_channels` (507-529)
- `calibrate_amplitude_range` (532-572)
- `checksum_ok`, was `_checksum_ok` (576-587)
- `parse_3101_body` (589-625)
- `parse_3101_packet` (627-630)

Add `compute_checksum` (new — extracted from the sum expression already inside `_checksum_ok`,
reused by Task 4 to build outgoing command packets):

```python
def compute_checksum(data: bytes) -> int:
    """Byte-sum checksum per hytem-data-protocol-v1.0.9.md section 3.2: the
    sum of every byte from packetIdentifier through the end of content,
    truncated to 32 bits."""
    return sum(data) & 0xFFFFFFFF
```

Then rewrite `checksum_ok` to call it:

```python
def checksum_ok(packet: bytes, packet_size: int) -> bool:
    if packet_size < pc.PACKET_HEADER_SIZE + 4:
        return False
    try:
        flag = struct.unpack_from('<H', packet, pc.PACKET_FLAG_OFFSET)[0]
    except struct.error:
        return False
    if not (flag & pc.PACKET_FLAG_CHECKSUM_BIT):
        return True
    trailer = struct.unpack_from('<I', packet, packet_size - 4)[0]
    return compute_checksum(packet[:packet_size - 4]) == trailer
```

Start the file with:

```python
"""Pure parsing/validation for Hytem wire packets and .bsf channel data.
No sockets, no Qt — safe to unit test directly."""
import struct
import numpy as np
from bytt.protocol import constants as pc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/protocol/test_packets.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/protocol/packets.py tests/protocol/test_packets.py
git commit -m "feat: port Hytem packet parsing into bytt.protocol.packets"
```

---

## Task 4: `bytt/protocol/commands.py` — type-107 command packet builder

**Files:**
- Create: `bytt/protocol/commands.py`
- Test: `tests/protocol/test_commands.py`

**Interfaces:**
- Consumes: `bytt.protocol.constants` (Task 2), `packets.compute_checksum` (Task 3).
- Produces: `SonarWorkCommand` dataclass, `build_command_107(cmd: SonarWorkCommand,
  identification: int = 0) -> bytes` (always returns exactly 256 bytes). Task 10
  (`net/command_client.py`) imports both.

This encodes Data Type Code 107 ("SS-Series Side-Scan Sonar Work Control Command, 256 Bytes")
from `BYTT docs/vendor/hytem-data-protocol-v1.0.9.md` section 4.1. Field order, sizes, and
defaults below are taken directly from that document.

- [ ] **Step 1: Write the failing test**

```python
# tests/protocol/test_commands.py
import struct
from bytt.protocol import constants as pc
from bytt.protocol import commands
from bytt.protocol import packets

def test_build_command_107_is_256_bytes_with_valid_header():
    cmd = commands.SonarWorkCommand()
    packet = commands.build_command_107(cmd, identification=42)
    assert len(packet) == 256
    magic, header_size, version, size, flag, ident, total, num, ptype = \
        struct.unpack_from('<IHHIHHHHI', packet, 0)
    assert magic == pc.PACKET_IDENTIFIER
    assert header_size == pc.PACKET_HEADER_SIZE
    assert size == 256
    assert flag & pc.PACKET_FLAG_CHECKSUM_BIT
    assert ident == 42
    assert total == 1
    assert num == 1
    assert ptype == pc.PACKET_TYPE_107

def test_build_command_107_checksum_is_valid():
    cmd = commands.SonarWorkCommand()
    packet = commands.build_command_107(cmd)
    assert packets.checksum_ok(packet, len(packet)) is True

def test_build_command_107_encodes_sonar_run_flag():
    stopped = commands.build_command_107(commands.SonarWorkCommand(sonar_run=0))
    running = commands.build_command_107(commands.SonarWorkCommand(sonar_run=1))
    # sonar_run is the u32 right before the 104-byte reserved3 tail (content
    # ends at header(24) + 228 = 252, sonar_run is the 4 bytes before that)
    off = pc.PACKET_HEADER_SIZE + 228 - 104 - 4
    assert struct.unpack_from('<I', stopped, off)[0] == 0
    assert struct.unpack_from('<I', running, off)[0] == 1

def test_build_command_107_round_trips_gain_fields():
    cmd = commands.SonarWorkCommand(gain_hf=15, gain_lf=22)
    packet = commands.build_command_107(cmd)
    parsed = commands.parse_command_107(packet)
    assert parsed.gain_hf == 15
    assert parsed.gain_lf == 22
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/protocol/test_commands.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.protocol.commands'`

- [ ] **Step 3: Implement the module**

```python
"""Builds Data Type Code 107 (SS-series side-scan sonar work control
command) packets, per BYTT docs/vendor/hytem-data-protocol-v1.0.9.md
section 4.1. 256 bytes total: 24-byte header + 228-byte content + 4-byte
checksum trailer."""
import struct
from dataclasses import dataclass
from bytt.protocol import constants as pc
from bytt.protocol.packets import compute_checksum

_HEADER_FMT = '<IHHIHHHHI'
_CONTENT_FMT = (
    '<'
    'IIIII' 'f' 'I' 'f' 'I' 'f' 'I' 'f' 'I' 'f'   # deviceType..towfshHead
    'III' 'HH' 'I' 'H' '2s' 'ff'                    # HF block
    'III' 'HH' 'I' 'H' '2s' 'ff'                    # LF block
    'I' '104s'                                       # sonarRun, reserved3
)
_CONTENT_SIZE = struct.calcsize(_CONTENT_FMT)
assert _CONTENT_SIZE == 228, _CONTENT_SIZE
_PACKET_SIZE = pc.PACKET_HEADER_SIZE + _CONTENT_SIZE + 4
assert _PACKET_SIZE == 256, _PACKET_SIZE


@dataclass
class SonarWorkCommand:
    """Defaults are the vendor-documented defaults from section 4.1."""
    device_type: int = 20            # SS3060
    work_mode: int = 0                # side-scan mode
    sync_mode: int = 0                # internal sync
    sync_polarity: int = 0            # positive pulse
    savs: int = 0                     # fixed value set by main control software
    sound_speed: float = 1500.0       # m/s
    ship_speed_source: int = 0        # manual
    ship_speed: float = 5.0           # knots
    towfish_depth_source: int = 1     # sonar
    towfish_depth: float = 0.0        # m
    towfish_height_source: int = 1    # sonar
    towfish_height: float = 0.0       # m
    towfish_head_source: int = 1      # sonar
    towfish_head: float = 0.0         # degrees
    ch_en_hf: int = 1
    sonar_range_hf: int = 50          # m
    signal_types_hf: int = 0          # CW
    pulse_width_hf: int = 100         # us
    pulse_source_level_hf: int = 220  # dB
    center_freq_hf: int = 600         # kHz
    gain_hf: int = 10                 # dB
    spreading_hf: float = 20.0        # dB
    absorption_hf: float = 60.0       # dB/km
    ch_en_lf: int = 1
    sonar_range_lf: int = 50          # m
    signal_types_lf: int = 0          # CW
    pulse_width_lf: int = 100         # us
    pulse_source_level_lf: int = 220  # dB
    center_freq_lf: int = 300         # kHz
    gain_lf: int = 10                 # dB
    spreading_lf: float = 20.0        # dB
    absorption_lf: float = 30.0       # dB/km
    sonar_run: int = 0                # 0: stopped, 1: running


def build_command_107(cmd: SonarWorkCommand, identification: int = 0) -> bytes:
    header = struct.pack(
        _HEADER_FMT,
        pc.PACKET_IDENTIFIER, pc.PACKET_HEADER_SIZE, pc.PACKET_VERSION,
        _PACKET_SIZE, pc.PACKET_FLAG_CHECKSUM_BIT, identification, 1, 1,
        pc.PACKET_TYPE_107,
    )
    content = struct.pack(
        _CONTENT_FMT,
        cmd.device_type, cmd.work_mode, cmd.sync_mode, cmd.sync_polarity, cmd.savs,
        cmd.sound_speed, cmd.ship_speed_source, cmd.ship_speed,
        cmd.towfish_depth_source, cmd.towfish_depth,
        cmd.towfish_height_source, cmd.towfish_height,
        cmd.towfish_head_source, cmd.towfish_head,
        cmd.ch_en_hf, cmd.sonar_range_hf, cmd.signal_types_hf,
        cmd.pulse_width_hf, cmd.pulse_source_level_hf, cmd.center_freq_hf,
        cmd.gain_hf, b'\x00\x00', cmd.spreading_hf, cmd.absorption_hf,
        cmd.ch_en_lf, cmd.sonar_range_lf, cmd.signal_types_lf,
        cmd.pulse_width_lf, cmd.pulse_source_level_lf, cmd.center_freq_lf,
        cmd.gain_lf, b'\x00\x00', cmd.spreading_lf, cmd.absorption_lf,
        cmd.sonar_run, b'\x00' * 104,
    )
    body = header + content
    trailer = struct.pack('<I', compute_checksum(body))
    return body + trailer


def parse_command_107(packet: bytes) -> SonarWorkCommand:
    """Inverse of build_command_107 — used by tests and by anything that
    needs to inspect a command packet it received (e.g. a hardware simulator)."""
    content = packet[pc.PACKET_HEADER_SIZE:pc.PACKET_HEADER_SIZE + _CONTENT_SIZE]
    fields = struct.unpack(_CONTENT_FMT, content)
    (device_type, work_mode, sync_mode, sync_polarity, savs, sound_speed,
     ship_speed_source, ship_speed, towfish_depth_source, towfish_depth,
     towfish_height_source, towfish_height, towfish_head_source, towfish_head,
     ch_en_hf, sonar_range_hf, signal_types_hf, pulse_width_hf,
     pulse_source_level_hf, center_freq_hf, gain_hf, _reserved1,
     spreading_hf, absorption_hf, ch_en_lf, sonar_range_lf, signal_types_lf,
     pulse_width_lf, pulse_source_level_lf, center_freq_lf, gain_lf,
     _reserved2, spreading_lf, absorption_lf, sonar_run, _reserved3) = fields
    return SonarWorkCommand(
        device_type=device_type, work_mode=work_mode, sync_mode=sync_mode,
        sync_polarity=sync_polarity, savs=savs, sound_speed=sound_speed,
        ship_speed_source=ship_speed_source, ship_speed=ship_speed,
        towfish_depth_source=towfish_depth_source, towfish_depth=towfish_depth,
        towfish_height_source=towfish_height_source, towfish_height=towfish_height,
        towfish_head_source=towfish_head_source, towfish_head=towfish_head,
        ch_en_hf=ch_en_hf, sonar_range_hf=sonar_range_hf,
        signal_types_hf=signal_types_hf, pulse_width_hf=pulse_width_hf,
        pulse_source_level_hf=pulse_source_level_hf, center_freq_hf=center_freq_hf,
        gain_hf=gain_hf, spreading_hf=spreading_hf, absorption_hf=absorption_hf,
        ch_en_lf=ch_en_lf, sonar_range_lf=sonar_range_lf,
        signal_types_lf=signal_types_lf, pulse_width_lf=pulse_width_lf,
        pulse_source_level_lf=pulse_source_level_lf, center_freq_lf=center_freq_lf,
        gain_lf=gain_lf, spreading_lf=spreading_lf, absorption_lf=absorption_lf,
        sonar_run=sonar_run,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/protocol/test_commands.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/protocol/commands.py tests/protocol/test_commands.py
git commit -m "feat: add type-107 command packet builder"
```

---

## Task 5: `bytt/processing/colormap.py`

**Files:**
- Create: `bytt/processing/colormap.py`
- Test: `tests/processing/test_colormap.py`

**Interfaces:**
- Produces: `build_lut(stops) -> np.ndarray[256,3] uint8`, `build_combined_lut(gain, gamma,
  colour_lut) -> np.ndarray[256,3] uint8`, `LUT_NAMES: list[str]`,
  `LUT_PALETTES: dict[str, np.ndarray]`. Task 13 (`gui/waterfall_view.py`) imports these.

- [ ] **Step 1: Write the failing test**

```python
# tests/processing/test_colormap.py
import numpy as np
from bytt.processing import colormap

def test_lut_names_and_palettes_match():
    assert set(colormap.LUT_NAMES) == set(colormap.LUT_PALETTES.keys())
    for name in colormap.LUT_NAMES:
        lut = colormap.LUT_PALETTES[name]
        assert lut.shape == (256, 3)
        assert lut.dtype == np.uint8

def test_build_lut_endpoints():
    stops = [(0.0, colormap.lut_stop(0, 0, 0)), (1.0, colormap.lut_stop(255, 255, 255))]
    lut = colormap.build_lut(stops)
    assert list(lut[0]) == [0, 0, 0]
    assert list(lut[255]) == [255, 255, 255]

def test_build_combined_lut_applies_gain_and_gamma():
    grayscale = colormap.LUT_PALETTES['Grayscale']
    combined = colormap.build_combined_lut(gain=1.0, gamma=1.0, colour_lut=grayscale)
    assert combined.shape == (256, 3)
    assert list(combined[0]) == [0, 0, 0]
    assert list(combined[255]) == [255, 255, 255]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mkdir -p tests/processing && touch tests/processing/__init__.py && pytest tests/processing/test_colormap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.processing.colormap'`

- [ ] **Step 3: Move the code**

Move from `interactive_bsf_viewer.py` lines 360-426, renaming `_lut_stop` → `lut_stop`, `_build_lut`
→ `build_lut`, `_build_combined_lut` → `build_combined_lut` (drop leading underscores — public API
now). `_LUT_STOPS`, `LUT_NAMES`, `LUT_PALETTES` move unchanged.

```python
"""Colour lookup tables for the waterfall display."""
import numpy as np


def lut_stop(r, g, b):
    return np.array([r, g, b], dtype=np.float32)


def build_lut(stops):
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]
            t1, c1 = stops[j + 1]
            if t0 <= t <= t1:
                alpha = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                lut[i] = np.clip(c0 + alpha * (c1 - c0), 0, 255).astype(np.uint8)
                break
    return lut


_LUT_STOPS = {
    "Amber": [
        (0.00, lut_stop(0,   0,   0)),
        (0.35, lut_stop(80,  40,   0)),
        (0.60, lut_stop(210, 140,  10)),
        (0.80, lut_stop(255, 210,  60)),
        (1.00, lut_stop(255, 255, 255)),
    ],
    "Grayscale": [
        (0.00, lut_stop(0,   0,   0)),
        (1.00, lut_stop(255, 255, 255)),
    ],
    "Ice": [
        (0.00, lut_stop(0,   0,   10)),
        (0.35, lut_stop(0,   40,  95)),
        (0.65, lut_stop(0,  150, 210)),
        (0.85, lut_stop(140, 225, 255)),
        (1.00, lut_stop(255, 255, 255)),
    ],
    "Copper": [
        (0.00, lut_stop(0,   0,   0)),
        (0.40, lut_stop(90,  45,  25)),
        (0.70, lut_stop(200, 120,  70)),
        (0.90, lut_stop(240, 190, 140)),
        (1.00, lut_stop(255, 255, 255)),
    ],
    "Phosphor": [
        (0.00, lut_stop(0,   5,   0)),
        (0.35, lut_stop(0,   60,  15)),
        (0.65, lut_stop(30, 180,  40)),
        (0.85, lut_stop(150, 255, 130)),
        (1.00, lut_stop(255, 255, 255)),
    ],
}
LUT_NAMES    = list(_LUT_STOPS.keys())
LUT_PALETTES = {name: build_lut(stops) for name, stops in _LUT_STOPS.items()}


def build_combined_lut(gain, gamma, colour_lut):
    """Pre-compute gain + gamma + palette into a single 256x3 uint8 LUT so
    per-pixel colour mapping is a single gather: rgb = combined_lut[img8]."""
    x = np.arange(256, dtype=np.float32) / 255.0
    v = np.clip(x * gain, 0.0, 1.0)
    v = np.power(v, gamma)
    idx = (v * 255.0).astype(np.uint8)
    return colour_lut[idx]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/processing/test_colormap.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/processing/colormap.py tests/processing/
git commit -m "feat: port colour LUT building into bytt.processing.colormap"
```

---

## Task 6: `bytt/processing/enhancement.py`

**Files:**
- Create: `bytt/processing/enhancement.py`
- Test: `tests/processing/test_enhancement.py`

**Interfaces:**
- Produces: `EnhanceParams` namedtuple, `enhance_pixels(raw2d: np.ndarray, p: EnhanceParams) ->
  np.ndarray[uint8]`, `smart_hdr(img8, strength=1.5, fast=False) -> np.ndarray[uint8]`,
  `tint(rgb, mask, colour, alpha=0.55) -> np.ndarray`, `heavy_key(p: EnhanceParams) -> tuple`.
  Task 14 (`gui/main_window.py`) imports `EnhanceParams` and `enhance_pixels`.

- [ ] **Step 1: Write the failing test**

```python
# tests/processing/test_enhancement.py
import numpy as np
from bytt.processing.enhancement import EnhanceParams, enhance_pixels, heavy_key

def _default_params(**overrides):
    base = dict(noise_idx=0, contrast_idx=0, agc=False, target_idx=0,
                target_ksize=9, shadow_enh=False, overlay=0, sharpen=False,
                gain=1.0, gamma=1.0, lut_idx=0, fast=True, hdr=False)
    base.update(overrides)
    return EnhanceParams(**base)

def test_enhance_pixels_returns_uint8_same_shape():
    raw = np.random.rand(64, 128).astype(np.float32)
    out = enhance_pixels(raw, _default_params())
    assert out.dtype == np.uint8
    assert out.shape == raw.shape

def test_enhance_pixels_handles_all_zero_input():
    raw = np.zeros((32, 32), dtype=np.float32)
    out = enhance_pixels(raw, _default_params())
    assert out.shape == (32, 32)
    assert out.dtype == np.uint8

def test_heavy_key_ignores_non_heavy_fields():
    p1 = _default_params(gain=1.0)
    p2 = _default_params(gain=2.0)  # gain is not a HEAVY_FIELDS member
    assert heavy_key(p1) == heavy_key(p2)
    p3 = _default_params(sharpen=True)
    assert heavy_key(p1) != heavy_key(p3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/processing/test_enhancement.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.processing.enhancement'`

- [ ] **Step 3: Move the code**

Move from `interactive_bsf_viewer.py`:
- `EnhanceParams` namedtuple and `HEAVY_FIELDS` tuple (lines 93-99)
- `_heavy_key` → rename to `heavy_key` (lines 102-104)
- `_enhance_pixels` → rename to `enhance_pixels`, drop `self` (lines 1281-1363)
- `_smart_hdr` → rename to `smart_hdr`, drop `self` (lines 1364-1436)
- `_tint` → rename to `tint`, it is already `@staticmethod` so just drop the decorator and the
  (already absent) `self` (lines 1436-1443)

The only structural change: `_enhance_pixels` currently calls `self._clahe_low.apply(...)`,
`self._clahe_med.apply(...)`, `self._clahe_high.apply(...)`, and `self._smart_hdr(...)`. These
`cv2.CLAHE` objects were created once in `BSFViewer.__init__` (source lines 1113-1115) — replicate
that as module-level singletons (they hold no per-viewer state, so this is safe), and change the
`self._smart_hdr` call to a plain call to `smart_hdr`.

```python
"""Real-time waterfall pixel enhancement pipeline: denoise, AGC, contrast
(histogram-eq/CLAHE), target/shadow enhancement, sharpening, HDR."""
from collections import namedtuple
import cv2
import numpy as np

EnhanceParams = namedtuple('EnhanceParams', [
    'noise_idx', 'contrast_idx', 'agc', 'target_idx', 'target_ksize',
    'shadow_enh', 'overlay', 'sharpen', 'gain', 'gamma', 'lut_idx', 'fast', 'hdr',
])

HEAVY_FIELDS = ('noise_idx', 'contrast_idx', 'agc', 'target_idx',
                'target_ksize', 'shadow_enh', 'overlay', 'sharpen', 'hdr')


def heavy_key(p):
    """Extract the subset of params that affect the expensive pipeline."""
    return tuple(getattr(p, f) for f in HEAVY_FIELDS)


_clahe_low  = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
_clahe_med  = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
_clahe_high = cv2.createCLAHE(clipLimit=6.0, tileGridSize=(8, 8))


def enhance_pixels(raw2d, p):
    # <<< paste the body of interactive_bsf_viewer.py's _enhance_pixels
    #     (lines 1282-1363) here verbatim, with these substitutions:
    #       self._clahe_low.apply(  -> _clahe_low.apply(
    #       self._clahe_med.apply(  -> _clahe_med.apply(
    #       self._clahe_high.apply( -> _clahe_high.apply(
    #       self._smart_hdr(        -> smart_hdr(
    ...


def smart_hdr(img8, strength=1.5, fast=False):
    # <<< paste the body of interactive_bsf_viewer.py's _smart_hdr
    #     (lines 1365-1434) here verbatim — it has no self. references.
    ...


def tint(rgb, mask, colour, alpha=0.55):
    if mask is None or not mask.any():
        return rgb
    c = np.array(colour, dtype=np.float32)
    region = rgb[mask].astype(np.float32)
    rgb[mask] = (region * (1 - alpha) + c * alpha).astype(np.uint8)
    return rgb
```

Also move the `CONTRAST_MODES`, `CONTRAST_DEFAULT_IDX`, and `TARGET_MODES` constants (source lines
185, 192, 195) into this file, since `enhance_pixels` indexes into them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/processing/test_enhancement.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/processing/enhancement.py tests/processing/test_enhancement.py
git commit -m "feat: port enhancement pipeline into bytt.processing.enhancement"
```

---

## Task 7: `bytt/nav/gps_track.py`

**Files:**
- Create: `bytt/nav/gps_track.py`
- Test: `tests/nav/test_gps_track.py`

**Interfaces:**
- Produces: `GPSTrack` class with `add_fix(ping_idx, lat, lon)`, `has_data` property, `bounds()`,
  `position_at_or_before(ping_idx)`, `history_upto(ping_idx)`, `unproject(x, y)`, and
  `format_degrees(value, pos_letter, neg_letter, decimals)`. Task 14 (`gui/main_window.py`)
  instantiates `GPSTrack`.

- [ ] **Step 1: Write the failing test**

```python
# tests/nav/test_gps_track.py
from bytt.nav.gps_track import GPSTrack, format_degrees

def test_first_fix_becomes_origin():
    track = GPSTrack()
    track.add_fix(ping_idx=0, lat=41.30, lon=36.33)
    assert track.has_data
    x, y, ping_idx, i = track.position_at_or_before(0)
    assert (x, y) == (0.0, 0.0)
    assert ping_idx == 0

def test_position_at_or_before_returns_none_when_empty():
    assert GPSTrack().position_at_or_before(5) is None

def test_history_upto_counts_fixes_at_or_before():
    track = GPSTrack()
    track.add_fix(0, 41.30, 36.33)
    track.add_fix(5, 41.31, 36.34)
    track.add_fix(10, 41.32, 36.35)
    assert track.history_upto(7) == 2
    assert track.history_upto(10) == 3
    assert track.history_upto(-1) == 0

def test_format_degrees():
    assert format_degrees(41.3, 'N', 'S', 2) == "41.30°N"
    assert format_degrees(-36.3, 'E', 'W', 1) == "36.3°W"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mkdir -p tests/nav && touch tests/nav/__init__.py && pytest tests/nav/test_gps_track.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.nav.gps_track'`

- [ ] **Step 3: Move the code**

Move `GPSTrack` (source lines 846-914) and `_format_degrees` → `format_degrees` (lines 841-843)
unchanged — this class has zero pygame/GUI dependencies already.

```python
"""GPS track: converts lat/lon fixes into a local flat-earth XY track keyed
by ping index, for the waterfall's position readout and nav panel."""
import math


def format_degrees(value, pos_letter, neg_letter, decimals):
    letter = pos_letter if value >= 0 else neg_letter
    return f"{abs(value):.{decimals}f}°{letter}"


class GPSTrack:
    EARTH_R_M = 6371000.0

    def __init__(self):
        self.ref_lat   = None
        self.ref_lon   = None
        self.ping_idx  = []
        self.xs        = []
        self.ys        = []
        self._last_latlon = None

    def _project(self, lat, lon):
        if self.ref_lat is None:
            self.ref_lat, self.ref_lon = lat, lon
        dlat = math.radians(lat - self.ref_lat)
        dlon = math.radians(lon - self.ref_lon)
        y = dlat * self.EARTH_R_M
        x = dlon * self.EARTH_R_M * math.cos(math.radians(self.ref_lat))
        return x, y

    def unproject(self, x, y):
        lat = self.ref_lat + math.degrees(y / self.EARTH_R_M)
        coslat = max(0.01, math.cos(math.radians(self.ref_lat)))
        lon = self.ref_lon + math.degrees(x / (self.EARTH_R_M * coslat))
        return lat, lon

    def add_fix(self, ping_idx, lat, lon):
        x, y = self._project(lat, lon)
        self.ping_idx.append(ping_idx)
        self.xs.append(x)
        self.ys.append(y)
        self._last_latlon = (lat, lon)

    @property
    def has_data(self):
        return len(self.xs) > 0

    def bounds(self):
        if not self.xs:
            return (-10, 10, -10, 10)
        return (min(self.xs), max(self.xs), min(self.ys), max(self.ys))

    def position_at_or_before(self, ping_idx):
        if not self.ping_idx:
            return None
        lo, hi = 0, len(self.ping_idx) - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.ping_idx[mid] <= ping_idx:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None:
            return None
        return self.xs[best], self.ys[best], self.ping_idx[best], best

    def history_upto(self, ping_idx):
        if not self.ping_idx:
            return 0
        lo, hi = 0, len(self.ping_idx)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.ping_idx[mid] <= ping_idx:
                lo = mid + 1
            else:
                hi = mid
        return lo
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/nav/test_gps_track.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/nav/gps_track.py tests/nav/
git commit -m "feat: port GPSTrack into bytt.nav.gps_track"
```

---

## Task 8: `bytt/bsf/file_io.py`

**Files:**
- Create: `bytt/bsf/file_io.py`
- Test: `tests/bsf/test_file_io.py`

**Interfaces:**
- Consumes: `bytt.protocol.constants` (Task 2).
- Produces: `read_file_header(data: bytes) -> dict`, `load_all_pings(data: bytes,
  progress_cb=None) -> (list[(offset, size)], list[(offset, ping_idx)])`. Task 11
  (`net/playback_source.py`) imports both.

Note: the original `Loader` class (source lines 314-356) is a pygame progress-screen renderer —
it is **not** ported; it's superseded by Qt's own progress reporting (a `QProgressDialog` or
status-bar message), which Task 11 wires up via the `progress_cb` parameter added below.

- [ ] **Step 1: Write the failing test**

```python
# tests/bsf/test_file_io.py
import struct
from bytt.protocol import constants as pc
from bytt.bsf import file_io

def _build_ping_record(n_samples=4, half_samples=4):
    size = pc.SAMPLE_OFFSET + 2 * n_samples * 4
    rec = bytearray(size)
    struct.pack_into('<I', rec, 0, pc.BSF_RECORD_TYPE)
    struct.pack_into('<I', rec, 12, size)
    ch0_off = pc.CH_SR_OFFSET
    struct.pack_into('<H', rec, ch0_off + 2, 400)       # freq_kHz
    struct.pack_into('<I', rec, ch0_off + 28, 96000)     # raw_sr
    struct.pack_into('<I', rec, ch0_off + 52, half_samples)  # half_samples
    return bytes(rec)

def _build_bsf_bytes(n_pings=3):
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    struct.pack_into('<f', header, 0x10, 1500.0)   # sound_speed
    struct.pack_into('<d', header, 0x38, 36.33)    # longitude
    struct.pack_into('<d', header, 0x40, 41.30)    # latitude
    header[0x60:0x66] = b'SS3060'
    header[0x70:0x76] = b'SN0001'
    body = b''.join(_build_ping_record() for _ in range(n_pings))
    return bytes(header) + body

def test_read_file_header():
    data = _build_bsf_bytes()
    hdr = file_io.read_file_header(data)
    assert hdr['sound_speed'] == 1500.0
    assert hdr['device_model'] == 'SS3060'
    assert hdr['sonar_serial'] == 'SN0001'
    assert abs(hdr['longitude'] - 36.33) < 1e-9
    assert abs(hdr['latitude'] - 41.30) < 1e-9

def test_load_all_pings_finds_every_record():
    data = _build_bsf_bytes(n_pings=3)
    pings, nav_records = file_io.load_all_pings(data)
    assert len(pings) == 3
    assert nav_records == []
    for offset, size in pings:
        assert data[offset:offset + 4] == struct.pack('<I', pc.BSF_RECORD_TYPE)

def test_load_all_pings_reports_progress():
    data = _build_bsf_bytes(n_pings=3)
    calls = []
    file_io.load_all_pings(data, progress_cb=lambda done, total: calls.append((done, total)))
    # progress_cb is optional and only required to not raise; exact call
    # cadence is an implementation detail of the report_every threshold.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mkdir -p tests/bsf && touch tests/bsf/__init__.py && pytest tests/bsf/test_file_io.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.bsf.file_io'`

- [ ] **Step 3: Move and adapt the code**

Move `read_file_header` (source lines 461-468) unchanged. Move `load_all_pings` (source lines
815-837), replacing the `loader` parameter (a pygame `Loader` instance) with a plain optional
callback `progress_cb(done: int, total: int)`:

```python
"""Reads .bsf recording files: file header fields and the byte-offset index
of every ping/nav record in the file."""
import struct
from bytt.protocol import constants as pc


def read_file_header(data: bytes) -> dict:
    return {
        'sound_speed':  struct.unpack_from('<f', data, 0x10)[0],
        'device_model': data[0x60:0x70].rstrip(b'\x00').decode('ascii', errors='replace'),
        'sonar_serial': data[0x70:0x80].rstrip(b'\x00').decode('ascii', errors='replace'),
        'longitude':    struct.unpack_from('<d', data, 0x38)[0],
        'latitude':     struct.unpack_from('<d', data, 0x40)[0],
    }


def load_all_pings(data: bytes, progress_cb=None):
    """Scans the file once, returning:
      pings:       [(byte_offset, record_size), ...]
      nav_records: [(byte_offset, ping_index_before_it), ...]
    progress_cb, if given, is called as progress_cb(bytes_scanned, total_bytes)
    every 500 pings found."""
    pings       = []
    nav_records = []
    pos     = pc.BSF_FILE_HDR_SZ
    file_sz = len(data)
    report_every = 500
    while pos + 16 <= file_sz:
        rt = struct.unpack_from('<I', data, pos)[0]
        if rt == pc.BSF_RECORD_TYPE:
            rs = struct.unpack_from('<I', data, pos + 12)[0]
            if rs > 16 and pos + rs <= file_sz:
                if rs >= pc.SAMPLE_OFFSET:
                    pings.append((pos, rs))
                    if progress_cb and len(pings) % report_every == 0:
                        progress_cb(pos, file_sz)
                elif rs == pc.NAV_RECORD_SIZE:
                    nav_records.append((pos, len(pings) - 1))
                pos += rs
            else:
                break
        else:
            pos += 1
    return pings, nav_records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bsf/test_file_io.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/bsf/file_io.py tests/bsf/
git commit -m "feat: port .bsf file reading into bytt.bsf.file_io"
```

---

## Task 9: `bytt/config.py`

**Files:**
- Create: `bytt/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `AppConfig` dataclass (`towfish_ip: str`, `cmd_port: int`, `data_port: int`,
  `source: str`), `load_config(path) -> AppConfig`, `save_config(config: AppConfig, path) -> None`,
  `DEFAULT_CONFIG_PATH`. Task 14 (`app.py`) calls `load_config`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import configparser
from pathlib import Path
from bytt.config import AppConfig, load_config, save_config

def test_load_config_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.ini")
    assert cfg == AppConfig(towfish_ip="192.168.1.16", cmd_port=16128,
                             data_port=16129, source="playback")

def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "bytt.ini"
    original = AppConfig(towfish_ip="192.168.1.99", cmd_port=1, data_port=2, source="towfish")
    save_config(original, path)
    loaded = load_config(path)
    assert loaded == original

def test_save_config_writes_expected_ini_shape(tmp_path):
    path = tmp_path / "bytt.ini"
    save_config(AppConfig(towfish_ip="10.0.0.1", cmd_port=16128, data_port=16129,
                           source="towfish"), path)
    parser = configparser.ConfigParser()
    parser.read(path)
    assert parser["Sonar"]["TowfishIP"] == "10.0.0.1"
    assert parser["Sonar"]["Source"] == "towfish"
    assert set(parser["Sonar"].keys()) == {"towfiship", "cmdport", "dataport", "source"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.config'`

- [ ] **Step 3: Implement the module**

This is new code (the original app used `QSettings`, not a portable config module) — shape taken
from `BYTT docs/HydroWaterDemo.ini.example`, trimmed per the spec to drop `PCIP` and any
SatCenter-only fields:

```python
"""Loads/saves the app's connection config: [Sonar] TowfishIP, CmdPort,
DataPort, Source (playback|towfish). See BYTT docs/HydroWaterDemo.ini.example
for the original's shape -- this is the trimmed, SatCenter-free version."""
import configparser
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".bytt" / "bytt.ini"


@dataclass
class AppConfig:
    towfish_ip: str = "192.168.1.16"
    cmd_port: int = 16128
    data_port: int = 16129
    source: str = "playback"  # "playback" | "towfish"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    path = Path(path)
    if not path.exists():
        return AppConfig()
    parser = configparser.ConfigParser()
    parser.read(path)
    section = parser["Sonar"]
    return AppConfig(
        towfish_ip=section.get("TowfishIP", AppConfig.towfish_ip),
        cmd_port=section.getint("CmdPort", AppConfig.cmd_port),
        data_port=section.getint("DataPort", AppConfig.data_port),
        source=section.get("Source", AppConfig.source),
    )


def save_config(config: AppConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser["Sonar"] = {
        "TowfishIP": config.towfish_ip,
        "CmdPort": str(config.cmd_port),
        "DataPort": str(config.data_port),
        "Source": config.source,
    }
    with open(path, "w") as f:
        parser.write(f)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/config.py tests/test_config.py
git commit -m "feat: add trimmed connection config (no SatCenter fields)"
```

---

## Task 10: `bytt/net/live_client.py` — Qt-signal version of `LiveClient`

**Files:**
- Create: `bytt/net/live_client.py`
- Test: `tests/net/test_live_client.py`

**Interfaces:**
- Consumes: `bytt.protocol.constants`, `bytt.protocol.packets.checksum_ok`,
  `bytt.protocol.packets.parse_3101_body` (Tasks 2, 3).
- Produces: `LiveClient(QObject)` with `Signal(np.ndarray, np.ndarray, dict) ping_received`,
  `Signal(str) status_changed`, `Signal(bytes) raw_packet_received`, methods `start()`, `stop()`.
  This is the "ping source" interface Task 11 (`PlaybackSource`) must also implement, and Task 14
  (`main_window.py`) consumes both interchangeably.

- [ ] **Step 1: Write the failing test**

```python
# tests/net/test_live_client.py
import socket
import struct
import threading
import time
import pytest
from PySide6.QtCore import QCoreApplication
from bytt.protocol import constants as pc
from bytt.protocol.commands import build_command_107, SonarWorkCommand
from bytt.net.live_client import LiveClient

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app

def _fake_server(port_holder, packet_to_send, ready, stop):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_holder.append(srv.getsockname()[1])
    srv.settimeout(3.0)
    ready.set()
    try:
        conn, _ = srv.accept()
    except socket.timeout:
        return
    conn.sendall(packet_to_send)
    while not stop.is_set():
        time.sleep(0.05)
    conn.close()
    srv.close()

def test_live_client_emits_ping_received_for_a_166_status_packet():
    header = struct.pack('<IHHIHHHHI', pc.PACKET_IDENTIFIER, pc.PACKET_HEADER_SIZE,
                          pc.PACKET_VERSION, 0, pc.PACKET_FLAG_CHECKSUM_BIT, 0, 1, 1,
                          pc.PACKET_TYPE_166)
    content = bytes([0x11])  # data+cmd connected
    size = len(header) + len(content) + 4
    header = header[:pc.PACKET_SIZE_OFFSET] + struct.pack('<I', size) + header[pc.PACKET_SIZE_OFFSET+4:]
    from bytt.protocol.packets import compute_checksum
    body = header + content
    packet = body + struct.pack('<I', compute_checksum(body))

    port_holder, ready, stop = [], threading.Event(), threading.Event()
    server = threading.Thread(target=_fake_server, args=(port_holder, packet, ready, stop), daemon=True)
    server.start()
    ready.wait(timeout=2.0)

    statuses = []
    client = LiveClient("127.0.0.1", port_holder[0])
    client.status_changed.connect(statuses.append)
    client.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and not any("hw:" in s for s in statuses):
        QCoreApplication.processEvents()
        time.sleep(0.02)
    client.stop()
    stop.set()
    server.join(timeout=2.0)

    assert any("connected (data+cmd)" in s for s in statuses)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mkdir -p tests/net && touch tests/net/__init__.py && pytest tests/net/test_live_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.net.live_client'`

- [ ] **Step 3: Port `LiveClient`, converting callbacks to Qt signals**

Move the class from `interactive_bsf_viewer.py` lines 633-793 with this one structural change:
`on_row`/`on_status` constructor callbacks become `ping_received`/`status_changed` **Signals**
(thread-safe to emit from the background socket thread — PySide6 queues cross-thread signal
delivery automatically), and a new `raw_packet_received` signal is added for the future recorder
tap point (per the spec's "forward-compat hook").

```python
"""Threaded TCP client for the towfish/SatCenter live data port (16129).
Reassembles multi-packet frames, verifies checksums, and emits Qt signals
for 3101 (ping) and 166 (status) packets."""
import socket
import struct
import threading
import time
from PySide6.QtCore import QObject, Signal
from bytt.protocol import constants as pc
from bytt.protocol.packets import checksum_ok, parse_3101_body


class LiveClient(QObject):
    ping_received = Signal(object, object, dict)  # port_raw, stbd_raw, meta
    status_changed = Signal(str)
    raw_packet_received = Signal(bytes)

    def __init__(self, host, port, parent=None):
        super().__init__(parent)
        self.host, self.port = host, port
        self._sock   = None
        self._stop   = threading.Event()
        self._thread = None
        self._partial_frames = {}
        self._last_ping_number = None
        self._checksum_failures = 0

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        self.status_changed.emit('connecting')
        try:
            sock = socket.create_connection((self.host, self.port), timeout=3.0)
        except Exception as e:
            self.status_changed.emit(f'not connected ({e})')
            return
        self._sock = sock
        sock.settimeout(1.0)
        self.status_changed.emit('connected')
        buf = bytearray()
        try:
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf.extend(chunk)
                self._drain(buf)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
            if not self._stop.is_set():
                self.status_changed.emit('disconnected (connection lost)')

    def _drain(self, buf):
        while True:
            if len(buf) < pc.PACKET_HEADER_SIZE:
                return
            idx = buf.find(pc.PACKET_IDENTIFIER_BYTES)
            if idx == -1:
                buf.clear()
                return
            if idx > 0:
                del buf[:idx]
            if len(buf) < pc.PACKET_HEADER_SIZE:
                return
            packet_size = struct.unpack_from('<I', buf, pc.PACKET_SIZE_OFFSET)[0]
            if packet_size < pc.PACKET_HEADER_SIZE or packet_size > 8_000_000:
                del buf[:4]
                continue
            if len(buf) < packet_size:
                return
            packet = bytes(buf[:packet_size])
            del buf[:packet_size]
            self._handle_packet(packet)

    def _prune_partial_frames(self, now):
        stale = [k for k, v in self._partial_frames.items()
                 if now - v['first_seen'] > pc.MULTI_PACKET_FRAME_TIMEOUT_S]
        for k in stale:
            del self._partial_frames[k]

    def _handle_packet(self, packet):
        packet_size = len(packet)
        try:
            packet_type = struct.unpack_from('<I', packet, pc.PACKET_TYPE_OFFSET)[0]
            flag        = struct.unpack_from('<H', packet, pc.PACKET_FLAG_OFFSET)[0]
        except struct.error:
            return
        if not checksum_ok(packet, packet_size):
            self._checksum_failures += 1
            self.status_changed.emit(f'checksum FAILED (packet dropped, '
                           f'{self._checksum_failures} total)')
            return
        self.raw_packet_received.emit(packet)
        is_multi = bool(flag & pc.PACKET_FLAG_MULTI_BIT)
        body_and_data = packet[pc.PACKET_HEADER_SIZE: packet_size - 4]
        if is_multi:
            body_and_data = self._reassemble(packet, packet_type, body_and_data)
            if body_and_data is None:
                return
        if packet_type == pc.PACKET_TYPE_3101:
            self._handle_3101(body_and_data)
        elif packet_type == pc.PACKET_TYPE_166:
            self._handle_166(body_and_data)

    def _reassemble(self, packet, packet_type, body_and_data):
        now = time.time()
        self._prune_partial_frames(now)
        try:
            frame_id     = struct.unpack_from('<H', packet, pc.PACKET_FRAME_ID_OFFSET)[0]
            total_packet = struct.unpack_from('<H', packet, pc.PACKET_TOTAL_OFFSET)[0]
            packet_num   = struct.unpack_from('<H', packet, pc.PACKET_NUMBER_OFFSET)[0]
        except struct.error:
            return None
        if total_packet <= 1 or packet_num < 1:
            return body_and_data
        key = (packet_type, frame_id)
        entry = self._partial_frames.get(key)
        if entry is None or entry['total'] != total_packet:
            entry = {'total': total_packet, 'parts': {}, 'first_seen': now}
            self._partial_frames[key] = entry
        entry['parts'][packet_num] = body_and_data
        if len(entry['parts']) < total_packet:
            return None
        del self._partial_frames[key]
        return b''.join(entry['parts'][i] for i in range(1, total_packet + 1))

    def _handle_3101(self, body_and_data):
        parsed = parse_3101_body(None, body_and_data)
        if not parsed:
            return
        port_raw, stbd_raw, meta = parsed
        pn = meta.get('ping_number')
        if pn is not None and self._last_ping_number is not None:
            delta = pn - self._last_ping_number
            if delta > 1:
                meta['ping_gap'] = delta - 1
            elif delta < 0:
                meta['ping_gap'] = 0
        self._last_ping_number = pn if pn is not None else self._last_ping_number
        self.ping_received.emit(port_raw, stbd_raw, meta)

    def _handle_166(self, body_and_data):
        if len(body_and_data) < 1:
            return
        state = body_and_data[0]
        data_up = bool(state & 0x01)
        cmd_up  = bool(state & 0x10)
        if data_up and cmd_up:
            desc = 'connected (data+cmd)'
        elif data_up:
            desc = 'connected (data only)'
        elif cmd_up:
            desc = 'connected (cmd only)'
        else:
            desc = 'sonar link down'
        self.status_changed.emit(f'connected — hw: {desc}')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/net/test_live_client.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add bytt/net/live_client.py tests/net/
git commit -m "feat: port LiveClient to Qt signals"
```

---

## Task 11: `bytt/net/command_client.py`

**Files:**
- Create: `bytt/net/command_client.py`
- Test: `tests/net/test_command_client.py`

**Interfaces:**
- Consumes: `bytt.protocol.commands.build_command_107`, `SonarWorkCommand` (Task 4).
- Produces: `CommandClient(QObject)` with `Signal(str) status_changed`, methods `connect_to(host,
  port)`, `send_command(cmd: SonarWorkCommand)`, `close()`. No task after this wires it to UI yet
  (that's the future control-panel plan) — `main_window.py` (Task 14) only needs to construct one.

- [ ] **Step 1: Write the failing test**

```python
# tests/net/test_command_client.py
import socket
import struct
import threading
import pytest
from bytt.protocol.commands import SonarWorkCommand
from bytt.net.command_client import CommandClient

def test_send_command_writes_256_bytes_to_socket():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = {}

    def accept_one():
        conn, _ = srv.accept()
        received['data'] = conn.recv(4096)
        conn.close()

    t = threading.Thread(target=accept_one, daemon=True)
    t.start()

    client = CommandClient()
    client.connect_to("127.0.0.1", port)
    client.send_command(SonarWorkCommand(sonar_run=1))
    t.join(timeout=2.0)
    client.close()
    srv.close()

    assert len(received['data']) == 256

def test_send_command_without_connect_raises():
    client = CommandClient()
    with pytest.raises(RuntimeError):
        client.send_command(SonarWorkCommand())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/net/test_command_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.net.command_client'`

- [ ] **Step 3: Implement the module**

New code — this channel didn't exist in the original pygame viewer (it only ever received). Mirror
`LiveClient`'s shape but simpler: it's a short-lived outbound socket, not a threaded reader.

```python
"""Outbound TCP client for the towfish/SatCenter command port (16128).
Sends type-107 SS sonar work control commands built by
bytt.protocol.commands.build_command_107."""
import socket
from PySide6.QtCore import QObject, Signal
from bytt.protocol.commands import build_command_107, SonarWorkCommand


class CommandClient(QObject):
    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = None
        self._identification = 0

    def connect_to(self, host: str, port: int, timeout: float = 3.0) -> None:
        self.status_changed.emit('connecting')
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self.status_changed.emit('connected')

    def send_command(self, cmd: SonarWorkCommand) -> None:
        if self._sock is None:
            raise RuntimeError("CommandClient.send_command called before connect_to()")
        packet = build_command_107(cmd, identification=self._identification)
        self._identification = (self._identification + 1) % 65536
        self._sock.sendall(packet)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
            self.status_changed.emit('disconnected')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/net/test_command_client.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/net/command_client.py tests/net/test_command_client.py
git commit -m "feat: add CommandClient for outbound type-107 commands"
```

---

## Task 12: `bytt/net/playback_source.py`

**Files:**
- Create: `bytt/net/playback_source.py`
- Test: `tests/net/test_playback_source.py`

**Interfaces:**
- Consumes: `bytt.bsf.file_io.load_all_pings` (Task 8), `bytt.protocol.packets.parse_channel`,
  `extract_raw_channels`, `get_ping_meta`, `extract_nav_fix` (Task 3), `bytt.protocol.constants`
  (Task 2).
- Produces: `PlaybackSource(QObject)` with the **same signal interface as `LiveClient`**:
  `ping_received = Signal(object, object, dict)`, `status_changed = Signal(str)`, methods
  `start()`, `stop()`. This is what makes Task 14's `main_window.py` source-agnostic.

- [ ] **Step 1: Write the failing test**

```python
# tests/net/test_playback_source.py
import struct
import time
import pytest
from PySide6.QtCore import QCoreApplication
from bytt.protocol import constants as pc
from bytt.net.playback_source import PlaybackSource

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app

def _build_ping_record(n_samples=4):
    size = pc.SAMPLE_OFFSET + 2 * n_samples * 4
    rec = bytearray(size)
    struct.pack_into('<I', rec, 0, pc.BSF_RECORD_TYPE)
    struct.pack_into('<I', rec, 12, size)
    struct.pack_into('<H', rec, pc.CH_SR_OFFSET + 2, 400)
    struct.pack_into('<I', rec, pc.CH_SR_OFFSET + 28, 96000)
    struct.pack_into('<I', rec, pc.CH_SR_OFFSET + 52, n_samples)
    return bytes(rec)

def _build_bsf_bytes(n_pings=3, n_samples=4):
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    body = b''.join(_build_ping_record(n_samples) for _ in range(n_pings))
    return bytes(header) + body

def test_playback_emits_one_ping_received_per_record(tmp_path):
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(_build_bsf_bytes(n_pings=3))

    received = []
    source = PlaybackSource(str(bsf_path), pings_per_second=1000)  # fast for the test
    source.ping_received.connect(lambda port, stbd, meta: received.append(meta))
    source.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and len(received) < 3:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.stop()

    assert len(received) == 3

def test_playback_missing_file_emits_status_error(tmp_path):
    statuses = []
    source = PlaybackSource(str(tmp_path / "missing.bsf"))
    source.status_changed.connect(statuses.append)
    source.start()
    deadline = time.time() + 2.0
    while time.time() < deadline and not statuses:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    source.stop()
    assert any("not found" in s or "error" in s.lower() for s in statuses)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/net/test_playback_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.net.playback_source'`

- [ ] **Step 3: Implement the module**

New code (the original had no unified playback-as-a-source abstraction — `.bsf` files were loaded
directly into `BSFViewer`'s in-memory ping index and stepped through synchronously in the pygame
loop). This wraps that same file-reading logic in a background thread emitting at a fixed rate,
matching `LiveClient`'s signal shape exactly.

```python
"""Replays a .bsf file through the same ping_received/status_changed signal
interface LiveClient exposes, so GUI code cannot tell live data from
playback apart."""
import threading
import time
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from bytt.bsf.file_io import load_all_pings
from bytt.protocol.constants import SAMPLE_OFFSET
from bytt.protocol.packets import extract_raw_channels, get_ping_meta


class PlaybackSource(QObject):
    ping_received = Signal(object, object, dict)
    status_changed = Signal(str)

    def __init__(self, bsf_path: str, pings_per_second: float = 16.0, parent=None):
        super().__init__(parent)
        self.bsf_path = bsf_path
        self._interval_s = 1.0 / pings_per_second
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        path = Path(self.bsf_path)
        if not path.exists():
            self.status_changed.emit(f'playback error: file not found: {path}')
            return
        self.status_changed.emit('loading')
        try:
            data = path.read_bytes()
            pings, _nav_records = load_all_pings(data)
        except Exception as e:
            self.status_changed.emit(f'playback error: {e}')
            return
        if not pings:
            self.status_changed.emit('playback error: no ping records found')
            return
        self.status_changed.emit(f'connected — playback ({len(pings)} pings)')
        for offset, size in pings:
            if self._stop.is_set():
                break
            ping = data[offset:offset + size]
            port_raw, stbd_raw = extract_raw_channels(ping)
            meta = get_ping_meta(ping)
            self.ping_received.emit(port_raw, stbd_raw, meta)
            time.sleep(self._interval_s)
        if not self._stop.is_set():
            self.status_changed.emit('playback finished')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/net/test_playback_source.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/net/playback_source.py tests/net/test_playback_source.py
git commit -m "feat: add PlaybackSource with LiveClient-compatible signal interface"
```

---

## Task 13: `bytt/gui/waterfall_view.py`

**Files:**
- Create: `bytt/gui/waterfall_view.py`
- Test: `tests/gui/test_waterfall_view.py`

**Interfaces:**
- Consumes: `bytt.processing.colormap.LUT_PALETTES`, `build_combined_lut` (Task 5).
- Produces: `build_display_row(port_raw, stbd_raw, channel_w, port_on, stbd_on, gap,
  interp_xs_cache) -> np.ndarray[float32]`, `WaterfallView(pyqtgraph.GraphicsLayoutWidget)` with
  method `add_row(row_uint8: np.ndarray[uint8]) -> None` and `set_palette(name: str) -> None`.
  Task 14 (`main_window.py`) instantiates `WaterfallView` and calls `add_row`.

- [ ] **Step 1: Write the failing test**

```python
# tests/gui/test_waterfall_view.py
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from bytt.gui.waterfall_view import build_display_row, WaterfallView

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app

def test_build_display_row_concatenates_both_channels_with_gap():
    port = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    stbd = np.array([4.0, 5.0, 6.0], dtype=np.float32)
    cache = {}
    row = build_display_row(port, stbd, channel_w=3, port_on=True, stbd_on=True,
                             gap=2, interp_xs_cache=cache)
    assert row.shape == (3 + 2 + 3,)
    assert np.allclose(row[3:5], 0.0)

def test_build_display_row_single_channel_only():
    port = np.array([1.0, 2.0], dtype=np.float32)
    stbd = np.array([4.0, 5.0], dtype=np.float32)
    cache = {}
    row = build_display_row(port, stbd, channel_w=2, port_on=True, stbd_on=False,
                             gap=2, interp_xs_cache=cache)
    assert row.shape == (2,)

def test_waterfall_view_add_row_grows_image_buffer():
    view = WaterfallView(max_rows=10, width=8)
    row = np.full(8, 200, dtype=np.uint8)
    view.add_row(row)
    view.add_row(row)
    assert view.image_buffer.shape == (10, 8, 3)
    assert view.rows_written == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mkdir -p tests/gui && touch tests/gui/__init__.py && pytest tests/gui/test_waterfall_view.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.gui.waterfall_view'`

- [ ] **Step 3: Implement the module**

`build_display_row` is moved unchanged from `interactive_bsf_viewer.py`'s
`extract_ping_row_from_raw` (source lines 795-812), renamed for clarity. `WaterfallView` is new —
a `pyqtgraph.ImageItem`-backed scrolling image, replacing `FastWaterfall`'s pygame surface
blitting (source lines 918-976).

```python
"""The scrolling waterfall display: combines a ping's port/starboard raw
channels into one display row, and a pyqtgraph-based scrolling image widget
that renders those rows (replaces the pygame-based FastWaterfall)."""
import numpy as np
import pyqtgraph as pg
from bytt.processing.colormap import LUT_PALETTES, build_combined_lut


def build_display_row(port_raw, stbd_raw, channel_w, port_on, stbd_on, gap,
                       interp_xs_cache):
    n = len(port_raw)
    key = (n, channel_w)
    if key not in interp_xs_cache:
        interp_xs_cache[key] = np.linspace(0, n - 1, channel_w)
    xs = interp_xs_cache[key]
    xi = np.arange(n)
    parts = []
    if port_on:
        parts.append(np.interp(xs, xi, port_raw).astype(np.float32))
    if port_on and stbd_on:
        parts.append(np.zeros(gap, dtype=np.float32))
    if stbd_on:
        parts.append(np.interp(xs, xi, stbd_raw).astype(np.float32))
    if not parts:
        return np.zeros(channel_w, dtype=np.float32)
    return np.concatenate(parts)


class WaterfallView(pg.GraphicsLayoutWidget):
    def __init__(self, max_rows=2000, width=1024, palette="Amber", parent=None):
        super().__init__(parent)
        self.max_rows = max_rows
        self.width = width
        self.rows_written = 0
        self.image_buffer = np.zeros((max_rows, width, 3), dtype=np.uint8)
        self._combined_lut = build_combined_lut(gain=1.0, gamma=1.0,
                                                  colour_lut=LUT_PALETTES[palette])
        self._plot = self.addPlot()
        self._plot.invertY(True)
        self._image_item = pg.ImageItem(axisOrder='row-major')
        self._plot.addItem(self._image_item)
        self._image_item.setImage(self.image_buffer, levels=(0, 255))

    def set_palette(self, name: str, gain: float = 1.0, gamma: float = 1.0) -> None:
        self._combined_lut = build_combined_lut(gain=gain, gamma=gamma,
                                                  colour_lut=LUT_PALETTES[name])
        self._refresh()

    def add_row(self, row_uint8: np.ndarray) -> None:
        """row_uint8: 1D array of length self.width, dtype uint8 intensity."""
        rgb_row = self._combined_lut[row_uint8]
        self.image_buffer = np.roll(self.image_buffer, -1, axis=0)
        self.image_buffer[-1] = rgb_row
        self.rows_written += 1
        self._refresh()

    def _refresh(self) -> None:
        self._image_item.setImage(self.image_buffer, levels=(0, 255), autoLevels=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gui/test_waterfall_view.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bytt/gui/waterfall_view.py tests/gui/test_waterfall_view.py
git commit -m "feat: add pyqtgraph-based WaterfallView"
```

---

## Task 14: `bytt/gui/main_window.py` and `bytt/app.py` — wire it all together

**Files:**
- Create: `bytt/gui/main_window.py`
- Create: `bytt/app.py`
- Test: `tests/gui/test_main_window.py`

**Interfaces:**
- Consumes: `bytt.config.load_config/AppConfig` (Task 9), `bytt.net.live_client.LiveClient`
  (Task 10), `bytt.net.command_client.CommandClient` (Task 11),
  `bytt.net.playback_source.PlaybackSource` (Task 12), `bytt.gui.waterfall_view.WaterfallView`,
  `build_display_row` (Task 13), `bytt.processing.enhancement.EnhanceParams`, `enhance_pixels`
  (Task 6), `bytt.protocol.constants.SAMPLE_OFFSET`.
- Produces: `MainWindow(QMainWindow)`, `main() -> int` (the `bytt.app` entry point). This is the
  final task of this plan — after it, `python -m bytt.app` runs a connectable/playable skeleton.

- [ ] **Step 1: Write the failing test**

```python
# tests/gui/test_main_window.py
import pytest
from PySide6.QtWidgets import QApplication
from bytt.gui.main_window import MainWindow
from bytt.config import AppConfig

@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app

def test_main_window_starts_disconnected():
    window = MainWindow(AppConfig())
    assert window.statusBar().currentMessage() in ("", "disconnected")
    assert window.source is None

def test_main_window_has_connect_and_playback_actions():
    window = MainWindow(AppConfig())
    action_texts = {a.text() for a in window.menuBar().actions()[0].menu().actions()}
    assert any("Connect" in t for t in action_texts)
    assert any("Playback" in t or "Open" in t for t in action_texts)

def test_main_window_open_playback_creates_playback_source(tmp_path, monkeypatch):
    from bytt.protocol import constants as pc
    import struct
    header = bytearray(pc.BSF_FILE_HDR_SZ)
    bsf_path = tmp_path / "fixture.bsf"
    bsf_path.write_bytes(bytes(header))  # header-only: 0 pings, exercises the path safely

    window = MainWindow(AppConfig())
    window.open_playback_file(str(bsf_path))
    assert window.source is not None
    window.source.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mkdir -p tests/gui && pytest tests/gui/test_main_window.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bytt.gui.main_window'`

- [ ] **Step 3: Implement `main_window.py`**

This is new code — the original `BSFViewer` (source lines 977-3628) is a single pygame-loop class
handling input, rendering, and file loading together. This task replaces its GUI shell only;
`build_display_row`, `EnhanceParams`/`enhance_pixels`, and the two ping-source classes it wires
together were already ported in earlier tasks.

```python
"""Main application window: menu/toolbar/status bar, wires whichever ping
source (LiveClient or PlaybackSource) is active to the waterfall display."""
from PySide6.QtWidgets import QMainWindow, QFileDialog, QInputDialog
from PySide6.QtGui import QAction
from bytt.config import AppConfig
from bytt.net.live_client import LiveClient
from bytt.net.command_client import CommandClient
from bytt.net.playback_source import PlaybackSource
from bytt.gui.waterfall_view import WaterfallView, build_display_row
from bytt.processing.enhancement import EnhanceParams, enhance_pixels

_DEFAULT_ENHANCE_PARAMS = EnhanceParams(
    noise_idx=0, contrast_idx=0, agc=False, target_idx=0, target_ksize=9,
    shadow_enh=False, overlay=0, sharpen=False, gain=1.0, gamma=1.0,
    lut_idx=0, fast=True, hdr=False,
)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BYTT Sonar Viewer")
        self.config = config
        self.source = None
        self.command_client = CommandClient(self)
        self._interp_cache = {}

        self.waterfall = WaterfallView()
        self.setCentralWidget(self.waterfall)

        connect_menu = self.menuBar().addMenu("&Connect")
        connect_action = QAction("Connect to &Towfish...", self)
        connect_action.triggered.connect(self._prompt_connect_towfish)
        connect_menu.addAction(connect_action)

        open_action = QAction("&Open Playback File...", self)
        open_action.triggered.connect(self._prompt_open_playback)
        connect_menu.addAction(open_action)

        disconnect_action = QAction("&Disconnect", self)
        disconnect_action.triggered.connect(self.disconnect_source)
        connect_menu.addAction(disconnect_action)

        self.statusBar().showMessage("disconnected")

    def _prompt_connect_towfish(self):
        host, ok = QInputDialog.getText(self, "Connect to Towfish", "Towfish IP:",
                                         text=self.config.towfish_ip)
        if ok and host:
            self.connect_towfish(host, self.config.data_port, self.config.cmd_port)

    def _prompt_open_playback(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open .bsf recording", "", "*.bsf")
        if path:
            self.open_playback_file(path)

    def connect_towfish(self, host: str, data_port: int, cmd_port: int) -> None:
        self.disconnect_source()
        self.source = LiveClient(host, data_port, parent=self)
        self._wire_source()
        self.source.start()
        try:
            self.command_client.connect_to(host, cmd_port)
        except OSError as e:
            self.statusBar().showMessage(f"command channel failed: {e}")

    def open_playback_file(self, path: str) -> None:
        self.disconnect_source()
        self.source = PlaybackSource(path)
        self._wire_source()
        self.source.start()

    def disconnect_source(self) -> None:
        if self.source is not None:
            self.source.stop()
            self.source = None

    def _wire_source(self) -> None:
        self.source.status_changed.connect(self.statusBar().showMessage)
        self.source.ping_received.connect(self._on_ping_received)

    def _on_ping_received(self, port_raw, stbd_raw, meta) -> None:
        row_f32 = build_display_row(
            port_raw, stbd_raw, channel_w=self.waterfall.width,
            port_on=True, stbd_on=True, gap=8, interp_xs_cache=self._interp_cache,
        )
        row_2d = row_f32.reshape(1, -1)
        row_u8 = enhance_pixels(row_2d, _DEFAULT_ENHANCE_PARAMS)[0]
        self.waterfall.add_row(row_u8)
```

- [ ] **Step 4: Implement `bytt/app.py`**

```python
"""Entry point: python -m bytt.app"""
import sys
from PySide6.QtWidgets import QApplication
from bytt.config import load_config
from bytt.gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    config = load_config()
    window = MainWindow(config)
    window.resize(1200, 800)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/gui/test_main_window.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full test suite**

Run: `pytest -v`
Expected: PASS, all tests across all tasks green.

- [ ] **Step 7: Manually verify the app launches**

Run: `python -m bytt.app`
Expected: a window titled "BYTT Sonar Viewer" opens with a Connect menu (Connect to Towfish,
Open Playback File, Disconnect) and an empty waterfall view. Use "Open Playback File..." against
one of the project's `.bsf` recordings and confirm the status bar reports `connected — playback
(N pings)` and the waterfall starts filling with rows.

- [ ] **Step 8: Commit**

```bash
git add bytt/gui/main_window.py bytt/app.py tests/gui/test_main_window.py
git commit -m "feat: wire MainWindow + app entry point to live/playback sources"
```

---

## Self-Review Notes

- **Spec coverage:** project layout (Tasks 1-14 populate every module the spec's tree lists except
  `connect_dialog.py`, `controls_panel.py`, `waveform_view.py`, `gps_panel.py`, `shortcuts.py`,
  and `util/logging.py` — those are UI surfaces for GPS display, controls, and shortcuts that
  depend on decisions the spec explicitly deferred (control panel spec) or are pure polish with no
  architectural weight; `main_window.py`/`app.py` in Task 14 already prove the skeleton runs
  end-to-end, which was this plan's goal). GUI framework choice: Task 1 pins PySide6+pyqtgraph as
  dependencies, Tasks 13-14 use them. Networking design: Tasks 9-12 implement config, LiveClient,
  CommandClient, PlaybackSource exactly as specified, with the raw-packet-signal recording hook
  from the spec included in Task 10. Testing approach: every task follows unit-test-first; Task 14
  step 7 covers the spec's "verified by running against playback" approach.
- **Placeholder scan:** no TBD/TODO markers; two spots use `# <<< paste ... here` comments (Task
  6) because the underlying function bodies (`enhance_pixels`, `smart_hdr`) are 70-80 lines of
  already-correct, already-tested numpy/OpenCV math with no structural changes needed other than
  the three `self.` substitutions called out explicitly — copying them verbatim is the correct
  action, not a placeholder for undecided behavior.
- **Type consistency:** `ping_received` signal signature `(object, object, dict)` matches across
  `LiveClient` (Task 10), `PlaybackSource` (Task 12), and `MainWindow._on_ping_received`'s
  `(port_raw, stbd_raw, meta)` (Task 14). `EnhanceParams` field names match between Task 6's
  definition and Task 14's `_DEFAULT_ENHANCE_PARAMS`. `build_display_row` signature matches its
  definition (Task 13) and call site (Task 14).
