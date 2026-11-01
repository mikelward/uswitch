# TODO

- **macOS** (`SPEC.md` D14–D17): run Phase 0's Mac steps with uswitch installed, on the
  Intel Mac (through `ddcctl`) and on an Apple silicon one (through `m1ddc`) when there
  is one. It was built ahead of them; they decide whether it works, before anyone
  relies on it.
  - **Tried** (2026-11-01): the Intel Mac on the Dell's USB-C input passes Phase 0
    step 3 with `ddcctl`: it reads brightness and the input (27, `0x1b`), and switches
    the monitor back from another input. Its failure on 2026-10-31, over HDMI and then
    USB-C, came from the monitor's DDC/CI setting being off.
  - **Then decide: DDC/CI in the helper on an Intel Mac?** Kept `ddcutil`, `ddcctl` and
    `m1ddc` for now (2026-11-01): their retries, waits and quirk handling are what a
    rewrite would have to rediscover. Doing it in the helper would drop `ddcctl` and
    close its renumbering race (D17), since the helper could find a monitor and write
    to it in one step. If so, adapt MonitorControl's `IntelDDC.swift` (MIT), not
    `ddcctl` (GPL-3.0) or `ddcutil` (GPL-2.0+), which can't go into Apache-2.0 code.
    Linux keeps `ddcutil` either way: it has no race to close.

## Decisions needing review

- **Boot isn't a power-button press** (D13). A reboot, power restore or Wake-on-LAN
  looks the same as a power-button boot, so a boot switches only through the usual
  desk check.
- **Setup B wakes are unchanged** (D13). Every wake there still switches; limiting it
  to power-button wakes would stop a lid-open wake from switching a laptop.
- **The power button is on by default**, so rerunning `install` after upgrading needs
  `acpid` or `[power_button] enabled = no`.
