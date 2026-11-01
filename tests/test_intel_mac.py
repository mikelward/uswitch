"""An Intel Mac: monitors and DDC/CI through ddcctl (SPEC.md D14, D17).
ddcctl is faked at the command boundary, printing as its source does: every
line on stdout, a DDC/CI failure as an `E:` line with exit status 0, and the
`E:` line about WindowServer's preferences it prints on every run, which is
no failure."""

import os
import unittest
from unittest import mock

from support import uswitch
from test_mac import MacCase

DP1, HDMI1, USBC = 0x0F, 0x11, 0x1B
LEFT = "11111111-2222-3333-4444-555555555555"
RIGHT = "22222222-3333-4444-5555-666666666666"
DOCK = "33333333-4444-5555-6666-777777777777"


class FakeDdcctl:
    """ddcctl as uswitch runs it: `-d N`, `-d N -i ?` and `-d N -i VALUE`."""

    def __init__(self):
        self.calls = []
        self.displays = []  # in ddcctl's numbering: [uuid, name, serial, current, answers, ignore]
        self.on_run = None  # runs as a run starts, to change the displays under it
        self.writes = []  # (uuid, value) for every write, wherever it landed
        self.missing = False
        self.failing = {}  # uuid -> "framebuffer" or "edid": where ddcctl gives up on it

    def add(self, uuid, name="DELL U2723QE", serial="ABC1234", current=HDMI1, answers=True, ignore=0):
        self.displays.append([uuid, name, serial, current, answers, ignore])

    def display(self, uuid):
        return next(d for d in self.displays if d[0] == uuid)

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if self.missing:
            raise uswitch.MissingCommand("ddcctl not found")
        if self.on_run:
            self.on_run(argv)
        n = len(self.displays)
        lines = [f"D: CGDisplay {d[0].upper()} dispID(#{70 + i}) (3440x1440 0°) 109.00 DPI" for i, d in enumerate(self.displays, 1)]
        lines.append(f"I: found {n} external display{'s' if n > 1 else ''}")
        args = argv[1:]
        index = int(args[1]) if args[:1] == ["-d"] else 0
        if not 1 <= index <= n:
            return uswitch.Result(1, "\n".join(lines) + "\n", "ddcctl 0.1x - Usage: ...\n")
        d = self.displays[index - 1]
        lines.append(
            "E: Failed to find display in WindowServer's preferences! (/Library/Preferences/com.apple.windowserver.plist)"
        )
        if self.failing.get(d[0]) == "framebuffer":
            lines.append("E: Failed to acquire framebuffer device for display")
            return uswitch.Result(255, "\n".join(lines) + "\n", "")
        lines.append(f"I: polling EDID for #{index} (ID {70 + index} => (null))")
        if self.failing.get(d[0]) == "edid":
            lines.append("E: Failed to poll display!")
            return uswitch.Result(255, "\n".join(lines) + "\n", "")
        if d[2]:
            lines.append(f"I: got edid.serial: {d[2]}")
        if d[1]:
            lines.append(f"I: got edid.name: {d[1]}")
        if args[2:3] == ["-i"]:
            lines.append(f"D: action: i: {args[3]}")
            if args[3] == "?":
                lines.append("D: querying VCP control: #96 =?")
                if d[4]:
                    lines.append(f"I: VCP control #96 (0x60) = current: {d[3]}, max: 14")
                else:
                    lines += ["E: DDC send command failed!", "E: VCP control #96 (0x60) = current: 0, max: 0"]
            else:
                lines.append(f"D: setting VCP control #96 => {args[3]}")
                self.writes.append((d[0], int(args[3])))
                if d[5]:
                    d[5] -= 1  # a write the monitor ignores, mid-resync say
                else:
                    d[3] = int(args[3])
        return uswitch.Result(0, "\n".join(lines) + "\n", "")


class IntelCase(MacCase):
    def setUp(self):
        super().setUp()
        self.ddcctl = FakeDdcctl()
        self.ctx.platform = uswitch.Mac(machine="x86_64")

    def command(self, argv, timeout):
        if os.path.basename(argv[0]) == "ddcctl":
            return self.ddcctl(argv, timeout)
        return super().command(argv, timeout)

    def learned_intel(self, displays, usb=None):
        """A desk with each (uuid, this Mac's input, ddcctl.add arguments), known
        by what ddcctl reads from them."""
        for uuid, _, kw in displays:
            self.ddcctl.add(uuid, **kw)
        found = {d.uuid.lower(): d for d in uswitch.Ddcctl(self.ctx).displays()}
        config = uswitch.Config()
        config.desks["home"] = uswitch.Desk("home", usb)
        for uuid, value, _ in displays:
            config.monitors.append(uswitch.monitor_from_edid(found[uuid].edid, "home", value))
        uswitch.write_file(self.conf(), uswitch.config_text(config, self.ctx.platform))
        self.ddcctl.calls.clear()
        return config


