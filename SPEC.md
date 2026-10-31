# uswitch

uswitch switches your monitors to this machine when this machine should have them: when a
USB switch selects it, when it wakes, when you press its power button, or when you ask.

Every monitor is cabled to every machine on its own input, every machine runs uswitch, and
on a trigger uswitch tells each monitor over DDC/CI (VCP feature `0x60`, input source) to
show this machine's input. It serves two setups:

- **A. A USB switch** (a Cable Matters 4-port switch, say) moves the keyboard, mouse and
  anything else plugged into it between computers at the press of a button, but each
  monitor stays on whatever input it was showing. A video KVM closes that gap at a price
  and usually a resolution or refresh-rate ceiling; uswitch closes it in software. The
  trigger is the switch's devices arriving (D1).
- **B. The monitor is the switch.** A Dell with a built-in KVM moves its own USB ports,
  keyboard and mouse included, with the active input, but picking the input means its
  on-screen controls. uswitch lets the machine that wakes take the display, and the
  monitor's KVM brings the keyboard and mouse along (D10).

A machine can sit at several desks, a laptop at home and at work say, each set up either
way (D11).

This file is the contract: what uswitch does and why. Mechanics belong in code and tests.

## Scope

- **Linux only**, on systemd distributions (Debian, Ubuntu, Fedora). Not portable to
  macOS, Windows, or non-systemd init.
- **Dell monitors are the tested target.** Any monitor that honors MCCS VCP `0x60` from an
  input it isn't showing should work, but only Dell is supported.
- **External binary: `ddcutil`** (packaged as `ddcutil` on all three distributions). It does
  the DDC/CI protocol work; uswitch decides when and what to send.
- **External daemon: `acpid`** (packaged as `acpid` on all three), for the power button
  (D13), unless that is turned off. It is small, long-standing, and runs a command per ACPI
  event; uswitch only adds one event file.
- No network, no resident process of uswitch's own, no cost. The hot path is a button press to a monitor
  switch, targeted at under about two seconds.

## How it works

In setup A:

1. You press the USB switch's button. Its USB devices disappear from the machine you left
   and enumerate on the machine you picked.
2. On the picked machine, a udev rule matches the **trigger device** (D1) and hands off to a
   systemd oneshot unit, `uswitch.service` (D3).
3. The unit runs `uswitch switch`, which sets each connected monitor of this desk to the
   input this machine is cabled to (D6, D8, D11).
4. The machine you left does nothing.

In either setup, systemd also starts the unit when the machine boots or resumes; in
setup A that wake only switches if the switch already points here (D10). Pressing the
machine's power button, to wake it or while it is awake, always switches (D13).

**Only the gaining machine acts.** It is the one that knows it was picked, and the one
whose cable is certain to be connected. Switching away on removal would need to know where
the switch went, and a machine that has lost its USB devices can't tell; display-switch
documents its own disconnect action fighting a sleeping monitor (see *Prior art*).

## Decisions

### D1. The trigger is the switch's own USB hub by default, not a mouse

Most USB switches put a hub chip after the switching mux, so the hub enumerates on exactly
the selected machine whatever is plugged into it. Triggering on the hub means:

- replugging, replacing or unplugging the mouse doesn't break switching;
- a built-in trackpad or keyboard can never match, because only devices that come and go
  with the switch qualify (D2).

A passive switch with no hub of its own falls back to a device behind it (the mouse, say),
chosen the same way.

**Matching.** Vendor and product ID, plus the serial number when the device reports one.
Hub chips are generic: the same Genesys, VIA or Realtek part sits in docks and in the Dell
monitors' own hubs. So if any other device present at learn time has the same identity,
uswitch also pins the trigger's port path (`3-2`) and says so. That pin breaks if the
switch's cable moves to another port; rerun `learn`.

**One event per press.** A USB 3 hub appears as two devices, a USB 2 hub and its
SuperSpeed companion. uswitch picks the USB 2 half, which also appears on a USB 2 port.

**Device-supplied strings never reach a command line.** The service runs a fixed command
and takes nothing from the event. Values written into the udev rule are validated: hex IDs,
and a serial only if it is plain printable ASCII without quotes; otherwise the port path is
pinned instead. A spoofed USB device can make the monitor switch inputs and nothing more.

### D2. `uswitch learn` discovers the trigger and the inputs

