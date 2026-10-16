# MASTER PROMPT: Build SimRacer Coach — A Real-Time F1 22 Telemetry Dashboard & AI Voice Racing Coach

## 1. Your Role

Act as a **principal software engineer, senior Python architect, motorsport telemetry engineer, data visualization specialist, Windows desktop integration engineer, and AI product designer**.

Your task is to independently design, implement, test, debug, and document a complete application named **SimRacer Coach**.

Do not merely provide a tutorial, architecture proposal, pseudocode, or a collection of code snippets. **Build the actual working project in the current repository.**

Work autonomously. Inspect the environment, make sensible engineering decisions, create all required files, install or document dependencies appropriately, execute the application checks, diagnose errors, and fix them before finishing.

The objective is to deliver a polished, functional, locally running application for **F1 22 on Windows PC**, designed to sit on a second monitor while the user races.

### Critical operating principles

- Build the complete project in this session rather than stopping after the first milestone.
- Do not ask me to approve routine implementation decisions.
- Do not overwrite unrelated files or existing project work without inspecting them first.
- Never fabricate telemetry, lap times, track conditions, personal bests, or coaching conclusions in live mode.
- Never claim that a feature works unless its implementation and relevant tests support that claim.
- Prefer a reliable, complete core application over numerous unfinished features.
- If a feature cannot be implemented reliably with the available information, provide a clearly labeled limitation and a functional fallback.
- Keep the telemetry receiver, dashboard rendering, analysis, persistence, and voice output independent so that a failure in one subsystem does not unnecessarily crash the others.

---

## 2. Product Vision

SimRacer Coach is a real-time racing companion that transforms F1 22 telemetry into actionable driving insights.

It should feel like a combination of:

- A professional motorsport telemetry workstation.
- A clean, high-performance sim-racing dashboard.
- A real-time race engineer sitting beside the driver.
- A session analysis tool that helps the driver improve across multiple laps and sessions.

The application must receive telemetry directly from F1 22 through its UDP interface, visualize live vehicle data, detect meaningful events, record sessions, analyze lap performance, and optionally deliver short spoken coaching messages.

It must be designed for practical use during gameplay, not simply for demonstrating a concept.

### Primary user workflow

1. The user launches SimRacer Coach.
2. The application opens in a desktop browser or dedicated dashboard window.
3. The user starts F1 22 and enables UDP telemetry.
4. SimRacer Coach detects incoming packets and displays a clear connection status.
5. The user drives while the dashboard updates live.
6. The application records available telemetry and lap information.
7. The coach speaks only when it has a useful, sufficiently supported observation.
8. After the session, the user reviews lap performance, driving inputs, and session statistics.

The application must also include a simulation/demo mode so its interface can be explored and tested without launching F1 22.

---

## 3. Target Environment and Technology Stack

Build for the following target:

- Operating system: Windows 10/11, 64-bit.
- Game: F1 22 PC.
- Primary language: Python 3.11 or another compatible Python version if the environment requires it.
- Dashboard: Streamlit.
- Interactive charts: Plotly.
- Data processing: Python standard library, NumPy, and Pandas where useful.
- UDP networking: Python `socket` and `struct`.
- Voice output: `pyttsx3` or another reliable offline Windows TTS option.
- Persistence: SQLite for session metadata and structured records, plus CSV or compressed JSON/JSONL for telemetry exports where appropriate.
- Testing: `pytest`.
- Configuration: environment variables and a validated local configuration file where appropriate.

Use stable, mutually compatible dependency versions. Specify sensible version ranges or tested pins in `requirements.txt`.

Avoid unnecessary frameworks, external APIs, cloud services, paid services, API keys, or internet dependencies at runtime.

Do not use an LLM API for core coaching functionality. The core application must work locally and offline. If an optional future AI integration is added, it must be disabled by default and must not be required for core operation.

### Architecture requirements

