<p align="center">
  <img src="docs/banner.svg" alt="PurpleSector: live F1 22 telemetry and a race engineer that only speaks when the data backs it up" width="100%">
</p>

<p align="center">
  <img alt="F1 22" src="https://img.shields.io/badge/F1%2022-UDP%202022-A66CFF?style=flat-square">
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-1F232C?style=flat-square">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-1F232C?style=flat-square">
  <img alt="Runs offline" src="https://img.shields.io/badge/runs-100%25%20offline-39D98A?style=flat-square">
  <img alt="130 tests" src="https://img.shields.io/badge/tests-130%20passing-39D98A?style=flat-square">
</p>

<h3 align="center">Stop guessing where the lap went.<br>Put PurpleSector on your second monitor and find out.</h3>

---

You know the feeling. The lap felt mega, the timer says otherwise, and the game won't tell you why.

**PurpleSector** listens to F1 22's telemetry feed and turns it into a live pit-wall screen and a race engineer on your headset. It doesn't stop at lap times. It lines up your laps metre by metre, finds the corner where the time actually went, and tells you what was different there. In a race it tracks the gaps around you and your real fuel burn. It runs entirely on your PC, needs no account and no internet, and never makes things up.

<p align="center">
  <img src="docs/screenshots/live.png" alt="PurpleSector live dashboard in demo mode: speed, gear, inputs, live delta, timing-screen sectors, race engineer messages, tyre temperatures and live traces" width="100%">
  <br><sub>Live dashboard, shown here in Demo mode with simulated data</sub>
</p>

<p align="center">
  <img src="docs/screenshots/race.png" alt="Race row: track map with detected corner numbers and live car dots, race gaps to the cars ahead and behind with per-lap trend, and measured fuel and tyre use with the game's pit window" width="100%">
  <br><sub>Further down the Live page: track map, race gaps, fuel and tyres (Demo mode)</sub>
</p>

## Why it's different

| | |
|---|---|
| **⏱️ Live delta** | A big **−0.214** / **+0.380** next to your lap timer, measured against your recorded personal best on this track or your session best, whichever is faster. Green means you're gaining, red means you're losing. |
| **🏆 Real personal bests** | PurpleSector remembers your best lap on every track. Beat it and you hear *"Personal best!"*. Beat only today's best and you get a *session best*. It never mixes the two up. |
| **🧩 Ideal lap and consistency** | Your best three sectors added together: the lap you've *already* proven you can drive. Plus your lap-to-lap spread, so you know whether to chase pace or consistency. |
| **🟪 Timing-screen sectors** | Each sector lights up like the real timing tower: purple for your session best, yellow when you're slower. You can read it at a glance from the cockpit. |
| **🎧 A race engineer, not a chatterbox** | Short radio calls over offline Windows speech. Personal bests, invalidated laps, the sector that cost you, and the corner where you lost the most time. If the evidence isn't there, it stays quiet. |
| **📏 Metre-by-metre lap comparison** | Two laps are aligned on lap distance, not time, so the running delta shows exactly where you gained and lost. Pick any clean lap from any session on the same track as the reference. |
| **📊 Proper telemetry traces** | Speed, throttle, brake and steering scroll live. Compare laps with speed, throttle and brake overlays plus a delta line. |
| **🛞 Car status you can trust** | Tyre surface and inner temperatures, pressures, wear, brake temperatures, fuel, ERS and damage, exactly as the game reports them. Anything the game hasn't sent shows **n/a**, never a made-up number. |
| **💾 Every lap saved** | Completed laps and their telemetry go into a local SQLite database. Browse sessions, compare them, and export to CSV or JSON. |
| **🗺️ Track map with your corners** | The circuit draws itself from your own driving, with a live dot for every car. Corners are numbered from your speed trace, so *"lost 0.4 s at Corner 5"* replaces *"lost 0.4 s at 2200 m"*. After the session, the map turns green and red where you gained and lost time. |
| **⚔️ Race gaps** | The car ahead and the car behind, the gap to each, and whether it's closing or opening per lap. F1 22 doesn't send gaps, so PurpleSector measures them from every car's position trace. |
| **⛽ Fuel and tyres, measured** | Fuel burned and tyre wear added on every lap, read at the line. From that: laps of fuel left at your actual burn, your margin at the flag, and the game's own pit window and rejoin position. |
| **📻 Radio on request** | Press a button on your wheel (any F1 22 *UDP Action*) and the engineer reads out your delta, the gaps and your fuel margin. Nothing gets read out until you ask. |
| **🤝 Race a friend's lap** | Import a friend's exported session and pick one of their laps as your reference. You get the same metre-by-metre comparison against them. Their laps never count as your personal best. |
| **📈 Progress per track** | Your best lap per session over time, so you can see you're actually getting faster. |
| **🪟 Always-on-top overlay** | A small window over the game with delta, gear, gaps, fuel and the last radio call, for single-monitor setups. |
| **🎮 Demo mode** | A built-in simulator runs a 30-lap race on a fictional "Demo Ring" against three fictional AI cars, so you can explore everything before you launch the game. It is labelled as simulated everywhere. |

