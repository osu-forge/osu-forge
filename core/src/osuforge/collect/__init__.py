"""Recording what was played, as it is played.

The analysis in :mod:`osuforge.analysis` is limited by something no amount of
work on the existing replays can fix. Hits are nested in replays and replays in
sessions, so more hits from the same sittings add almost nothing: the local
corpus of 23,723 hits is worth about 893 independent ones, and the number that
matters is the twelve sessions. Halving the interval needs roughly four times as
many sessions, and sessions only accumulate forward.

There is a second thing that only works forward. The sign convention behind an
offset recommendation — that the new offset is the old one minus the mean error
— is a hypothesis, and testing it means watching what happens when the setting
actually changes. That cannot be done retrospectively unless the setting
happened to change during the period already recorded.

So this package records two things every time it runs: which replays exist, and
what the timing-relevant settings were when they appeared. Neither reads memory
and neither runs in the background. It is a command that scans and appends, safe
to run as often as you like and pointless to run while nothing has been played.

# What this cannot fix

A replay is only written when a play finishes. Quick-retry after a mistake —
which is most of what happens on a map being learned — leaves nothing behind at
all, and neither does failing or quitting. So this records the plays that
survived, and the ones it misses are not a random sample of the rest: they are
disproportionately the attempts where the player's timing fell apart.

That compounds with a bias already visible in the analysis. A hit error can only
be observed on an object that was hit, so a poor attempt has its worst errors
cut away before it is even a replay, and here the whole attempt is cut away as
well. The two lean the same direction and hide one another.

Nothing outside the running game records a retry — `scores.db` holds finished
plays too. Session boundaries and the settings history are unaffected, because
one replay is enough to establish that a session happened and settings are read
from the configuration rather than from play. Coverage within a session is not,
and only reading the game as it runs will fix it.
"""
