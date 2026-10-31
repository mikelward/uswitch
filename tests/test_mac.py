"""The Mac platform: m1ddc's monitors and DDC/CI, the user's Library, the lock
(SPEC.md D14-D17). m1ddc is faked at the command boundary, answering as its
source does: errors on stdout, a non-zero exit, and "(null)" for an attribute
macOS didn't give it."""

import fcntl
import os
import unittest
from unittest import mock

from support import Case, uswitch

DP1, HDMI1, USBC = 0x0F, 0x11, 0x1B
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


class MacCase(Case):
    def setUp(self):
        super().setUp()
        self.m1ddc = FakeM1ddc(self.clock)
        self.ctx.run = self.m1ddc
        self.ctx.platform = uswitch.Mac()
        self.ctx.home = os.path.join(self.tmp, "home")
        self.ctx.sysfs = os.path.join(self.tmp, "no-sysfs")

    def conf(self):
        return os.path.join(self.ctx.home, "Library/Application Support/mswitch/mswitch.conf")

    def learned(self, uuid=DELL, value=DP1, **kw):
        """A desk with one monitor, this Mac on `value`, written where mswitch reads it."""
        return self.learned_all([(uuid, value, kw)])

    def learned_all(self, monitors):
        """A desk with each (uuid, this Mac's input, m1ddc.add arguments)."""
        config = uswitch.Config()
        config.desks["home"] = uswitch.Desk("home")
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
        self.answers = ["home", ""]  # the desk's name, then Enter: no USB switch question
        self.assertEqual(uswitch.main(["learn"], self.ctx), 0)
        text = open(self.conf()).read()
        self.assertIn("# Written by mswitch learn", text)
        self.assertNotIn("[power_button]", text)
        config = uswitch.load_config(self.conf())
        self.assertEqual([(m.desk, m.serial, m.input) for m in config.monitors], [("home", "ABC1234", USBC)])
        self.assertIsNone(config.desks["home"].usb)
        self.assertIn("Color LCD", self.out.getvalue())  # the built-in panel doesn't answer: skipped
        self.assertIn(f"Wrote desk home to {self.conf()}.", self.out.getvalue())

    def test_a_usb_switch_is_refused_until_it_is_built(self):
        self.m1ddc.add(DELL, current=DP1)
        self.answers = ["home"]
        self.assertEqual(uswitch.main(["learn", "--usb", "1234:5678"], self.ctx), 2)
        self.assertIn("can't use a USB switch yet", self.err.getvalue())

    def test_status_has_no_power_button(self):
        self.learned()
        self.assertEqual(uswitch.main(["status"], self.ctx), 0)
        out = self.out.getvalue()
        self.assertIn("wake: on", out)
        self.assertNotIn("power button", out)
        self.assertIn("showing 0x11 (HDMI-1)", out)


if __name__ == "__main__":
    unittest.main()
