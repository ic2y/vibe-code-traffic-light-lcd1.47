"""
LED 显示屏 HTTP 服务 测试脚本

对 http_server.py 暴露的四个 API 进行各种情况测试：
    左绿亮 / 左红亮 / 右红亮 / 右绿亮
以及辅助接口 /led/all/off、/status、/health。

测试核心规则：
    - 同一区域内红绿互斥：红灯亮则绿灯灭，绿灯亮则红灯灭
    - 左右两个区域相互独立

远程测试：只需修改下面的 HOST 变量（例如改为服务器 IP "192.168.1.100"），
然后运行 python test_led.py。
"""
import sys
import time

import requests

# ===== 可配置项 =====
HOST = "127.0.0.1"      # 远程测试时改为服务器地址，如 "192.168.1.100"
PORT = 15000
TIMEOUT = 5
PAUSE = 1.0             # 每次操作后的暂停秒数，便于肉眼观察灯效
# ====================

BASE_URL = f"http://{HOST}:{PORT}"


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def record(self, name, ok, detail=""):
        if ok:
            self.passed += 1
            print(f"  [PASS] {name}")
        else:
            self.failed += 1
            self.failures.append((name, detail))
            print(f"  [FAIL] {name}  {detail}")


def req(path):
    """请求接口并返回响应对象"""
    return requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT)


def op(path):
    """执行灯控操作请求，并暂停 1 秒供肉眼观察灯效"""
    resp = req(path)
    time.sleep(PAUSE)
    return resp


def assert_on(resp, side, color):
    """校验响应状态码、success 标志，以及 side 侧 color 灯为亮"""
    ok = resp.status_code == 200 and resp.json().get("success") is True
    data = resp.json()
    if not ok:
        return False, f"status={resp.status_code} body={data}"
    side_state = data.get(side, {})
    if not side_state.get(color) is True:
        return False, f"期望 {side}.{color}=True，实际 {data}"
    return True, ""


def assert_off(resp, side, color):
    """校验 side 侧 color 灯为灭（红绿互斥的另一色）"""
    data = resp.json()
    side_state = data.get(side, {})
    if side_state.get(color) is not False:
        return False, f"期望 {side}.{color}=False（互斥），实际 {data}"
    return True, ""


def check_status_side(side, expected):
    """通过 /status 校验某一侧的状态"""
    resp = req("/status")
    if resp.status_code != 200:
        return False, f"status_code={resp.status_code}"
    data = resp.json()
    actual = data.get(side, {})
    for color, val in expected.items():
        if actual.get(color) is not val:
            return False, f"{side}.{color} 期望 {val}，实际 {actual}"
    return True, ""


