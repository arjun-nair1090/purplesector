from analysis.coach import (
    Category,
    Coach,
    CoachConfig,
    CoachEvent,
    Confidence,
    Priority,
    rule_brake_abruptness,
    rule_lap_completed,
    rule_sector_loss,
    rule_segment_loss,
    rule_session_best,
)
from analysis.lap_tracker import CompletedLap


def mk(n, ms, sectors=(30000, 30000, 30000), *, valid=True, complete=True, samples=None, pit=False):
    return CompletedLap(n, ms, *sectors, valid=valid, samples_complete=complete, pit_lap=pit, out_lap=False,
                        samples=samples or [])


def samples(rise_s, n_brakes=5, lap_s=60.0, hz=60, speed=60.0):
    """Lap with n brake applications ramping 0->100% over rise_s; distance at constant speed."""
    out, t = [], 0.0
    period = lap_s / n_brakes
    while t < lap_s:
        phase = t % period
        b = 0.0
        if 1.0 <= phase < 3.0:
            b = min(100.0, (phase - 1.0) / max(rise_s, 1e-6) * 100.0)
        out.append((t, int(t * 1000), t * speed, speed * 3.6, 0.0 if b else 100.0, b, 0.0, 5, 9000))
        t += 1 / hz
    return out


def test_session_best_needs_previous_valid_lap_and_improvement():
    assert rule_session_best(mk(1, 90000), None) is None          # first lap: nothing to beat
    assert rule_session_best(mk(2, 91000), mk(1, 90000)) is None  # slower
    assert rule_session_best(mk(2, 89000, valid=False), mk(1, 90000)) is None  # invalid lap never a PB
    e = rule_session_best(mk(2, 89500), mk(1, 90000))
    assert e.priority == Priority.HIGH and "0.500" in e.message and e.confidence == Confidence.HIGH


def test_sector_loss_threshold():
    best = mk(1, 90000, (30000, 30000, 30000))
    assert rule_sector_loss(mk(2, 90200, (30000, 30200, 30000)), best, 0.25) is None
    e = rule_sector_loss(mk(2, 90400, (30000, 30400, 30000)), best, 0.25)
    assert e and "Sector 2" in e.message and "0.40" in e.message
    assert rule_sector_loss(mk(2, 90400, (30000, None, None)), best, 0.25) is None  # missing sectors: silent


def test_segment_loss_requires_complete_samples():
    best = mk(1, 60000, samples=samples(0.3))
    assert rule_segment_loss(mk(2, 61000, complete=False, samples=samples(0.3)), best, 0.15) is None


def test_brake_abruptness_rule():
    prev = [mk(i, 60000, samples=samples(0.30)) for i in (1, 2, 3)]
    assert rule_brake_abruptness(mk(4, 60000, samples=samples(0.28)), prev, 0.6, 0.04, 2) is None
    e = rule_brake_abruptness(mk(4, 60000, samples=samples(0.05)), prev, 0.6, 0.04, 2)
    assert e and e.category == Category.BRAKING and "ms" in e.evidence
    # not enough reference laps -> silent
    assert rule_brake_abruptness(mk(4, 60000, samples=samples(0.05)), prev[:1], 0.6, 0.04, 2) is None


def test_lap_completed_message():
    e = rule_lap_completed(mk(7, 83456))
    assert e.message == "Lap 7: 1:23.456" and e.confidence == Confidence.HIGH
    assert "invalid" in rule_lap_completed(mk(7, 83456, valid=False)).message


def ev(key="k", prio=Priority.MEDIUM, conf=Confidence.HIGH):
    return CoachEvent(key, Category.SYSTEM, "msg", prio, conf, "evidence")


def test_cooldown_and_duplicate_suppression():
    t = [0.0]
    c = Coach(CoachConfig(cooldown_s=30), clock=lambda: t[0])
    assert c.accept(ev())
    assert not c.accept(ev())
    t[0] = 29.9
    assert not c.accept(ev())
    t[0] = 30.1
    assert c.accept(ev())
    assert c.accept(ev("other"))
    assert c.suppressed == 2


def test_verbosity_and_confidence_filters():
    c = Coach(CoachConfig(verbosity="quiet", cooldown_s=0))
    assert not c.accept(ev(prio=Priority.MEDIUM))
    assert c.accept(ev(prio=Priority.HIGH))
    c = Coach(CoachConfig(verbosity="detailed", min_confidence="high", cooldown_s=0))
    assert c.accept(ev(prio=Priority.LOW))
    assert not c.accept(ev(conf=Confidence.MEDIUM))


def test_new_best_suppresses_contradictory_loss_messages():
    class T:
        laps = [mk(1, 90000, (29000, 31000, 30000)), mk(2, 89000, (29500, 29500, 30000))]
    c = Coach(CoachConfig(cooldown_s=0))
    keys = [e.key for e in c.on_lap_completed(T.laps[-1], T)]
    assert "pb:2" in keys and "sector_loss" not in keys
