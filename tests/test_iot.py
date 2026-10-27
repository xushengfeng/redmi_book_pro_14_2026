import unittest
from unittest import mock

from iot_agent import Policy, Runner
from iot_event import (
    EVENT_FUNC_POWER,
    Event,
    action_from_input,
    find_frame,
    parse_frame,
    parse_text,
    parse_wmi_payload,
    simulate,
)
from iot_mailbox import BASE, STATUS
from iot_status import (FW_VERSION, RESET_DEVICE, SHUTING, SUSPENDING,
                        WIN_READY, get_firmware_version, parse_wifi_reply,
                        send_status)
from mifs import read_fans


class FakeMailbox:
    def __init__(self):
        self.mem = bytearray(0x2000)
        self.writes = []

    def _off(self, addr):
        return addr - BASE

    def read(self, addr, length):
        off = self._off(addr)
        return bytes(self.mem[off:off + length])

    def write(self, addr, data):
        off = self._off(addr)
        self.mem[off:off + len(data)] = data
        self.writes.append((addr, bytes(data)))

    def close(self):
        pass


class EventTest(unittest.TestCase):
    def test_power_frames(self):
        self.assertEqual(parse_frame(bytes([1, 0x2A, 1])).kind, "shutdown")
        self.assertEqual(parse_frame(bytes([1, 0x2A, 2])).kind, "sleep")
        self.assertEqual(parse_frame(bytes([1, 0x2A, 3])).kind, "wake")
        self.assertEqual(parse_frame(bytes([1, 0x2A, 9])).kind, "unknown")

    def test_status_and_state(self):
        self.assertEqual(parse_frame(bytes([1, 0x29, 0])).kind, "status")
        self.assertEqual(parse_frame(bytes([1, 0x2B, 0])).kind, "state")

    def test_bad_frames(self):
        self.assertIsNone(parse_frame(b""))
        self.assertIsNone(parse_frame(bytes([0, 0x2A, 1])))
        self.assertIsNone(parse_frame(bytes([1, 0x2A])))

    def test_find_frame_ignores_noise(self):
        blob = b"\x00\x01\x02" + bytes([1, 0x2A, 1]) + b"\xff"
        ev = find_frame(blob)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.kind, "shutdown")

    def test_text(self):
        self.assertEqual(parse_text("01 2a 02").kind, "sleep")
        self.assertEqual(parse_text("shutdown").kind, "shutdown")

    def test_simulate(self):
        self.assertEqual(simulate("wake").kind, "wake")

    def test_wmi_payload(self):
        self.assertEqual(parse_wmi_payload(76289).kind, "shutdown")
        self.assertEqual(parse_wmi_payload(141825).kind, "sleep")
        self.assertEqual(parse_wmi_payload(207361).kind, "wake")
        self.assertIsNone(parse_wmi_payload(12345))

    def test_input_action(self):
        self.assertEqual(action_from_input(1, 0x290, 1), "shutdown")
        self.assertEqual(action_from_input(1, 0x291, 1), "sleep")
        self.assertEqual(action_from_input(1, 0x292, 1), "wake")
        self.assertIsNone(action_from_input(1, 0x290, 0))
        self.assertIsNone(action_from_input(4, 4, 76289))


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.mbox = FakeMailbox()
        self.sleep = mock.patch("iot_status.time.sleep", return_value=None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()

    def test_status_sequence(self):
        send_status(WIN_READY, bus=self.mbox)
        detail = (STATUS + 1, bytes([WIN_READY, 1, 1, 0, 0, 0, 0]))
        self.assertIn(detail, self.mbox.writes)
        self.assertIn((STATUS, b"\x55"), self.mbox.writes)
        self.assertIn((STATUS, bytes([0x55, WIN_READY, 1, 2])), self.mbox.writes)
        self.assertIn((STATUS, bytes([0x55, WIN_READY, 1, 2,
                                      0x55, WIN_READY, 1, 3])), self.mbox.writes)
        self.assertEqual(self.mbox.read(STATUS, 8),
                         bytes([0x55, WIN_READY, 1, 2, 0x55, WIN_READY, 1, 3]))

    def test_status_values(self):
        self.assertEqual(SHUTING, 0x0E)
        self.assertEqual(SUSPENDING, 0x0F)
        self.assertEqual(WIN_READY, 0x10)
        self.assertEqual(FW_VERSION, 0x0A)
        self.assertEqual(RESET_DEVICE, 0x03)

    def test_reset_sequence(self):
        send_status(RESET_DEVICE, bus=self.mbox)
        self.assertIn((STATUS, bytes([0x55, RESET_DEVICE, 1, 2,
                                      0x55, RESET_DEVICE, 1, 3])), self.mbox.writes)

    def test_wifi_reply(self):
        ssid = "root 的 Xiaomi 17 Max".encode()
        reply = bytes([0x24, 0x01, 0x08, 0x3E, len(ssid)]) + ssid + b"\x00" * 8
        self.assertEqual(parse_wifi_reply(reply), "root 的 Xiaomi 17 Max")

    def test_firmware_reply(self):
        version = b"1.0.3_0010"
        off = STATUS + 8 - BASE
        self.mbox.mem[off] = len(version)
        self.mbox.mem[off + 1:off + 1 + len(version)] = version
        self.assertEqual(get_firmware_version(bus=self.mbox), "1.0.3_0010")


def event(kind="shutdown"):
    return simulate(kind)


class AgentTest(unittest.TestCase):
    def make(self, **kw):
        policy = Policy(**kw)
        runner = Runner(policy)
        runner._status = mock.Mock()
        runner._run = mock.Mock()
        return runner

    def test_dry_run_never_runs(self):
        r = self.make(dry_run=True, allow_shutdown=True)
        r.dispatch(event("shutdown"))
        r._status.assert_not_called()
        r._run.assert_not_called()

    def test_shutdown_requires_flag(self):
        r = self.make(dry_run=False, allow_shutdown=False)
        r.dispatch(event("shutdown"))
        r._status.assert_not_called()
        r._run.assert_not_called()

    def test_shutdown_when_allowed(self):
        r = self.make(dry_run=False, allow_shutdown=True)
        r.dispatch(event("shutdown"))
        r._status.assert_called_once_with(SHUTING)
        r._run.assert_called_once_with(["systemctl", "poweroff"])

    def test_sleep_when_allowed(self):
        r = self.make(dry_run=False, allow_sleep=True)
        r.dispatch(event("sleep"))
        r._status.assert_called_once_with(SUSPENDING)
        r._run.assert_called_once_with(["systemctl", "suspend"])

    def test_wake_only_acks(self):
        r = self.make(dry_run=False)
        r.dispatch(event("wake"))
        r._status.assert_called_once_with(WIN_READY)
        r._run.assert_not_called()

    def test_unknown_ignored(self):
        r = self.make(dry_run=False)
        r.dispatch(Event("unknown", 0x99, 0, b"\x01\x99\x00"))
        r._status.assert_not_called()
        r._run.assert_not_called()


class SensorTest(unittest.TestCase):
    def test_fan_ec_window(self):
        mbox = FakeMailbox()
        mbox.mem[0x369:0x36B] = (2631).to_bytes(2, "little")
        mbox.mem[0x36B:0x36D] = (2730).to_bytes(2, "little")
        self.assertEqual(read_fans(bus=mbox), {"CPU": 2631, "GPU": 2730})


if __name__ == "__main__":
    unittest.main()
