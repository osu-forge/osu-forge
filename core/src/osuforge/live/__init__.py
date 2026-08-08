"""What you look at while you play.

Not during a play — between them. A replay appears when a play finishes, so a
page rewritten as they appear shows you what the last one did within a second or
two of it ending.

That covers more than it sounds like. Offset, key balance and settings are
post-hoc questions: watching a number move mid-play does not help you tune an
offset, and seeing what the play you just finished actually did does. What it
cannot show is a play that left no replay — a quick retry after a mistake, or a
fail — and those need the memory engine.

# Why there is no server here

The architecture specifies a local HTTP server with a careful security design:
bound to the loopback address, `Host` header validated, `Origin` allow-listed on
upgrade, a per-session bearer token. All of that is for third-party overlays and
extensions, and none of those exist yet. Opening a local HTTP surface in order
to display one page would be paying that cost before anything needs it.

So this writes a self-contained HTML file and the browser reloads it. No socket,
no port, no token, nothing listening. It works as an OBS browser source too. The
server gets built when an extension needs it, with its design intact.
"""
