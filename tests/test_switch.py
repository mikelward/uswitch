import unittest

from support import Case, FakeMonitor, make_edid, uswitch

DP1, HDMI1 = 0x0F, 0x11
HUB = uswitch.UsbTrigger("1234", "5678")


class SwitchTest(Case):
    def desk(self, usb=None, current=HDMI1, **monitor):
        """One desk with one monitor on bus 4, this machine on DisplayPort-1."""
        edid = make_edid(serial="AAA")
        self.sysfs.add_monitor("card0-DP-1", edid, bus=4)
        self.monitor = self.runner.add(("--bus", "4"), FakeMonitor(current, **monitor), DP1)
        return self.config({"home": usb}, [("home", edid, DP1)])

    def run_switch(self, config, **kw):
        return uswitch.switch(self.ctx, config, **kw)

    def writes(self, monitor=None):
        return [value for _, value in (monitor or self.monitor).writes]

    def test_switches_and_confirms(self):
        self.assertEqual(self.run_switch(self.desk()), 0)
        self.assertEqual(self.writes(), [DP1])
        self.assertIn("confirmed", self.out.getvalue())

    def test_already_showing_this_machine_writes_nothing(self):
        self.assertEqual(self.run_switch(self.desk(current=DP1)), 0)
        self.assertEqual(self.writes(), [])
        self.assertIn("already showing", self.out.getvalue())

    def test_a_failed_read_on_an_inactive_input_still_writes(self):
        self.assertEqual(self.run_switch(self.desk(inactive_reads=False)), 0)
        self.assertEqual(self.writes(), [DP1])
        self.assertEqual(self.monitor.current, DP1)

    def test_a_write_the_monitor_ignored_is_written_again_after_settling(self):
        self.assertEqual(self.run_switch(self.desk(ignore_writes=1)), 0)
        times = [t for t, _ in self.monitor.writes]
        self.assertEqual(len(times), 2)
        self.assertGreaterEqual(times[1] - times[0], self.ctx.timing.settle)
        self.assertEqual(self.monitor.current, DP1)

    def test_a_write_it_cant_confirm_is_reported_not_repeated(self):
        config = self.desk(inactive_reads=False, ignore_writes=99)
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(self.writes(), [DP1])
        self.assertIn("doesn't answer reads to confirm", self.out.getvalue())

    def test_setup_a_desk_switches_while_the_switch_points_here(self):
        self.sysfs.add_usb("3-2", hub=True)
        self.assertEqual(self.run_switch(self.desk(usb=HUB)), 0)
        self.assertEqual(self.writes(), [DP1])

    def test_setup_a_desk_leaves_the_monitors_while_the_switch_points_elsewhere(self):
        self.assertEqual(self.run_switch(self.desk(usb=HUB)), 0)
        self.assertEqual(self.runner.commands("ddcutil"), [])
        self.assertIn("points at another machine", self.out.getvalue())
        self.assertGreaterEqual(self.clock.now(), self.ctx.timing.run)

    def test_a_trigger_returning_mid_run_is_not_lost(self):
        config = self.desk(usb=HUB)
        self.clock.at(3.0, lambda: self.sysfs.add_usb("3-2", hub=True))
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(len(self.monitor.writes), 1)
        self.assertGreaterEqual(self.monitor.writes[0][0], 3.0)

    def test_the_desk_is_checked_before_every_write(self):
        self.sysfs.add_usb("3-2", hub=True)
        config = self.desk(usb=HUB, ignore_writes=1)
        self.clock.at(1.0, lambda: self.sysfs.remove_usb("3-2"))
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(self.writes(), [DP1])  # the second write never happens
        self.assertIn("left alone", self.out.getvalue())

    def test_a_press_away_and_back_while_the_run_waits_is_not_lost(self):
        here, absent = make_edid(serial="HERE"), make_edid(serial="UNPLUGGED")
        self.sysfs.add_monitor("card0-DP-1", here, bus=4)
        self.sysfs.add_usb("3-2", hub=True)
        monitor = self.runner.add(("--bus", "4"), FakeMonitor(HDMI1), DP1)
        config = self.config({"home": HUB}, [("home", here, DP1), ("home", absent, DP1)])

        def away():
            self.sysfs.remove_usb("3-2")
            monitor.current = HDMI1  # the other machine takes it

        self.clock.at(5.0, away)
        self.clock.at(8.0, lambda: self.sysfs.add_usb("3-2", hub=True))
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual([round(t) for t, _ in monitor.writes], [0, 8])
        self.assertEqual(monitor.current, DP1)

    def test_a_switch_moving_during_the_read_stops_the_write(self):
        self.sysfs.add_usb("3-2", hub=True)
        config = self.desk(usb=HUB)
        def once():
            self.monitor.on_read = None
            self.sysfs.remove_usb("3-2")

        self.monitor.on_read = once
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(self.writes(), [])
        self.assertIn("left alone", self.out.getvalue())

    def test_a_press_away_and_back_during_another_monitors_read_is_not_lost(self):
        # Polling never sees the hub gone: it leaves and comes back while the run
        # waits on monitor TWO's read. Its new enumeration number gives it away.
        one, two = make_edid(serial="ONE"), make_edid(serial="TWO")
        self.sysfs.add_monitor("card0-DP-1", one, bus=4)
        self.sysfs.add_monitor("card0-DP-2", two, bus=5)
        self.sysfs.add_usb("3-2", hub=True)
        first = self.runner.add(("--bus", "4"), FakeMonitor(DP1), DP1)
        second = self.runner.add(("--bus", "5"), FakeMonitor(HDMI1), DP1)

        def away_and_back():
            second.on_read = None
            self.sysfs.remove_usb("3-2")
            first.current = HDMI1  # the other machine took it meanwhile
            self.sysfs.add_usb("3-2", hub=True)

        second.on_read = away_and_back
        config = self.config({"home": HUB}, [("home", one, DP1), ("home", two, DP1)])
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual((first.current, second.current), (DP1, DP1))
        self.assertEqual([v for _, v in first.writes], [DP1])
        self.assertIn("went away and came back", self.out.getvalue())

    def test_a_press_during_the_last_read_is_caught_before_the_run_ends(self):
        # Every monitor is done after the read that hid the press: the run looks
        # once more before ending, since systemd folded that press into it.
        one, two = make_edid(serial="ONE"), make_edid(serial="TWO")
        self.sysfs.add_monitor("card0-DP-1", one, bus=4)
        self.sysfs.add_monitor("card0-DP-2", two, bus=5)
        self.sysfs.add_usb("3-2", hub=True)
        first = self.runner.add(("--bus", "4"), FakeMonitor(DP1), DP1)
        second = self.runner.add(("--bus", "5"), FakeMonitor(DP1), DP1)

        def away_and_back():
            second.on_read = None
            self.sysfs.remove_usb("3-2")
            first.current = HDMI1
            self.sysfs.add_usb("3-2", hub=True)

        second.on_read = away_and_back
        config = self.config({"home": HUB}, [("home", one, DP1), ("home", two, DP1)])
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(first.current, DP1)

    def test_a_press_after_a_write_but_before_its_confirmation_writes_again(self):
        # The other machine's write lands over ours, and the monitor doesn't answer
        # reads on an input it isn't showing, so the first write would otherwise
        # end as "unconfirmed" and the run as a success.
        self.sysfs.add_usb("3-2", hub=True)
        config = self.desk(usb=HUB, inactive_reads=False, ignore_writes=1)

        def away_and_back():
            self.sysfs.remove_usb("3-2")
            self.monitor.current = HDMI1  # the other machine takes it
            self.sysfs.add_usb("3-2", hub=True)

        self.clock.at(1.0, away_and_back)
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual([round(t) for t, _ in self.monitor.writes], [0, 1])
        self.assertEqual(self.monitor.current, DP1)

    def test_settling_is_timed_from_the_write_not_the_loop(self):
        # A six-second read before the first write: the settle still runs from the write.
        config = self.desk(ignore_writes=1)

        def slow_read():
            self.monitor.on_read = None
            self.clock.t += 6.0

        self.monitor.on_read = slow_read
        self.assertEqual(self.run_switch(config), 0)
        times = [t for t, _ in self.monitor.writes]
        self.assertEqual(len(times), 2)
        self.assertGreaterEqual(times[1] - times[0], self.ctx.timing.settle)

    def test_a_steady_switch_never_restarts_finished_monitors(self):
        self.sysfs.add_usb("3-2", hub=True)
        self.assertEqual(self.run_switch(self.desk(usb=HUB, current=DP1)), 0)
        self.assertEqual(len(self.runner.commands("ddcutil")), 1)  # one read, no rework
        self.assertNotIn("went away", self.out.getvalue())

    def test_force_skips_the_desk_check(self):
        self.assertEqual(self.run_switch(self.desk(usb=HUB), force=True), 0)
        self.assertEqual(self.writes(), [DP1])

    def test_a_power_button_wake_skips_the_desk_check(self):
        config = self.desk(usb=HUB)
        self.sysfs.press_power_button()  # seen by the run before the sleep
        self.run_switch(config)
        self.sysfs.press_power_button()  # the press that woke it from sleep
        self.out.truncate(0)
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(self.writes(), [DP1])
        self.assertIn("power button woke", self.out.getvalue())

    def test_power_button_only_switches_on_a_power_button_wake(self):
        config = self.desk(usb=HUB)
        self.sysfs.press_power_button()
        self.run_switch(config)
        self.sysfs.press_power_button()
        self.assertEqual(self.run_switch(config, power_button_only=True), 0)
        self.assertEqual(self.writes(), [DP1])

    def test_power_button_only_at_boot_records_counts_for_the_first_resume(self):
        config = self.desk(usb=HUB)
        self.sysfs.press_power_button()
        self.assertEqual(self.run_switch(config, power_button_only=True), 0)  # boot
        self.assertEqual(self.writes(), [])
        self.sysfs.press_power_button()
        self.assertEqual(self.run_switch(config, power_button_only=True), 0)  # resume
        self.assertEqual(self.writes(), [DP1])

    def test_power_button_only_does_nothing_on_another_wake(self):
        config = self.desk()  # setup B, where any other run would switch
        self.sysfs.press_power_button()
        self.run_switch(config, power_button_only=True)
        self.assertEqual(self.run_switch(config, power_button_only=True), 0)
        self.assertEqual(self.writes(), [])
        self.assertIn("didn't wake", self.out.getvalue())

    def test_a_power_button_wake_during_a_run_is_not_lost(self):
        config = self.desk(usb=HUB)
        self.sysfs.press_power_button()
        self.run_switch(config)
        # It suspends mid-run and the press wakes it; the resume folds into this run.
        self.clock.at(self.clock.now() + 10, self.sysfs.press_power_button)
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(self.writes(), [DP1])

    def test_a_press_during_a_forced_run_is_not_left_for_the_next_run(self):
        config = self.desk(usb=HUB, ignore_writes=100)  # keeps the forced run going
        self.sysfs.press_power_button()
        self.run_switch(config)
        self.clock.at(self.clock.now() + 10, self.sysfs.press_power_button)
        self.run_switch(config, force=True)
        self.monitor.writes.clear()
        self.monitor.ignore_writes = 0
        self.assertEqual(self.run_switch(config), 0)  # the switch still points elsewhere
        self.assertEqual(self.writes(), [])

    def test_a_power_button_wake_during_a_run_rechecks_finished_monitors(self):
        edid2 = make_edid(serial="BBB")
        config = self.desk(usb=HUB, current=DP1)
        self.sysfs.add_monitor("card0-DP-2", edid2, bus=5)
        config.monitors.append(uswitch.monitor_from_edid(uswitch.parse_edid(edid2), "home", DP1))
        self.runner.add(("--bus", "5"), FakeMonitor(HDMI1, ignore_writes=1000), DP1)  # keeps the run going
        self.sysfs.press_power_button()
        self.run_switch(config)
        self.sysfs.add_usb("3-2", hub=True)

        def sleep_and_wake():
            self.monitor.current = HDMI1  # another machine took it while this one slept
            self.sysfs.press_power_button()

        self.clock.at(self.clock.now() + 10, sleep_and_wake)
        self.run_switch(config)
        self.assertEqual(self.monitor.current, DP1)

    def test_a_press_during_the_last_pass_is_not_left_for_the_next_run(self):
        config = self.desk(usb=HUB, current=DP1)
        self.sysfs.press_power_button()
        self.run_switch(config)
        self.sysfs.add_usb("3-2", hub=True)

        def pressed():
            self.monitor.on_read = None
            self.sysfs.press_power_button()

        self.monitor.on_read = pressed
        self.assertEqual(self.run_switch(config), 0)
        self.sysfs.remove_usb("3-2")  # the switch moves away
        self.monitor.current = HDMI1
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(self.writes(), [])

    def test_other_wakes_keep_the_desk_check(self):
        config = self.desk(usb=HUB)
        self.sysfs.press_power_button()
        self.run_switch(config)
        self.assertEqual(self.run_switch(config), 0)  # a timer wake: no new press
        self.assertEqual(self.writes(), [])

    def test_a_first_run_after_boot_is_no_power_button_wake(self):
        self.sysfs.press_power_button()
        self.sysfs.press_power_button()
        self.assertEqual(self.run_switch(self.desk(usb=HUB)), 0)
        self.assertEqual(self.writes(), [])

    def test_power_button_off_ignores_its_presses(self):
        config = self.desk(usb=HUB)
        config.power_button = False
        self.sysfs.press_power_button()
        self.run_switch(config)
        self.sysfs.press_power_button()
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(self.writes(), [])

    def test_other_wakeup_sources_are_not_the_power_button(self):
        config = self.desk(usb=HUB)
        self.sysfs.press_power_button(name="PNP0C0D:00")  # the lid
        self.run_switch(config)
        self.sysfs.press_power_button(name="PNP0C0D:00")
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(self.writes(), [])

    def test_waits_for_a_monitor_to_appear(self):
        edid = make_edid(serial="AAA")
        config = self.config({"home": None}, [("home", edid, DP1)])
        monitor = self.runner.add(("--bus", "4"), FakeMonitor(HDMI1), DP1)
        self.clock.at(2.0, lambda: self.sysfs.add_monitor("card0-DP-1", edid, bus=4))
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(monitor.writes[0], (2.0, DP1))
        self.assertIn("At desk home after 2.0s", self.out.getvalue())

    def test_no_learned_monitor_here_ends_quietly_at_its_bound(self):
        config = self.config({"home": None}, [("home", make_edid(serial="AAA"), DP1)])
        self.sysfs.add_monitor("card0-DP-1", make_edid(serial="OTHER"), bus=4)
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(self.runner.commands("ddcutil"), [])
        self.assertGreaterEqual(self.clock.now(), self.ctx.timing.appear)
        self.assertIn("not at a learned desk", self.out.getvalue())

    def test_one_monitor_failing_never_stops_the_others(self):
        bad, good = make_edid(serial="BAD"), make_edid(serial="GOOD")
        self.sysfs.add_monitor("card0-DP-1", bad, bus=4)
        self.sysfs.add_monitor("card0-DP-2", good, bus=5)
        broken = self.runner.add(("--bus", "4"), FakeMonitor(HDMI1, fail_writes=99), DP1)
        working = self.runner.add(("--bus", "5"), FakeMonitor(HDMI1), DP1)
        config = self.config({"home": None}, [("home", bad, DP1), ("home", good, DP1)])
        self.assertEqual(self.run_switch(config), 1)
        self.assertEqual(working.current, DP1)
        self.assertGreater(len(broken.writes), 1)
        self.assertIn("not switched: DDC communication failed", self.err.getvalue())

    def test_only_the_connected_desk_is_switched(self):
        home, work = make_edid(serial="HOME"), make_edid(serial="WORK")
        self.sysfs.add_monitor("card0-DP-1", home, bus=4)
        monitor = self.runner.add(("--bus", "4"), FakeMonitor(HDMI1), DP1)
        config = self.config({"home": None, "work": None}, [("home", home, DP1), ("work", work, 0x1B)])
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(monitor.current, DP1)
        self.assertNotIn("WORK", self.out.getvalue())

    def test_a_monitor_without_a_bus_is_addressed_by_its_edid(self):
        edid = make_edid(serial="")
        self.sysfs.add_monitor("card0-DP-1", edid)
        monitor = self.runner.add(("--edid", uswitch.parse_edid(edid).hex), FakeMonitor(HDMI1), DP1)
        config = self.config({"home": None}, [("home", edid, DP1)])
        self.assertEqual(self.run_switch(config), 0)
        self.assertEqual(monitor.current, DP1)

    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.run_switch(self.desk(), dry_run=True), 0)
        self.assertEqual(self.writes(), [])
        self.assertIn("0x11 (HDMI-1); would write 0x0f (DisplayPort-1)", self.out.getvalue())

    def test_nothing_learned_says_so(self):
        self.assertEqual(self.run_switch(uswitch.Config()), 0)
        self.assertIn("No desk learned yet", self.out.getvalue())


if __name__ == "__main__":
    unittest.main()
