"""The MicroPython sensor driver, exercised against a simulated MAX30102.

This checks register values and FIFO decoding on the PC. It cannot replace a
run on the real board (timing, wiring and the I2C peripheral are not simulated).
"""

import importlib.util
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[1] / "hardware" / "firmware" / "micropython" / "max30102.py"
spec = importlib.util.spec_from_file_location("max30102_driver", DRIVER)
drv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drv)


class FakeSensor:
    """Register-level model of the MAX30102 FIFO (roll-over disabled)."""

    def __init__(self, part_id=0x15):
        self.reg = {0xFF: part_id, 0x04: 0, 0x05: 0, 0x06: 0, 0x09: 0}
        self.fifo = []           # list of (red, ir)
        self.writes = []

    def push(self, red, ir):
        if len(self.fifo) >= 32:
            self.reg[0x05] = min(self.reg[0x05] + 1, 31)
            return
        self.fifo.append((red, ir))
        self.reg[0x04] = (self.reg[0x04] + 1) & 0x1F

    def readfrom_mem(self, addr, reg, n):
        assert addr == 0x57 and n == 1
        return bytes([self.reg.get(reg, 0)])

    def writeto_mem(self, addr, reg, data):
        assert addr == 0x57
        value = data[0]
        self.writes.append((reg, value))
        if reg == 0x09 and value & 0x40:
            value = 0                      # reset bit clears itself
        self.reg[reg] = value
        if reg in (0x04, 0x06) and value == 0:
            self.fifo.clear()

    def readfrom_mem_into(self, addr, reg, buf):
        assert addr == 0x57 and reg == 0x07 and len(buf) % 6 == 0
        for k in range(len(buf) // 6):
            red, ir = self.fifo.pop(0)
            buf[6 * k : 6 * k + 6] = red.to_bytes(3, "big") + ir.to_bytes(3, "big")
            self.reg[0x06] = (self.reg[0x06] + 1) & 0x1F
        self.reg[0x05] = 0                 # overflow counter clears when data is read


def test_detects_the_part():
    assert drv.MAX30102(FakeSensor()).is_connected()
    assert not drv.MAX30102(FakeSensor(part_id=0x11)).is_connected()


def test_missing_device_is_not_an_exception():
    class Dead:
        def readfrom_mem(self, *args):
            raise OSError("ENODEV")

    assert not drv.MAX30102(Dead()).is_connected()


def test_setup_writes_the_documented_configuration():
    bus = FakeSensor()
    drv.MAX30102(bus).setup(led_current=0x1F)
    assert bus.writes[0] == (0x09, 0x40)          # soft reset first
    final = dict(bus.writes)
    assert final[0x08] == 0x40                    # 4x averaging, roll-over off
    assert final[0x0A] == 0x2F                    # 4096 nA, 400 sps, 411 us / 18 bit
    assert final[0x0C] == 0x1F and final[0x0D] == 0x1F
    assert final[0x09] == 0x03                    # SpO2 mode: red + IR
    assert drv.OUTPUT_RATE_HZ == 400 // 4


def test_samples_are_decoded_in_order_as_18_bit_values():
    bus = FakeSensor()
    sensor = drv.MAX30102(bus)
    samples = [(95_000 + k, 118_000 + 3 * k) for k in range(5)] + [(0x3FFFF, 1)]
    for red, ir in samples:
        bus.push(red, ir)
    n, lost, data = sensor.read_fifo()
    assert (n, lost) == (6, 0)
    assert [(sensor.red(data, k), sensor.ir(data, k)) for k in range(n)] == samples
    assert sensor.read_fifo()[0] == 0             # FIFO now empty


def test_upper_bits_outside_18_are_masked():
    bus = FakeSensor()
    sensor = drv.MAX30102(bus)
    bus.push(0xFFFFFF, 0xFC0001)
    _, _, data = sensor.read_fifo()
    assert sensor.red(data, 0) == 0x3FFFF and sensor.ir(data, 0) == 1


def test_pointer_wraparound():
    bus = FakeSensor()
    sensor = drv.MAX30102(bus)
    for k in range(30):
        bus.push(k, k)
    assert sensor.read_fifo()[0] == 30
    for k in range(10):                           # write pointer wraps past 31
        bus.push(100 + k, 200 + k)
    n, lost, data = sensor.read_fifo()
    assert (n, lost) == (10, 0)
    assert sensor.ir(data, 9) == 209


def test_full_fifo_and_lost_samples_are_reported():
    bus = FakeSensor()
    sensor = drv.MAX30102(bus)
    for k in range(40):                           # 8 more than the FIFO holds
        bus.push(k, k)
    n, lost, data = sensor.read_fifo()
    assert n == 32 and lost == 8
    assert sensor.red(data, 0) == 0 and sensor.red(data, 31) == 31
    bus.push(7, 7)
    assert sensor.read_fifo()[:2] == (1, 0)       # counter cleared after the read


def test_stream_format_matches_the_host_parser():
    from ppg_resp.acquisition import SERIAL_HEADER, parse_serial_line

    main = (DRIVER.parent / "main.py").read_text()
    assert f'"{SERIAL_HEADER}\\n"' in main
    line = "%d,%d,%d,%d,%d\n" % (12, 3456789, 118000, 95000, 1)
    assert '"%d,%d,%d,%d,%d\\n" % (index, clock_us, ir, red, marker)' in main
    assert parse_serial_line(line) == (12, 3456789, 118000, 95000, 1)


@pytest.mark.parametrize(
    "name", ["main.py", "max30102.py", "check_sensor.py", "plot_pulse.py", "beat.py", "display.py", "ssd1306.py"]
)
def test_firmware_files_are_valid_python_syntax(name):
    compile((DRIVER.parent / name).read_text(), name, "exec")
