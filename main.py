from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.slider import Slider
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.graphics import Color, Ellipse, Line, Rectangle, InstructionGroup
from kivy.properties import NumericProperty, ListProperty, StringProperty, BooleanProperty
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
import threading
import math
import random
import time

# ---------------------------------------------------------------------------
# Platform Detection
# ---------------------------------------------------------------------------
try:
    from jnius import autoclass, cast, JavaException
    from android.runnable import run_on_ui_thread
    from android.permissions import request_permissions, Permission
    ANDROID = True
except ImportError:
    ANDROID = False

# ---------------------------------------------------------------------------
# Data Parser
# ---------------------------------------------------------------------------
def parse_sensor_line(line):
    """Parse 'TX,110.1,132.4,151.5' into [None, 110.1, 132.4, 151.5]
    TX = sensor is transmitting (no reading)
    Float = receiver reading (distance or time-of-flight)
    Returns None if format is invalid.
    """
    parts = line.split(",")
    if len(parts) < 4:
        return None
    values = []
    for p in parts[:4]:
        p = p.strip().upper()
        if p == "TX":
            values.append(None)
        else:
            try:
                values.append(float(p))
            except ValueError:
                return None
    return values

# ---------------------------------------------------------------------------
# BLE Manager (Android native via pyjnius - BluetoothGatt)
# ---------------------------------------------------------------------------
class BLEManager:
    """Native Android BLE using pyjnius + BluetoothGatt.
    No bleak/asyncio - works reliably with Kivy on Android."""

    def __init__(self, on_line, on_status):
        self.on_line = on_line
        self.on_status = on_status
        self.connected = False
        self._gatt = None
        self._thread = None
        self._running = False
        self._buffer = ""
        # Nordic UART Service UUIDs
        self.NUS_SERVICE = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
        self.NUS_TX_CHAR = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
        self.NUS_RX_CHAR = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

    def connect(self, device_name):
        if not ANDROID:
            self.on_status("MOCK: Desktop mode - use Mock Data")
            return False
        self._thread = threading.Thread(target=self._connect_ble, args=(device_name,), daemon=True)
        self._thread.start()
        return True

    def _connect_ble(self, device_name):
        try:
            self.on_status(f"Scanning for BLE '{device_name}'...")

            BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
            UUID = autoclass('java.util.UUID')

            adapter = BluetoothAdapter.getDefaultAdapter()
            if not adapter or not adapter.isEnabled():
                self.on_status("ERROR: Bluetooth disabled")
                return

            # Get bonded (paired) BLE devices first
            bonded = adapter.getBondedDevices()
            target = None
            if bonded:
                for dev in bonded.toArray():
                    if dev.getName() and device_name in dev.getName():
                        target = dev
                        break

            # If not bonded, scan for it
            if not target:
                scanner = adapter.getBluetoothLeScanner()
                if scanner:
                    from java.util import ArrayList
                    filters = ArrayList()
                    settings = autoclass('android.bluetooth.le.ScanSettings').Builder().build()

                    # Simple scan - 5 seconds
                    self.on_status("BLE scanning...")
                    found = []

                    class ScanCallback(autoclass('android.bluetooth.le.ScanCallback')):
                        def onScanResult(self, callbackType, result):
                            dev = result.getDevice()
                            name = dev.getName()
                            if name and device_name in name:
                                found.append(dev)

                    callback = ScanCallback()
                    scanner.startScan(filters, settings, callback)
                    time.sleep(5)
                    scanner.stopScan(callback)

                    if found:
                        target = found[0]

            if not target:
                self.on_status(f"ERROR: BLE device '{device_name}' not found")
                return

            self.on_status(f"Found {target.getName()}, connecting...")

            # Create Gatt callback
            class GattCallback(autoclass('android.bluetooth.BluetoothGattCallback')):
                def __init__(self, manager):
                    super().__init__()
                    self.manager = manager

                def onConnectionStateChange(self, gatt, status, newState):
                    if newState == autoclass('android.bluetooth.BluetoothProfile').STATE_CONNECTED:
                        self.manager.on_status("BLE Connected, discovering services...")
                        gatt.discoverServices()
                    elif newState == autoclass('android.bluetooth.BluetoothProfile').STATE_DISCONNECTED:
                        self.manager.connected = False
                        self.manager.on_status("BLE Disconnected")

                def onServicesDiscovered(self, gatt, status):
                    if status == 0:  # GATT_SUCCESS
                        service = gatt.getService(UUID.fromString(self.manager.NUS_SERVICE))
                        if service:
                            tx_char = service.getCharacteristic(UUID.fromString(self.manager.NUS_TX_CHAR))
                            if tx_char:
                                gatt.setCharacteristicNotification(tx_char, True)
                                # Enable notifications
                                desc_uuid = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
                                descriptor = tx_char.getDescriptor(desc_uuid)
                                if descriptor:
                                    descriptor.setValue(autoclass('android.bluetooth.BluetoothGattDescriptor').ENABLE_NOTIFICATION_VALUE)
                                    gatt.writeDescriptor(descriptor)
                                self.manager.connected = True
                                self.manager.on_status("BLE Ready - receiving data")
                        else:
                            self.manager.on_status("ERROR: NUS service not found")
                    else:
                        self.manager.on_status(f"ERROR: Service discovery failed {status}")

                def onCharacteristicChanged(self, gatt, characteristic):
                    data = characteristic.getValue()
                    if data:
                        text = bytes(data).decode('utf-8', errors='ignore')
                        self.manager._buffer += text
                        while '\n' in self.manager._buffer:
                            line, self.manager._buffer = self.manager._buffer.split('\n', 1)
                            line = line.strip()
                            if line:
                                self.manager.on_line(line)

            callback = GattCallback(self)
            self._gatt = target.connectGatt(
                autoclass('org.kivy.android.PythonActivity').mActivity,
                False, callback
            )

            self._running = True
            while self._running and self.connected:
                time.sleep(1)

        except JavaException as e:
            self.on_status(f"ERROR: {str(e)}")
        except Exception as e:
            self.on_status(f"ERROR: {str(e)}")
        finally:
            self.connected = False
            if self._gatt:
                try:
                    self._gatt.close()
                except:
                    pass
                self._gatt = None

    def disconnect(self):
        self._running = False
        self.connected = False
        if self._gatt:
            try:
                self._gatt.disconnect()
                self._gatt.close()
            except:
                pass
            self._gatt = None
        self.on_status("Disconnected")