class ParseTest(unittest.TestCase):
    def test_reads_the_displays_their_numbering_and_display_ns_edid(self):
        run = uswitch.parse_ddcctl(
            "D: CGDisplay 11111111-2222-3333-4444-555555555555 dispID(#71) (3440x1440 0°) 109.00 DPI\n"
            "D: CGDisplay 22222222-3333-4444-5555-666666666666 dispID(#72) (2560x1440 0°) HiDPI\n"
            "I: found 2 external displays\n"
            "E: Failed to find display in WindowServer's preferences! (/Library/Preferences/com.apple.windowserver.plist)\n"
            "I: polling EDID for #2 (ID 72 => (null))\n"
            "I: got edid.serial: ABC1234\n"
            "I: got edid.name: DELL U2723QE\n"
            "D: action: i: ?\n"
            "D: querying VCP control: #96 =?\n"
            "I: VCP control #96 (0x60) = current: 27, max: 14\n"
        )
        self.assertEqual((run.found, run.uuids), (2, [LEFT, RIGHT]))
        self.assertEqual((run.name, run.serial, run.value, run.error), ("DELL U2723QE", "ABC1234", 27, ""))

    def test_a_failed_read_is_an_error_not_an_input(self):
        run = uswitch.parse_ddcctl(
            "I: found 1 external display\n"
            "D: querying VCP control: #96 =?\n"
            "E: DDC send command failed!\n"
            "E: VCP control #96 (0x60) = current: 0, max: 0\n"
        )
        self.assertEqual((run.value, run.error), (None, "DDC send command failed!"))


class IntelSwitchTest(IntelCase):
    def test_switches_through_ddcctl_by_number(self):
        self.learned_intel([(LEFT, DP1, {"current": DP1}), (RIGHT, USBC, {"serial": "DEF5678"})])
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertEqual(self.ddcctl.writes, [(RIGHT, USBC)])
        self.assertIn(["ddcctl", "-d", "2", "-i", "27"], self.ddcctl.calls)
        self.assertEqual(self.m1ddc.calls, [])  # an Intel Mac never runs m1ddc
        self.assertIn("confirmed", self.out.getvalue())

    def test_a_monitor_that_doesnt_answer_reads_is_still_written(self):
        self.learned_intel([(LEFT, USBC, {"answers": False})])
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertEqual(self.ddcctl.display(LEFT)[3], USBC)
        self.assertIn("doesn't answer reads to confirm (DDC send command failed!", self.out.getvalue())

    def test_a_renumbered_display_is_never_written_in_its_place(self):
        # Between reading number 1 and writing it, a dock's display takes number 1
        # and the monitor this run means becomes number 2.
        self.learned_intel([(LEFT, USBC, {})])
        self.ddcctl.add(DOCK, name="DOCK DISPLAY", serial="DOCK1")
        state = {"read": False}

        def dock_comes_first(argv):
            if state["read"] and self.ddcctl.displays[0][0] == LEFT:
                self.ddcctl.displays.reverse()
            state["read"] = state["read"] or argv[1:] == ["-d", "1", "-i", "?"]

        self.ddcctl.on_run = dock_comes_first
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn("ddcctl's display 1 is another monitor now", self.err.getvalue())
        self.assertEqual(self.ddcctl.writes, [(LEFT, USBC)])  # never the dock's
        self.assertIn(["ddcctl", "-d", "2", "-i", "27"], self.ddcctl.calls)
        self.assertEqual(self.ddcctl.display(LEFT)[3], USBC)

    def test_a_display_coming_as_the_write_goes_is_reported_with_where_it_went(self):
        # Codex on #20: the dock's display takes number 1 between the look and the
        # write, and ddcctl takes no other address, so the write lands on the dock.
        self.learned_intel([(LEFT, USBC, {})])
        self.ddcctl.add(DOCK, name="DOCK DISPLAY", serial="DOCK1")

        def dock_comes_first(argv):
            writing = argv[3:4] == ["-i"] and argv[4] != "?"
            if writing and self.ddcctl.displays[0][0] == LEFT:
                self.ddcctl.displays.reverse()

        self.ddcctl.on_run = dock_comes_first
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn(
            "write failed: the displays changed as uswitch wrote, so the write went to DOCK DISPLAY DOCK1",
            self.err.getvalue(),
        )
        self.assertEqual(self.ddcctl.writes[0], (DOCK, USBC))
        self.assertEqual(self.ddcctl.display(LEFT)[3], USBC)  # found again at its new number

    def unreadable(self, kind, error):
        # Codex on #20: a framebuffer or EDID failure isn't an absent monitor.
        self.learned_intel([(LEFT, USBC, {}), (RIGHT, USBC, {"serial": "DEF5678"})])
        self.ddcctl.failing[LEFT] = kind
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        warning = f"uswitch: ddcctl can't read display 1 ({LEFT.upper()}): {error}"
        self.assertEqual(self.err.getvalue().count(warning), 1)  # once, not every look
        self.assertEqual(self.ddcctl.display(RIGHT)[3], USBC)  # the other still switches
        self.assertEqual(self.ddcctl.display(LEFT)[3], HDMI1)

    def test_a_framebuffer_it_cant_get_is_said_once(self):
        self.unreadable("framebuffer", "Failed to acquire framebuffer device for display")

    def test_an_edid_it_cant_read_is_said_once(self):
        self.unreadable("edid", "Failed to poll display!")

    def test_no_external_display_is_not_a_learned_desk(self):
        self.learned_intel([(LEFT, USBC, {})])
        self.ddcctl.displays.clear()
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertIn("not at a learned desk", self.out.getvalue())

    def test_output_without_a_display_count_is_an_error(self):
        self.learned_intel([(LEFT, USBC, {})])
        self.ctx.run = lambda argv, timeout: uswitch.Result(134, "", "Abort trap: 6")
        self.assertEqual(uswitch.main(["switch"], self.ctx), 1)
        self.assertIn("ddcctl failed: Abort trap: 6", self.err.getvalue())

    def test_missing_ddcctl_names_the_install(self):
        self.learned_intel([(LEFT, USBC, {})])
        self.ddcctl.missing = True
        self.assertEqual(uswitch.main(["switch"], self.ctx), 1)
        self.assertIn("ddcctl is not installed; install it with `brew install ddcctl`", self.err.getvalue())


