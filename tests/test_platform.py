"""The shared logic reaches the system only through ctx.platform (D14)."""

import os
import unittest
from dataclasses import dataclass
from unittest import mock

from support import Case, make_edid, uswitch

DP1, HDMI1 = 0x0F, 0x11


@dataclass(frozen=True)
class FakeConnector:
    edid: object


class FakeDdc:
    def __init__(self, inputs):
        self.inputs = inputs  # connector -> current input
        self.writes = []

    def get_input(self, connector):
        return self.inputs[connector], None

    def set_input(self, connector, value):
        self.writes.append((connector, value))
        self.inputs[connector] = value
        return None


class FakePlatform:
    """A platform with its monitors and USB devices in memory, and nothing
    of Linux's: no sysfs, no ddcutil, no udev."""

    name = "fakeswitch"
    learn_command = "fakeswitch learn"
    ddc_check = "the fake's own check"
    install_command = "fakeswitch install"
    power_button = False

    def __init__(self, config_path, connectors, ddc, devices=None):
        self.path = config_path
        self.connectors = connectors
        self.fake_ddc = ddc
        self.devices = devices or {}
        self.triggers_written = []

    def config_path(self, ctx):
        return self.path

    def wrote(self, ctx):
        return "the fake's configuration"

    def serialize(self, ctx, force, run):
        return run(force)

    def require_privilege(self, ctx):
        pass

    def can_read(self, ctx):
        return True

    def monitors(self, ctx):
        return list(self.connectors)

    def usb_devices(self, ctx):
        return dict(self.devices)

    def ddc(self, ctx):
        return self.fake_ddc

    def power_button_woke(self, ctx):
        return False

    def write_triggers(self, ctx, config):
        self.triggers_written.append(config)


class PlatformTest(Case):
    def setUp(self):
        super().setUp()
        # Nothing shared may look here: only the platform knows where things are.
        self.ctx.sysfs = os.path.join(self.tmp, "no-sysfs")
        self.ctx.root = os.path.join(self.tmp, "no-root")
        self.edid = uswitch.parse_edid(make_edid(serial="AAA"))
        self.connector = FakeConnector(self.edid)
        self.fake_ddc = FakeDdc({self.connector: HDMI1})
        self.ctx.platform = FakePlatform(
            os.path.join(self.tmp, "fake.conf"), [self.connector], self.fake_ddc
        )

    def test_switch_writes_through_the_platform(self):
        config = uswitch.Config()
        config.desks["home"] = uswitch.Desk("home")
        config.monitors.append(uswitch.monitor_from_edid(self.edid, "home", DP1))
        self.assertEqual(uswitch.switch(self.ctx, config), 0)
        self.assertEqual(self.fake_ddc.writes, [(self.connector, DP1)])
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(os.path.exists(self.ctx.sysfs))

    def test_learn_writes_the_platforms_configuration_and_triggers(self):
        self.fake_ddc.inputs[self.connector] = DP1
        self.answers = ["home", "b", ""]
        self.assertEqual(uswitch.learn(self.ctx), 0)
        config = uswitch.load_config(self.ctx.platform.path)
        self.assertEqual([m.input for m in config.monitors], [DP1])
        self.assertEqual(len(self.ctx.platform.triggers_written), 1)
        self.assertIn("Wrote desk home to the fake's configuration.", self.out.getvalue())
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(os.path.exists(self.ctx.root))

    def test_status_and_warnings_use_the_platforms_names(self):
        self.assertEqual(uswitch.main(["status"], self.ctx), 0)
        self.assertIn("No desk learned yet: run `fakeswitch learn`.", self.out.getvalue())
        self.ctx.warn("something")
        self.assertEqual(self.err.getvalue(), "fakeswitch: something\n")


class DefaultPlatformTest(unittest.TestCase):
    def test_a_mac_runs_as_mswitch_and_anything_else_as_uswitch(self):
        with mock.patch.object(uswitch.sys, "platform", "darwin"):
            self.assertIsInstance(uswitch.Context().platform, uswitch.Mac)
        with mock.patch.object(uswitch.sys, "platform", "linux"):
            self.assertIsInstance(uswitch.Context().platform, uswitch.Linux)

    def test_the_shared_fixture_tests_linux_whatever_it_runs_on(self):
        # Codex on #13: run on a Mac, the Linux tests must still test Linux.
        with mock.patch.object(uswitch.sys, "platform", "darwin"):
            case = Case()
            case.setUp()
            self.addCleanup(case.doCleanups)
        self.assertIsInstance(case.ctx.platform, uswitch.Linux)


if __name__ == "__main__":
    unittest.main()