## Your engineer only says what it can prove

Every message comes with a confidence level and the evidence behind it, shown on screen. Here is the complete list of calls:

| Radio call | When you hear it | Based on |
|---|---|---|
| *"Personal best! 0.12 seconds quicker than your best recorded lap here."* | A **valid** lap beats every lap PurpleSector has recorded on this track, including earlier laps today | Game lap timing + your recorded laps |
| *"Session best by 0.31 seconds. Nice lap."* | A **valid** lap beats your previous best valid lap this session, but not your recorded personal best | Game lap timing |
| *"Lap 7, 1 25.9"* | Every completed lap | Game lap timing |
| *"Lap invalidated."* | The game flags your current lap invalid | Game lap-invalid flag |
| *"Sector 2 was 0.4 slower than your best."* | A valid lap's worst sector is ≥ 0.25 s off your best lap | Game sector times |
| *"Lost 0.4 at Corner 5 versus your best lap. Minimum speed 6 km/h lower than reference."* | A distance-aligned comparison is conclusive and a 200 m segment lost ≥ 0.15 s. The corner is named when the track map has one in that segment; otherwise it gives the distance. | Aligned telemetry. Input differences are reported as observations, not as proven causes. |
| *"Delta minus 0.2. Gap ahead 1.4, closing 0.3 a lap. Gap behind 0.9. Fuel plus 0.8 laps."* | You press your assigned wheel button or **Radio check** | Live delta, measured gaps, measured fuel burn (or the game's estimate, and it says which). Anything not available yet is left out. |
| *"Brake input is unusually abrupt compared with your previous laps."* | Your median brake build-up time drops below 60% of your recent laps' average | Brake samples from at least 2 earlier clean laps |
| *"Telemetry lost."* | Packets stop for 2 s after being connected | Packet timestamps |
| Pit entry/exit, session start/end | Only at *Detailed* verbosity | Game pit status and events |

It **won't** invent braking points, racing lines or tyre-grip estimates. Fuel projections come only from fuel you've actually burned on earlier laps. It won't call a personal best before the game confirms the lap time, and it won't repeat itself (each message type has a cooldown). Every threshold above can be changed on the Settings page.

## 60-second setup

**1. Launch PurpleSector** (Windows PowerShell, from the project folder):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
```

On the first run this creates a virtual environment in `.venv`, installs the dependencies, and opens the dashboard at **http://localhost:8501**.

**2. Turn on telemetry in F1 22.** Go to *Game Options → Settings → Telemetry Settings*:

| Option | Value |
|---|---|
| UDP Telemetry | **On** |
| UDP Broadcast Mode | Off |
| UDP IP Address | `127.0.0.1` |
| UDP Port | `20777` |
| UDP Send Rate | **60 Hz** |
| UDP Format | **2022** |

**3. Drive.** As soon as you're on track, the header pill turns green and reads **Connected**. Packets only flow while you're driving, not in menus.

**4. Optional: radio on request.** In F1 22's controller settings, assign a wheel button to one of the *UDP Action* slots. Then pick the same slot under *Settings → Radio on request* in PurpleSector.

> **No game handy?** Click **Demo** in the sidebar. The simulator starts lapping straight away, and you can pick a scenario: continuous laps, a lap completed in about 15 s, or a telemetry drop-out.

<details>
<summary><b>Manual install (if you prefer)</b></summary>

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run app.py
```

You don't need to activate the venv. If you want to and `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or keep using the launch script with `-ExecutionPolicy Bypass`. No administrator rights are needed.

Launch-script options: `-Port 8502` serves on another port, and `-NoBrowser` stops it opening a browser tab.
</details>

<details>
<summary><b>Playing on a console or a second PC?</b></summary>

F1 22 can send telemetry across your network. In PurpleSector, set *Settings → Listen address* to `0.0.0.0`. In the game, set *UDP IP Address* to the PC's local IP (for example `192.168.1.20`). Allow Python through Windows Firewall for UDP 20777 when prompted. *(This setup hasn't been tested.)*

