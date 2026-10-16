"""Fixed application constants. User-tunable values live in config.settings.Settings."""
from pathlib import Path

APP_NAME = "PurpleSector"
APP_VERSION = "1.0.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_ROOT / "data" / "settings.json"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_PREFIX = "PURPLESECTOR_"

SUPPORTED_PACKET_FORMATS = (2022,)
CONNECTION_STALE_S = 2.0          # no valid packet for this long => no longer "connected"
UDP_RECV_BUFFER_BYTES = 1 << 20   # absorbs short stalls on the receive thread
TELEMETRY_RATE_HZ = 60            # nominal; used only to size bounded buffers
MAX_LAP_SAMPLES = TELEMETRY_RATE_HZ * 60 * 10  # hard cap per lap (10 min at 60 Hz)
SCHEMA_VERSION = 1