Nobody types vendor IDs or input codes. `sudo uswitch learn`, run with the monitors showing
this machine, sets up the desk this machine is at now (D11): run it once at each desk, and
again after recabling. It asks whether the desk has a USB switch (setup A) or a monitor
that is its own switch (setup B) and shows all its instructions up front, because during
steps 2 and 4 the screen and keyboard may be at another machine, so nothing in them waits
for this machine's keyboard.

1. Finds each connected monitor by EDID (manufacturer, model, serial) and reads its current
   input source, the `0x60` low byte. Reading the value from this monitor over this cable
   is the one source that is always right: Dell puts USB-C at `0x1b`, outside the standard
   names, and monitors misreport their capabilities strings.
2. Setup A: you press the switch to another machine and back. `learn` watches the devices
   leave and come back on its own; if the other machine runs uswitch, the monitors follow it
   on the first press. It takes as the trigger the device closest to the root port among
   those that left and came back, preferring a hub (D1). If more than one device qualifies
   equally, it lists them and asks you to pick, once the keyboard is back.
   `--usb VENDOR:PRODUCT[:SERIAL]` skips the presses and names the device directly.
3. Writes the desk to the configuration and rewrites the udev rule.
4. Proves the switch works from an input the monitors aren't showing. Each monitor has to
   be away from this machine's input first: step 2's presses may already have sent it to
   the other machine; otherwise (setup B, or no uswitch on the other machine) you switch it
   away with its buttons. `learn` counts a monitor as away once a read shows another input
   or stops answering, since some monitors don't answer reads on an input they aren't
   showing (D12), or once the countdown it showed before you started runs out. Then it
   writes the learned input regardless, so a monitor that never answered still comes back.
   In setup B the monitor's KVM takes the keyboard away and brings it back with the input.
   A monitor that doesn't come back returns with its buttons (the keyboard with it), and
   `learn` then reports that it can't be switched from an inactive input (Phase 0 step 3).

### D3. udev hands off to a systemd oneshot; nothing runs in the rule itself

udev's `RUN` is for short commands and holds up event processing, and a DDC/CI write can
take a second or more per monitor. The rule only starts `uswitch.service`
(`Type=oneshot`), which gets the journal for logs and folds a burst of starts into one, so
udev is never held up. Between triggers nothing runs: no daemon, no polling.

### D4. System-wide, run as root

The configuration lives in `/etc/uswitch.conf`, alongside a udev rule, the systemd units,
a polkit rule (D10), and a modules-load entry for `i2c-dev`. A USB hotplug or a wake is a
system event, and switching should work at the login screen and with nobody logged in.
Running as root also avoids setting up an `i2c` group for every user. The interactive
commands run under `sudo`. The unit is sandboxed with systemd's usual options as far as
access to `/dev/i2c-*` allows.

### D5. At a setup A desk the display follows the USB switch, at boot and on resume too

The invariant is "the monitors show whichever machine has the USB devices". Boot with the
switch pointed here, and the trigger's coldplug event switches the monitors here. If a
resume re-enumerates the devices while the switch points here, the monitors switch again,
consistent with the invariant, and a resume where they stayed connected switches too,
through the wake trigger (D10). A machine that boots or wakes while the switch points
elsewhere finds no trigger device and does nothing, so **a waking machine can't steal the
display**, unless its power button woke it: a press is a request for the monitors (D13). The monitor itself can: Dell's *Auto Select* and *USB-C Prioritization* jump
to whichever input just found a signal. Turn both off (see *Setup*).

### D6. Monitors are identified by EDID, never by display or bus number

ddcutil's display numbers and the kernel's I2C bus numbers shift with plug order and
across boots; a monitor's EDID manufacturer, model and serial don't. The kernel's DRM
connectors say which monitors are connected and give each one's EDID with no DDC/CI
traffic; a laptop's own panel is never part of a desk. Configuration and every switch use
the EDID; one whose EDID has no serial is identified by the whole EDID. A
configured monitor that doesn't appear within its wait (D12), the laptop at another desk
say, is skipped with a log line, not an error.

### D7. One monitor failing never stops the others

Each monitor is switched independently, as soon as it is reachable (D12), so one that is
slow to appear or missing holds up nobody. A failure is logged with the monitor and
ddcutil's exit status and error text. The unit exits non-zero if any monitor failed, so
`systemctl status uswitch` and `journalctl -u uswitch` show it.