def main():
    print("=" * 60)
    print(f"LED HTTP 服务测试  -> {BASE_URL}")
    print("=" * 60)

    # 0. 前置检查：服务器是否可达
    try:
        health = req("/health")
        if health.status_code != 200:
            print(f"[ERROR] 服务器不可达或未就绪: {health.status_code} {health.text}")
            sys.exit(1)
        print(f"[INFO] 健康检查 OK: {health.json()}\n")
    except requests.RequestException as e:
        print(f"[ERROR] 无法连接服务器 {BASE_URL}: {e}")
        print("        请先启动 http_server.py，并检查 HOST/PORT 配置。")
        sys.exit(1)

    result = TestResult()

    # 1. 初始状态：全部熄灭
    print("[用例] 初始状态")
    resp = op("/led/all/off")
    result.record("led/all/off 初始调用", resp.status_code == 200 and resp.json().get("success") is True,
                  f"status={resp.status_code} body={resp.text}")
    for side in ("left", "right"):
        ok, detail = check_status_side(side, {"red": False, "green": False})
        result.record(f"{side} 初始双灯熄灭", ok, detail)
    print()

    # 2. 单灯点亮：4 个灯逐一单独点亮，每次只有目标灯亮，其余 3 个全灭
    print("[用例] 单灯点亮")
    for region, color, label in (
        ("left", "red", "左红"),
        ("left", "green", "左绿"),
        ("right", "red", "右红"),
        ("right", "green", "右绿"),
    ):
        # 从全灭开始
        op("/led/all/off")
        resp = op(f"/led/{region}/{color}/on")
        ok, detail = assert_on(resp, region, color)
        result.record(f"单灯 {label}亮 -> 响应正确", ok, detail)
        # 其余 3 个灯应全部熄灭
        all_off = True
        detail = ""
        for r in ("left", "right"):
            for col in ("red", "green"):
                if (r, col) == (region, color):
                    continue
                ok2, d2 = check_status_side(r, {col: False})
                if not ok2:
                    all_off = False
                    detail = f"{r}.{col} 应为灭: {d2}"
                    break
        result.record(f"单灯 {label}亮 -> 其余灯全灭", all_off, detail)
        # 目标灯确认为亮
        ok, detail = check_status_side(region, {color: True})
        result.record(f"单灯 {label}亮 -> 目标灯确认亮", ok, detail)
    op("/led/all/off")
    print()

    # 3. 左绿亮：左绿亮、左红灭，右区不受影响（仍全灭）
    print("[用例] 左绿亮")
    resp = op("/led/left/green/on")
    ok, detail = assert_on(resp, "left", "green")
    result.record("左绿亮 -> 响应正确", ok, detail)
    ok, detail = assert_off(resp, "left", "red")
    result.record("左绿亮 -> 左红灭（互斥）", ok, detail)
    ok, detail = check_status_side("right", {"red": False, "green": False})
    result.record("左绿亮 -> 右区不受影响", ok, detail)
    ok, detail = check_status_side("left", {"red": False, "green": True})
    result.record("左绿亮 -> /status 状态一致", ok, detail)
    print()

    # 3. 左红亮：左红亮、左绿灭（覆盖左绿）
    print("[用例] 左红亮")
    resp = op("/led/left/red/on")
    ok, detail = assert_on(resp, "left", "red")
    result.record("左红亮 -> 响应正确", ok, detail)
    ok, detail = assert_off(resp, "left", "green")
    result.record("左红亮 -> 左绿灭（互斥）", ok, detail)
    ok, detail = check_status_side("left", {"red": True, "green": False})
    result.record("左红亮 -> /status 状态一致", ok, detail)
    print()

    # 4. 右红亮：右红亮、右绿灭，左区保持左红不变
    print("[用例] 右红亮")
    resp = op("/led/right/red/on")
    ok, detail = assert_on(resp, "right", "red")
    result.record("右红亮 -> 响应正确", ok, detail)
    ok, detail = assert_off(resp, "right", "green")
    result.record("右红亮 -> 右绿灭（互斥）", ok, detail)
    ok, detail = check_status_side("left", {"red": True, "green": False})
    result.record("右红亮 -> 左区保持左红", ok, detail)
    print()

    # 5. 右绿亮：右绿亮、右红灭（覆盖右红）
    print("[用例] 右绿亮")
    resp = op("/led/right/green/on")
    ok, detail = assert_on(resp, "right", "green")
    result.record("右绿亮 -> 响应正确", ok, detail)
    ok, detail = assert_off(resp, "right", "red")
    result.record("右绿亮 -> 右红灭（互斥）", ok, detail)
    ok, detail = check_status_side("right", {"red": False, "green": True})
    result.record("右绿亮 -> /status 状态一致", ok, detail)
    print()

    # 6. 左右同时红：左红亮、右红亮
    print("[用例] 左右同时红")
    op("/led/left/red/on")
    resp = op("/led/right/red/on")
    ok, detail = assert_on(resp, "right", "red")
    result.record("左右同时红 -> 响应正确", ok, detail)
    ok, detail = check_status_side("left", {"red": True, "green": False})
    result.record("左右同时红 -> 左红保持", ok, detail)
    ok, detail = check_status_side("right", {"red": True, "green": False})
    result.record("左右同时红 -> 右红保持", ok, detail)
    print()

    # 7. 左右同时绿：左绿亮、右绿亮（红灯均灭）
    print("[用例] 左右同时绿")
    op("/led/left/green/on")
    resp = op("/led/right/green/on")
    ok, detail = assert_on(resp, "right", "green")
    result.record("左右同时绿 -> 响应正确", ok, detail)
    ok, detail = check_status_side("left", {"red": False, "green": True})
    result.record("左右同时绿 -> 左绿保持", ok, detail)
    ok, detail = check_status_side("right", {"red": False, "green": True})
    result.record("左右同时绿 -> 右绿保持", ok, detail)
    print()

    # 8. 交叉互斥（关键用例）：
    #    左绿 + 右红 -> 左红 应只灭左绿，右区保持右红不变
    print("[用例] 交叉互斥")
    op("/led/left/green/on")
    op("/led/right/red/on")
    op("/led/left/red/on")
    ok, detail = check_status_side("left", {"red": True, "green": False})
    result.record("交叉互斥 -> 左区切红，左绿灭", ok, detail)
    ok, detail = check_status_side("right", {"red": True, "green": False})
    result.record("交叉互斥 -> 右区保持右红不变", ok, detail)
    print()

    # 9. 重复点亮同一灯：幂等，状态不变
    print("[用例] 重复点亮（幂等）")
    op("/led/left/red/on")
    resp = op("/led/left/red/on")
    ok, detail = assert_on(resp, "left", "red")
    result.record("重复左红亮 -> 状态不变", ok, detail)
    ok, detail = check_status_side("left", {"red": True, "green": False})
    result.record("重复左红亮 -> /status 一致", ok, detail)
    print()

    # 10. all/off：全部熄灭
    print("[用例] all/off 全部熄灭")
    op("/led/left/red/on")
    op("/led/right/green/on")
    resp = op("/led/all/off")
    result.record("all/off 调用成功", resp.status_code == 200 and resp.json().get("success") is True,
                  f"status={resp.status_code} body={resp.text}")
    for side in ("left", "right"):
        ok, detail = check_status_side(side, {"red": False, "green": False})
        result.record(f"all/off -> {side} 双灯熄灭", ok, detail)
    print()

    # 11. 完整状态序列：红绿互斥 + 左右独立同时成立
    print("[用例] 完整状态序列")
    sequence = [
        ("/led/left/green/on", "left", {"red": False, "green": True}),
        ("/led/left/red/on", "left", {"red": True, "green": False}),
        ("/led/right/green/on", "right", {"red": False, "green": True}),
        ("/led/right/red/on", "right", {"red": True, "green": False}),
        ("/led/right/green/on", "right", {"red": False, "green": True}),
        ("/led/left/green/on", "left", {"red": False, "green": True}),
    ]
    for path, side, expected in sequence:
        op(path)
        ok, detail = check_status_side(side, expected)
        result.record(f"序列 {path} -> {side} 状态正确", ok, detail)
    # 序列结束时：左绿 + 右绿
    ok, detail = check_status_side("left", {"red": False, "green": True})
    result.record("序列结束 -> 左绿", ok, detail)
    ok, detail = check_status_side("right", {"red": False, "green": True})
    result.record("序列结束 -> 右绿", ok, detail)
    print()

    # 12. 非法参数（服务器应返回 500/400，而不是崩溃）
    print("[用例] 非法参数")
    for path in ("/led/left/blue/on", "/led/middle/red/on", "/led/left/red/off"):
        try:
            resp = req(path)
            result.record(f"非法路径 {path} -> 返回 {resp.status_code}",
                          resp.status_code in (400, 404, 500), f"body={resp.text}")
        except requests.RequestException as e:
            result.record(f"非法路径 {path} -> 请求异常", False, str(e))
    # 非法请求后服务仍应正常工作
    resp = op("/led/left/red/on")
    ok, detail = assert_on(resp, "left", "red")
    result.record("非法请求后服务仍正常", ok, detail)
    print()

    # ===== 汇总 =====
    print("=" * 60)
    print(f"测试结果: 通过 {result.passed}  失败 {result.failed}")
    if result.failures:
        print("失败明细:")
        for name, detail in result.failures:
            print(f"  - {name}: {detail}")
    else:
        print("所有用例全部通过 ✓")
    print("=" * 60)
    sys.exit(1 if result.failed else 0)


if __name__ == "__main__":
    main()
