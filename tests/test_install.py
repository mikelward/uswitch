import os
import unittest

from support import PROGRAM, Case, FakeMonitor, make_edid, read, uswitch

INSTALLED = [uswitch.PROGRAM, uswitch.UNIT, uswitch.POLKIT_RULE, uswitch.MODULES_LOAD]
POWER = [uswitch.POWER_UNIT, uswitch.POWER_WAKE_UNIT, uswitch.ACPID_EVENT, uswitch.LOGIND_DROPIN]


class InstallTest(Case):
    def learned(self, wake=True, power_button=True):
        config = self.config({"home": uswitch.UsbTrigger("05e3", "0610")}, [("home", make_edid(serial="AAA"), 0x0F)], wake)
        config.power_button = power_button
        self.write_config(config)

    def files(self):
        found = []
        for base, _, names in os.walk(self.root):
            found += [os.path.relpath(os.path.join(base, n), self.root) for n in names]
        return sorted(found)

    def test_installs_every_file_and_enables_wake(self):
        self.learned()
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertEqual(self.files(), sorted(INSTALLED + POWER + [uswitch.CONFIG, uswitch.UDEV_RULE]))
        self.assertTrue(os.access(self.path(uswitch.PROGRAM), os.X_OK))
        self.assertEqual(read(self.path(uswitch.PROGRAM)), read(PROGRAM))
        self.assertIn(["systemctl", "--root", self.root, "enable", "uswitch.service"], self.runner.calls)
        self.assertIn('ATTR{idProduct}=="0610"', read(self.path(uswitch.UDEV_RULE)))

    def test_hands_the_power_button_to_acpid(self):
        self.learned()
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertIn("HandlePowerKey=ignore", read(self.path(uswitch.LOGIND_DROPIN)))
        event = read(self.path(uswitch.ACPID_EVENT))
        self.assertIn("event=button/power.*", event)
        self.assertIn("action=/usr/bin/systemctl --no-block start uswitch-power.service", event)
        self.assertIn("ExecStart=/usr/local/sbin/uswitch switch --force\n", read(self.path(uswitch.POWER_UNIT)))
        self.assertIn(["systemctl", "--root", self.root, "enable", "acpid.service"], self.runner.calls)

    def test_power_button_off_takes_it_back(self):
        self.learned()
        uswitch.install(self.ctx)
        self.learned(power_button=False)
        self.runner.missing.add("acpid")
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertEqual(self.files(), sorted(INSTALLED + [uswitch.CONFIG, uswitch.UDEV_RULE]))

    def test_refuses_without_acpid_naming_the_package(self):
        self.learned()
        self.runner.missing.add("acpid")
        with self.assertRaises(uswitch.UswitchError) as e:
            uswitch.install(self.ctx)
        self.assertIn("install the acpid package", str(e.exception))
        self.assertIn("[power_button] enabled = no", str(e.exception))
        self.assertEqual(self.files(), [uswitch.CONFIG])

    def test_refuses_while_another_acpid_rule_handles_the_power_button(self):
        self.learned()
        uswitch.write_file(self.path("etc/acpi/events/powerconf"), "event=button/power.*\naction=/etc/acpi/actions/power.sh\n")
        uswitch.write_file(self.path("etc/acpi/events/videoconf"), "event=video.*\naction=/bin/true\n")
        uswitch.write_file(self.path("etc/acpi/events/power.bak"), "event=button/power.*\n")  # acpid skips it
        with self.assertRaises(uswitch.UswitchError) as e:
            uswitch.install(self.ctx)
        self.assertIn("/etc/acpi/events/powerconf", str(e.exception))
        self.assertNotIn("videoconf", str(e.exception))
        self.assertNotIn("power.bak", str(e.exception))
        self.assertNotIn(uswitch.ACPID_EVENT, self.files())

    def test_wake_off_disables_the_boot_and_resume_hook(self):
        self.learned(wake=False)
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertIn(["systemctl", "--root", self.root, "disable", "uswitch.service"], self.runner.calls)

    def test_wake_off_still_hooks_the_power_button_to_resume(self):
        self.learned(wake=False)
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertIn(["systemctl", "--root", self.root, "enable", "uswitch-power-wake.service"], self.runner.calls)
        text = read(self.path(uswitch.POWER_WAKE_UNIT))
        self.assertIn("ExecStart=/usr/local/sbin/uswitch switch --power-button-only\n", text)
        # Boot too, so the first resume after it has counts to compare against.
        for target in ("multi-user.target",) + uswitch.SLEEP_TARGETS:
            self.assertIn(target, text.split("[Install]")[1])

    def test_wake_on_leaves_the_power_button_to_the_main_unit(self):
        self.learned()
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertIn(["systemctl", "--root", self.root, "disable", "uswitch-power-wake.service"], self.runner.calls)

    def test_is_idempotent(self):
        self.learned()
        uswitch.install(self.ctx)
        first = {f: read(self.path(f)) for f in self.files()}
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertEqual({f: read(self.path(f)) for f in self.files()}, first)

    def test_refuses_without_ddcutil_naming_the_package(self):
        self.runner.missing.add("ddcutil")
        with self.assertRaises(uswitch.UswitchError) as e:
            uswitch.install(self.ctx)
        self.assertIn("install the ddcutil package", str(e.exception))
        self.assertEqual(self.files(), [])

    def test_learns_when_nothing_is_learned_yet(self):
        self.sysfs.add_monitor("card0-DP-1", make_edid(serial="AAA"), bus=4)
        monitor = self.runner.add(("--bus", "4"), FakeMonitor(0x0F), 0x0F)
        self.clock.at(1.0, lambda: setattr(monitor, "current", 0x11))
        self.answers = ["work", "b", ""]
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertIn("running `uswitch learn`", self.out.getvalue())
        self.assertIn(uswitch.CONFIG, self.files())

    def test_live_install_loads_i2c_dev_and_reloads(self):
        self.learned()
        live = []
        self.ctx.run = lambda argv, timeout: live.append(argv) or uswitch.Result(0, "yes\n" if "show" in argv else "", "")
        # A live root writes the real system; point every path into the test root instead.
        self.ctx.path = lambda rel: os.path.join(self.root, rel)
        self.ctx.root = "/"
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertEqual(
            live,
            [
                ["modprobe", "i2c-dev"],
                ["systemctl", "daemon-reload"],
                ["systemctl", "enable", "acpid.service"],
                ["systemctl", "restart", "acpid.service"],
                ["systemctl", "show", "--property=CanReload", "--value", "systemd-logind.service"],
                ["systemctl", "reload", "systemd-logind.service"],
                ["udevadm", "control", "--reload"],
                ["systemctl", "enable", "uswitch.service"],
                ["systemctl", "disable", "uswitch-power-wake.service"],
            ],
        )

    def test_an_old_logind_waits_for_the_next_boot_rather_than_restarting(self):
        self.learned()
        live = []
        self.ctx.run = lambda argv, timeout: live.append(argv) or uswitch.Result(0, "no\n" if "show" in argv else "", "")
        self.ctx.path = lambda rel: os.path.join(self.root, rel)
        self.ctx.root = "/"
        self.assertEqual(uswitch.install(self.ctx), 0)
        self.assertFalse([c for c in live if "systemd-logind.service" in c and c[1] in ("reload", "restart")])
        self.assertIn("takes effect at the next boot", self.out.getvalue())

    def test_a_failing_command_is_reported(self):
        self.learned()
        self.runner.failing["systemctl"] = "Failed to enable unit"
        with self.assertRaises(uswitch.UswitchError) as e:
            uswitch.install(self.ctx)
        self.assertIn("Failed to enable unit", str(e.exception))


