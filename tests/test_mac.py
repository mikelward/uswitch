"""The Mac platform: m1ddc's monitors and DDC/CI, ioreg's USB devices, the
user's Library, the lock (SPEC.md D14-D17). m1ddc is faked at the command
boundary, answering as its source does: errors on stdout, a non-zero exit, and
"(null)" for an attribute macOS didn't give it. ioreg is faked the same way,
shaped as a real Mac printed its USB plane."""

import fcntl
import os
import plistlib
import unittest
from unittest import mock

from support import Case, uswitch

DP1, HDMI1, USBC = 0x0F, 0x11, 0x1B
HIGH, FULL, SUPER = 2, 1, 3  # IOKit's "Device Speed"
DELL = "11111111-2222-3333-4444-555555555555"
NO_SERIAL = "66666666-7777-8888-9999-aaaaaaaaaaaa"
BUILT_IN = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


def listing(*displays):
    """`m1ddc display list detailed` for (uuid, name, mfg, serial, edid_uuid)."""
    lines = []
    for i, (uuid, name, mfg, serial, edid_uuid) in enumerate(displays, 1):
        lines += [
            f"[{i}] {name} ({uuid})",
            f" - Product name:  {name}",
            f" - Manufacturer:  {mfg}",
            f" - AN Serial:     {serial}",
            " - Vendor:        4268 (0x10ac)",
            " - Model:         16932 (0x4224)",
            " - Serial:        0 (0x0000)",
            f" - Display ID:    {i}",
            f" - System UUID:   {uuid}",
            f" - EDID UUID:     {edid_uuid}",
            " - IO Location:   IOService:/AppleARMPE/arm-io/disp0",
            " - Adapter:       1234",
        ]
    return "\n".join(lines) + "\n"


class FakeM1ddc:
    """m1ddc as uswitch calls it: list, get input, set input."""

    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.displays = {}  # system uuid -> [name, mfg, serial, edid uuid, current, answers, ignore]
        self.on_set = None  # runs after a write, as (uuid, value)
        self.missing = False
        self.high_byte = 0

    def add(self, uuid, name="DELL U2723QE", mfg="DEL", serial="ABC1234", edid_uuid=None, current=HDMI1, answers=True, ignore=0):
        self.displays[uuid] = [name, mfg, serial, edid_uuid or uuid, current, answers, ignore]

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if argv[0] != "m1ddc":
            return uswitch.Result(0, "", "")
        if self.missing:
            raise uswitch.MissingCommand("m1ddc not found")
        if argv[1:3] == ["display", "list"]:
            if not self.displays:
                return uswitch.Result(1, "No external display found, aborting", "")
            rows = [(u, d[0], d[1], d[2], d[3]) for u, d in self.displays.items()]
            return uswitch.Result(0, listing(*rows), "")
        display = self.displays.get(argv[2])
        if display is None:
            return uswitch.Result(1, "The specified display does not exist.\n", "")
        if argv[3] == "get":
            if not display[5]:
                return uswitch.Result(1, "DDC communication failure: (ipc/send) timed out\n", "")
            return uswitch.Result(0, f"{self.high_byte << 8 | display[4]}\n", "")
        if display[6]:
            display[6] -= 1  # a write the monitor ignores, mid-resync say
        else:
            display[4] = int(argv[5])
        if self.on_set:
            self.on_set(argv[2], int(argv[5]))
        return uswitch.Result(0, f"Writing {argv[5]}\n", "")

    def m1ddc(self):
        return [c for c in self.calls if c[0] == "m1ddc"]


def parent(location):
    """The hub a location ID hangs from: its last hub tier cleared."""
    for shift in range(0, 24, 4):
        if location >> shift & 0xF:
            return location & ~(0xF << shift)
    return None