Separate these concerns into independent modules:

1. UDP packet reception.
2. F1 22 packet decoding.
3. Normalized telemetry models.
4. Connection monitoring and session state.
5. Live dashboard state and visualization.
6. Lap and sector analysis.
7. Coaching rules and event detection.
8. Voice queue and speech output.
9. Session recording and persistence.
10. Demo telemetry generation.
11. Configuration and logging.
12. Automated tests.

Do not put the entire application into a single Python file.

---

## 4. First Action: Inspect the Environment

Before writing code:

1. Inspect the current working directory and repository.
2. Identify existing files, Python version, operating system, available dependencies, and project conventions.
3. Preserve useful existing work.
4. Identify any incompatibilities with the target stack.
5. Create a concise internal implementation plan.
6. Begin implementation immediately.

Do not stop after presenting the plan.

If the repository is empty, initialize the complete project structure. If a virtual environment already exists, inspect and reuse it where appropriate. Do not install packages globally without a good reason.

If a dependency cannot be installed because of network or permission restrictions, continue with the best practical alternative and document the limitation.

---

## 5. F1 22 Telemetry Integration

This is a core requirement. The live telemetry path must be implemented carefully.

### Game configuration

Support the F1 22 UDP telemetry settings:

- UDP telemetry: enabled.
- UDP format: 2022.
- Destination IP: `127.0.0.1`.
- Destination port: `20777`.
- Send rate: preferably 60 Hz, where supported.

Explain these settings in the application's connection/setup screen and README.

The application must not require the user to modify game files manually.

### UDP receiver

Implement a dedicated receiver that:

- Binds to the configured UDP port.
- Receives packets continuously on a background thread.
- Uses a configurable IP and port.
- Handles socket timeouts and shutdown cleanly.
- Validates packet lengths before reading fields.
- Decodes only packet structures whose layout is known and verified.
- Filters unsupported packet formats.
- Identifies the player vehicle using the correct player-car index in the packet header.
- Tracks last-packet time, packets received, packet types, malformed packets, and connection health.
- Recovers gracefully from malformed packets and temporary interruptions.
- Avoids blocking the dashboard, analysis engine, database, or voice subsystem.
- Uses a thread-safe latest-state store and a bounded queue or another appropriate mechanism for recording telemetry without unbounded memory growth.
- Supports orderly shutdown and restart without leaving the port or worker threads in a bad state.

Do not create a new UDP socket every time the dashboard reruns.

### Packet decoding

Use the **F1 22 / 2022 UDP telemetry specification** to verify packet headers, packet IDs, field types, record sizes, and offsets.

Implement decoders for the packet types required by the product, prioritizing:

- Car telemetry.
- Lap data.
- Session data.
- Event data.
- Participant information.
- Car status.
- Car damage.
- Session history when needed and supported.

Start with the packets required for live speed, throttle, brake, steering, gear, RPM, lap timing, and session status. Expand the decoder systematically.

Use named constants and explicit packet models rather than unexplained numeric offsets throughout the application.

**Important:** Do not assume every packet type has the same record structure. Account for packet-specific layouts, variable arrays, the correct player-car index, and the exact 2022 format.

For each supported packet:

1. Validate the packet format.
2. Validate the minimum required packet length.
3. Decode using the specification's field sizes and byte order.
4. Reject invalid or incomplete data safely.
5. Normalize the data into a documented internal model.
6. Add unit tests using synthetic packet bytes or known sample packets.

Do not silently interpret unsupported packets as valid telemetry.

### Telemetry fields

Where provided by the game, capture and normalize:

- Speed in km/h.
- Throttle from 0 to 100%.
- Brake from 0 to 100%.
- Steering input, with its documented raw or normalized scale.
- Current gear.
- Engine RPM.
- DRS status.
- Engine temperature.
- Tire temperatures and pressures when available.
- Fuel remaining and fuel usage estimates when available.
- Current lap and last lap times.
- Best lap and sector times when provided.
- Lap distance and total distance where available.
- Current position and session type when available.
- Tire compound and wear information when available.
- Damage and car status when available.
- Session flags and event information when available.

