from voice.hold import ACTIVE, BEGIN, CANCEL, DISCARD, FINISH, IDLE, RECORD, Hold


def run(steps, threshold=0.3):
    """Feed (time, event) pairs; "tick" advances the clock. Return all actions."""
    hold, actions = Hold(threshold), []
    for now, name in steps:
        actions += hold.tick(now) if name == "tick" else hold.event(name, now)
    return hold, actions


def test_tap_is_no_dictation():
    hold, actions = run([(0, "down"), (0.15, "tick"), (0.2, "up")])
    assert actions == [RECORD, DISCARD] and hold.state == IDLE


def test_hold_alone_is_a_dictation():
    hold, actions = run([(0, "down"), (0.3, "tick"), (2.0, "up")])
    assert actions == [RECORD, BEGIN, FINISH] and hold.state == IDLE


def test_fast_combination_cancels():
    _, actions = run([(0, "down"), (0.1, "key"), (0.3, "tick"), (0.5, "up")])
    assert actions == [RECORD, DISCARD]


def test_slow_combination_cancels_the_dictation():
    _, actions = run([(0, "down"), (0.3, "tick"), (1.0, "key"), (1.2, "up")])
    assert actions == [RECORD, BEGIN, CANCEL]


def test_deadline_only_while_pending():
    hold = Hold(0.3)
    assert hold.deadline() is None
    hold.event("down", 5.0)
    assert hold.deadline() == 5.3
    hold.tick(5.3)
    assert hold.state == ACTIVE and hold.deadline() is None


def test_lost_up_starts_over():
    _, actions = run([(0, "down"), (0.3, "tick"), (1.0, "down")])
    assert actions == [RECORD, BEGIN, CANCEL, RECORD]


def test_helper_lost_cancels():
    _, actions = run([(0, "down"), (0.3, "tick"), (1.0, "lost")])
    assert actions == [RECORD, BEGIN, CANCEL]
