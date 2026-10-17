"""Car setup differences between two laps, as the game reported them at each line crossing."""
from __future__ import annotations

# (field, label, format) in the order the game's setup screen shows them
SETUP_FIELDS = (
    ("front_wing", "Front wing", "{:.0f}"), ("rear_wing", "Rear wing", "{:.0f}"),
    ("on_throttle", "Differential on throttle", "{:.0f}%"), ("off_throttle", "Differential off throttle", "{:.0f}%"),
    ("engine_braking", "Engine braking", "{:.0f}%"),
    ("front_camber", "Front camber", "{:.2f}°"), ("rear_camber", "Rear camber", "{:.2f}°"),
    ("front_toe", "Front toe", "{:.2f}°"), ("rear_toe", "Rear toe", "{:.2f}°"),
    ("front_suspension", "Front suspension", "{:.0f}"), ("rear_suspension", "Rear suspension", "{:.0f}"),
    ("front_anti_roll_bar", "Front anti-roll bar", "{:.0f}"), ("rear_anti_roll_bar", "Rear anti-roll bar", "{:.0f}"),
    ("front_suspension_height", "Front ride height", "{:.0f}"), ("rear_suspension_height", "Rear ride height", "{:.0f}"),
    ("brake_pressure", "Brake pressure", "{:.0f}%"), ("brake_bias", "Brake bias", "{:.0f}%"),
    ("front_left_tyre_pressure", "Front-left tyre pressure", "{:.1f} psi"),
    ("front_right_tyre_pressure", "Front-right tyre pressure", "{:.1f} psi"),
    ("rear_left_tyre_pressure", "Rear-left tyre pressure", "{:.1f} psi"),
    ("rear_right_tyre_pressure", "Rear-right tyre pressure", "{:.1f} psi"),
    ("ballast", "Ballast", "{:.0f}"), ("fuel_load", "Fuel load", "{:.1f} kg"),
)


def setup_diff(lap: dict | None, ref: dict | None) -> list[tuple[str, str, str]] | None:
    """(label, this lap, reference) for every setting that differs as displayed; None when either lap has
    no recorded setup. Settings only one game sends (engine braking: F1 24 on) show as 'not sent'."""
    if not lap or not ref:
        return None
    rows = []
    for key, label, fmt in SETUP_FIELDS:
        a, b = (fmt.format(d[key]) if d.get(key) is not None else "not sent" for d in (lap, ref))
        if a != b:
            rows.append((label, a, b))
    return rows
