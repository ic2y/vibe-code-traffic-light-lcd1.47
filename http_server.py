"""
LED 显示屏 HTTP 服务（重构版）

布局：显示屏横放（160x80），中间画一条竖线，分为左右两个区域，
每个区域各有一个红灯和一个绿灯，灯用长方形绘制（模拟红绿灯）：
灰色空心长方形为灯座（常显），点亮时内部填充红/绿颜色。
每侧上红下绿，上下各占一半、占满整屏高度。

规则：同一区域内红绿互斥 —— 红灯亮则绿灯灭，绿灯亮则红灯灭；
左右两个区域相互独立。

对外 API：
    GET /led/left/green/on    左绿亮
    GET /led/left/red/on      左红亮
    GET /led/right/red/on     右红亮
    GET /led/right/green/on   右绿亮
    GET /led/all/off          左右全部熄灭（辅助接口）
    GET /status               查询当前左右状态
    GET /health               健康检查

运行：
    python http_server.py                  # 连接真实显示屏（串口）
    LED_SIMULATE=1 python http_server.py   # 模拟模式，无需硬件，便于联调/测试

监听地址与端口可通过环境变量覆盖：LED_HOST / LED_PORT
"""
import os
import re
import threading
import time
from typing import Dict, Optional, Tuple

from flask import Flask, jsonify
import serial
import serial.tools.list_ports


# ============ 显示常量 ============
LCD_X = 160
LCD_Y = 80
MIDDLE_LINE_X = LCD_X // 2  # 中间竖线 x 坐标

COLOR_RED = 0xF800
COLOR_GREEN = 0x03E0   # 深绿（RGB565 G 分量减半，避免刺眼）
COLOR_BLACK = 0x0000
COLOR_RING = 0x8410           # 灯座颜色（熄灭时的灯座，中灰色，清晰可见）
MIDDLE_LINE_COLOR = 0x7BEF    # 中间分隔线颜色（浅灰）

# 每个区域两个灯的位置与大小：{区域: {灯色: (x, y, w, h)}}
# x,y 为灯座外框左上角，w,h 为宽高（长方形）；上红下绿，上下占满整屏高度
LIGHT_BOXES: Dict[str, Dict[str, Tuple[int, int, int, int]]] = {
    "left": {
        "red": (3, 0, 74, 40),
        "green": (3, 40, 74, 40),
    },
    "right": {
        "red": (83, 0, 74, 40),
        "green": (83, 40, 74, 40),
    },
}
LIT_BOX_INSET = 4   # 点亮时内部填充的长方形，比灯座外框每边小 4 像素

LIGHT_COLORS = {"red": COLOR_RED, "green": COLOR_GREEN}
REGIONS = ("left", "right")
COLORS = ("red", "green")

# 灯内字母标签：左侧灯显示 OP（OpenCode），右侧灯显示 CC（Claude Code），常显白色
BOX_LABELS = {"left": "OP", "right": "CC"}
LETTER_COLOR = 0xFFFF
LETTER_SCALE = 4     # 5x7 点阵放大倍数

# 5x7 点阵字库（仅用到 O/P/C）
FONT_5X7 = {
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
}

# ============ 运行配置 ============
SIMULATE = os.environ.get("LED_SIMULATE", "0").lower() in ("1", "true", "yes")
HOST = os.environ.get("LED_HOST", "0.0.0.0")
PORT = int(os.environ.get("LED_PORT", "15000"))