class FakeIoreg:
    """`ioreg -a -l -p IOUSB`: a root hub per bus, and each device under the hub
    its location ID names, with a new session ID every time it's plugged in."""

    def __init__(self):
        self.devices = {}  # location ID -> its properties
        self.session = 1000
        self.calls = 0
        self.output = None  # stands in for the whole output when set

    def plug(self, location, vendor, product, name="", serial="", hub=False, speed=HIGH, maker="Generic"):
        self.session += 1
        entry = {
            "idVendor": vendor,
            "idProduct": product,
            "locationID": location,
            "sessionID": self.session,
            "bDeviceClass": 9 if hub else 0,
            "Device Speed": speed,
            "USB Product Name": name,
            "USB Vendor Name": maker,
            "IOObjectClass": "AppleUSBDevice",
        }
        if serial:
            entry["USB Serial Number"] = serial
        self.devices[location] = entry

    def unplug(self, location):
        del self.devices[location]

    def tree(self):
        def under(hub):
            found = []
            for location in sorted(self.devices):
                if parent(location) == hub:
                    entry = dict(self.devices[location])
                    if location & 0x80000000:  # as a T2 Mac's internal bus reports it
                        entry["locationID"] = location | 0xFFFFFFFF00000000
                    children = under(location)
                    if children:
                        entry["IORegistryEntryChildren"] = children
                    found.append(entry)
            return found

        roots = []
        for bus in sorted({location >> 24 for location in self.devices}):
            roots.append(
                {
                    "IOObjectClass": "AppleUSBRootHubDevice",
                    "idVendor": 0x05AC,
                    "idProduct": 0x8007,
                    "locationID": bus << 24 | (0xFFFFFFFF00000000 if bus & 0x80 else 0),
                    "bDeviceClass": 9,
                    "Device Speed": SUPER,
                    "USB Product Name": "AppleUSBXHCI Root Hub Simulation",
                    "IORegistryEntryChildren": under(bus << 24),
                }
            )
        return {"IORegistryEntryName": "Root", "IOObjectClass": "IORegistryEntry", "IORegistryEntryChildren": roots}

    def __call__(self, argv, timeout):
        self.calls += 1
        if self.output is not None:
            return self.output
        return uswitch.Result(0, plistlib.dumps(self.tree()).decode(), "")


class MacCase(Case):
    def setUp(self):
        super().setUp()
        self.m1ddc = FakeM1ddc(self.clock)
        self.ioreg = FakeIoreg()
        self.ctx.run = lambda argv, timeout: (self.ioreg if argv[0] == "ioreg" else self.m1ddc)(argv, timeout)
        self.ctx.platform = uswitch.Mac()
        self.ctx.home = os.path.join(self.tmp, "home")
        self.ctx.sysfs = os.path.join(self.tmp, "no-sysfs")

    def conf(self):
        return os.path.join(self.ctx.home, "Library/Application Support/mswitch/mswitch.conf")

    def learned(self, uuid=DELL, value=DP1, usb=None, **kw):
        """A desk with one monitor, this Mac on `value`, written where mswitch reads it."""
        return self.learned_all([(uuid, value, kw)], usb)

    def learned_all(self, monitors, usb=None):
        """A desk with each (uuid, this Mac's input, m1ddc.add arguments)."""
        config = uswitch.Config()
        config.desks["home"] = uswitch.Desk("home", usb)
        for uuid, value, kw in monitors:
            self.m1ddc.add(uuid, **kw)
            edid = uswitch.parse_m1ddc_list(listing((uuid, *self.m1ddc.displays[uuid][:4])))[uuid]
            config.monitors.append(uswitch.monitor_from_edid(edid, "home", value))
        uswitch.write_file(self.conf(), uswitch.config_text(config, self.ctx.platform))
        return config


class ListTest(unittest.TestCase):
    def test_reads_identity_and_addresses_by_system_uuid(self):
        found = uswitch.parse_m1ddc_list(
            listing(
                (DELL, "DELL U2723QE", "DEL", "ABC1234", "edid-1"),
                (NO_SERIAL, "DELL P2422H", "DEL", "(null)", NO_SERIAL.upper()),
            )
        )
        self.assertEqual(list(found), [DELL, NO_SERIAL])
        dell = found[DELL]
        self.assertEqual((dell.mfg, dell.model, dell.serial), ("DEL", "DELL U2723QE", "ABC1234"))
        self.assertEqual(found[NO_SERIAL].serial, "")
        self.assertEqual(found[NO_SERIAL].key, NO_SERIAL)

    def test_nothing_listed_is_no_monitors(self):
        self.assertEqual(uswitch.parse_m1ddc_list("No external display found, aborting"), {})


