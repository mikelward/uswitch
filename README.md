# uswitch

Switches your monitors to this machine when it should have them: when a USB
switch hands it the keyboard and mouse, when it wakes behind a monitor that is
its own KVM, or from a keyboard shortcut. It talks to the monitors over DDC/CI
through `ddcutil`. Linux with systemd, and Apple silicon Macs as `mswitch`
([below](#on-a-mac)); Dell monitors are the tested target.

[`SPEC.md`](SPEC.md) is the contract: what uswitch does, and why.

## Before installing

1. Install `ddcutil` and `acpid` (`apt install ddcutil acpid`, or
   `dnf install ddcutil acpid`) and Python 3.10 or later. `acpid` is for the
   power button; set `[power_button] enabled = no` in `/etc/uswitch.conf` to do
   without it.
2. On each monitor's on-screen menu, turn **DDC/CI** on and **Auto Select** and
   **USB-C Prioritization** off.
3. Check by hand that the monitor accepts an input switch from an input it
   isn't showing: `SPEC.md`'s *Phase 0* walks through it. Some monitors only
   listen on the input they show, and uswitch can't work with those.

## Install

From a checkout, on each machine:

```sh
sudo ./uswitch install
```

That installs `/usr/local/sbin/uswitch` and a systemd unit, then runs
`uswitch learn` for the desk you're at. Learn asks whether the desk has a USB
switch or a monitor that is its own switch, shows its instructions, and then
needs no keys from you while the screen and keyboard are at the other machine.

At another desk (a laptop at work, say), run `sudo uswitch learn` there too.
Each machine learns only its own inputs.

## Using it

- With a USB switch, press its button: the machine it selects switches the
  monitors to itself.
- With a monitor that is its own switch, wake the machine.
- Press the machine's power button, to wake it or while it's awake: it always
  switches the monitors to that machine, and no longer suspends it. In a
  desktop session, set the desktop's power-button action to *Do nothing*.
- To switch by hand, bind a keyboard shortcut to
  `systemctl start uswitch.service`. No password is needed for that one unit.

## When it doesn't switch

```sh
sudo uswitch status             # desks, triggers, and each monitor's input
sudo uswitch switch --dry-run   # what a switch would do now
journalctl -u uswitch           # what each run did, and how long it took
```

## Uninstall

```sh
sudo uswitch uninstall            # keeps /etc/uswitch.conf
sudo uswitch uninstall --purge    # removes it too
```

## On a Mac

On an Apple silicon Mac the same program runs as `mswitch`, through
[`m1ddc`](https://github.com/waydabber/m1ddc) instead of `ddcutil`. It runs as
you, not as root, and keeps everything in your home directory. It's newer than
the Linux side and hasn't been tried on a real Apple silicon Mac yet:
`SPEC.md`'s *Phase 0* has the Mac steps that decide whether it works. Intel
Macs aren't supported: the one tried passed no DDC/CI to the monitor.

### Before installing

1. Install `m1ddc` and Python 3.10 or later from Homebrew
   (`brew install m1ddc python`), and Apple's Command Line Tools
   (`xcode-select --install`), which build `mswitch`'s small helper.
2. Turn on **DDC/CI** in each monitor's on-screen menu, as on Linux, and check
   by hand that `m1ddc` can switch it from an input it isn't showing (*Phase 0*).
   DisplayPort, or USB-C to the monitor's DisplayPort, is the likeliest to
   work; some Macs' HDMI ports pass no DDC/CI.

### Install

From a checkout, with Homebrew's Python and without `sudo`:

```sh
"$(brew --prefix)/bin/python3" ./uswitch install
```

That installs `~/.local/bin/mswitch`, builds the helper, and loads a
LaunchAgent that runs it while you're logged in, then runs `mswitch learn` for
the desk you're at, which works as it does on Linux. At another desk, run
`~/.local/bin/mswitch learn` there.

### Using it

- With a USB switch, press its button, as on Linux.
- With a monitor that is its own switch, wake the Mac or log in.
- To switch by hand, make a shortcut in Shortcuts with a *Run Shell Script*
  action running `~/.local/bin/mswitch switch`, and give it a keyboard
  shortcut.
- There's no power-button trigger: macOS gives no hook for it.
- Nothing switches at the login window, only once you've logged in.

### When it doesn't switch

```sh
~/.local/bin/mswitch status
~/.local/bin/mswitch switch --dry-run
cat ~/Library/Logs/mswitch.log    # what the helper saw, and each run it started
```

### Uninstall

```sh
~/.local/bin/mswitch uninstall           # keeps the configuration and the log
~/.local/bin/mswitch uninstall --purge   # removes them too
```

## Development

`make test` runs the `unittest` suite; it needs no hardware and no root, and
tests the Mac side on Linux against faked `m1ddc`, `ioreg` and `launchctl`.
`mswitch-helper.swift` builds only on a Mac; CI builds it whenever it changes.