Represent unavailable values as unavailable, not as believable-looking fabricated numbers.

Keep units explicit in both models and the UI.

### Connection state

Implement meaningful states such as:

- Disconnected.
- Waiting for telemetry.
- Connected.
- Reconnecting.
- Unsupported telemetry format.
- Receiver error.

Use packet timestamps to determine whether the connection is fresh. Do not declare the game connected just because the UDP socket has been opened.

Show the last packet time and basic receiver diagnostics in a collapsible diagnostics panel.

---

## 6. Real-Time Dashboard

Build a polished, information-dense but readable interface for a second monitor.

### Visual direction

Use a professional motorsport telemetry aesthetic:

- Dark, low-glare interface.
- High-contrast text and numerical readouts.
- Restrained accent colors.
- Clear visual hierarchy.
- Compact cards and efficient use of horizontal space.
- Subtle borders and dividers.
- Minimal unnecessary decoration.
- Consistent spacing and typography.
- Responsive layout for different monitor resolutions.
- Large, easily readable values during gameplay.

Use a consistent theme rather than changing colors randomly. Use colors semantically for warnings, positive events, and neutral telemetry.

Do not add excessive animations, distracting backgrounds, giant empty cards, or decorative elements that reduce the information visible during a race.

Make the dashboard look intentionally designed rather than like an unstyled Streamlit prototype.

### Main dashboard

Build the following sections.

#### A. Header and connection bar

Display:

- SimRacer Coach branding.
- Current operating mode: Live or Demo.
- Game telemetry connection status.
- Session type, when available.
- Current lap.
- Current position, when available.
- Voice coach status.
- Session recording status.
- Settings access.

Make connection problems immediately obvious without covering the dashboard in warning messages.

#### B. Live telemetry panel

Display prominent live values for:

- Speed.
- RPM.
- Gear.
- Throttle.
- Brake.
- Steering input.
- DRS state.
- Lap time.
- Best lap time, when available.

Use appropriate gauges, bars, or compact visualizations.

The speed readout should be especially prominent. Throttle and brake should be easy to distinguish. Gear and RPM must remain readable at a glance.

Use genuine incoming values in Live mode.

#### C. Throttle and brake visualization

Provide a clear visualization of throttle and brake over time.

- Use separate visual encodings.
- Show their percentage scales.
- Retain a configurable recent-history window.
- Avoid plotting unbounded data.
- Handle missing data without misleading interpolation.
- Keep updates smooth without needlessly redrawing the entire application.

#### D. Steering and vehicle telemetry

Display a steering input visualization and useful additional telemetry that is genuinely available.

Do not imply that raw steering input is an actual steering-wheel angle unless the underlying field represents that quantity.

Show unavailable fields as unavailable.

#### E. Lap and sector panel

Display available current, previous, and best lap times.

Include sector times, personal-best indicators, and session progress when supported by incoming telemetry.

Handle session resets, out-laps, incomplete laps, invalid laps, and missing sector information without inventing results.

#### F. Live coaching panel

Show:

- The most recent coaching message.
- Time of the message.
- Coaching category.
- Confidence or evidence level.
- A short reason explaining the observation.

Keep the panel compact. A useful observation is more valuable than a constant stream of generic tips.

#### G. Session statistics

Show metrics that can be calculated reliably from collected data, such as:

- Laps completed.
- Best lap time.
- Average speed over a clearly defined interval.
- Peak speed.
- Throttle and brake usage statistics.
- Session duration.
- Available tire or damage information.

Distinguish measured values from estimates.

#### H. Diagnostics panel

Include a collapsible developer panel containing:

