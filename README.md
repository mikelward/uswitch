# uswitch

Switches your monitors to this machine when it should have them: when a USB
switch hands it the keyboard and mouse, when it wakes behind a monitor that is
its own KVM, or from a keyboard shortcut. It talks to the monitors over DDC/CI
through `ddcutil`. Linux with systemd only; Dell monitors are the tested target.

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

## Development

`make test` runs the `unittest` suite; it needs no hardware and no root.