class MSU2LiteDisplay:
    """MSU2 LITE 显示屏控制类（串口）"""

    def __init__(self, serial_port: serial.Serial):
        self.ser = serial_port

    @staticmethod
    def auto_connect() -> "MSU2LiteDisplay":
        """自动检测并连接 MSU2 设备（优先 USB 虚拟串口，ttyS* 传统串口放最后）

        可用环境变量 LED_PORT_DEV 直接指定串口设备（如 /dev/ttyACM0），跳过自动扫描。
        """
        forced = os.environ.get("LED_PORT_DEV", "").strip()
        if forced:
            print(f"[手] 使用 LED_PORT_DEV 指定的串口: {forced}")
            ser = MSU2LiteDisplay._open_port(forced)
            if ser is None:
                raise RuntimeError(f"无法打开指定的串口设备: {forced}")
            if MSU2LiteDisplay._handshake(ser):
                print(f"[OK] 已连接串口: {ser.port}")
                return MSU2LiteDisplay(ser)
            ser.close()
            raise RuntimeError(f"指定的串口设备 {forced} 握手失败（设备未响应）")

        ports = list(serial.tools.list_ports.comports())
        if not ports:
            raise RuntimeError("未检测到串口设备")

        # USB 转串口（ttyACM*/ttyUSB*）才是真设备所在地，优先握手；
        # 主板自带的 ttyS* 大多数没接设备，放最后以免逐个等握手超时拖慢启动。
        def sort_key(port) -> int:
            name = port.name.lower()
            if name.startswith(("ttyacm", "ttyusb")):
                return 0
            if name.startswith("tty"):
                return 1
            return 2

        ports.sort(key=sort_key)

        for port in ports:
            print(f"[手] 尝试串口: {port.name} - {port.description}")
            # 用 port.device（全路径，如 /dev/ttyACM0）打开；port.name 在 Linux 上不带 /dev/ 前缀
            ser = MSU2LiteDisplay._open_port(port.device)
            if ser is None:
                continue

            try:
                if MSU2LiteDisplay._handshake(ser):
                    print(f"[OK] 已连接串口: {ser.port}")
                    return MSU2LiteDisplay(ser)
            except Exception as e:
                print(f"  [手] {port.name} 握手异常: {e}")

            ser.close()
            print(f"  [手] {port.name} 握手超时，未收到设备响应")

        raise RuntimeError("未找到可通信的 MSU2 设备")

    @staticmethod
    def _open_port(name: str) -> Optional[serial.Serial]:
        """打开串口；ACM/USB 设备不支持硬件流控时回退为无流控重试"""
        for rtscts in (True, False):
            try:
                return serial.Serial(
                    port=name,
                    baudrate=921600,
                    timeout=0.2,
                    xonxoff=False,
                    rtscts=rtscts,
                )
            except Exception as e:
                print(f"  [手] {name} rtscts={rtscts} 打开失败: {e}")
        return None

    @staticmethod
    def _handshake(ser: serial.Serial) -> bool:
        """与设备握手：等待设备主动上报 MSN 信息，收到后回复确认

        设备只在特定时机（上电/被主机打开）发送欢迎消息，因此读取要灵敏，
        并打印收到的原始字节便于排查。
        """
        deadline = time.time() + 2.0
        buf = b""
        while time.time() < deadline:
            if ser.in_waiting:
                recv = ser.read(ser.in_waiting)
            else:
                recv = ser.read(1)  # 阻塞最多 timeout(0.2s)
            if not recv:
                continue

            print(f"  [手] {ser.port} 收到 {len(recv)} 字节: {recv!r}")
            buf += recv  # 设备可能分帧发送（如 \x00 与 MSN01 分开到达），须累积匹配
            text = buf.decode("gbk", errors="ignore")
            if re.search(r"\x00MSN\d\d", text):
                ser.write(b"\x00MSNCN")
                time.sleep(0.1)
                return True

        return False

    def close(self) -> None:
        """关闭串口连接"""
        if self.ser.is_open:
            self.ser.close()

    def _send(self, data: bytes) -> None:
        """发送数据"""
        self.ser.write(data)

    @staticmethod
    def _hi(value: int) -> int:
        """获取高字节"""
        return (value // 256) & 0xFF

    @staticmethod
    def _lo(value: int) -> int:
        """获取低字节"""
        return value & 0xFF

    def lcd_set_xy(self, x: int, y: int) -> None:
        """设置 LCD 坐标"""
        self._send(bytes([2, 0, self._hi(x), self._lo(x), self._hi(y), self._lo(y)]))

    def lcd_set_size(self, width: int, height: int) -> None:
        """设置 LCD 尺寸"""
        self._send(
            bytes([2, 1, self._hi(width), self._lo(width), self._hi(height), self._lo(height)])
        )

    def lcd_set_state(self, state: int) -> None:
        """设置 LCD 状态"""
        self._send(bytes([2, 3, 10, state & 0xFF, 0, 0]))

    def lcd_color_fill(self, x: int, y: int, width: int, height: int, color: int) -> None:
        """填充矩形颜色"""
        self.lcd_set_xy(x, y)
        self.lcd_set_size(width, height)
        self._send(bytes([2, 3, 11, self._hi(color), self._lo(color), 0]))


class SimulatedDisplay:
    """模拟显示屏：不连接硬件，记录每次填充操作，便于无硬件联调与测试"""

    def __init__(self):
        self.fills: list = []

    def close(self) -> None:
        pass

    def lcd_set_state(self, state: int) -> None:
        pass

    def lcd_color_fill(self, x: int, y: int, width: int, height: int, color: int) -> None:
        self.fills.append((x, y, width, height, color))


class LedController:
    """管理左右两个区域的红绿灯状态与渲染"""

    REFRESH_INTERVAL = 2.0  # 秒；周期性重绘，压制真实设备自动休眠/屏保
    AUTO_OFF_TIMEOUT = 1800.0   # 秒；最后一次请求后 30 分钟无请求则自动全部熄灭
    AUTO_OFF_CHECK_INTERVAL = 5.0  # 秒；自动熄灭检查间隔

    def __init__(self, display):
        self.display = display
        self.lock = threading.Lock()
        self.state: Dict[str, Optional[str]] = {region: None for region in REGIONS}
        self._last_request = time.monotonic()
        self._thread: Optional[threading.Thread] = None
        self._auto_off_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def initialize(self) -> None:
        """初始化：全部熄灭，启动防休眠重绘线程（真实设备）与超时自动熄灭线程"""
        self.display.lcd_set_state(0)
        self.all_off()
        self._stop.clear()
        if not isinstance(self.display, SimulatedDisplay):
            self._thread = threading.Thread(target=self._keep_alive, daemon=True)
            self._thread.start()
        self._auto_off_thread = threading.Thread(target=self._auto_off_monitor, daemon=True)
        self._auto_off_thread.start()

    def note_request(self) -> None:
        """记录一次 HTTP 请求，刷新最后活动时间"""
        self._last_request = time.monotonic()

    def _keep_alive(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.REFRESH_INTERVAL)
            with self.lock:
                # 不清屏直接重画相同内容：画面不变，串口仍持续活动，压制设备休眠，避免黑屏闪烁
                self._render(clear=False)

    def _auto_off_monitor(self) -> None:
        """超时自动熄灭：最后一次请求后超过 AUTO_OFF_TIMEOUT 无请求，则全部熄灭"""
        while not self._stop.is_set():
            time.sleep(self.AUTO_OFF_CHECK_INTERVAL)
            if time.monotonic() - self._last_request >= self.AUTO_OFF_TIMEOUT:
                with self.lock:
                    if any(v is not None for v in self.state.values()):
                        self._set_all_off()

    def turn_on(self, region: str, color: str) -> None:
        """点亮指定区域指定颜色的灯；同区域另一色自动熄灭"""
        if region not in REGIONS:
            raise ValueError(f"无效区域: {region}")
        if color not in COLORS:
            raise ValueError(f"无效灯色: {color}")
        with self.lock:
            self.state[region] = color
            self._render()

    def all_off(self) -> None:
        """左右区域全部熄灭"""
        with self.lock:
            self._set_all_off()

    def _set_all_off(self) -> None:
        """左右区域全部熄灭（须持有锁）"""
        for region in REGIONS:
            self.state[region] = None
        self._render()

    def get_status(self) -> Dict:
        """获取左右区域当前状态"""
        with self.lock:
            return {
                "mode": "simulate" if isinstance(self.display, SimulatedDisplay) else "real",
                "left": {"red": self.state["left"] == "red", "green": self.state["left"] == "green"},
                "right": {"red": self.state["right"] == "red", "green": self.state["right"] == "green"},
            }

    def close(self) -> None:
        """停止重绘/自动熄灭线程并关闭显示设备"""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._auto_off_thread:
            self._auto_off_thread.join(timeout=2.0)
        self.display.close()

    @staticmethod
    def _draw_box_border(display, x: int, y: int, w: int, h: int, thickness: int, color: int) -> None:
        """空心正方形边框，线宽 thickness（四条边分别填充）"""
        display.lcd_color_fill(x, y, w, thickness, color)            # 上边
        display.lcd_color_fill(x, y + h - thickness, w, thickness, color)  # 下边
        display.lcd_color_fill(x, y + thickness, thickness, h - 2 * thickness, color)   # 左边
        display.lcd_color_fill(x + w - thickness, y + thickness, thickness, h - 2 * thickness, color)  # 右边

    @staticmethod
    def _draw_box(display, x: int, y: int, w: int, h: int, color: int) -> None:
        """实心长方形"""
        display.lcd_color_fill(x, y, w, h, color)

    @staticmethod
    def _draw_text(display, x: int, y: int, text: str, scale: int, color: int) -> None:
        """按 5x7 点阵放大 scale 倍绘制文本（每个点为 scale x scale 的方块）"""
        cursor_x = x
        for ch in text:
            glyph = FONT_5X7.get(ch)
            if glyph is None:
                continue
            for row_i, row in enumerate(glyph):
                for col_i, bit in enumerate(row):
                    if bit != "0":
                        display.lcd_color_fill(
                            cursor_x + col_i * scale,
                            y + row_i * scale,
                            scale,
                            scale,
                            color,
                        )
            cursor_x += (len(glyph[0]) + 1) * scale  # 字符间距 1 个点

    def _render(self, clear: bool = True) -> None:
        """按当前状态绘制整屏（须持有锁）

        clear=False 时不先清屏，直接重画相同内容，用于周期重绘：
        画面不变、串口仍持续活动，可压制设备休眠而不引起黑屏闪烁。
        """
        # 清屏
        if clear:
            self.display.lcd_color_fill(0, 0, LCD_X, LCD_Y, COLOR_BLACK)
        # 中间竖线
        self.display.lcd_color_fill(MIDDLE_LINE_X, 0, 1, LCD_Y, MIDDLE_LINE_COLOR)
        # 每个区域：灯座空心长方形常显，点亮时内部填充红/绿，灯内常显字母标签
        for region in REGIONS:
            for color in COLORS:
                x, y, w, h = LIGHT_BOXES[region][color]
                lit = self.state[region] == color
                # 先画暗色灯座边框
                self._draw_box_border(self.display, x, y, w, h, 2, COLOR_RING)
                # 点亮时填充内部长方形
                if lit:
                    self._draw_box(
                        self.display,
                        x + LIT_BOX_INSET,
                        y + LIT_BOX_INSET,
                        w - 2 * LIT_BOX_INSET,
                        h - 2 * LIT_BOX_INSET,
                        LIGHT_COLORS[color],
                    )
                # 灯内字母标签（居中常显）
                label = BOX_LABELS[region]
                tw = (len(label) * 6 - 1) * LETTER_SCALE
                th = 7 * LETTER_SCALE
                self._draw_text(
                    self.display,
                    x + (w - tw) // 2,
                    y + (h - th) // 2,
                    label,
                    LETTER_SCALE,
                    LETTER_COLOR,
                )


# ============ Flask 应用 ============
app = Flask(__name__)
controller: Optional[LedController] = None


@app.before_request
def _note_request():
    """每个 HTTP 请求刷新最后活动时间，用于超时自动熄灭"""
    ctrl = controller
    if ctrl is not None:
        ctrl.note_request()


def _get_controller() -> LedController:
    if controller is None:
        raise RuntimeError("服务器尚未初始化")
    return controller


def _turn_on(region: str, color: str):
    try:
        ctrl = _get_controller()
        ctrl.turn_on(region, color)
        state = ctrl.get_status()
        side = "左" if region == "left" else "右"
        color_cn = "红" if color == "red" else "绿"
        return jsonify({
            "success": True,
            "message": f"{side}侧{color_cn}灯已点亮",
            "left": state["left"],
            "right": state["right"],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """健康检查"""
    return jsonify({"status": "ok", "service": "LED Display Service"})


@app.route("/status", methods=["GET"])
def status():
    """查询左右区域当前状态"""
    try:
        return jsonify(_get_controller().get_status())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/led/left/green/on", methods=["GET"])
def left_green_on():
    """左绿亮"""
    return _turn_on("left", "green")


@app.route("/led/left/red/on", methods=["GET"])
def left_red_on():
    """左红亮"""
    return _turn_on("left", "red")


@app.route("/led/right/red/on", methods=["GET"])
def right_red_on():
    """右红亮"""
    return _turn_on("right", "red")


@app.route("/led/right/green/on", methods=["GET"])
def right_green_on():
    """右绿亮"""
    return _turn_on("right", "green")


@app.route("/led/all/off", methods=["GET"])
def all_off():
    """左右区域全部熄灭"""
    try:
        ctrl = _get_controller()
        ctrl.all_off()
        return jsonify({"success": True, "message": "左右区域已全部熄灭"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def create_controller() -> LedController:
    """按配置创建并初始化控制器"""
    if SIMULATE:
        print("[初始化] 使用模拟显示屏（无硬件）")
        display = SimulatedDisplay()
    else:
        print("[初始化] 正在连接 MSU2 显示屏...")
        display = MSU2LiteDisplay.auto_connect()
    ctrl = LedController(display)
    ctrl.initialize()
    return ctrl


def main():
    global controller
    print("=" * 60)
    print("LED 显示屏 HTTP 服务")
    print("=" * 60)
    print(f"[初始化] 模式: {'模拟' if SIMULATE else '真实串口'}")
    try:
        controller = create_controller()
        print("[OK] [初始化] 显示屏就绪\n")

        print("=" * 60)
        print("对外 API:")
        print("  GET /led/left/green/on    左绿亮")
        print("  GET /led/left/red/on      左红亮")
        print("  GET /led/right/red/on     右红亮")
        print("  GET /led/right/green/on   右绿亮")
        print("  GET /led/all/off          左右全部熄灭")
        print("  GET /status               查询状态")
        print("  GET /health               健康检查")
        print("=" * 60)
        print(f"[服务器] 监听地址: http://{HOST}:{PORT}")
        print("[服务器] 按 Ctrl+C 停止\n")
        print("=" * 60 + "\n")

        app.run(host=HOST, port=PORT, debug=False, threaded=True)
    except Exception as e:
        print(f"[ERR] [错误] 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        print("\n[关闭] 正在清理资源...")
        if controller is not None:
            controller.close()
            print("[OK] [关闭] 资源清理完成")


if __name__ == "__main__":
    main()
