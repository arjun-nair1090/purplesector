"""Fixed application constants. User-tunable values live in config.settings.Settings."""
import sys
from pathlib import Path

APP_NAME = "PurpleSector"
APP_VERSION = "1.1.0"

# Packaged (PurpleSector.exe): code and assets sit in the bundle; data, settings and .env next to the exe.
FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
APP_HOME = Path(sys.executable).resolve().parent if FROZEN else PROJECT_ROOT
SETTINGS_PATH = APP_HOME / "data" / "settings.json"
ENV_FILE = APP_HOME / ".env"
ENV_PREFIX = "PURPLESECTOR_"

CONNECTION_STALE_S = 2.0          # no valid packet for this long => no longer "connected"
UDP_RECV_BUFFER_BYTES = 1 << 20   # absorbs short stalls on the receive thread
TELEMETRY_RATE_HZ = 60            # nominal; used only to size bounded buffers
MAX_LAP_SAMPLES = TELEMETRY_RATE_HZ * 60 * 10  # hard cap per lap (10 min at 60 Hz)
SCHEMA_VERSION = 3