To view the dashboard on a tablet, change `address = "localhost"` to `address = "0.0.0.0"` in `.streamlit/config.toml` and open `http://<pc-ip>:8501`. That exposes the dashboard to your whole network, so only do it on a network you trust.
</details>

## Find the time

<p align="center">
  <img src="docs/screenshots/analysis.png" alt="Session analysis: lap table, lap and sector deltas against a reference, running delta and speed, throttle and brake overlays aligned by lap distance" width="100%">
  <br><sub>Session analysis: any lap against the fastest clean lap on the same track</sub>
</p>

On the **Session analysis** page, pick a lap and a reference. You get:

- **Lap and sector deltas** straight from game timing, measured rather than estimated.
- **Running delta** along the lap, from the distance-aligned telemetry. The same idea powers the live delta on the dashboard: your current game lap time against the reference lap's time at the same lap distance.
- **Speed, throttle and brake overlays** on the same distance axis.
- **Segments to review**: the five 200 m segments where you lost the most time, labelled with the corner in each, and what was measurably different there (minimum speed, braking point, full-throttle point).
- **Where the time went**: the track map coloured by where you gained (green) and lost (red), plus every detected corner with its distance and minimum speed.
- **A friend's lap as the reference**: imported laps show up in the reference list with the driver's name.

<p align="center">
  <img src="docs/screenshots/map.png" alt="Track map coloured by the slope of the running delta: red where time was lost, green where it was gained, with numbered corners and a corner table" width="100%">
  <br><sub>Where the time went: red is time lost to the reference, green is time gained</sub>
</p>

If the data can't support a comparison, for example because a lap was only partly recorded, has a telemetry gap, or comes from another track, the page says so and explains why rather than showing a misleading chart.

<p align="center">
  <img src="docs/screenshots/history.png" alt="Session history: progress chart of best valid lap per session, recorded and imported sessions with laps, best valid lap and status" width="100%">
  <br><sub>History: progress on each track, every session and lap, export, import and delete-with-confirmation</sub>
</p>

### Race a friend

1. Your friend opens **History**, selects a session and presses **Export JSON**. They send you the file.
2. You open **History → Import a friend's lap**, choose the file and type their name.
3. On **Session analysis**, pick one of your laps on the same track. Their laps appear in the reference list as *"1:25.873 — lap 30, session #12, Sam"*.

Imported files are checked field by field before anything is stored. A file for another track simply won't show up as a reference for your laps.

### Always-on-top overlay

Press **Open overlay** in the sidebar, or run `.venv\Scripts\python overlay.py`. It's a small window with your delta, gear, gaps, fuel margin and the engineer's last call. Drag it anywhere; right-click to close. Windows only draws it over F1 22 when the game runs in **borderless** or **windowed** mode, because exclusive fullscreen covers every other window.

## How it works

```mermaid
flowchart LR
    G[F1 22<br>UDP :20777] --> R[Receiver thread<br>validate + decode]
    D[Demo simulator] -. labelled demo .-> S
    R --> S{{AppService.ingest}}
    S --> T[Telemetry state<br>bounded history]
    S --> L[Lap tracker<br>laps, sectors, flashbacks]
    S --> P[Gaps + track map<br>all 22 cars]
    L --> F[Fuel and tyre use<br>per lap]
    L --> C[Coach rules<br>evidence + cooldowns]
    P --> C
    C --> V[Voice thread<br>Windows SAPI]
    L --> W[DB writer thread<br>SQLite]
    T --> UI[Streamlit dashboard]
    W --> UI
    S --> O[Overlay endpoint<br>127.0.0.1 only]
```

