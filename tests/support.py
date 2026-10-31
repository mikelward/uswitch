"""Shared fakes: a sysfs tree, a clock, and the commands uswitch runs."""

import importlib.machinery
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.join(os.path.dirname(HERE), "uswitch")

sys.dont_write_bytecode = True
_loader = importlib.machinery.SourceFileLoader("uswitch", PROGRAM)
_spec = importlib.util.spec_from_loader("uswitch", _loader)
uswitch = importlib.util.module_from_spec(_spec)
_loader.exec_module(uswitch)


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def make_edid(mfg="DEL", model="DELL U2723QE", serial="ABC1234", week=0):
    data = bytearray(128)
    data[0:8] = uswitch.EDID_HEADER
    data[16] = week
    v = 0
    for ch in mfg:
        v = v << 5 | (ord(ch) - 64)
    data[8], data[9] = v >> 8, v & 0xFF
    blocks = []
    if model:
        blocks.append((0xFC, model))
    if serial:
        blocks.append((0xFF, serial))
    for offset, (tag, text) in zip((54, 72, 90, 108), blocks):
        raw = text.encode()[:13]
        raw = raw + (b"\n" + b" " * 12 if len(raw) < 13 else b"")
        data[offset : offset + 18] = bytes([0, 0, 0, tag, 0]) + raw[:13]
    return bytes(data)


class FakeClock:
    """Time that moves only when uswitch sleeps, running events scheduled for then."""

    def __init__(self):
        self.t = 0.0
        self.events = []

    def now(self):
        return self.t

    def at(self, when, action):
        self.events.append((when, action))
        self.events.sort(key=lambda e: e[0])

    def sleep(self, seconds):
        self.t += seconds
        while self.events and self.events[0][0] <= self.t:
            _, action = self.events.pop(0)
            action()


class FakeSysfs:
    def __init__(self, root):
        self.root = root
        os.makedirs(self.path("class/drm"), exist_ok=True)
        os.makedirs(self.path("bus/usb/devices"), exist_ok=True)
        self.devnum = 1  # like the kernel, a new number for every enumeration

    def path(self, rel):
        return os.path.join(self.root, rel)

    def add_monitor(self, connector, edid, status="connected", bus=None):
        path = self.path(f"class/drm/{connector}")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "status"), "w") as f:
            f.write(status + "\n")
        with open(os.path.join(path, "edid"), "wb") as f:
            f.write(edid)
        if bus is not None:
            os.makedirs(os.path.join(path, f"i2c-{bus}"), exist_ok=True)

    def remove_monitor(self, connector):
        shutil.rmtree(self.path(f"class/drm/{connector}"))

    def add_usb(self, port, vendor="1234", product="5678", serial="", hub=False, speed="480", name=""):
        path = self.path(f"bus/usb/devices/{port}")
        os.makedirs(path, exist_ok=True)
        self.devnum += 1
        attrs = {
            "idVendor": vendor,
            "idProduct": product,
            "bDeviceClass": "09" if hub else "00",
            "speed": speed,
            "devnum": str(self.devnum),
        }
        if serial:
            attrs["serial"] = serial
        if name:
            attrs["product"] = name
        for key, value in attrs.items():
            with open(os.path.join(path, key), "w") as f:
                f.write(value + "\n")

    def press_power_button(self, name="PNP0C0C:00", source="wakeup0"):
        """A wakeup source's event count, as the kernel counts a press."""
        path = self.path(f"class/wakeup/{source}")
        os.makedirs(path, exist_ok=True)
        count_path = os.path.join(path, "event_count")
        count = int(open(count_path).read()) + 1 if os.path.exists(count_path) else 0
        for key, value in (("name", name), ("event_count", str(count))):
            with open(os.path.join(path, key), "w") as f:
                f.write(value + "\n")

    def remove_usb(self, port):
        shutil.rmtree(self.path(f"bus/usb/devices/{port}"))


class FakeMonitor:
    """A monitor answering DDC/CI the ways real ones do."""

    def __init__(self, current, inactive_reads=True, ignore_writes=0, fail_writes=0):
        self.current = current
        self.mine = None  # the input of the machine running uswitch
        self.inactive_reads = inactive_reads
        self.ignore_writes = ignore_writes
        self.fail_writes = fail_writes
        self.writes = []
        self.on_read = None  # runs as a read starts, to change the world mid-read


class FakeRunner:
    """Stands in for every command uswitch runs, recording each one."""

    def __init__(self, clock=None):
        self.calls = []
        self.monitors = {}  # selector ("--bus", "4") or ("--edid", hex) -> FakeMonitor
        self.missing = set()
        self.failing = {}
        self.clock = clock

    def add(self, selector, monitor, mine):
        monitor.mine = mine
        self.monitors[selector] = monitor
        return monitor

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if argv[0] in self.missing:
            raise uswitch.MissingCommand(f"{argv[0]} not found")
        if argv[0] in self.failing:
            return uswitch.Result(1, "", self.failing[argv[0]])
        if argv[0] != "ddcutil":
            return uswitch.Result(0, "", "")
        monitor = self.monitors.get((argv[1], argv[2]))
        if monitor is None:
            return uswitch.Result(1, "", "Display not found")
        if "getvcp" in argv:
            if monitor.on_read:
                monitor.on_read()
            if monitor.current != monitor.mine and not monitor.inactive_reads:
                return uswitch.Result(1, "", "DDC communication failed")
            return uswitch.Result(0, f"VCP 60 SNC x{monitor.current:02x}\n", "")
        value = int(argv[-1], 16)
        monitor.writes.append((self.clock.now() if self.clock else None, value))
        if monitor.fail_writes:
            monitor.fail_writes -= 1
            return uswitch.Result(1, "", "DDC communication failed")
        if monitor.ignore_writes:
            monitor.ignore_writes -= 1
            return uswitch.Result(0, "", "")
        monitor.current = value
        return uswitch.Result(0, "", "")

    def commands(self, name):
        return [c for c in self.calls if c[0] == name]


class Case(unittest.TestCase):
    """A temporary root and sysfs, a fake clock and runner, and captured output."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uswitch-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(self.root)
        self.sysfs = FakeSysfs(os.path.join(self.tmp, "sys"))
        self.clock = FakeClock()
        self.runner = FakeRunner(self.clock)
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.answers = []
        self.ctx = uswitch.Context(
            root=self.root,
            sysfs=self.sysfs.root,
            clock=self.clock,
            run=self.runner,
            which=lambda name: None if name in self.runner.missing else f"/usr/bin/{name}",
            euid=0,
            stdin=self,
            stdout=self.out,
            stderr=self.err,
            timing=uswitch.Timing(),
            self_path=PROGRAM,
        )

    def readline(self):
        """The context's stdin: one scripted answer per question; EOF when out."""
        return self.answers.pop(0) + "\n" if self.answers else ""

    def path(self, rel):
        return os.path.join(self.root, rel)

    def config(self, desks, monitors, wake=True):
        """A configuration: desks as {name: UsbTrigger or None}, monitors as
        (desk, edid_bytes, input)."""
        config = uswitch.Config(wake=wake)
        for name, usb in desks.items():
            config.desks[name] = uswitch.Desk(name, usb)
        for desk, edid, value in monitors:
            config.monitors.append(uswitch.monitor_from_edid(uswitch.parse_edid(edid), desk, value))
        return config

    def write_config(self, config):
        uswitch.write_file(self.path(uswitch.CONFIG), uswitch.config_text(config))