### D8. uswitch writes the raw learned value

The configuration stores the `0x60` value `learn` read. Names (`DisplayPort-1`, `HDMI-1`,
`USB-C`) are for display only; nothing is looked up from a name table at switch time.

### D9. One command installs and uninstalls itself

There are no separate install and remove scripts, because the rule and unit embed the
installed path and one command writing and removing the same list can't drift from itself.

- `sudo ./uswitch install`, from a checkout, copies itself to `/usr/local/sbin/uswitch`,
  writes the units, polkit rule and modules-load entry, loads `i2c-dev`, reloads udev and
  systemd, and runs `learn` if there is no configuration yet. It is idempotent. It
  refuses, naming the package, when `ddcutil` is missing.
- `sudo uswitch uninstall` removes everything `install` and `learn` wrote, and reloads udev
  and systemd. It keeps `/etc/uswitch.conf` unless given `--purge`.

### D10. Four triggers, one action

Every trigger runs the same `uswitch switch`, so they run the same check (D11), behave
the same, and fold together when they coincide; the power button's skips the check (D13).

- **`usb`** (setup A): a configured trigger device arrives (D1).
- **`wake`**: the machine boots (once the rest of boot is done, so boot never waits on it)
  or resumes from suspend or hibernation. On by default. At a setup A desk a wake switches
  only while the desk's trigger device is present (D11), so a machine the switch doesn't
  point at never takes the display, and a switch whose devices stayed connected through
  the suspend still switches. At a setup B desk every wake switches, including wakes
  nobody asked for, like a timer or a laptop waking when its charger goes in; the
  monitor's KVM then moves the keyboard and mouse too, and getting back to a desktop means
  the monitor's buttons. Turn it off with `enabled = no` and rerun `sudo uswitch install`,
  which unhooks the unit from boot and resume. A laptop that stays awake with its lid shut
  never wakes; the manual trigger covers it.
- **manual**: a keyboard shortcut running `systemctl start uswitch.service`, which a
  polkit rule `install` writes lets the active local user start without a password;
  switching inputs is the whole of what it grants. At a setup A desk it does nothing while
  the switch points elsewhere, as the invariant says (D5); `sudo uswitch switch --force`
  skips the check.
- **`power`**: the power button (D13).

### D11. A machine can belong to several desks, and the monitors say which

A desk is a set of monitors, each with the input this machine is cabled to, plus at a setup
A desk the switch's trigger device. The configuration holds every desk the machine has
learned, and **the current desk is whichever configured monitors are connected**. A
monitor's EDID belongs to one physical monitor, so it says both where the machine is and
which input to pick; there is no desk to choose, and no network or location lookup.

On every trigger, each desk with a monitor connected gets the same check: a desk with a
trigger device switches only while that device is present; a desk without one switches.
Two desks with the same switch model are fine, since their monitors tell them apart.

`learn` replaces only the desk whose monitors are connected and keeps the rest. A monitor
belongs to one desk; learning it at another moves it there.

### D12. Waits are for conditions, bounded and logged

Nothing in uswitch sleeps a fixed time hoping the hardware is ready. Every wait is for
something it can observe, polled under a bound, and the journal records how long each took,
so slow hardware shows up as numbers rather than as switching that works most of the time.
Phase 0 measures what the bounds need; until it reports, they are provisional defaults in
the code. The orderings it respects:

- **The monitor must be reachable before a write.** At boot the trigger's coldplug event
  can come before the GPU driver and `i2c-dev` are up; after a resume the display link and
  the monitor's own wake take seconds. uswitch waits for a configured monitor's EDID to
  appear, which also says which desk this is (D11), and for each monitor to answer DDC/CI.
  None appearing within the bound means no learned desk is here, and the run ends quietly.
- **Read before write.** A monitor already showing this machine gets no write: a resume
  fires both `usb` and `wake`, and a redundant write can blank a monitor while it resyncs.
  A failed read is no reason to skip the write, since some monitors don't answer reads on
  an input they aren't showing.
- **Confirm after write.** A monitor resyncs for a second or more after switching, and can
  ignore a command that arrives meanwhile or while it wakes from power saving; ddcutil's
  own read-back is known to report failure on switches that worked. uswitch polls the input
  until it reads this machine's, and writes again if the monitor is elsewhere while the
  desk check (D11) still passes.