# ---------------------------------------------------------------------------
# Classic Bluetooth SPP Manager (fallback for HC-05/06)
# ---------------------------------------------------------------------------
class BluetoothSerial:
    def __init__(self, on_line, on_status):
        self.on_line = on_line
        self.on_status = on_status
        self.socket = None
        self.connected = False
        self._thread = None
        self._running = False
        self._buffer = ""

    def connect(self, device_name):
        if not ANDROID:
            self.on_status("MOCK: Desktop mode - use Mock Data")
            return False
        try:
            BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
            UUID = autoclass('java.util.UUID')
            adapter = BluetoothAdapter.getDefaultAdapter()
            if not adapter or not adapter.isEnabled():
                self.on_status("ERROR: Bluetooth disabled")
                return False
            paired = adapter.getBondedDevices().toArray()
            target = None
            for dev in paired:
                if dev.getName() == device_name or dev.getAddress() == device_name:
                    target = dev
                    break
            if not target:
                self.on_status(f"ERROR: '{device_name}' not paired")
                return False
            uuid = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
            self.socket = target.createRfcommSocketToServiceRecord(uuid)
            self.socket.connect()
            self.connected = True
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            self.on_status("SPP Connected")
            return True
        except Exception as e:
            self.on_status(f"ERROR: {str(e)}")
            return False

    def disconnect(self):
        self._running = False
        self.connected = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        self.on_status("Disconnected")

    def _read_loop(self):
        while self._running and self.socket:
            try:
                instr = self.socket.getInputStream()
                if instr.available() > 0:
                    data = bytearray()
                    while instr.available() > 0:
                        data.append(instr.read() & 0xFF)
                    self._buffer += data.decode('utf-8', errors='ignore')
                    while '\n' in self._buffer:
                        line, self._buffer = self._buffer.split('\n', 1)
                        line = line.strip()
                        if line:
                            self.on_line(line)
            except Exception as e:
                if self._running:
                    self.on_status(f"READ ERROR: {str(e)}")
                break

