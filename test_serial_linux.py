#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CH32x035（MSU2 LITE LED 屏）串口通信测试脚本
============================================

硬编码与 /dev/ttyACM0（CH32x035 主控）的串口通信，测试 LED 屏通信是否正常，
无需启动 HTTP 服务。所有步骤尽量打印详细错误信息，便于排查。

通信验证通过完整握手实现（双向）：
    等设备上报 MSN 版本 -> 回复 MSNCN -> 等设备确认

用法：
    python3 test_serial_linux.py         # 直接测试 /dev/ttyACM0
    python3 test_serial_linux.py --list  # 列出全部串口（排查设备节点变化）

权限：
    若提示 Permission denied，请把当前用户加入 dialout 组后重新登录：
        sudo usermod -a -G dialout $USER
    或临时用 sudo 运行。

依赖：pip install pyserial
"""
import argparse
import errno
import os
import re
import sys
import time
import traceback

import serial
import serial.tools.list_ports

# ============ 硬编码配置 ============
PORT = "/dev/ttyACM0"   # CH32x035 固定串口节点
BAUD = 921600
SERIAL_TIMEOUT = 0.5        # 串口读取超时（秒）
HANDSHAKE_TIMEOUT = 3.0     # 握手每步等待超时（秒）


def hexdump(data: bytes, limit: int = 128) -> str:
    """字节数组转十六进制，供排查打印"""
    if not data:
        return "(空)"
    show = data[:limit]
    out = " ".join(f"{b:02X}" for b in show)
    if len(data) > limit:
        out += f" ... (共 {len(data)} 字节)"
    return out


def rx(ser, n):
    """读 n 字节，每次收到数据都打印原始字节"""
    data = ser.read(n)
    if data:
        print(f"  [RX] {ser.port} <- {len(data)} 字节: {hexdump(data)}")
    return data


def open_port():
    """打开 /dev/ttyACM0，失败时打印尽可能详细的错误"""
    for rtscts in (True, False):
        try:
            ser = serial.Serial(port=PORT, baudrate=BAUD, timeout=SERIAL_TIMEOUT,
                                xonxoff=False, rtscts=rtscts)
            print(f"  [手] 串口打开成功: {ser.port}  (rtscts={rtscts})")
            print(f"      参数: baudrate={ser.baudrate}, timeout={ser.timeout}, "
                  f"bytesize={ser.bytesize}, parity={ser.parity}, stopbits={ser.stopbits}")
            return ser
        except Exception as e:
            e_errno = getattr(e, "errno", None)
            print(f"  [手] rtscts={rtscts} 打开失败:")
            print(f"      异常类型: {type(e).__name__}")
            print(f"      错误信息: {e}")
            print(f"      errno:   {e_errno}"
                  + (f" ({errno.errorcode.get(e_errno, '?')})" if e_errno else ""))
            if e_errno == errno.EACCES or "Permission" in str(e):
                print("      权限不足（Permission denied）！请执行:")
                print("        sudo usermod -a -G dialout $USER   # 然后重新登录")
                print("        或临时用 sudo 运行本脚本")
            elif e_errno == errno.EBUSY:
                print("      设备被占用（EBUSY）！可能是 http_server.py 等程序正在使用该串口")
                print("      请先停止占用串口的程序再测试")
            elif e_errno == errno.ENOENT or "No such file" in str(e):
                print(f"      设备节点不存在: {PORT}")
                print("      请用 dmesg | grep -i tty 查看实际识别到的节点，或用 --list 列出所有串口")
    print(f"  [手] 最终结果: 无法打开 {PORT}")
    return None


def _wait_for(ser, pred, timeout):
    """累计读取直到满足条件或超时；每次收到数据都打印"""
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting:
            chunk = rx(ser, ser.in_waiting)
        else:
            chunk = rx(ser, 1)  # 最多阻塞 SERIAL_TIMEOUT 秒
        if not chunk:
            continue
        buf += chunk
        if pred(buf):
            break
    return buf


def handshake(ser):
    """完整握手：等设备上报 \\x00MSNxx -> 回复 \\x00MSNCN -> 等设备确认
    返回 (ok, detail)"""
    # 1. 等待设备上报 MSN 版本号
    print(f"  [手] 步骤1: 等待设备上报 \\x00MSNxx (最长 {HANDSHAKE_TIMEOUT}s)...")
    buf = _wait_for(ser, lambda b: re.search(rb"\x00MSN\d\d", b), HANDSHAKE_TIMEOUT)
    m = re.search(rb"\x00MSN(\d\d)", buf)
    if not m:
        return False, (f"步骤1失败: 超时未收到设备上报 MSN。"
                       f"累计收到 {len(buf)} 字节: {hexdump(buf)}")
    print(f"  [手] 步骤1完成: 设备版本 MSN{m.group(1).decode()}")

    # 2. 回复确认
    ack = b"\x00MSNCN"
    print(f"  [手] 步骤2: 发送确认 {hexdump(ack)}")
    try:
        ser.write(ack)
        ser.flush()
    except Exception as e:
        return False, f"步骤2失败: 发送 {hexdump(ack)} 出错: {type(e).__name__}: {e}"

    # 3. 等待设备确认
    print(f"  [手] 步骤3: 等待设备确认 \\x00MSNCN (最长 {HANDSHAKE_TIMEOUT}s)...")
    buf2 = _wait_for(ser, lambda b: b"\x00MSNCN" in b, HANDSHAKE_TIMEOUT)
    if b"\x00MSNCN" in buf2:
        print("  [手] 步骤3完成: 收到设备确认")
        return True, ""
    return False, (f"步骤3失败: 未收到设备确认。累计收到 {len(buf2)} 字节: {hexdump(buf2)}")


def list_ports():
    """列出全部串口，附 USB 信息，用于排查设备节点变化"""
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("未检测到任何串口设备")
        return
    print("检测到以下串口:")
    for p in ports:
        print(f"  {p.name:<16} {p.description}")
        for attr in ("usb_info", "usb_description"):
            try:
                val = getattr(p, attr)()
                if val:
                    print(f"    {attr}: {val}")
            except Exception:
                pass


def parse_args():
    p = argparse.ArgumentParser(description="CH32x035 (MSU2 LITE LED 屏) 串口通信测试")
    p.add_argument("--list", action="store_true", help="列出全部串口设备")
    return p.parse_args()


def main():
    args = parse_args()

    if args.list:
        list_ports()
        return 0

    print("=" * 60)
    print("CH32x035 (MSU2 LITE LED 屏) 串口通信测试")
    print(f"目标: {PORT} @ {BAUD} baud")
    print("=" * 60)

    # 设备节点存在性检查
    if not os.path.exists(PORT):
        print(f"[ERR] 设备节点不存在: {PORT}")
        print("      可能原因: 设备未连接 / 已拔出 / 节点名变化")
        print("      排查: dmesg | grep -i tty  或 运行本脚本 --list")
        list_ports()
        return 1

    ser = open_port()
    if ser is None:
        print("\n[FAIL] 无法打开串口，通信测试未开始")
        return 1

    try:
        ok, detail = handshake(ser)
    except Exception as e:
        print(f"\n[ERR] 握手过程抛出未捕获异常: {type(e).__name__}: {e}")
        print("完整堆栈:")
        traceback.print_exc()
        ser.close()
        return 1

    if not ok:
        print(f"\n[FAIL] 通信异常: {detail}")
        ser.close()
        return 1

    print(f"\n[OK] 通信正常: {ser.port} @ {BAUD} baud (CH32x035)")
    ser.close()
    print("[OK] 串口已关闭")
    print("通信测试通过: 完整握手成功，LED 屏通信正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
