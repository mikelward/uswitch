import unittest

from support import Case, make_edid, read, uswitch

HUB = uswitch.UsbTrigger("1234", "5678", "S1", "")


class ConfigTest(Case):
    def test_round_trips(self):
        config = self.config(
            {"home": HUB, "work": None},
            [("home", make_edid(serial="AAA"), 0x0F), ("work", make_edid(model="DELL U2724DE", serial=""), 0x1B)],
            wake=False,
        )
        config.power_button = False
        self.write_config(config)
        loaded = uswitch.load_config(self.path(uswitch.CONFIG))
        self.assertFalse(loaded.wake)
        self.assertFalse(loaded.power_button)
        self.assertEqual(loaded.desks["home"].usb, HUB)
        self.assertIsNone(loaded.desks["work"].usb)
        self.assertEqual([(m.desk, m.serial, m.input) for m in loaded.monitors], [("home", "AAA", 0x0F), ("work", "", 0x1B)])
        self.assertEqual(len(loaded.monitors[1].edid), 256)
        text = read(self.path(uswitch.CONFIG))
        self.assertIn("# DisplayPort-1\ninput = 0x0f", text)
        self.assertIn("# USB-C\ninput = 0x1b", text)

    def test_same_model_monitors_without_serials_get_their_own_sections(self):
        # Their EDIDs differ only in the manufacture week (byte 16).
        a, b = make_edid(serial="", week=1), make_edid(serial="", week=2)
        config = uswitch.replace_desk(uswitch.Config(), "home", None, [uswitch.monitor_from_edid(uswitch.parse_edid(a), "", 0x0F)])
        config = uswitch.replace_desk(config, "work", None, [uswitch.monitor_from_edid(uswitch.parse_edid(b), "", 0x11)])
        self.write_config(config)
        loaded = uswitch.load_config(self.path(uswitch.CONFIG))
        self.assertEqual(sorted((m.desk, m.input) for m in loaded.monitors), [("home", 0x0F), ("work", 0x11)])
        self.assertEqual(len({m.label for m in loaded.monitors}), 2)

    def test_refuses_to_write_two_monitors_into_one_section(self):
        edid = uswitch.parse_edid(make_edid(serial="AAA"))
        config = uswitch.Config(desks={"home": uswitch.Desk("home")})
        config.monitors = [uswitch.monitor_from_edid(edid, "home", 0x0F), uswitch.monitor_from_edid(edid, "home", 0x11)]
        with self.assertRaises(uswitch.UswitchError):
            uswitch.config_text(config)

    def test_missing_file_is_none(self):
        self.assertIsNone(uswitch.load_config(self.path("etc/nope.conf")))

    def write_raw(self, text):
        path = self.path(uswitch.CONFIG)
        uswitch.write_file(path, text)
        return path

    def assert_rejected(self, text, message):
        path = self.write_raw(text)
        with self.assertRaises(uswitch.UswitchError) as e:
            uswitch.load_config(path)
        self.assertIn(message, str(e.exception))

    def test_rejects_a_monitor_of_an_unknown_desk(self):
        self.assert_rejected("[monitor M A]\ndesk = gone\nmfg = DEL\nmodel = M\nserial = A\ninput = 0x0f\n", "no [desk] section")

    def test_rejects_a_monitor_without_serial_or_edid(self):
        self.assert_rejected("[desk d]\n[monitor M]\ndesk = d\nmfg = DEL\nmodel = M\nserial =\ninput = 0x0f\n", "serial or its whole EDID")

    def test_rejects_unsafe_usb_values(self):
        self.assert_rejected("[desk d]\nusb_vendor = 12\nusb_product = 5678\n", "four hex digits")
        self.assert_rejected('[desk d]\nusb_vendor = 1234\nusb_product = 5678\nusb_serial = a*\n', "can't match safely")
        self.assert_rejected("[desk d]\nusb_vendor = 1234\nusb_product = 5678\nusb_port = 3-x\n", "usb_port")

    def test_rejects_bad_input_and_unknown_sections(self):
        self.assert_rejected("[desk d]\n[monitor M A]\ndesk = d\nserial = A\ninput = DP\n", "invalid literal")
        self.assert_rejected("[wat]\n", "unknown section")
        self.assert_rejected("[desk bad name!]\n", "desk names")


class ReplaceDeskTest(Case):
    def test_learning_a_desk_keeps_the_others_and_moves_monitors(self):
        config = self.config(
            {"home": HUB, "work": None},
            [("home", make_edid(serial="AAA"), 0x0F), ("home", make_edid(serial="OLD"), 0x0F), ("work", make_edid(serial="WWW"), 0x1B)],
        )
        fresh = [uswitch.monitor_from_edid(uswitch.parse_edid(make_edid(serial="AAA")), "", 0x10)]
        result = uswitch.replace_desk(config, "home", None, fresh)
        self.assertEqual(sorted(result.desks), ["home", "work"])
        self.assertIsNone(result.desks["home"].usb)
        self.assertEqual(sorted((m.desk, m.serial, m.input) for m in result.monitors), [("home", "AAA", 0x10), ("work", "WWW", 0x1B)])

    def test_a_desk_left_without_monitors_goes(self):
        config = self.config({"old": HUB}, [("old", make_edid(serial="AAA"), 0x0F)])
        fresh = [uswitch.monitor_from_edid(uswitch.parse_edid(make_edid(serial="AAA")), "", 0x0F)]
        result = uswitch.replace_desk(config, "new", None, fresh)
        self.assertEqual(list(result.desks), ["new"])
        self.assertEqual([m.desk for m in result.monitors], ["new"])


if __name__ == "__main__":
    unittest.main()