class UninstallTest(Case):
    def setUp(self):
        super().setUp()
        self.write_config(self.config({"home": None}, [("home", make_edid(serial="AAA"), 0x0F)]))
        uswitch.install(self.ctx)

    def remaining(self):
        found = []
        for base, _, names in os.walk(self.root):
            found += [os.path.relpath(os.path.join(base, n), self.root) for n in names]
        return found

    def test_removes_everything_but_the_configuration(self):
        self.assertEqual(uswitch.uninstall(self.ctx), 0)
        self.assertEqual(self.remaining(), [uswitch.CONFIG])
        self.assertIn(["systemctl", "--root", self.root, "disable", "uswitch.service"], self.runner.calls)
        self.assertIn("uninstall --purge", self.out.getvalue())

    def test_purge_removes_the_configuration_too(self):
        self.assertEqual(uswitch.uninstall(self.ctx, purge=True), 0)
        self.assertEqual(self.remaining(), [])

    def test_is_idempotent(self):
        uswitch.uninstall(self.ctx, purge=True)
        self.runner.calls.clear()
        self.assertEqual(uswitch.uninstall(self.ctx, purge=True), 0)
        self.assertEqual(self.runner.commands("systemctl"), [])
        self.assertIn("Removed nothing", self.out.getvalue())


if __name__ == "__main__":
    unittest.main()