- **Check the desk before every write.** A machine the switch has moved away from writes
  nothing more, so a slow switch on the machine you left can't land after the one you went
  back to.
- **A trigger during a run is not lost.** systemd folds a start into a run already in
  progress, so a run continues until every connected monitor shows this machine or its
  bound runs out, writing only while the desk check passes, and looks once more before it
  ends. It recognizes a press away and back by the switch's hub re-enumerating (its USB
  device number changes), not by happening to look while the hub was gone, so a press
  during a slow DDC/CI call to another monitor still sends every monitor at that desk back
  to this machine.
- Whether to switch several monitors in parallel waits on Phase 0's timings. ddcutil keeps
  the protocol's own delays between commands.

### D13. The power button always switches

Pressing a machine's power button asks for the monitors, so it switches them to that
machine past the desk check (D11), at either kind of desk. On by default; turn it off with
`[power_button] enabled = no` and rerun `sudo uswitch install`.

- **While awake**, the press no longer suspends or powers off. `install` sets logind's
  `HandlePowerKey=ignore` and adds an acpid event that starts `uswitch-power.service`,
  which runs `uswitch switch --force`. A separate unit, so a press during another run is
  not folded into that run's desk check; both write the same input. A long press still
  powers off in firmware. logind takes the setting on reload where it supports one;
  restarting logind can end sessions, so otherwise it waits for the next boot. A desktop
  session (KDE, GNOME) takes the power key from logind while it runs: set its power-button
  action to *Do nothing*, or it acts on the press too.
- **Waking from sleep**, the press usually never reaches acpid. The kernel counts it on the
  power button's wakeup source (`PNP0C0C` or `LNXPWRBN` in `/sys/class/wakeup`). Every run
  records those counts in `/run/uswitch/power-button`; a press while awake starts a run
  that records it, so a count that grew between runs is a press that woke the machine, and
  that run skips the desk check. A run looks on every pass, and once more before it
  ends, since a suspend and resume during a run folds the resume's start into it; a press
  it finds starts every monitor over, like a press of the switch (D12). A boot records the first counts and never counts as a
  press, since a reboot, power restore or Wake-on-LAN looks the same; a machine booted
  with its power button switches only through the usual check. With `wake` off, `install`
  hooks `uswitch-power-wake.service` to boot and resume instead, running `uswitch switch
  --power-button-only`, which switches only on a power-button wake; its boot run only
  records the counts.
- **One handler.** `install` refuses while another acpid event file acts on the power
  button: a distribution's acpid can ship one that powers off the machine. It names the file to
  move aside. An unwanted press, a cat on the desk say, takes the display.
- At a setup B desk every wake already switches; the power button changes nothing there
  except while awake.

## Commands

| Command | Does |
|---|---|
| `uswitch install` | Install, then `learn` if unconfigured (D9). |
| `uswitch uninstall [--purge]` | Remove what `install` and `learn` wrote (D9). |
| `uswitch learn [--desk NAME] [--usb VENDOR:PRODUCT[:SERIAL]]` | Learn this desk's trigger and monitor inputs; write config and rule (D2, D11). |
| `uswitch switch [--dry-run] [--force] [--power-button-only]` | Switch the connected monitors to this machine, after the desk check unless `--force` or a power-button wake; with `--power-button-only`, only on a power-button wake; what the units run (D10, D11, D13). |
| `uswitch status` | Show the configured desks, which one is connected, whether its trigger is present, and each monitor's current input against this machine's. |

## What gets installed

Every trigger ends in the same place: `uswitch.service` running `uswitch switch`. udev
never runs uswitch itself (D3).