- UDP host and port.
- Last packet timestamp.
- Packet rate.
- Packets received and malformed.
- Packet types observed.
- Decoder errors.
- Recording state.
- Recent application errors.

This panel should make troubleshooting possible without modifying source code.

### Dashboard usability

- Provide clear navigation between Live, Session Analysis, History, and Settings.
- Make important controls accessible without excessive scrolling.
- Ensure all buttons work.
- Provide loading, empty, disconnected, and error states.
- Avoid unnecessary browser refreshes.
- Keep rendering responsive even when packets arrive at high frequency.
- Do not block the UI thread while speaking, saving data, or decoding large session files.

Use Streamlit's supported state and fragment/refresh mechanisms where appropriate for the installed version. If a feature is incompatible with the chosen version, use a tested alternative rather than guessing at APIs.

---

## 7. Live Voice Race Engineer

Implement an offline voice coach that delivers short, useful messages during gameplay.

### Speech implementation

Use `pyttsx3` or a similarly appropriate offline TTS solution for Windows.

Requirements:

- Initialize speech in a dedicated worker thread.
- Use a thread-safe speech queue.
- Keep UDP reception and dashboard rendering independent from TTS.
- Avoid blocking calls from the UI thread.
- Provide Enable Voice, Disable Voice, and Test Voice controls.
- Provide adjustable speech rate and volume.
- Support a configurable minimum interval between messages.
- Avoid queueing an unlimited number of outdated messages.
- Discard stale low-priority messages where appropriate.
- Handle missing speech engines or unavailable voices gracefully.
- Shut down the worker cleanly.

The application must remain fully usable if voice synthesis fails.

### Coaching philosophy

**The coach must be evidence-driven, context-aware, concise, and non-intrusive.**

Implement a deterministic rule-based coaching engine first. It should not need an internet connection or external AI service.

Potential coaching events include:

- A new personal best has been confirmed.
- A lap has been completed.
- The player has entered a pit or changed session state, where detectable.
- A meaningful change in speed or driving inputs has been detected.
- A sustained driving-input pattern may warrant attention.
- The session has ended or recording has stopped.

Example voice messages:

- "Personal best. Nice lap."
- "You've completed the lap. Check the sector breakdown."
- "Brake input is unusually abrupt compared with your previous laps."
- "Your throttle application was later than on your reference lap in this section."
- "Session saved."

These are examples, not hardcoded claims to speak regardless of evidence.

### Coaching safeguards

1. Do not claim the player lost time in a sector unless comparable sector timing supports the conclusion.
2. Do not say "you are too slow here" without a valid comparison or a defensible rule.
3. Do not give precise braking-point advice without suitable distance, speed, corner, and reference data.
4. Do not claim a driving line is optimal without the required track-position and reference-line information.
5. Do not fabricate tire grip, tire wear, fuel predictions, damage, or weather conditions.
6. Do not announce a personal best before the timing data confirms it.
7. Do not repeat the same message continuously.
8. Prioritize urgent or important messages over routine observations.
9. Support coaching cooldowns, minimum confidence thresholds, and user-configurable verbosity.
10. If there is insufficient evidence, say nothing.

### Coaching categories

Use a structured event model with categories such as:

- Lap performance.
- Driving inputs.
- Braking.
- Throttle.
- Session events.
- Personal records.
- System notifications.

Each event should contain a message, timestamp, priority, evidence, and confidence where appropriate.

Use rule thresholds that are configurable and testable.

### Important distinction

An initial working coach should focus on **reliable telemetry-based feedback**, not pretend to be a professional engineer with access to unavailable information.

More advanced corner-by-corner coaching must be built on recorded laps, comparable reference laps, track-position alignment, and validated analysis.

---

## 8. Lap Analysis and Performance Comparison

Implement a reliable first version of session and lap analysis.

### Lap recording

Record telemetry samples with timestamps and relevant session identifiers.

Handle:

- Session start and end.
- Lap boundaries.
- Incomplete laps.
- Invalid or missing timing data.
- Duplicate packets.
- Session restarts.
- Gaps in telemetry.
- Interrupted application shutdowns.

Use appropriate identifiers so samples from different sessions are never mixed accidentally.

### Lap comparison

Where the available telemetry supports it, compare:

- Lap times.
- Sector times.
- Speed profiles.
- Throttle traces.
- Brake traces.
- Steering input.
- Lap distance.
- Other meaningful fields.

For time-aligned comparisons, implement a documented alignment method. If lap distance is available and reliable, use it for spatial comparison. Otherwise use an appropriate alternative and label its limitations.

Calculate a time delta only when the alignment and timing basis make the result meaningful.

### Time-loss analysis

Implement the strongest reliable comparison possible from the data collected.

Potential outputs:

- Which sector was slower.
- Where speed profiles diverged.
- Where throttle or braking patterns differed.
- Which segments warrant review.
- Whether a comparison is inconclusive.

Do not claim that a particular action caused time loss simply because it occurred at the same time.

Clearly distinguish correlation, measured time differences, and coaching hypotheses.

### Reference laps

Allow the user to select a valid reference lap from recorded sessions.

Support personal-best references where the recorded data permits them.

If the application does not have enough data to generate a meaningful reference comparison, explain why and provide a helpful empty state.

Do not fabricate an ideal racing line or optimal lap.

---

## 9. Session Storage and History

Implement persistent session storage using SQLite and appropriately structured telemetry files.

Requirements:

- Create the database and schema automatically.
- Use safe database access patterns.
- Handle concurrent writes appropriately.
- Keep database operations off the real-time receive path.
- Avoid unbounded in-memory session buffers.
- Store session metadata, lap records, sector records where available, and relevant statistics.
- Associate telemetry samples with the correct session and lap.
- Support clean shutdown and recovery from incomplete sessions.
- Prevent accidental overwriting of existing sessions.
- Use stable, understandable filenames.
- Include timestamps and a schema/version strategy.

Provide session-history screens where the user can:

- View recorded sessions.
- Review lap and sector statistics.
- Select a session for analysis.
- Compare two compatible laps.
- Export data to CSV or JSON where appropriate.
- Delete a session with an explicit confirmation.

Document what is stored and where.

Do not collect or transmit data externally.

---

## 10. Demo Mode and Telemetry Simulator

The entire UI and most application functionality must be testable without F1 22.

Build a demo mode that generates deterministic, realistic-looking synthetic telemetry for interface development and testing.

The simulator should model plausible changes in:

- Speed.
- Throttle.
- Brake.
- Steering.
- Gear.
- RPM.
- Lap timing.
- Lap completion events.
- Session states.

The generated signals should vary in a coherent way rather than simply using random numbers.

Provide:

- A clear Demo Mode label.
- Start and stop controls.
- A configurable simulation speed if practical.
- Repeatable scenarios for testing.
- A scenario that produces a completed lap.
- A scenario that simulates a telemetry interruption.

**Strict separation:** Demo telemetry must never be mixed with live telemetry or persisted as a real game session without an explicit, clearly labeled action.

No synthetic data may be presented as real F1 22 telemetry.

---

## 11. Configuration and Settings

Build a Settings page with practical controls:

- UDP host and port.
- Telemetry format.
- Live/demo mode.
- Voice enabled/disabled.
- Voice rate and volume.
- Coaching verbosity.
- Coaching cooldown.
- Telemetry history window.
- Recording enabled/disabled.
- Data directory.
- Theme preferences if supported.
- Reset to safe defaults.

Validate user input and explain invalid settings.

Do not allow unsafe or invalid configuration to crash the application.

Changes that require restarting the UDP receiver should be applied through a controlled restart process.

Keep defaults appropriate for a local F1 22 installation.

Do not require the user to edit Python source code to change routine settings.

---