# ---------------------------------------------------------------------------
# Circular Gauge Widget
# ---------------------------------------------------------------------------
class SensorGauge(Widget):
    value = NumericProperty(255)
    active = BooleanProperty(True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ig = InstructionGroup()
        self.canvas.add(self._ig)
        Clock.schedule_interval(self.draw, 0.05)
        self._pulse = 0.0

    def draw(self, dt):
        self._ig.clear()
        ig = self._ig
        w, h = self.size
        cx, cy = w/2, h/2
        r = min(w, h) * 0.38
        if r < 10:
            return

        if self.active:
            ig.add(Color(0.12, 0.14, 0.18, 1))
        else:
            ig.add(Color(0.08, 0.08, 0.10, 1))
        ig.add(Ellipse(pos=(cx-r, cy-r), size=(r*2, r*2)))

        ig.add(Color(0.04, 0.04, 0.08, 1))
        ig.add(Ellipse(pos=(cx-r*0.85, cy-r*0.85), size=(r*1.7, r*1.7)))

        if not self.active:
            ig.add(Color(0.3, 0.3, 0.35, 0.5))
            ig.add(Ellipse(pos=(cx-4, cy-4), size=(8, 8)))
            return

        pct = 1.0 - (self.value / 255.0)
        ticks = int(max(0, min(1, pct)) * 60)

        if pct < 0.33:
            ig.add(Color(1.0, 0.2, 0.4, 1))
        elif pct < 0.66:
            ig.add(Color(1.0, 0.8, 0.0, 1))
        else:
            ig.add(Color(0.0, 0.9, 1.0, 1))

        for i in range(ticks):
            a = math.radians(135 + i * 4.5)
            x1 = cx + (r*0.78) * math.cos(a)
            y1 = cy + (r*0.78) * math.sin(a)
            x2 = cx + r * math.cos(a)
            y2 = cy + r * math.sin(a)
            ig.add(Line(points=[x1, y1, x2, y2], width=2.5))

        self._pulse += dt * 5
        pulse_alpha = 0.3 + 0.2 * abs(math.sin(self._pulse))
        ig.add(Color(0.0, 0.9, 1.0, pulse_alpha))
        ig.add(Ellipse(pos=(cx-4, cy-4), size=(8, 8)))

# ---------------------------------------------------------------------------
# Radar / Sonar Canvas
# ---------------------------------------------------------------------------
class RadarCanvas(Widget):
    sensor_values = ListProperty([255, 255, 255, 255])
    sensor_active = ListProperty([True, True, True, True])
    obj_x = NumericProperty(0)
    obj_y = NumericProperty(0)
    obj_valid = BooleanProperty(False)
    inter_space = NumericProperty(30)
    total_length = NumericProperty(90)
    height = NumericProperty(100)
    max_range = NumericProperty(400)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ig = InstructionGroup()
        self.canvas.add(self._ig)
        Clock.schedule_interval(self.draw, 0.033)
        self.pings = []
        self.trail = []
        self._tick = 0
        self._last_values = [255, 255, 255, 255]

    def draw(self, dt):
        self._tick += dt
        self._ig.clear()
        ig = self._ig
        w, h = self.size
        if w < 50 or h < 50:
            return

        ig.add(Color(0.04, 0.04, 0.08, 1))
        ig.add(Rectangle(pos=self.pos, size=self.size))

        margin = dp(20)
        draw_w = w - 2 * margin
        draw_h = h - 2 * margin
        origin_x = self.x + margin
        origin_y = self.y + margin + draw_h * 0.88

        total = max(self.total_length, 1)
        max_r = max(self.max_range, 1)

        def px(cm_x):
            return origin_x + (cm_x / total) * draw_w
        def py(cm_y):
            return origin_y + (cm_y / max_r) * (draw_h * 0.85)

        # Grid
        ig.add(Color(0.0, 0.5, 0.6, 0.12))
        for i in range(7):
            x = origin_x + (i / 6) * draw_w
            ig.add(Line(points=[x, origin_y, x, origin_y + draw_h*0.85], width=1))
        for i in range(6):
            y = origin_y + (i / 5) * (draw_h * 0.85)
            ig.add(Line(points=[origin_x, y, origin_x + draw_w, y], width=1))

        ig.add(Color(0.0, 0.7, 0.8, 0.4))
        for i in range(7):
            x = origin_x + (i / 6) * draw_w
            ig.add(Line(points=[x, origin_y-3, x, origin_y+3], width=1))
        for i in range(6):
            y = origin_y + (i / 5) * (draw_h * 0.85)
            ig.add(Line(points=[origin_x-3, y, origin_x+3, y], width=1))

        # Sensor rail
        rail_y = origin_y
        ig.add(Color(0.2, 0.25, 0.3, 1))
        ig.add(Line(points=[origin_x, rail_y, origin_x + draw_w, rail_y], width=3))

        # Sensors
        sensor_xs = [0, self.inter_space, 2*self.inter_space, 3*self.inter_space]
        for i, sx in enumerate(sensor_xs):
            x = px(sx)
            active = self.sensor_active[i]
            val = self.sensor_values[i]
            intensity = 1.0 - (val / 255.0) if active else 0

            if not active:
                ig.add(Color(0.8, 0.2, 0.2, 1))
                ig.add(Ellipse(pos=(x-10, rail_y-10), size=(20, 20)))
                ig.add(Color(0.04, 0.04, 0.08, 1))
                ig.add(Ellipse(pos=(x-6, rail_y-6), size=(12, 12)))
            else:
                if intensity > 0.3:
                    ig.add(Color(0.0 + 0.9*intensity, 0.8, 1.0, 1))
                else:
                    ig.add(Color(0.25, 0.28, 0.35, 1))
                ig.add(Ellipse(pos=(x-10, rail_y-10), size=(20, 20)))
                ig.add(Color(0.04, 0.04, 0.08, 1))
                ig.add(Ellipse(pos=(x-6, rail_y-6), size=(12, 12)))

                if val < 240:
                    glow = 0.2 + 0.3 * intensity
                    ig.add(Color(0.0, 0.9, 1.0, glow * 0.4))
                    ig.add(Ellipse(pos=(x-18, rail_y-18), size=(36, 36)))

                if abs(val - self._last_values[i]) > 5:
                    self.pings.append({
                        'x': x, 'y': rail_y, 'r': 0, 'alpha': 0.8,
                        'speed': 80 + intensity * 100, 'birth': self._tick
                    })

        self._last_values = list(self.sensor_values)

        # Ping rings
        new_pings = []
        for p in self.pings:
            p['r'] += p['speed'] * dt
            p['alpha'] -= 1.2 * dt
            if p['alpha'] > 0 and p['r'] < draw_h:
                ig.add(Color(0.0, 0.85, 1.0, p['alpha'] * 0.35))
                ig.add(Ellipse(pos=(p['x']-p['r'], p['y']-p['r']), size=(p['r']*2, p['r']*2)))
                if p['r'] > 15:
                    ig.add(Color(0.0, 0.6, 0.8, p['alpha'] * 0.2))
                    ig.add(Ellipse(pos=(p['x']-p['r']+8, p['y']-p['r']+8), size=((p['r']-16)*2, (p['r']-16)*2)))
                new_pings.append(p)
        self.pings = new_pings

        # Object
        if self.obj_valid:
            ox = px(self.obj_x)
            oy = py(self.obj_y)

            if not self.trail or math.hypot(self.trail[-1][0]-ox, self.trail[-1][1]-oy) > 5:
                self.trail.append((ox, oy, self._tick))

            new_trail = []
            for tx, ty, birth in self.trail:
                age = self._tick - birth
                if age < 3.0:
                    alpha = 1.0 - (age / 3.0)
                    ig.add(Color(1.0, 0.2, 0.5, alpha * 0.6))
                    ig.add(Ellipse(pos=(tx-3, ty-3), size=(6, 6)))
                    new_trail.append((tx, ty, birth))
            self.trail = new_trail

            for layer in [(30, 0.15), (20, 0.25), (12, 0.5)]:
                r, a = layer
                ig.add(Color(1.0, 0.15, 0.4, a))
                ig.add(Ellipse(pos=(ox-r, oy-r), size=(r*2, r*2)))

            ig.add(Color(1.0, 0.9, 0.95, 1))
            ig.add(Ellipse(pos=(ox-6, oy-6), size=(12, 12)))

            ig.add(Color(1.0, 0.9, 0.0, 0.8))
            ig.add(Line(points=[ox-15, oy, ox+15, oy], width=1.5))
            ig.add(Line(points=[ox, oy-15, ox, oy+15], width=1.5))

            sweep = (self._tick * 2) % 6.283
            sx = ox + 25 * math.cos(sweep)
            sy = oy + 25 * math.sin(sweep)
            ig.add(Color(1.0, 0.9, 0.0, 0.3))
            ig.add(Line(points=[ox, oy, sx, sy], width=1))

        # CRT scanline
        scan_y = origin_y + ((self._tick * 40) % int(draw_h * 0.85))
        ig.add(Color(0.0, 0.9, 1.0, 0.04))
        ig.add(Line(points=[origin_x, scan_y, origin_x+draw_w, scan_y], width=2))

# ---------------------------------------------------------------------------
# Setting Slider
# ---------------------------------------------------------------------------
class SettingSlider(BoxLayout):
    def __init__(self, label_text="Param", min_val=0, max_val=100, default=50, **kwargs):
        self.label_text = label_text
        self.min_val = min_val
        self.max_val = max_val
        self.default = default
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.size_hint_y = None
        self.height = dp(70)
        self.spacing = dp(4)

        top = BoxLayout(size_hint_y=None, height=dp(20))
        self.name_lbl = Label(
            text=self.label_text, color=(0.7, 0.8, 0.9, 1),
            font_size=sp(12), halign='left'
        )
        self.name_lbl.bind(size=self.name_lbl.setter('text_size'))
        self.val_lbl = Label(
            text=str(self.default), color=(0.0, 0.9, 1.0, 1),
            font_size=sp(12), halign='right'
        )
        self.val_lbl.bind(size=self.val_lbl.setter('text_size'))
        top.add_widget(self.name_lbl)
        top.add_widget(self.val_lbl)
        self.add_widget(top)

        self.slider = Slider(
            min=self.min_val, max=self.max_val, value=self.default,
            cursor_size=(dp(20), dp(20))
        )
        self.slider.bind(value=self.on_value)
        self.add_widget(self.slider)

    def on_value(self, inst, val):
        self.val_lbl.text = f"{val:.1f}"

    def get_value(self):
        return self.slider.value

# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------
class UltraSonicApp(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        Window.clearcolor = (0.02, 0.02, 0.04, 1)

        self.ble = BLEManager(self.on_data_line, self.on_status)
        self.spp = BluetoothSerial(self.on_data_line, self.on_status)
        self.active_conn = None

        self._lock = threading.Lock()
        self._latest_values = [None, None, None, None]
        self._data_count = 0
        self._last_data_time = 0

        # ---- HEADER ----
        header = BoxLayout(size_hint_y=None, height=dp(56), padding=dp(8), spacing=dp(10))
        with header.canvas.before:
            Color(0.06, 0.07, 0.12, 1)
            self.header_rect = Rectangle(pos=header.pos, size=header.size)
        header.bind(pos=self._update_header_rect, size=self._update_header_rect)

        title = Label(
            text="[b]ULTRA-SONIC[/b]  DEMONSTRATOR",
            markup=True, color=(0.0, 0.85, 1.0, 1),
            font_size=sp(18), size_hint_x=0.30, halign='left'
        )
        title.bind(size=title.setter('text_size'))

        self.status_box = BoxLayout(size_hint_x=0.18, spacing=dp(6))
        self.led = Widget(size_hint=(None, None), size=(dp(14), dp(14)), pos_hint={'center_y': 0.5})
        with self.led.canvas:
            Color(0.3, 0.3, 0.3, 1)
            self.led_ellipse = Ellipse(pos=self.led.pos, size=self.led.size)
        self.led.bind(pos=self._update_led)
        self.led.bind(size=self._update_led)
        self.status_lbl = Label(text="IDLE", color=(0.5, 0.6, 0.7, 1), font_size=sp(13))
        self.status_box.add_widget(self.led)
        self.status_box.add_widget(self.status_lbl)

        self.rate_lbl = Label(text="0 Hz", color=(0.4, 0.5, 0.6, 1), font_size=sp(12), size_hint_x=0.08)

        self.dev_input = TextInput(
            text="USPA-Sensor", hint_text="DEV NAME", multiline=False, size_hint_x=0.16,
            background_color=(0.08, 0.09, 0.14, 1), foreground_color=(0.0, 0.9, 1.0, 1),
            cursor_color=(0.0, 0.9, 1.0, 1), padding=[dp(8), dp(8), 0, 0], font_size=sp(13)
        )

        self.ble_btn = Button(
            text="BLE", size_hint_x=0.10,
            background_color=(0.0, 0.5, 0.7, 1), color=(1,1,1,1), font_size=sp(12)
        )
        self.ble_btn.bind(on_press=self.toggle_ble)

        self.spp_btn = Button(
            text="SPP", size_hint_x=0.10,
            background_color=(0.3, 0.3, 0.4, 1), color=(1,1,1,1), font_size=sp(12)
        )
        self.spp_btn.bind(on_press=self.toggle_spp)

        self.mock_btn = Button(
            text="MOCK", size_hint_x=0.10,
            background_color=(0.6, 0.3, 0.0, 1), color=(1,1,1,1), font_size=sp(12)
        )
        self.mock_btn.bind(on_press=self.toggle_mock)

        header.add_widget(title)
        header.add_widget(self.status_box)
        header.add_widget(self.rate_lbl)
        header.add_widget(self.dev_input)
        header.add_widget(self.ble_btn)
        header.add_widget(self.spp_btn)
        header.add_widget(self.mock_btn)
        self.add_widget(header)

        # ---- BODY ----
        body = BoxLayout(spacing=dp(10), padding=dp(10))

        self.radar = RadarCanvas(size_hint_x=0.70)
        body.add_widget(self.radar)

        right_panel = BoxLayout(orientation='vertical', size_hint_x=0.30, spacing=dp(10))

        # Gauges with labels
        gauges_box = GridLayout(cols=2, spacing=dp(10), size_hint_y=0.42)
        self.gauges = []
        for i in range(4):
            cell = BoxLayout(orientation='vertical', spacing=dp(4))
            g = SensorGauge()
            lbl = Label(text=f"S{i+1}", color=(0.6, 0.7, 0.8, 1), font_size=sp(13), size_hint_y=None, height=dp(18))
            val_lbl = Label(text="—", color=(0.0, 0.9, 1.0, 1), font_size=sp(14), size_hint_y=None, height=dp(18))
            cell.add_widget(g)
            cell.add_widget(lbl)
            cell.add_widget(val_lbl)
            gauges_box.add_widget(cell)
            self.gauges.append({'gauge': g, 'val_lbl': val_lbl})
        right_panel.add_widget(gauges_box)

        # Settings
        settings_box = BoxLayout(orientation='vertical', spacing=dp(6), size_hint_y=0.38)
        settings_title = Label(
            text="[b]GEOMETRY[/b]", markup=True, color=(0.5, 0.6, 0.7, 1),
            font_size=sp(14), size_hint_y=None, height=dp(24)
        )
        settings_box.add_widget(settings_title)

        self.sliders = {}
        for name, min_v, max_v, default in [
            ("Height", 10, 300, 100),
            ("Inter-Space", 5, 100, 30),
            ("Total Length", 30, 300, 90),
            ("Max Range", 50, 800, 400),
        ]:
            s = SettingSlider(label_text=f"{name} (cm)", min_val=min_v, max_val=max_v, default=default)
            s.slider.bind(value=self.on_slider_change)
            settings_box.add_widget(s)
            self.sliders[name] = s

        right_panel.add_widget(settings_box)

        # Lock indicator
        self.lock_box = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        self.lock_led = Widget(size_hint=(None, None), size=(dp(12), dp(12)), pos_hint={'center_y': 0.5})
        with self.lock_led.canvas:
            Color(0.2, 0.2, 0.2, 1)
            self.lock_ellipse = Ellipse(pos=self.lock_led.pos, size=self.lock_led.size)
        self.lock_led.bind(pos=self._update_lock_led)
        self.lock_led.bind(size=self._update_lock_led)
        self.lock_lbl = Label(text="NO LOCK", color=(0.4, 0.4, 0.5, 1), font_size=sp(13))
        self.lock_box.add_widget(self.lock_led)
        self.lock_box.add_widget(self.lock_lbl)
        right_panel.add_widget(self.lock_box)

        # Log
        self.log_lbl = Label(
            text="System ready. Select BLE (Pico 2W) or SPP (HC-05).",
            color=(0.3, 0.4, 0.5, 1), font_size=sp(11),
            size_hint_y=None, height=dp(36), halign='left', valign='middle'
        )
        self.log_lbl.bind(size=self.log_lbl.setter('text_size'))
        right_panel.add_widget(self.log_lbl)

        body.add_widget(right_panel)
        self.add_widget(body)

        self.apply_settings()
        Clock.schedule_interval(self.update_rate, 1.0)

        if ANDROID:
            request_permissions([
                Permission.BLUETOOTH, Permission.BLUETOOTH_ADMIN,
                Permission.BLUETOOTH_CONNECT, Permission.BLUETOOTH_SCAN,
                Permission.ACCESS_FINE_LOCATION,
            ])

    def _update_header_rect(self, obj, val):
        self.header_rect.pos = obj.pos
        self.header_rect.size = obj.size

    def _update_led(self, obj, val):
        self.led_ellipse.pos = obj.pos
        self.led_ellipse.size = obj.size

    def _update_lock_led(self, obj, val):
        self.lock_ellipse.pos = obj.pos
        self.lock_ellipse.size = obj.size

    def on_slider_change(self, inst, val):
        self.apply_settings()

    def apply_settings(self):
        try:
            self.radar.height = self.sliders["Height"].get_value()
            self.radar.inter_space = self.sliders["Inter-Space"].get_value()
            self.radar.total_length = self.sliders["Total Length"].get_value()
            self.radar.max_range = self.sliders["Max Range"].get_value()
        except:
            pass

    def _disconnect_all(self):
        if self.active_conn == 'ble':
            self.ble.disconnect()
        elif self.active_conn == 'spp':
            self.spp.disconnect()
        self.active_conn = None

    def toggle_ble(self, btn):
        if self.active_conn == 'ble':
            self._disconnect_all()
            btn.text = "BLE"
            btn.background_color = (0.0, 0.5, 0.7, 1)
            self._set_status("IDLE", (0.3, 0.3, 0.3, 1))
        else:
            self._disconnect_all()
            self.spp_btn.text = "SPP"
            self.spp_btn.background_color = (0.3, 0.3, 0.4, 1)
            self.mock_btn.text = "MOCK"
            self.mock_btn.background_color = (0.6, 0.3, 0.0, 1)
            if hasattr(self, '_mock_ev') and self._mock_ev:
                Clock.unschedule(self._mock_ev)
                self._mock_ev = None
            name = self.dev_input.text.strip()
            if name:
                btn.text = "..."
                self.active_conn = 'ble'
                self.ble.connect(name)

    def toggle_spp(self, btn):
        if self.active_conn == 'spp':
            self._disconnect_all()
            btn.text = "SPP"
            btn.background_color = (0.3, 0.3, 0.4, 1)
            self._set_status("IDLE", (0.3, 0.3, 0.3, 1))
        else:
            self._disconnect_all()
            self.ble_btn.text = "BLE"
            self.ble_btn.background_color = (0.0, 0.5, 0.7, 1)
            self.mock_btn.text = "MOCK"
            self.mock_btn.background_color = (0.6, 0.3, 0.0, 1)
            if hasattr(self, '_mock_ev') and self._mock_ev:
                Clock.unschedule(self._mock_ev)
                self._mock_ev = None
            name = self.dev_input.text.strip()
            if name:
                btn.text = "..."
                self.active_conn = 'spp'
                threading.Thread(target=self._do_spp_connect, args=(name,), daemon=True).start()

    def _do_spp_connect(self, name):
        ok = self.spp.connect(name)
        Clock.schedule_once(lambda dt: self._post_spp_connect(ok), 0)

    def _post_spp_connect(self, ok):
        if ok:
            self.spp_btn.text = "DISCON"
            self.spp_btn.background_color = (0.7, 0.15, 0.2, 1)
            self._set_status("SPP", (0.0, 0.7, 0.9, 1))
        else:
            self.spp_btn.text = "SPP"
            self.spp_btn.background_color = (0.3, 0.3, 0.4, 1)
            self._set_status("FAIL", (0.9, 0.2, 0.2, 1))
            self.active_conn = None

    def _set_status(self, text, color):
        self.status_lbl.text = text
        self.status_lbl.color = color
        self.led.canvas.clear()
        with self.led.canvas:
            Color(*color)
            self.led_ellipse = Ellipse(pos=self.led.pos, size=self.led.size)

    def toggle_mock(self, btn):
        if hasattr(self, '_mock_ev') and self._mock_ev:
            Clock.unschedule(self._mock_ev)
            self._mock_ev = None
            btn.text = "MOCK"
            btn.background_color = (0.6, 0.3, 0.0, 1)
            self.log_lbl.text = "Mock stopped."
            self._disconnect_all()
        else:
            self._disconnect_all()
            self.ble_btn.text = "BLE"
            self.ble_btn.background_color = (0.0, 0.5, 0.7, 1)
            self.spp_btn.text = "SPP"
            self.spp_btn.background_color = (0.3, 0.3, 0.4, 1)
            btn.text = "STOP"
            btn.background_color = (0.8, 0.2, 0.2, 1)
            self.log_lbl.text = "Mock data running..."
            self._mock_ev = Clock.schedule_interval(self._mock_tick, 0.4)

    def _mock_tick(self, dt):
        t = Clock.get_time()
        ox = 15 + 50 * abs(math.sin(t * 0.7))
        oy = 30 + 80 * abs(math.cos(t * 0.5))
        inter = self.radar.inter_space
        h = self.radar.height
        max_r = self.radar.max_range
        vals = [None]
        for i in range(1, 4):
            sx = i * inter
            d_g = math.sqrt((ox - sx)**2 + oy**2)
            d_s = math.sqrt(d_g**2 + h**2)
            raw = min(max_r, d_s)
            raw = max(0, raw + random.uniform(-2, 2))
            vals.append(raw)
        line = f"TX,{vals[1]:.1f},{vals[2]:.1f},{vals[3]:.1f}"
        self.on_data_line(line)

    def update_rate(self, dt):
        now = time.time()
        if now - self._last_data_time > 2.0:
            self.rate_lbl.text = "0 Hz"
        else:
            hz = self._data_count
            self.rate_lbl.text = f"{hz} Hz"
        self._data_count = 0
        self._last_data_time = now

    def on_status(self, msg):
        Clock.schedule_once(lambda dt: setattr(self.log_lbl, 'text', msg), 0)
        if "Connected" in msg or "Ready" in msg:
            if "BLE" in msg:
                self.ble_btn.text = "DISCON"
                self.ble_btn.background_color = (0.7, 0.15, 0.2, 1)
                self._set_status("BLE", (0.0, 0.7, 0.9, 1))
            else:
                self._set_status("SPP", (0.0, 0.7, 0.9, 1))
        elif "Disconnected" in msg:
            self._set_status("IDLE", (0.3, 0.3, 0.3, 1))
            if self.active_conn == 'ble':
                self.ble_btn.text = "BLE"
                self.ble_btn.background_color = (0.0, 0.5, 0.7, 1)
            elif self.active_conn == 'spp':
                self.spp_btn.text = "SPP"
                self.spp_btn.background_color = (0.3, 0.3, 0.4, 1)
        elif "ERROR" in msg:
            self._set_status("ERR", (0.9, 0.2, 0.2, 1))

    def on_data_line(self, msg):
        if msg.startswith("ERROR:") or msg.startswith("MOCK:") or msg.startswith("Connected") or msg.startswith("Disconnected") or msg.startswith("Scanning"):
            self.on_status(msg)
            return

        parsed = parse_sensor_line(msg)
        if parsed is None:
            self.on_status(f"Parse error: {msg[:40]}")
            return

        with self._lock:
            self._latest_values = parsed
        self._data_count += 1
        self._last_data_time = time.time()
        Clock.schedule_once(lambda dt: self.update_ui(parsed), 0)

    def update_ui(self, values):
        max_r = self.radar.max_range

        vis_values = []
        active_flags = []
        for i, v in enumerate(values):
            if v is None:
                self.gauges[i]['gauge'].active = False
                self.gauges[i]['gauge'].value = 255
                self.gauges[i]['val_lbl'].text = "TX"
                self.gauges[i]['val_lbl'].color = (0.8, 0.2, 0.2, 1)
                vis_values.append(255)
                active_flags.append(False)
            else:
                self.gauges[i]['gauge'].active = True
                vis = min(255, int((v / max_r) * 255))
                self.gauges[i]['gauge'].value = vis
                self.gauges[i]['val_lbl'].text = f"{v:.1f}"
                self.gauges[i]['val_lbl'].color = (0.0, 0.9, 1.0, 1)
                vis_values.append(vis)
                active_flags.append(True)

        self.radar.sensor_values = vis_values
        self.radar.sensor_active = active_flags

        inter = self.radar.inter_space
        h = self.radar.height

        sensors = []
        for i, v in enumerate(values):
            if v is not None and v > 0:
                g2 = v*v - h*h
                ground = math.sqrt(g2) if g2 > 0 else 0.0
                sensors.append((i, i * inter, ground))

        sensors.sort(key=lambda x: x[2])
        valid = [s for s in sensors if s[2] > 1.0]

        x = y = None
        if len(valid) >= 2:
            i, j = valid[0], valid[1]
            xi, xj = i[1], j[1]
            di, dj = i[2], j[2]
            dx = xj - xi
            if abs(dx) > 0.01:
                x = (di*di - dj*dj + xj*xj - xi*xi) / (2.0 * dx)
                yy = di*di - (x - xi)**2
                y = math.sqrt(yy) if yy > 0 else 0.0
            else:
                x, y = xi, di
        elif len(valid) == 1:
            i = valid[0]
            x, y = i[1], i[2]

        if x is not None and y is not None and x >= -10 and y >= -10:
            self.radar.obj_x = x
            self.radar.obj_y = y
            self.radar.obj_valid = True
            self.lock_lbl.text = f"LOCK  ({x:.1f}, {y:.1f})"
            self.lock_lbl.color = (1.0, 0.2, 0.5, 1)
            self.lock_led.canvas.clear()
            with self.lock_led.canvas:
                Color(1.0, 0.2, 0.5, 1)
                self.lock_ellipse = Ellipse(pos=self.lock_led.pos, size=self.lock_led.size)
        else:
            self.radar.obj_valid = False
            self.lock_lbl.text = "NO LOCK"
            self.lock_lbl.color = (0.4, 0.4, 0.5, 1)
            self.lock_led.canvas.clear()
            with self.lock_led.canvas:
                Color(0.2, 0.2, 0.2, 1)
                self.lock_ellipse = Ellipse(pos=self.lock_led.pos, size=self.lock_led.size)

class UltraSonicDemonstratorApp(App):
    kv_file = None
    def build(self):
        return UltraSonicApp()

if __name__ == '__main__':
    UltraSonicDemonstratorApp().run()