class IoregTest(unittest.TestCase):
    def setUp(self):
        self.ioreg = FakeIoreg()

    def devices(self):
        return uswitch.parse_ioreg_usb(self.ioreg(["ioreg"], 1).stdout.encode())

    def test_reads_each_device_at_its_location_id(self):
        self.ioreg.plug(0x14500000, 0x0BDA, 0x5409, "USB2.1 Hub", hub=True)
        self.ioreg.plug(0x14510000, 0x0BDA, 0x5409, "USB2.1 Hub", hub=True)
        self.ioreg.plug(0x14512000, 0x0BDA, 0x5411, "4-Port USB 2.0 Hub", hub=True)
        self.ioreg.plug(0x14512200, 0x046D, 0xC543, "USB Receiver", serial="SERIAL1", speed=FULL, maker="Logitech")
        found = self.devices()
        self.assertEqual(sorted(found), ["0x14500000", "0x14510000", "0x14512000", "0x14512200"])  # no root hub
        hub, receiver = found["0x14500000"], found["0x14512200"]
        self.assertEqual((hub.vendor, hub.product, hub.hub, hub.speed, hub.depth), ("0bda", "5409", True, 480.0, 1))
        self.assertEqual(hub.name, "Generic USB2.1 Hub")
        self.assertEqual((receiver.serial, receiver.hub, receiver.speed, receiver.depth), ("SERIAL1", False, 12.0, 4))
        self.assertEqual(receiver.describe(), "046d:c543 serial SERIAL1 at 0x14512200 Logitech USB Receiver")

    def test_a_new_enumeration_is_a_new_session(self):
        self.ioreg.plug(0x14500000, 0x0BDA, 0x5409, hub=True)
        before = self.devices()["0x14500000"]
        self.ioreg.plug(0x14500000, 0x0BDA, 0x5409, hub=True)  # away and back
        after = self.devices()["0x14500000"]
        self.assertEqual(before, after)  # the same device...
        self.assertNotEqual(before.devnum, after.devnum)  # ...enumerated again (D12)
        trigger = uswitch.UsbTrigger("0bda", "5409")
        self.assertNotEqual(trigger.generation({"a": before}), trigger.generation({"a": after}))

    def test_a_sign_extended_location_reads_as_its_32_bits(self):
        self.ioreg.plug(0x80200000, 0x05AC, 0x8103, "Headset")
        self.assertEqual(list(self.devices()), ["0x80200000"])
        self.assertEqual(self.devices()["0x80200000"].depth, 1)

    def test_a_device_without_strings_reads_with_empty_ones(self):
        self.ioreg.plug(0x14100000, 0x1234, 0x5678, name="", maker="")
        device = self.devices()["0x14100000"]
        self.assertEqual((device.name, device.serial), ("", ""))

    def test_output_it_cant_read_is_an_error(self):
        for bad in (b"", b"<plist", b"not a plist"):
            with self.subTest(bad=bad), self.assertRaises(uswitch.UswitchError) as e:
                uswitch.parse_ioreg_usb(bad)
            self.assertIn("can't read ioreg's USB devices", str(e.exception))

    def test_a_location_id_pins_a_port(self):
        self.assertEqual(uswitch.UsbDevice("0x14512200", "1234", "5678", "", "", False, 12.0).depth, 4)
        uswitch.UsbTrigger("1234", "5678", port="0x14500000").validate("here")
        for bad in ("0x1450", "14500000", "0x1450000g"):
            with self.subTest(port=bad), self.assertRaises(uswitch.UswitchError) as e:
                uswitch.UsbTrigger("1234", "5678", port=bad).validate("here")
            self.assertIn("a location ID like 0x14100000", str(e.exception))