## 12. Project Structure

Use a modular structure similar to the following, adjusting only when there is a clear engineering reason:

```text
simracer-coach/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
├── .env.example
├── config/
│   ├── __init__.py
│   ├── settings.py
│   └── defaults.py
├── telemetry/
│   ├── __init__.py
│   ├── receiver.py
│   ├── decoder.py
│   ├── packet_models.py
│   ├── state.py
│   └── connection.py
├── analysis/
│   ├── __init__.py
│   ├── lap_tracker.py
│   ├── lap_comparison.py
│   ├── statistics.py
│   └── coach.py
├── voice/
│   ├── __init__.py
│   ├── speaker.py
│   └── queue_manager.py
├── storage/
│   ├── __init__.py
│   ├── database.py
│   ├── session_repository.py
│   └── telemetry_writer.py
├── demo/
│   ├── __init__.py
│   └── simulator.py
├── ui/
│   ├── __init__.py
│   ├── dashboard.py
│   ├── analysis_page.py
│   ├── history_page.py
│   ├── settings_page.py
│   └── components.py
├── tests/
│   ├── test_decoder.py
│   ├── test_receiver.py
│   ├── test_lap_tracker.py
│   ├── test_lap_comparison.py
│   ├── test_coach.py
│   ├── test_storage.py
│   └── test_demo.py
├── scripts/
│   ├── run.ps1
│   └── diagnostics.py
└── data/
    └── .gitkeep
```

Use a single, well-defined source of configuration and a single shared application state service.

Do not create empty modules merely to make the folder tree look complete. Every module must have a clear purpose and appropriate implementation.

---

## 13. Reliability, Performance, and Resource Management

Treat the application as a continuously running local telemetry system.

### Performance

- Handle incoming UDP packets without unnecessary blocking.
- Avoid excessive UI refresh rates.
- Use bounded buffers and configurable retention limits.
- Limit chart history to a reasonable window.
- Avoid repeatedly rebuilding large datasets.
- Keep plotting and database work off the packet receiver.
- Use appropriate synchronization for shared state.
- Avoid unbounded queues.
- Keep resource consumption reasonable on a gaming PC.

### Reliability

- Handle socket failures.
- Handle malformed or unsupported packets.
- Handle missing optional fields.
- Handle database failures without silently losing track of the error.
- Handle TTS initialization failures.
- Handle empty session history.
- Handle invalid user settings.
- Handle demo mode and live mode transitions.
- Handle application shutdown cleanly.
- Avoid thread leaks, duplicate workers, and port conflicts.
- Provide useful logs without flooding the console.

### Windows compatibility

- Use Windows-compatible paths and commands.
- Do not assume Unix-only utilities exist.
- Use proper path handling with `pathlib`.
- Ensure scripts work from the project root.
- Document PowerShell activation and execution-policy issues.
- Provide a launch script that does not assume the virtual environment is already activated.
- Never require administrator privileges unless an unavoidable, documented requirement exists.

---

## 14. Testing Requirements

Create and run a real automated test suite.

Do not treat merely importing the main file as sufficient testing.

### Unit tests

Test:

- Valid packet-header decoding.
- Packet-format rejection.
- Packet-ID routing.
- Packet-length validation.
- Player-car selection.
- Correct decoding of telemetry values.
- Malformed and truncated packets.
- Missing and invalid data.
- Lap boundary detection.
- Personal-best detection.
- Sector handling.
- Lap comparison alignment.
- Coaching rule thresholds.
- Coaching cooldowns and duplicate suppression.
- Demo-mode event generation.
- Database creation and persistence.
- CSV/JSON export.
- Configuration validation.

Use synthetic packet bytes constructed according to the actual F1 22 specification. Do not rely on real game telemetry being available for unit tests.

### Integration tests

Where practical, test:

