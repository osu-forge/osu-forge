//! osu-forge engine — read-only state reader for osu!stable.
//!
//! # Guarantees
//!
//! This binary opens the osu! process with `PROCESS_VM_READ |
//! PROCESS_QUERY_INFORMATION` and nothing else. It never requests
//! `PROCESS_VM_WRITE`, never enables `SeDebugPrivilege`, never calls
//! `WriteProcessMemory`, and never synthesizes input. No elevation is required.
//! CI greps the whole repository for the forbidden APIs; see `CONTRIBUTING.md`.
//!
//! # Failure model
//!
//! Three things can go wrong, and only the third is dangerous:
//!
//! 1. A signature does not resolve. The engine refuses to attach. Loud, harmless.
//! 2. A pointer chain hits null. The field is reported unavailable. Loud.
//! 3. A signature still matches but the *field* moved, so we read a neighbouring
//!    value. **This is assumed to be the default, not the exception.** Every field
//!    carries a validity predicate, and `validate` runs a self-test battery before
//!    the engine declares itself attached.
//!
//! A plausible-looking zero is never emitted. Fields are reported with an explicit
//! validity flag so consumers can distrust us instead of being quietly misled.

fn main() {
    eprintln!(
        "osu-forge-engine {} — not implemented yet",
        env!("CARGO_PKG_VERSION")
    );
    eprintln!();
    eprintln!("Implementation order (see the project plan):");
    eprintln!("  1. Python + ctypes prototype to pin down the signatures and offsets");
    eprintln!("     interactively against a live osu!.exe");
    eprintln!("  2. Port the confirmed table here");
    std::process::exit(1);
}
