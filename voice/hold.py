"""Decide whether a hold of the launch key is a dictation.

voice-keys reports three events: "down", "key" and "up". A hold becomes a
dictation when the launch key stays down alone for the threshold. Any other key
cancels the hold, also after the threshold, because then the hold was a key
combination that the user started slowly.

keyd uses the same threshold (overload_tap_timeout): a shorter hold is a tap,
and keyd sends the launch key on release. A longer hold sends nothing.
"""

from __future__ import annotations

IDLE, PENDING, ACTIVE, CANCELED = "idle", "pending", "active", "canceled"

# What the daemon must do after an event.
RECORD = "record"     # open the microphone; the hold may become a dictation
BEGIN = "begin"       # the hold is a dictation now
DISCARD = "discard"   # throw the audio away
FINISH = "finish"     # transcribe what is left and insert the text


class Hold:
    def __init__(self, threshold: float):
        self.threshold = threshold
        self.state = IDLE
        self.since = 0.0

    def deadline(self) -> float | None:
        """When the pending hold becomes a dictation, if nothing happens before."""
        return self.since + self.threshold if self.state == PENDING else None

    def event(self, name: str, now: float) -> list[str]:
        state = self.state
        if name == "down":
            actions = [DISCARD] if state in (PENDING, ACTIVE) else []   # an "up" was lost
            self.state, self.since = PENDING, now
            return actions + [RECORD]
        if name == "key":
            if state in (PENDING, ACTIVE):
                self.state = CANCELED
                return [DISCARD]
            return []
        if name == "up":
            self.state = IDLE
            return {PENDING: [DISCARD], ACTIVE: [FINISH]}.get(state, [])
        if name == "lost":                      # the helper went away
            self.state = IDLE
            return [DISCARD] if state in (PENDING, ACTIVE) else []
        if name == "limit":                     # the dictation is too long
            if state == ACTIVE:
                self.state = CANCELED           # the release then does nothing
                return [FINISH]
            return []
        raise ValueError(f"unknown event {name!r}")

    def tick(self, now: float) -> list[str]:
        if self.state == PENDING and now >= self.since + self.threshold:
            self.state = ACTIVE
            return [BEGIN]
        return []