class MacSwitchTest(MacCase):
    def test_switches_through_m1ddc_by_system_uuid(self):
        self.learned()
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn(["m1ddc", "display", DELL, "set", "input", "15"], self.m1ddc.m1ddc())
        self.assertEqual(self.m1ddc.displays[DELL][4], DP1)
        self.assertIn("confirmed", self.out.getvalue())

    def test_reads_the_inputs_low_byte(self):
        self.learned()
        self.m1ddc.displays[DELL][4] = DP1
        self.m1ddc.high_byte = 0x01  # Dells answer with a high byte set
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn("already showing", self.out.getvalue())

    def test_a_monitor_that_doesnt_answer_reads_is_still_written(self):
        self.learned(answers=False)
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertEqual(self.m1ddc.displays[DELL][4], DP1)
        self.assertIn("doesn't answer reads to confirm (DDC communication failure", self.out.getvalue())

    def test_a_monitor_without_a_serial_is_known_by_its_edid_uuid(self):
        self.learned(uuid=NO_SERIAL, serial="(null)", edid_uuid=NO_SERIAL.upper())
        self.assertIn(f"edid = {NO_SERIAL}", open(self.conf()).read())
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertEqual(self.m1ddc.displays[NO_SERIAL][4], DP1)

    def test_no_external_display_is_not_a_learned_desk(self):
        self.learned()
        del self.m1ddc.displays[DELL]
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn("not at a learned desk", self.out.getvalue())

    def test_a_failing_listing_is_reported_not_taken_as_no_monitors(self):
        self.learned()
        real = self.m1ddc.__call__

        def broken(argv, timeout):
            if argv[1:3] == ["display", "list"]:
                return uswitch.Result(134, "", "Abort trap: 6")
            return real(argv, timeout)

        self.ctx.run = broken
        self.assertEqual(uswitch.main(["switch"], self.ctx), 1)
        self.assertIn("m1ddc display list failed: Abort trap: 6", self.err.getvalue())

    def test_missing_m1ddc_names_the_install(self):
        self.learned()
        self.m1ddc.missing = True
        self.assertEqual(uswitch.main(["switch"], self.ctx), 1)
        self.assertIn("mswitch: m1ddc is not installed; install it with `brew install m1ddc`", self.err.getvalue())

    def test_a_switch_already_running_takes_the_trigger(self):
        self.learned()
        lock = os.path.join(os.path.dirname(self.conf()), "switch.lock")
        with open(lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn("already running", self.out.getvalue())
        self.assertEqual(self.m1ddc.m1ddc(), [])
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)  # the lock goes with the run
        self.assertEqual(self.m1ddc.displays[DELL][4], DP1)

    def test_a_trigger_during_a_run_brings_back_a_monitor_it_had_finished(self):
        # Codex on #13: the first monitor is done and the run is settling the
        # second when another machine takes the first and a trigger arrives.
        second = "22222222-2222-3333-4444-555555555555"
        self.learned_all([(DELL, DP1, {}), (second, DP1, {"serial": "DEF5678", "ignore": 1})])
        writes = []

        def take_the_first(uuid, value):
            if uuid != second:
                return
            writes.append(value)
            if len(writes) == 2:  # the rewrite, once the first is confirmed
                self.assertIn("confirmed", self.out.getvalue())
                self.m1ddc.displays[DELL][4] = HDMI1  # the other machine took it
                self.assertEqual(uswitch.main(["switch"], self.ctx), 0)

        self.m1ddc.on_set = take_the_first
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn("already running; it takes this trigger too", self.out.getvalue())
        self.assertEqual(self.m1ddc.displays[DELL][4], DP1)
        self.assertEqual(self.m1ddc.displays[second][4], DP1)

    def test_a_trigger_after_the_last_look_is_served_once_the_lock_is_free(self):
        # A start that finds the lock held just before the run lets go leaves
        # its request behind: the run looks again once it has let go.
        self.learned()
        pending = os.path.join(os.path.dirname(self.conf()), "switch.pending")
        real_flock = fcntl.flock
        arrived = []

        def flock(f, op):
            if op == fcntl.LOCK_UN and not arrived:
                arrived.append(True)
                self.m1ddc.displays[DELL][4] = HDMI1  # taken away again
                open(pending, "a").close()  # a start that found the lock held
            return real_flock(f, op)

        with mock.patch.object(uswitch.fcntl, "flock", flock):
            self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertEqual(self.m1ddc.displays[DELL][4], DP1)
        self.assertFalse(os.path.exists(pending))

    def test_a_forced_trigger_left_to_a_run_is_run_forced(self):
        self.learned()
        directory = os.path.dirname(self.conf())
        forced = []
        self.ctx.platform.serialize(self.ctx, True, lambda force: forced.append(force) or 0)
        self.ctx.platform.serialize(self.ctx, False, lambda force: forced.append(force) or 0)
        self.assertEqual(forced, [True, False])
        self.assertEqual(sorted(os.listdir(directory)), ["mswitch.conf", "switch.lock"])

    def test_a_setup_a_desk_switches_only_while_its_switch_points_here(self):
        self.learned(usb=uswitch.UsbTrigger("0bda", "5409"))
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn("points at another machine", self.out.getvalue())
        self.assertEqual(self.m1ddc.displays[DELL][4], HDMI1)
        self.ioreg.plug(0x14500000, 0x0BDA, 0x5409, hub=True)
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertEqual(self.m1ddc.displays[DELL][4], DP1)

    def test_a_press_away_and_back_unseen_is_caught_by_its_session(self):
        # The hub leaves and comes back between two looks: only its new session
        # ID shows the press, which brings back a monitor already finished with.
        second = "22222222-2222-3333-4444-555555555555"
        self.ioreg.plug(0x14500000, 0x0BDA, 0x5409, hub=True)
        self.learned_all([(DELL, DP1, {}), (second, DP1, {"serial": "DEF5678", "ignore": 1})], uswitch.UsbTrigger("0bda", "5409"))
        writes = []

        def away_and_back(uuid, value):
            if uuid == second:
                writes.append(value)
                if len(writes) == 1:
                    self.m1ddc.displays[DELL][4] = HDMI1  # the other machine took it
                    self.ioreg.plug(0x14500000, 0x0BDA, 0x5409, hub=True)

        self.m1ddc.on_set = away_and_back
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn("went away and came back", self.out.getvalue())
        self.assertEqual((self.m1ddc.displays[DELL][4], self.m1ddc.displays[second][4]), (DP1, DP1))

    def test_a_failing_ioreg_is_reported(self):
        self.learned(usb=uswitch.UsbTrigger("0bda", "5409"))
        self.ioreg.output = uswitch.Result(1, "", "ioreg: something went wrong")
        self.assertEqual(uswitch.main(["switch"], self.ctx), 1)
        self.assertIn("mswitch: ioreg failed: ioreg: something went wrong", self.err.getvalue())

    def test_refuses_to_run_as_root(self):
        self.ctx.root = "/"  # live
        self.ctx.euid = 0
        self.assertEqual(uswitch.main(["switch"], self.ctx), 1)
        self.assertIn("runs as you, not as root", self.err.getvalue())
        self.assertEqual(self.m1ddc.calls, [])