| Path | Written by | Does |
|---|---|---|
| `/usr/local/sbin/uswitch` | `install` | The program. |
| `/etc/uswitch.conf` | `learn` | Desks, monitors and inputs (*Configuration*). |
| `/etc/udev/rules.d/90-uswitch.rules` | `learn` | One line per learned switch: when that hub appears, start `uswitch.service`. No switch, no line. |
| `/etc/systemd/system/uswitch.service` | `install` | The oneshot that runs `uswitch switch`, hooked to boot and resume while `wake` is on. |
| `/etc/polkit-1/rules.d/50-uswitch.rules` | `install` | Lets the active local user start `uswitch.service` from a shortcut. |
| `/etc/modules-load.d/uswitch.conf` | `install` | Loads `i2c-dev` at boot. |
| `/etc/systemd/system/uswitch-power.service` | `install` | The oneshot acpid starts on a power-button press: `uswitch switch --force` (D13). |
| `/etc/systemd/system/uswitch-power-wake.service` | `install` | Hooked to boot and resume only while `wake` is off: `uswitch switch --power-button-only` (D13). |
| `/etc/acpi/events/uswitch` | `install` | On `button/power`, start `uswitch-power.service` (D13). |
| `/etc/systemd/logind.conf.d/uswitch.conf` | `install` | `HandlePowerKey=ignore`, so a press doesn't suspend (D13). |

The last four only while `power_button` is on; `install` removes them when it is off. It
also enables `acpid.service`, and leaves it enabled on uninstall.

`uninstall` removes all of them except the configuration, which `--purge` removes too.
Each run also records the power button's wakeup counts in `/run/uswitch/power-button`,
which goes with the next boot (D13).

## Configuration

Written by `learn` and readable by hand. The values below are placeholders:

```ini
# On by default; each desk's check still applies (D10, D11).
[wake]
enabled = yes

# On by default: the power button switches past the desk check (D13).
[power_button]
enabled = yes

# Setup A: the switch's own hub (D1).
[desk home]
usb_vendor = 1234
usb_product = 5678
usb_serial =
# Set only when another device shares this identity (D1).
usb_port =

# Setup B: no usb keys; the monitor is the switch.
[desk work]

[monitor DELL U2723QE ABC1234]
desk = home
mfg = DEL
model = DELL U2723QE
serial = ABC1234
# DisplayPort-1
input = 0x0f

[monitor DELL U2724DE XYZ5678]
desk = work
mfg = DEL
model = DELL U2724DE
serial = XYZ5678
# USB-C
input = 0x1b
```

Each machine has its own file, because each machine is cabled to different inputs.

## Laptops over USB-C

A laptop works like any other machine if three things hold:

- **Video goes straight to the monitor's USB-C input.** DisplayPort alternate mode carries
  the DisplayPort AUX channel, which carries DDC/CI. Whether the kernel exposes it for
  ddcutil depends on the GPU driver (i915, amdgpu and nouveau generally do); `ddcutil
  detect` on the laptop settles it.
- **In setup A, the laptop has its own USB path to the switch**, a cable from one of the
  switch's computer ports, separate from the video cable. Keyboard and mouse stay on the
  switch, not on the monitor's own ports: the Dell's hub rides the USB-C upstream (and, on
  models with a built-in KVM, follows the active input), so devices on it would move on a
  second schedule. `learn` never picks the monitor's hub as the trigger, because the hub
  doesn't come and go with the switch.
- **In setup B, the one USB-C cable is the whole connection**: video, the monitor's USB
  ports, and power. The monitor's KVM gives the laptop the keyboard and mouse once the
  input is USB-C, and waking the laptop (D10) is what picks that input. The monitor has to
  keep the inactive USB-C input's link alive for the laptop to reach it over DDC/CI; Phase 0
  step 3 shows whether it does.

**Adapters are the risky option.** A USB-C hub with an HDMI port converts DisplayPort to
HDMI with a chip that often doesn't pass DDC/CI through, and DisplayLink carries none.
DDC/CI through an MST dock depends on the driver. Prefer the direct connection; if a hub is
unavoidable, check `ddcutil detect` through it before relying on it.

## Setup

On each monitor's on-screen menu: **DDC/CI on**, and **Auto Select** and **USB-C
Prioritization** off (D5).

## Phase 0: prove it by hand before building

On each machine, before any code:

1. `sudo modprobe i2c-dev && sudo ddcutil detect`: the Dells appear with their EDID.
2. With the monitor showing this machine, `sudo ddcutil getvcp 0x60`: note the value.
3. Switch the monitor to another input with its buttons, then from this machine
   `sudo ddcutil --sn SERIAL setvcp 0x60 VALUE`. **This is the step that decides whether
   uswitch can work:** some monitors only listen to DDC/CI on the input they are showing.
4. Time step 3; it sets whether switch-time detection needs caching (D6, D12).
5. Setup A: `udevadm monitor --udev --subsystem-match=usb` while pressing the switch:
   which devices come and go, and whether the switch has a hub of its own (D1).