- A local UDP sender transmitting synthetic valid packets to the receiver.
- Receiver-to-state updates.
- State-to-dashboard data preparation.
- Demo telemetry flowing through the same normalized data interface as live telemetry.
- Recording a session and loading it again.
- Voice queue behavior using a mocked TTS engine.
- Safe shutdown and receiver restart.

Automated tests must not require F1 22 to be installed or running.

### Code quality

Run the available checks for:

- Syntax and import errors.
- Test failures.
- Incorrect dependencies.
- Obvious type and data-model inconsistencies.
- Missing files.
- Incorrect relative paths.
- Invalid configuration defaults.
- UI callbacks that do not work.
- Background threads that do not stop.
- Data that is mislabeled or falsely presented as live.

Use appropriate lightweight tools such as Ruff and pytest if they can be installed or are already available. Do not let an optional linting dependency prevent the core application from running.

---

## 15. Definition of Done

The project is complete only when all applicable requirements below are satisfied.

### Application

- [ ] The project structure is complete.
- [ ] Dependencies are documented.
- [ ] The application launches using the documented Windows workflow.
- [ ] The dashboard renders without fatal errors.
- [ ] Demo mode works without F1 22.
- [ ] Live UDP reception is implemented.
- [ ] Packet decoding follows the verified F1 22 2022 format.
- [ ] Connection status reflects actual incoming packets.
- [ ] Live telemetry is normalized and displayed with correct units.
- [ ] Unsupported or missing data is handled honestly.
- [ ] Charts update without unbounded memory growth.
- [ ] Voice output works when supported and enabled.
- [ ] Voice failures do not crash the application.
- [ ] Coaching events have evidence and appropriate cooldowns.
- [ ] Lap and session data can be recorded.
- [ ] Stored sessions can be reopened.
- [ ] Lap analysis works with valid recorded data.
- [ ] Export functionality works.
- [ ] Settings can be changed and validated.
- [ ] Shutdown and restart work cleanly.
- [ ] Automated tests pass.
- [ ] README instructions match the actual implementation.

### Honesty requirements

The application must not label demo data as live.

It must not report invented personal bests, lap deltas, sector times, or driving recommendations.

It must not claim real-world telemetry integration has been verified if the game was unavailable for testing.

Clearly state any feature that is implemented but not verified against a running F1 22 session.

---

## 16. Required Implementation Workflow

Follow this workflow internally, but continue all the way to completion without asking for permission between phases.

### Phase 1 — Foundation

- Inspect the repository and environment.
- Establish project configuration.
- Implement normalized telemetry models.
- Implement verified packet decoding.
- Implement the background UDP receiver.
- Add receiver diagnostics.
- Add packet-decoder unit tests.

### Phase 2 — Functional dashboard

- Build the Streamlit dashboard.
- Implement live state management.
- Add telemetry cards, gauges, and charts.
- Add connection and error states.
- Add navigation and settings.
- Verify that the application launches.

### Phase 3 — Demo mode

- Implement a deterministic telemetry simulator.
- Connect it to the same state interface as live telemetry.
- Add scenario controls.
- Verify the entire UI can be demonstrated without the game.

### Phase 4 — Voice coach

- Implement the background TTS worker.
- Implement the speech queue and cooldown system.
- Implement evidence-based coaching rules.
- Add voice controls and tests.
- Verify that speech failures do not interrupt telemetry.

### Phase 5 — Recording and analysis

- Implement session persistence.
- Implement lap tracking and statistics.
- Implement reference-lap selection and valid comparisons.
- Add history and export functionality.
- Add tests for persistence and analysis.

### Phase 6 — Verification and hardening

- Run all automated tests.
- Launch the dashboard and fix runtime errors.
- Verify demo mode end to end.
- Verify local UDP reception using synthetic packets.
- Check resource management and shutdown.
- Check the Windows launch script.
- Review the README against the actual code.
- Fix all failures that can be resolved in the current environment.

