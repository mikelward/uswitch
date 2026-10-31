import contextlib
import io
import unittest

from support import Case, FakeMonitor, make_edid, uswitch


class UdevRuleTest(Case):
    def test_one_line_per_switch_shared_by_desks(self):
        hub = uswitch.UsbTrigger("05e3", "0610")
        config = self.config({"home": hub, "work": hub, "lab": None}, [])
        text = uswitch.udev_rules_text(config, "/usr/bin/systemctl")
        rules = [line for line in text.splitlines() if line.startswith("ACTION")]
        self.assertEqual(
            rules,
            ['ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="05e3", '
             'ATTR{idProduct}=="0610", RUN+="/usr/bin/systemctl --no-block start uswitch.service"'],
        )
        self.assertIn("# desk home, work", text)

    def test_serial_and_port_narrow_the_match(self):
        config = self.config({"home": uswitch.UsbTrigger("05e3", "0610", "S1", "3-2")}, [])
        text = uswitch.udev_rules_text(config, "/usr/bin/systemctl")
        self.assertIn('ATTR{serial}=="S1", KERNEL=="3-2", RUN+=', text)

    def test_refuses_an_unsafe_value(self):
        config = self.config({"home": uswitch.UsbTrigger("05e3", "0610", 'x" RUN+="/bin/sh')}, [])
        with self.assertRaises(uswitch.UswitchError):
            uswitch.udev_rules_text(config, "/usr/bin/systemctl")

    def test_no_switch_no_lines(self):
        text = uswitch.udev_rules_text(self.config({"work": None}, []), "/usr/bin/systemctl")
        self.assertNotIn("ACTION", text)


class UnitTest(unittest.TestCase):
    def test_oneshot_running_switch_hooked_to_boot_and_resume(self):
        text = uswitch.unit_text()
        self.assertIn("Type=oneshot\nExecStart=/usr/local/sbin/uswitch switch\n", text)
        for target in ("multi-user.target",) + uswitch.SLEEP_TARGETS:
            self.assertIn(target, text.split("[Install]")[1])
        # After multi-user.target, or boot waits for the oneshot; after the
        # sleep targets, so it runs on resume.
        for target in ("multi-user.target",) + uswitch.SLEEP_TARGETS:
            self.assertIn(target, text.split("\nAfter=")[1].splitlines()[0])

    def test_polkit_grants_starting_only_this_unit(self):
        self.assertIn('action.lookup("unit") == "uswitch.service"', uswitch.POLKIT_TEXT)
        self.assertIn('action.lookup("verb") == "start"', uswitch.POLKIT_TEXT)
        self.assertIn("subject.local && subject.active", uswitch.POLKIT_TEXT)


class DdcTest(Case):
    def test_parses_ddcutil_terse_output(self):
        self.assertEqual(uswitch.parse_getvcp("VCP 60 SNC x0f\n"), 0x0F)
        self.assertEqual(uswitch.parse_getvcp("VCP 60 CNC x00 x00 x01 x1b\n"), 0x1B)
        self.assertIsNone(uswitch.parse_getvcp("VCP 60 ERR\n"))
        self.assertIsNone(uswitch.parse_getvcp(""))

    def test_missing_ddcutil_names_the_package(self):
        self.runner.missing.add("ddcutil")
        connector = uswitch.Connector("card0-DP-1", uswitch.parse_edid(make_edid()), 4)
        with self.assertRaises(uswitch.UswitchError) as e:
            uswitch.Ddc(self.ctx).get_input(connector)
        self.assertIn("install the ddcutil package", str(e.exception))

    def test_unexpected_output_is_a_failed_read(self):
        self.ctx.run = lambda argv, timeout: uswitch.Result(0, "garbage\n", "")
        connector = uswitch.Connector("card0-DP-1", uswitch.parse_edid(make_edid()), 4)
        value, why = uswitch.Ddc(self.ctx).get_input(connector)
        self.assertIsNone(value)
        self.assertIn("unexpected ddcutil output", why)

    def test_commands_are_argument_lists(self):
        connector = uswitch.Connector("card0-DP-1", uswitch.parse_edid(make_edid()), 4)
        self.runner.add(("--bus", "4"), FakeMonitor(0x11), 0x0F)
        uswitch.Ddc(self.ctx).set_input(connector, 0x0F)
        self.assertEqual(self.runner.calls[-1], ["ddcutil", "--bus", "4", "setvcp", "60", "0x0f"])


class CommandLineTest(Case):
    def test_needs_root_on_the_live_system(self):
        ctx = uswitch.Context(euid=1000, stdout=io.StringIO(), stderr=io.StringIO(), platform=uswitch.Linux())
        self.assertEqual(uswitch.main(["switch"], ctx), 1)
        self.assertIn("needs root", ctx.stderr.getvalue())

    def test_switch_reads_the_configuration(self):
        edid = make_edid(serial="AAA")
        self.write_config(self.config({"home": None}, [("home", edid, 0x0F)]))
        self.sysfs.add_monitor("card0-DP-1", edid, bus=4)
        monitor = self.runner.add(("--bus", "4"), FakeMonitor(0x11), 0x0F)
        self.assertEqual(uswitch.main(["switch"], self.ctx), 0)
        self.assertEqual(monitor.current, 0x0F)

    def test_a_bad_configuration_exits_nonzero(self):
        uswitch.write_file(self.path(uswitch.CONFIG), "[wat]\n")
        self.assertEqual(uswitch.main(["switch"], self.ctx), 1)
        self.assertIn("unknown section", self.err.getvalue())

    def test_status_shows_desks_triggers_and_inputs(self):
        edid = make_edid(serial="AAA")
        self.write_config(self.config({"home": uswitch.UsbTrigger("05e3", "0610"), "work": None}, [("home", edid, 0x0F)]))
        self.sysfs.add_monitor("card0-DP-1", edid, bus=4)
        self.runner.add(("--bus", "4"), FakeMonitor(0x11), 0x0F)
        self.assertEqual(uswitch.main(["status"], self.ctx), 0)
        out = self.out.getvalue()
        self.assertIn("desk home (connected): USB switch 05e3:0610, not present", out)
        self.assertIn("this machine is 0x0f (DisplayPort-1); showing 0x11 (HDMI-1)", out)
        self.assertIn("desk work: the monitor is the switch", out)

    def test_usage_errors_exit_2(self):
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
            uswitch.parse_args(["nonsense"])
        self.assertEqual(e.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
