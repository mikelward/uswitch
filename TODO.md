# TODO

- **macOS** (`mswitch`, `SPEC.md` D14–D17): run Phase 0's Mac steps on an Apple silicon
  Mac. It was built ahead of them, since none is on hand yet; they decide whether it
  works, before anyone relies on it.
  - **Tried** (2026-10-31): an Intel Mac on the Dell's HDMI input, then its USB-C input.
    `ddcctl` finds the monitor and its EDID, and `ioreg`'s `IODisplayEDID` parses with
    `parse_edid` as is, but no DDC/CI gets through on either port: reading `0x60` times
    out, writing the input from another one doesn't switch, a brightness write leaves
    the monitor alone, and MonitorControl falls back to dimming in software. That Mac
    is unsupported; DisplayPort from the same Thunderbolt port is the same output, so
    it wasn't tried. An M6 Mac mini over Thunderbolt to the Dell's DisplayPort input is
    the next candidate.

## Decisions needing review

- **Boot isn't a power-button press** (D13). A reboot, power restore or Wake-on-LAN
  looks the same as a power-button boot, so a boot switches only through the usual
  desk check.
- **Setup B wakes are unchanged** (D13). Every wake there still switches; limiting it
  to power-button wakes would stop a lid-open wake from switching a laptop.
- **The power button is on by default**, so rerunning `install` after upgrading needs
  `acpid` or `[power_button] enabled = no`.