If a test reveals an implementation defect, fix the underlying cause instead of weakening the test simply to make it pass.

If a feature depends on the actual game, unavailable hardware, or unavailable external resources, test the parts that can be tested locally and document exactly what remains unverified.

---

## 17. User Experience and Final Polish

The result should look and behave like a coherent product.

Pay particular attention to:

- Fast visual recognition of important driving information.
- Good dashboard density on a second monitor.
- Clear live versus demo labeling.
- Useful empty states.
- Human-readable errors.
- Consistent units and terminology.
- Predictable navigation.
- Accessible controls.
- Safe defaults.
- Minimal configuration effort.
- Concise, non-annoying voice coaching.

Avoid:

- Fake buttons.
- Placeholder pages presented as finished features.
- Random telemetry in live mode.
- Hardcoded lap records.
- Misleading charts.
- Unexplained exceptions.
- Silent failures.
- Unnecessary cloud dependencies.
- A single giant application file.
- Unnecessary abstractions that make the project harder to debug.

---

## 18. README and Windows Launch Experience

Write a complete README with:

1. Project overview.
2. Feature list.
3. Architecture and data flow.
4. Supported game and operating system.
5. Python requirements.
6. Installation instructions.
7. Virtual environment setup in PowerShell.
8. Dependency installation.
9. How to launch the dashboard.
10. F1 22 UDP configuration instructions.
11. How to use Demo Mode.
12. How to enable and test voice coaching.
13. How to record and analyze sessions.
14. Data storage locations.
15. Troubleshooting.
16. Known limitations.
17. Test commands.

Provide a PowerShell launch script that:

- Locates the project root.
- Uses the project's virtual environment when available.
- Produces a clear message if Python or dependencies are missing.
- Starts the application using the documented command.
- Does not silently swallow startup errors.

Use the correct Python executable and Streamlit invocation for the selected environment.

---

## 19. Final Execution Instructions

Begin now.

Inspect the repository, implement the complete application, run the available tests, start the dashboard to validate it, and fix errors found during execution.

Do not stop after creating a plan or printing code into the conversation. Make changes directly in the project files.

Do not ask me to manually create files that you can create yourself.

Do not report success based solely on code generation. Distinguish between:

- Implemented.
- Automated-tested.
- Locally runtime-verified.
- Verified against a real F1 22 session.

If something fails, investigate and fix it where possible. If something cannot be verified in this environment, explain the precise reason and provide the smallest practical manual verification step.

### Final response format

When finished, provide a concise implementation report containing:

**1. What was built**
- Main functionality.
- Dashboard pages.
- Telemetry and coaching capabilities.
- Persistence and analysis features.

**2. How to run it**
- Exact PowerShell commands.
- Dashboard URL.
- Any first-run setup steps.

**3. F1 22 setup**
- Exact UDP settings.
- How to confirm that live packets are being received.

**4. Verification results**
- Tests executed.
- Tests passed and failed.
- Whether the app launched successfully.
- Whether live F1 22 integration was actually tested.

**5. Known limitations**
- Only genuine remaining limitations.
- No pretending that unverified functionality has been validated.

**6. Next actions**
- Only the specific steps I need to perform myself, such as enabling telemetry in the game.

The priority is a **working, maintainable, polished SimRacer Coach application**, not a superficial demo or a collection of attractive but nonfunctional screens.

Start inspecting the repository and implement it now.

---

## Follow-up Prompt If Claude Stops Early

Continue working in the existing repository. Inspect the actual implementation and current test results, identify every incomplete requirement and runtime failure from the original SimRacer Coach master prompt, and implement or fix them. Do not rewrite working modules unnecessarily, do not weaken tests to hide defects, and do not claim that a feature is verified unless you have actually tested it. Run the relevant tests after each fix, then run the full suite and validate the dashboard startup. Continue until all requirements that can be verified in this environment are satisfied. Report only genuine remaining limitations.
