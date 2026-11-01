# uswitch

Switches your monitors to this machine when it should have them: when a USB
switch hands it the keyboard and mouse, when it wakes behind a monitor that is
its own KVM, or from a keyboard shortcut. It talks to the monitors over DDC/CI
through `ddcutil`. Linux with systemd, and Apple silicon Macs
([below](#on-a-mac)); Dell monitors are the tested target.

[`SPEC.md`](SPEC.md) is the contract: what uswitch does, and why.

## Before installing

1. Install `ddcutil` and `acpid` (`apt install ddcutil acpid`, or
   `dnf install ddcutil acpid`) and Python 3.10 or later. `acpid` is for the
   power button; set `[power_button] enabled = no` in `/etc/uswitch.conf` to do
   without it.
2. In each monitor's on-screen menu, turn **DDC/CI** on: on a Dell it's under
   **Menu → Others → DDC/CI**, and a monitor can come with it off. While you're
   there, turn **Input Source → Auto Select** and **USB-C Prioritization** off.
3. Check that the monitor answers: with it showing this machine,
   `sudo ddcutil detect` lists it with a `VCP version`. If it says "This
   monitor does not support DDC/CI" instead, check step 2's setting.
4. Check that it switches from an input it isn't showing:
   `sudo ddcutil getvcp 60` shows this machine's input (`sl=0x0f`, say). Then
   `sleep 10; sudo ddcutil setvcp 60 0x0f`, switching the monitor to another
   input with its buttons during the sleep, should bring it back. Add
   `--bus N` from `detect` when there's more than one monitor. Some monitors
   only listen on the input they show, and uswitch can't work with those;
   `SPEC.md`'s *Phase 0* has the details.

## Install

From a checkout, on each machine:

```sh
sudo make install    # runs ./uswitch install
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
sudo make uninstall               # keeps /etc/uswitch.conf
sudo uswitch uninstall --purge    # removes it too
```

## On a Mac

On a Mac the same program runs through
[`m1ddc`](https://github.com/waydabber/m1ddc) on Apple silicon, or
[`ddcctl`](https://github.com/kfix/ddcctl) on an Intel Mac, instead of
`ddcutil`. It runs as you, not as root, and keeps everything in your home
directory. It's newer than the Linux side: an Intel Mac has switched a Dell
with `ddcctl` by hand, but uswitch itself hasn't run on a Mac yet, and
`SPEC.md`'s *Phase 0* has the Mac steps that decide whether it works.

### Before installing

1. Install Python 3.10 or later and the DDC/CI tool from Homebrew:
   `brew install m1ddc python` on Apple silicon, `brew install ddcctl python`
   on an Intel Mac. Then Apple's Command Line Tools
   (`xcode-select --install`), which build uswitch's small helper.
2. Turn on **DDC/CI** in each monitor's on-screen menu (on a Dell,
   **Menu → Others → DDC/CI**), and **Auto Select** off, as on Linux.
3. Check that it switches from an input it isn't showing: with the monitor
   showing the Mac, `m1ddc display list detailed` lists it with its system
   UUID, and `m1ddc display UUID get input` gives the Mac's input. Then
   `sleep 10; m1ddc display UUID set input VALUE`, switching the monitor away
   with its buttons during the sleep, should bring it back (*Phase 0*). On an
   Intel Mac it's `ddcctl -d 1 -i ?` for the input, then
   `sleep 10; ddcctl -d 1 -i VALUE`. DisplayPort, or USB-C to the monitor's
   DisplayPort or USB-C input, is the likeliest to work; some Macs' HDMI ports
   pass no DDC/CI.

### Install

From a checkout, with Homebrew's Python and without `sudo`:

```sh
make install PYTHON="$(brew --prefix)/bin/python3"
```

That installs `~/.local/bin/uswitch`, builds the helper, and loads a
LaunchAgent that runs it while you're logged in, then runs `uswitch learn` for
the desk you're at, which works as it does on Linux. At another desk, run
`~/.local/bin/uswitch learn` there.

### Using it

- With a USB switch, press its button, as on Linux.
- With a monitor that is its own switch, wake the Mac or log in.
- To switch by hand, make a shortcut in Shortcuts with a *Run Shell Script*
  action running `~/.local/bin/uswitch switch`, and give it a keyboard
  shortcut.
- There's no power-button trigger: macOS gives no hook for it.
- Nothing switches at the login window, only once you've logged in.

### When it doesn't switch

```sh
~/.local/bin/uswitch status
~/.local/bin/uswitch switch --dry-run
cat ~/Library/Logs/uswitch.log    # what the helper saw, and each run it started
```

### Uninstall

```sh
~/.local/bin/uswitch uninstall           # keeps the configuration and the log
~/.local/bin/uswitch uninstall --purge   # removes them too
```

## Development

`make test` runs the `unittest` suite; it needs no hardware and no root, and
tests the Mac side on Linux against faked `m1ddc`, `ddcctl`, `ioreg` and
`launchctl`.
`uswitch-helper.swift` builds only on a Mac; CI builds it whenever it changes.