class MacLearnTest(MacCase):
    def test_learns_a_monitor_as_its_own_switch_into_the_users_library(self):
        self.m1ddc.add(DELL, current=USBC)
        self.m1ddc.add(BUILT_IN, name="Color LCD", mfg="APP", serial="(null)", answers=False)
        self.answers = ["home", "b", ""]
        self.assertEqual(uswitch.main(["learn"], self.ctx), 0)
        text = open(self.conf()).read()
        self.assertIn("# Written by mswitch learn", text)
        self.assertNotIn("[power_button]", text)
        config = uswitch.load_config(self.conf())
        self.assertEqual([(m.desk, m.serial, m.input) for m in config.monitors], [("home", "ABC1234", USBC)])
        self.assertIsNone(config.desks["home"].usb)
        self.assertIn("Color LCD", self.out.getvalue())  # the built-in panel doesn't answer: skipped
        self.assertIn(f"Wrote desk home to {self.conf()}.", self.out.getvalue())

    def add_switch(self):
        """The switch's USB 3 hub pair and a receiver behind it, plus a built-in device."""
        self.ioreg.plug(0x14500000, 0x0BDA, 0x5409, "USB2.1 Hub", hub=True)
        self.ioreg.plug(0x14100000, 0x0BDA, 0x0409, "USB3.1 Hub", hub=True, speed=SUPER)
        self.ioreg.plug(0x14510000, 0x046D, 0xC543, "USB Receiver", speed=FULL)
        self.ioreg.plug(0x80100000, 0x05AC, 0x8233, "Apple T2 Controller", serial="SERIAL2")

    def press(self, away_at=1.0, back_at=3.0):
        """Press the switch away and back; the other machine takes the monitor."""
        pair = [(0x14500000, (0x0BDA, 0x5409, "USB2.1 Hub"), {"hub": True}),
                (0x14100000, (0x0BDA, 0x0409, "USB3.1 Hub"), {"hub": True, "speed": SUPER}),
                (0x14510000, (0x046D, 0xC543, "USB Receiver"), {"speed": FULL})]

        def away():
            for location, _, _ in pair:
                self.ioreg.unplug(location)
            self.m1ddc.displays[DELL][4] = HDMI1

        def back():
            for location, args, kw in pair:
                self.ioreg.plug(location, *args, **kw)

        self.clock.at(away_at, away)
        self.clock.at(back_at, back)

    def test_learns_a_usb_switchs_usb2_hub(self):
        self.m1ddc.add(DELL, current=DP1)
        self.add_switch()
        self.press()
        self.answers = ["home", "a", ""]
        self.assertEqual(uswitch.main(["learn"], self.ctx), 0)
        config = uswitch.load_config(self.conf())
        self.assertEqual(config.desks["home"].usb, uswitch.UsbTrigger("0bda", "5409"))
        self.assertIn("Trigger: 0bda:5409.", self.out.getvalue())
        self.assertEqual(self.m1ddc.displays[DELL][4], DP1)  # switched back
        self.assertEqual(uswitch.main(["status"], self.ctx), 0)
        self.assertIn("USB switch 0bda:5409, present", self.out.getvalue())

    def test_a_shared_hub_chip_pins_the_location_id(self):
        self.m1ddc.add(DELL, current=DP1)
        self.add_switch()
        self.ioreg.plug(0x14200000, 0x0BDA, 0x5409, "USB2.1 Hub", hub=True)  # a dock's
        self.press()
        self.answers = ["home", "a", ""]
        self.assertEqual(uswitch.main(["learn"], self.ctx), 0)
        self.assertIn("usb_port = 0x14500000", open(self.conf()).read())
        config = uswitch.load_config(self.conf())
        self.assertEqual(config.desks["home"].usb, uswitch.UsbTrigger("0bda", "5409", "", "0x14500000"))

    def test_usb_option_takes_the_switch_without_presses(self):
        self.m1ddc.add(DELL, current=DP1)
        self.add_switch()
        self.clock.at(1.0, lambda: self.m1ddc.displays[DELL].__setitem__(4, HDMI1))  # its buttons
        self.answers = [""]
        self.assertEqual(uswitch.main(["learn", "--desk", "home", "--usb", "0BDA:5409"], self.ctx), 0)
        self.assertEqual(uswitch.load_config(self.conf()).desks["home"].usb, uswitch.UsbTrigger("0bda", "5409"))

    def test_status_has_no_power_button(self):
        self.learned()
        self.assertEqual(uswitch.main(["status"], self.ctx), 0)
        out = self.out.getvalue()
        self.assertIn("wake: on", out)
        self.assertNotIn("power button", out)
        self.assertIn("showing 0x11 (HDMI-1)", out)


if __name__ == "__main__":
    unittest.main()