class IntelLearnTest(IntelCase):
    def test_learns_a_monitor_by_ddcctls_name_and_serial(self):
        self.ddcctl.add(LEFT, current=USBC)
        self.answers = ["home", "b", ""]
        self.assertEqual(uswitch.main(["learn"], self.ctx), 0)
        config = uswitch.load_config(self.conf())
        self.assertEqual(
            [(m.mfg, m.model, m.serial, m.input) for m in config.monitors], [("", "DELL U2723QE", "ABC1234", USBC)]
        )
        self.ddcctl.display(LEFT)[3] = HDMI1  # taken away, then a switch brings it back
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertEqual(self.ddcctl.display(LEFT)[3], USBC)

    def test_a_monitor_without_a_serial_is_known_by_its_cgdisplay_uuid(self):
        self.ddcctl.add(LEFT, serial="", current=USBC)
        self.answers = ["home", "b", ""]
        self.assertEqual(uswitch.main(["learn"], self.ctx), 0)
        self.assertEqual(uswitch.load_config(self.conf()).monitors[0].edid, LEFT)

    def test_no_monitor_answering_names_ddcctl(self):
        self.ddcctl.add(LEFT, answers=False)
        self.answers = ["home"]
        self.assertEqual(uswitch.main(["learn"], self.ctx), 1)
        self.assertIn("check its DDC/CI setting and `ddcctl -d 1`", self.err.getvalue())


class IntelInstallTest(IntelCase):
    def test_needs_ddcctl_not_m1ddc(self):
        self.runner.missing.add("ddcctl")
        self.assertEqual(uswitch.main(["install"], self.ctx), 1)
        self.assertIn("ddcctl is not installed; install it with `brew install ddcctl`", self.err.getvalue())
        self.runner.missing = {"m1ddc"}
        self.learned_intel([(LEFT, USBC, {})])
        self.assertEqual(uswitch.main(["install"], self.ctx), 0)


class MachineTest(unittest.TestCase):
    def test_the_processor_picks_the_tool(self):
        ctx = uswitch.Context(root="/nonexistent")
        self.assertIsInstance(uswitch.Mac(machine="arm64").ddc(ctx), uswitch.M1ddc)
        self.assertIsInstance(uswitch.Mac(machine="x86_64").ddc(ctx), uswitch.Ddcctl)
        with mock.patch.object(uswitch.os, "uname", return_value=mock.Mock(machine="x86_64")):
            self.assertEqual(uswitch.Mac().tool[0], "ddcctl")
        with mock.patch.object(uswitch.os, "uname", return_value=mock.Mock(machine="arm64")):
            self.assertEqual(uswitch.Mac().tool[0], "m1ddc")


if __name__ == "__main__":
    unittest.main()