- **Decoder**: built field by field from the F1 22 UDP specification. Every packet is checked for format, packet ID, full length and player-car index. Tests check each record against the spec's byte sizes (Car Telemetry 1347 bytes, Lap Data 972, Motion 1464, and so on) and against hand-computed byte offsets, including the pit-window bytes at 613–615 of the session packet and the *UDP Action* button bits.
- **Gaps without a gap field**: F1 22's lap data has no interval field. PurpleSector keeps a short distance/time trace for each car. The gap to the car ahead is how long ago it passed the spot where you are now. If it was never seen there, the gap shows *—* rather than an estimate.
- **Isolation**: the receiver, lap analysis, voice and database each run independently, so a speech failure or a database error never stops telemetry.
- **Bounded everything**: chart history, per-lap samples, the speech queue and the write queue all have hard limits, so a long session won't eat your RAM mid-race.
- **Flashbacks, restarts and out-laps** are handled. Rewound samples are dropped, a new session ID starts a new session, and in-laps and out-laps never become references.

## Your data stays on your PC

| What | Where |
|---|---|
| Sessions, laps and per-lap telemetry (60 Hz) | `data/purplesector.db` (SQLite) |
| Your settings | `data/settings.json` |
| Exports | `data/exports/purplesector_<source>_session<id>_<start>.csv` / `.json` |
| Logs | `data/logs/purplesector.log` |

Nothing is sent anywhere. The overlay reads from a small read-only endpoint bound to `127.0.0.1` (port 8777 by default), so other devices on your network can't reach it. Streamlit's usage statistics are turned off, and the dashboard only listens on `localhost`. A session is saved one completed lap at a time, so a crash costs you at most the lap in progress; the next launch marks that session *Interrupted* and keeps every finished lap. Demo sessions are only saved if you turn on *Save demo sessions*, and they are always labelled Demo.

## Settings

Everything is adjustable on the **Settings** page and validated before it's saved. An invalid value is never saved, and the app tells you exactly what's wrong. You can also set startup defaults in `.env` (see `.env.example`); values saved from the Settings page take precedence.

| Area | Settings |
|---|---|
| Receiver | Listen address, UDP port, telemetry format, Live/Demo source |
| Voice | On/off, speech rate, volume, minimum gap between messages, **Test voice** |
| Coaching | Verbosity (*Quiet*: personal bests, invalid laps, telemetry loss and calls you ask for; *Normal*; *Detailed*), minimum confidence, repeat cooldown, sector/segment loss thresholds, abrupt-brake ratio |
| Radio and overlay | Wheel button for radio on request (*UDP Action 1–12*, or off), overlay port |
| Dashboard | Trace history window, readout refresh rate |
| Recording | Record sessions, save demo sessions, data directory |

Changing the port or address restarts the receiver cleanly, and **Reset to safe defaults** is always one click away.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Stuck on **Waiting for telemetry** | Check the six F1 22 settings above. You must be on track, not in a menu. Run `.venv\Scripts\python scripts\diagnostics.py listen` with the app closed to see raw packets. |
| **Unsupported telemetry format** | Set *UDP Format* to **2022** in the game. |
| **Receiver error: port already in use** | Another telemetry tool (or a second PurpleSector) holds port 20777. Close it and press **Try again**, or change the port in both the game and Settings. |
| **Reconnecting** | Packets stopped: the game is paused, you're in a menu, or telemetry was switched off. It recovers on its own. |
| Overlay doesn't show over the game | Switch F1 22 to borderless or windowed mode. If **Open overlay** is greyed out, another program holds the overlay port: change it under Settings. |
| Wheel button does nothing | Check that the button is assigned to the same *UDP Action* number in F1 22 and under *Settings → Radio on request*. The sidebar's **Radio check** should work either way. |
| No voice | Press **Test voice** in the sidebar. If it shows *unavailable*, Windows speech isn't installed. Check *Settings → Time & language → Speech*. Everything else keeps working. |
| `running scripts is disabled` | Use `powershell -ExecutionPolicy Bypass -File scripts\run.ps1`. |

