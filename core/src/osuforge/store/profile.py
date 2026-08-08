"""The hardware profile, as rows.

:mod:`osuforge.hardware` owns the types and the validation; this owns where they
are kept. Splitting them that way is what keeps one source of truth: a `Mouse`
is validated in exactly one place whether it arrived from a CLI flag, a web
form, or a row written by an older build.

Reading goes through the same validation as writing. A row is not trusted for
being in the database — someone with a text editor and a copy of `sqlite3` is
as capable of putting an impossible DPI in it as a typo in a form is, and an
impossible denominator turns a real measurement into a confident wrong
recommendation.
"""

from __future__ import annotations

from pathlib import Path

from osuforge import hardware
from osuforge.hardware import Mouse, Profile, ProfileError, Tracking
from osuforge.store.db import DeviceRow, Store, open_store

__all__ = ["POINTER_ROLE", "TRACKING_KEY", "import_legacy_profile", "load", "save"]

POINTER_ROLE = "pointer"
"""The device the player aims with. One per profile."""

TRACKING_KEY = "pointer.tracking"
"""Where the sensor cut-off lives.

A setting rather than a device attribute, because it describes the surface and
the sensor together rather than the device alone — the same mouse on a
different pad has a different lift-off distance.
"""


def load(store: Store) -> Profile:
    """Read the profile. An empty store is an empty profile, not an error."""
    pointer: Mouse | None = None
    row = store.device(POINTER_ROLE)
    if row is not None:
        if row.kind != Mouse.kind:
            # Refused rather than skipped, exactly as the JSON reader did. A
            # tablet row read by a build without tablet support must not come
            # back as "no pointer declared" and get analysed as though the
            # question had never been asked.
            raise ProfileError(
                f"pointer kind {row.kind!r} is not supported by this build; only {Mouse.kind!r} is"
            )
        try:
            pointer = Mouse(dpi_x=int(row.attributes["dpi_x"]), dpi_y=int(row.attributes["dpi_y"]))
        except KeyError as exc:
            raise ProfileError(f"stored mouse is missing {exc.args[0]}") from exc
        except (TypeError, ValueError) as exc:
            raise ProfileError(f"stored mouse DPI must be a whole number: {exc}") from exc

    tracking: Tracking | None = None
    stored = store.get(TRACKING_KEY)
    if stored is not None:
        if not isinstance(stored, dict):
            raise ProfileError(f"{TRACKING_KEY} should be an object")
        try:
            landing = stored.get("landing_mm")
            lift_off = stored.get("lift_off_mm")
            tracking = Tracking(
                landing_mm=None if landing is None else float(landing),
                lift_off_mm=None if lift_off is None else float(lift_off),
                label=str(stored.get("label", "")),
            )
        except (TypeError, ValueError) as exc:
            raise ProfileError(f"stored tracking distances must be numbers: {exc}") from exc

    return Profile(pointer=pointer, tracking=tracking)


def save(store: Store, profile: Profile, *, vendor: str = "", model: str = "") -> None:
    """Write the profile, replacing what was there.

    `vendor` and `model` are recorded on the device row rather than in the
    attribute bag because every kind of device has them. They are free text: a
    vendor list that has to be updated before someone can name their mouse is a
    vendor list that will be wrong.
    """
    if profile.mouse is not None:
        store.put_device(
            DeviceRow(
                role=POINTER_ROLE,
                kind=Mouse.kind,
                vendor=vendor,
                model=model,
                attributes={"dpi_x": profile.mouse.dpi_x, "dpi_y": profile.mouse.dpi_y},
            )
        )
    if profile.tracking is not None:
        store.put(
            TRACKING_KEY,
            {
                "landing_mm": profile.tracking.landing_mm,
                "lift_off_mm": profile.tracking.lift_off_mm,
                "label": profile.tracking.label,
            },
        )


def import_legacy_profile(store: Store, path: Path | None = None) -> bool:
    """Bring across a `profile.json` written before the store existed.

    Returns whether anything was imported. Does nothing when the store already
    holds a pointer — the database is authoritative once it has one, and
    re-importing would quietly overwrite a newer value with an older file.

    Kept as its own function rather than run on every open so that it is visible
    in a call stack. A migration that happens invisibly is one nobody can
    explain when it goes wrong.
    """
    if store.device(POINTER_ROLE) is not None:
        return False

    legacy = path or hardware.default_profile_path()
    if not legacy.is_file():
        return False

    profile = hardware.load(legacy)
    if profile.pointer is None and profile.tracking is None:
        return False

    save(store, profile)
    return True


def load_from(path: Path | None = None) -> Profile:
    """Open the store, import any legacy file, and read the profile."""
    with open_store(path) as store:
        import_legacy_profile(store)
        return load(store)