6. Setup B: suspend the machine, switch the monitor to another input, wake the machine, and
   run step 3 at once: whether the link is back in time, how long it took (D12), and whether
   the monitor's KVM moves its USB ports when DDC/CI changes the input, as it does for the
   buttons.
7. The power button (D13): `cat /sys/class/wakeup/*/name` lists `PNP0C0C:00` or
   `LNXPWRBN:00`; its `event_count` grows when the button wakes the machine from suspend,
   and `acpi_listen` shows `button/power` for a press while awake.
8. The numbers and quirks D12's bounds need: how long after a press the hub appears
   (`udevadm monitor` timestamps); how long after boot and after resume until
   `ddcutil detect` finds each monitor; how long a monitor takes to settle after
   `setvcp 0x60` (poll `getvcp 0x60`); whether `getvcp 0x60` answers on an input the
   monitor isn't showing; whether a monitor in power saving answers, and whether
   `setvcp 0x60` wakes it; whether a second `setvcp 0x60` sent mid-switch is ignored.

## Implementation

Python 3.10 or later (Ubuntu 22.04's), standard library only, in one executable file,
`uswitch`, that runs from a checkout with no build step. `ddcutil` is the only external
binary. The configuration is read and written with `configparser`.

## Testing

- `unittest`, run by `make test`.
- Learning is tested against a fake sysfs tree: which devices left and came back, the hub
  preference, USB 2 and SuperSpeed pairs, ambiguous identities forcing a port pin.
- ddcutil is mocked at the subprocess boundary: switch order, skipped absent monitors, one
  failure not stopping the rest (D7), exit status.
- Generated udev rule, units and polkit rule are tested as text, including rejection of
  unsafe device strings (D1).
- Desk checks (D10, D11): `wake` at a setup B desk switches; at a setup A desk only while
  its trigger device is present; `--force` skips the check; monitors of other desks are
  skipped; `learn` replaces only the connected desk.
- Waits and races (D12) run on an injected clock against a fake monitor that becomes
  reachable, settles, or ignores a write after a set number of polls, and a trigger device
  that leaves and returns mid-run. No test sleeps.
- `install` and `uninstall` run against a temporary root and leave nothing behind; the
  power-button files come and go with the setting, and `install` refuses without acpid or
  beside another acpid power-button handler.
- Power-button wakes (D13) against a fake `/sys/class/wakeup`: a count that grew between
  runs skips the desk check; an unchanged one, the first run after boot, another wakeup
  source, or the setting off don't.
- Real udev, real monitors and the USB switch need hardware: Phase 0, then a manual
  checklist per release.

## Known limitations

- A write stuck in ddcutil for longer than the other machine's whole run can still land
  last and leave the monitors on the machine you left (D12). Press again.
- Two identical monitors without an EDID serial can't be told apart; `learn` keeps one
  (D6).
- Daisy-chained (DisplayPort MST) monitors aren't covered: switching the nearer one can cut
  the farther one's signal.
- In setup B, `wake` can't tell a wake you asked for from one you didn't (D10).
- A power button whose wakeup source isn't `PNP0C0C` or `LNXPWRBN`, or that the kernel
  doesn't count when it wakes the machine, wakes it without skipping the desk check (D13);
  Phase 0 step 7 tells. A press while awake that acpid misses goes uncounted until the next
  run, which then takes it as a wake.

## Out of scope

- Switching away when the devices leave (see *How it works*).
- Moving the USB devices themselves; the switch does that.
- Coordinating machines over the network, or a software KVM (Barrier, Input Leap).
- Audio routing.
- Non-Dell monitors beyond best effort; non-Linux; non-systemd.

## Prior art

- **[display-switch](https://github.com/haimgel/display-switch)** does the same job across
  Linux, macOS and Windows: a background program per user that watches one USB
  `vendor:product` and switches inputs you name in an INI file. uswitch differs where this
  setup bites: it runs from udev with no resident process, works with nobody logged in
  (D4), learns the trigger and inputs instead of having them typed (D2), disambiguates a
  generic hub chip by serial or port (D1), covers a monitor that is its own KVM (D10), and
  follows a laptop between desks (D11).
- **`ddcinput`** in mikelward/scripts: the 2021 script switching an input by name through
  ddcutil. It stays as it is; uswitch doesn't depend on it.