Check your setup any time with:

```powershell
.venv\Scripts\python scripts\diagnostics.py check      # Python, packages, port, voice, database
.venv\Scripts\python scripts\diagnostics.py listen     # what F1 22 is sending (close the app first)
.venv\Scripts\python scripts\diagnostics.py send       # synthetic loopback packets to test the live path
```

`send` encodes simulator frames as genuine F1 22-format UDP packets. The app recognises them, labels them *synthetic loopback test* and never records them.

## What's verified (and what isn't)

| | Status |
|---|---|
| Packet decoding against the F1 22 spec layout | ✅ Automated tests (byte sizes, hand-built offsets, malformed, truncated and wrong-format packets) |
| UDP receive → dashboard → coach → voice → database | ✅ Automated tests plus local runs with real UDP loopback packets. Latest run, all 22 cars per packet: 14,254 received, 14,254 decoded, 0 malformed. The sender's other 4,248 packets went out before the receiver was started. |
| Race gaps, track map, fuel and tyre use, pit window | ✅ Automated tests (constant-speed gap cases, flashbacks, tyre changes, corner detection) plus the demo race and the loopback run in the running app |
| Import, progress chart, radio check, wheel-button edge detection | ✅ Automated tests (including rejection of malformed import files) plus manual checks in the running app |
| Always-on-top overlay | ✅ Opened over other windows on Windows 11 at 200% display scaling, reading live demo data |
| Demo mode, lap analysis, history, export, delete, settings validation, crash recovery | ✅ Automated tests plus manual checks in the running app |
| Windows SAPI voice on a worker thread | ✅ Spoken on Windows 11 |
| *UDP Action* button bits and pit-window offsets | ✅ Checked against the F1 22 specification text. ⚠️ Not yet seen coming from the real game. |
| **A real F1 22 session** | ⚠️ **Not yet verified**: the game wasn't available during development. If something looks off with live data, `diagnostics.py listen` output plus `data/logs/purplesector.log` will pinpoint it. |

## Known limitations

- The lap in progress when a session ends or the app closes is not saved. Only completed laps are stored.
- Corner numbers are PurpleSector's own, from the speed minima of your reference lap. They usually line up with a circuit's turns, but chicanes and flat-out kinks may be merged or skipped.
- The track map needs one complete lap with position data. Laps recorded before this version have no positions, so they compare fine but don't draw a map.
- Race gaps only appear in race sessions. In practice and qualifying, positions are by lap time, not by track position.
- Fuel and tyre use is measured from your second lap onward: the first lap you join has nothing to compare against.
- In online sessions, other players with *restricted* UDP telemetry may send zeros, so their dots and names can be missing.
- Lap analysis covers your car only; the split-screen second player is ignored.
- Only the **2022** UDP format is decoded. If a newer F1 game offers a 2022-format option it may work, but that's untested.
- Voice uses Windows SAPI directly. `pyttsx3` was evaluated, but its `runAndWait()` never returned on a worker thread under Python 3.14.

## For developers

```powershell
.venv\Scripts\python -m pytest     # 130 tests, about 10 s, no game required
.venv\Scripts\ruff check .
```

```text
app.py               Streamlit entry point and navigation
service.py           AppService: owns every subsystem and the data flow
overlay.py           always-on-top overlay window and its localhost endpoint
config/              defaults, validated Settings, logging
telemetry/           packet models (spec layouts), decoder, UDP receiver, connection monitor, state
analysis/            lap tracker, comparison, statistics, coaching rules, gaps, track map, fuel/tyre strategy
voice/               bounded priority speech queue, SAPI worker
storage/             SQLite schema and recovery, repository, background writer
demo/                deterministic telemetry simulator
ui/                  dashboard, analysis, history and settings pages, shared components
scripts/             run.ps1 launcher, diagnostics.py
tests/               decoder, receiver, lap tracker, comparison, coach, storage, demo, pace, race, settings/voice/UI
```

<p align="center"><sub>PurpleSector is a fan-made tool and is not affiliated with Formula 1, EA or Codemasters. "F1" is a trademark of Formula One Licensing B.V.</sub></p>
