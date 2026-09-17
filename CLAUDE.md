# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

"You Are the Operator" — a museum exhibit at the Maine State Museum simulating a 1950s
telephone switchboard. A Raspberry Pi 4 drives a physical panel: visitors (mostly children)
hear an incoming call, then plug real patch cords into real jacks to connect caller to callee.
Audio and on-screen captions carry the story.

**It has never run publicly.** The build shipped in 2024, but the museum has been closed about
two years for a remodel, so no visitor has used it yet. The museum reopens in **October 2026**
and the final version is due **2026-10-01**. Current work is pre-opening polish: finishing
defects and making it survive unattended operation.

**This is the copy that runs.** Since 2026-09-15 `switchboard.service` starts sb-pyqt5 on boot,
so a change here takes effect the next time the Pi restarts. The Pi is on Don's bench, not in
the gallery — there is no live exhibit to take down, so testing is cheap; the deliverable is the
SD card he mails. `../sb-pyqt4` is the known-good fallback — never edit it; rolling back means
pointing the service at it again. The two copies will diverge.

Working notes live in Obsidian at
`_Work/Maine/Maine-Switchboard/`, starting from `hub-switchboard.md`.

## Where things run — you cannot run this app

Claude Code runs on the Mac, against the Pi's home mounted at `/Volumes/piswitch's home`.
The app needs real I²C hardware and an X display, so **it cannot be run or tested from here.**
Don't offer to run it, and don't claim a change is verified without a hardware run by Don.

On the Pi (`ssh piswitch@192.168.50.186`):

```bash
cd ~/Apps/sb-pyqt5/app
source ../venv/bin/activate
python control.py
```

Two path facts that make cwd matter:

- Audio is **outside the repo** at `/home/piswitch/Apps/sb-audio/`, hard-coded in `model.py`.
- Captions load by relative path (`captions/<type>/<file>.srt`), so cwd must be `app/`.

In production the app is started by systemd (`switchboard.service`); see
`working-auto-start.md` in Obsidian for the service commands.

## Architecture

Two live modules, talking only through Qt signals.

| File | Owns |
|---|---|
| `app/control.py` | Qt window, both MCP23017s, the GPIO interrupt, all timers, LEDs, caption display |
| `app/model.py` | Call state machine, VLC playback, the JSON data |

Signals out of `control.py` into `model.py`: `plugInToHandle` → `handlePlugIn`,
`unPlugToHandle` → `handleUnPlug`, `dualUnplugToHandle` → `handleDualUnplug`,
plus `startSim` calling `model.handleStart` directly.

Signals out of `model.py` into `control.py`: `displayTextSignal`, `setLEDSignal`,
`blinkerStart`/`blinkerStop`, `displayCaptionSignal`/`stopCaptionSignal`, `stopSimSignal`.

## Two rules that must not be broken

**1. All GPIO stays in `control.py`.** `model.py` never touches a pin — it asks for LED changes
by signal. This was learned the hard way; the note sits at `control.py:151`.

**2. Callbacks from foreign threads may only emit a signal.** GPIO interrupt callbacks and VLC
`MediaPlayerEndReached` callbacks arrive on threads Qt doesn't own. Doing real work there —
touching widgets, starting timers, mutating state — is what caused the freezes and crashes
fixed through 2025. Every such callback is paired with a main-thread `handle*` slot:

| Callback (foreign thread) | Main-thread slot |
|---|---|
| `checkPin` | `handleGpioInterrupt` |
| `endOperatorOnlyHello` | `handleEndOperatorOnly` |
| `playFullConvo` | `handlePlayFullConvo` |
| `playFullWrongNum` | `handlePlayFullWrongNum` |
| `setCallCompleted` | `handleSetCallCompleted` |
| `startPlayRequestCorrect` | `handleStartPlayRequestCorrect` |
| `restartOnTimeout` | `handleRestartOnTimeout` |
| `restartOnEndTimeout` | `handleRestartOnEndTimeout` |

Keep the pattern when adding anything new on either path. Collapsing one of these pairs
"for simplicity" reintroduces the freezes.

## Hardware map

Raspberry Pi 4, two Adafruit MCP23017 I²C bonnets:

| Address | Pins | Use |
|---|---|---|
| `0x20` | 0–11 | Jack tips — grounded (`False`) when a plug is in |
| `0x20` | 12 | Stop button |
| `0x20` | 13 | Start button (fires on `False`) |
| `0x21` | 0–11 | LEDs, one per jack, outputs |

Interrupts: all 16 pins on `0x20`, on any change, open-drain and mirrored, into **BCM 17**
with `GPIO.BOTH` and a 50 ms hardware bouncetime.

Timings, all tuned against real visitor behavior — change only with a hardware test:

- **300 ms** bounce timer before reading a pin. Below 200 ms the second line fails to register.
- **150 ms** delay before clearing `just_checked`, in case a plug is wiggled.
- **600 ms** LED blink interval for an incoming call.
- **500 ms** window for treating two unplugs as one dual-unplug.
- **Misuse catcher**: 4 plug-ins within 12 s stops the sim and asks the visitor to start over,
  calmly, one thing at a time.

## Data model

`app/persons.json` — one entry per person. **Array index = jack index = LED index.** Indices
0–11 are real people; 15 is the Operator sentinel (used as `callee` for operator-only calls);
12–14 and 16 are placeholders. Each carries the audio file and text for answering as a wrong
number.

`app/conversations.json` — 9 calls, in play order. `caller.index` and `callee.index` point into
`persons.json`. `okTimeHello` and `okTimeConvo` are millisecond thresholds: unplug before them
and the call replays, after them and the sim advances.

Two hard-coded literals travel with this data and must change together with it:

- `model.py:172` — `if (self.currConvo < 9)` is the call count.
- `model.py:203` and `model.py:587` — `_currConvo == 3 or _currConvo == 8` marks the
  operator-only calls (Tressa, Chief Burns), which have no callee and end after the hello.

Captions are SRT files in `app/captions/hello/` and `app/captions/convo/`, named to match the
`helloFile` / `convoFile` values.

## Files that are dead

`app/control3.py`, `app/model3.py`, `app/control-complex.py`, `app/model-complex.py` are frozen
history — an earlier pre-anti-chaos version and the older multi-line `phoneLines` design.
Nothing imports them. Never edit them, and never cite them as current behavior.

Only `control.py` and `model.py` are live.

## The stack is frozen, deliberately

Raspberry Pi OS 11 (Bullseye) · Python 3.9.2 · PyQt5 from apt · python-vlc 3.0.18122 ·
RPi.GPIO 0.7.1 · Adafruit Blinka 8.20.1.

- **RPi.GPIO pins this to a Pi 4.** It does not work on a Pi 5, which uses a different GPIO chip.
- **PyQt5 is not pip-installable here.** It comes from `apt install python3-pyqt5` and is
  symlinked into the venv — see `pyqt5-install.md`. `requirements.txt` therefore has no PyQt5 line.
- Don't propose PyQt6, gpiozero, lgpio, or an OS upgrade as part of unrelated work. The exhibit's
  job is to keep running unattended; a stack move is its own project, for SD-card replacement time.

## Working style

- The visitors are children doing unpredictable things. The defensive state handling, the
  ghost-unplug and dual-unplug detection, and the misuse catcher all exist because of specific
  observed failures. Don't simplify them away as redundant.
- The heavy `print()` tracing is intentional debugging scaffolding — the app is diagnosed by
  reading its console output on the Pi. Match the style when adding code.
- Minimum change that solves the problem. Touch only what the task requires.
