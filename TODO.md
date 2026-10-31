# TODO

- **macOS.** Can any of uswitch run on macOS? DDC/CI input switching works there
  through `m1ddc` (Apple silicon) or `ddcctl` (Intel), and display-switch shows a USB
  trigger is possible, but udev, systemd, logind and acpid have no macOS equivalents
  (launchd, IOKit notifications, power-button handling would all be new). Scope what
  a macOS port would cover before building; `SPEC.md` rules it out today.

## Decisions needing review

- **Boot isn't a power-button press** (D13). A reboot, power restore or Wake-on-LAN
  looks the same as a power-button boot, so a boot switches only through the usual
  desk check.
- **Setup B wakes are unchanged** (D13). Every wake there still switches; limiting it
  to power-button wakes would stop a lid-open wake from switching a laptop.
- **The power button is on by default**, so rerunning `install` after upgrading needs
  `acpid` or `[power_button] enabled = no`.
