# TODO

- **macOS.** Viable in part; `SPEC.md` rules it out today, so it starts there.
  - **First, by hand**: with the monitor on another input, `m1ddc set input VALUE`
    from the Mac. If the monitor doesn't come back, stop for that monitor and port.
  - **Tried** (2026-10-31): an Intel Mac on the Dell's HDMI input, then its USB-C input.
    `ddcctl` finds the monitor and its EDID, and `ioreg`'s `IODisplayEDID` parses with
    `parse_edid` as is, but no DDC/CI gets through on either port: reading `0x60` times
    out, writing the input from another one doesn't switch, a brightness write leaves
    the monitor alone, and MonitorControl falls back to dimming in software. That Mac
    is unsupported; DisplayPort from the same Thunderbolt port is the same output, so
    it wasn't tried. Resuming needs a Mac whose port passes DDC/CI.
  - **Switching**: `m1ddc` (Apple silicon) or `ddcctl` (Intel), free from Homebrew.
    Both use a private API (as MonitorControl and BetterDisplay do), so an OS update
    can break them. Several Apple silicon HDMI ports (M1/M2 Mac mini) pass no DDC/CI;
    USB-C/Thunderbolt to DisplayPort does.
  - **Best effort** (decided): it works where the hardware allows. Older Apple silicon
    Macs and ports that can't switch are documented as unsupported, not worked around.
  - **Shared logic**: EDIDs come from `ioreg`, so desks (D6, D11), the desk check and
    D12's waits carry over; a macOS backend fills `Context` instead of sysfs and
    `ddcutil`.
  - **Triggers**: a shortcut through Shortcuts.app or Hammerspoon (easy); wake through
    `sleepwatcher` or a helper listening for wake (yes); USB arrival through a launchd
    agent on IOKit matching or a resident watcher, which needs a small Swift helper
    since Python's standard library can't subscribe to IOKit (yes, with a resident
    process, against D3's "nothing runs between triggers"); a power-button wake from
    `pmset -g log`'s wake reason (unproven, and parsing it is fragile); the power
    button while awake (no: macOS has no hook for it).
  - **Unknowns**: whether switching works before login, since these tools run in a
    user session.
  - **Proposed scope**: shortcut, wake and USB triggers on `m1ddc` plus a Swift event
    helper; no power button while awake.

## Decisions needing review

- **Boot isn't a power-button press** (D13). A reboot, power restore or Wake-on-LAN
  looks the same as a power-button boot, so a boot switches only through the usual
  desk check.
- **Setup B wakes are unchanged** (D13). Every wake there still switches; limiting it
  to power-button wakes would stop a lid-open wake from switching a laptop.
- **The power button is on by default**, so rerunning `install` after upgrading needs
  `acpid` or `[power_button] enabled = no`.
