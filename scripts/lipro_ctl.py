#!/usr/bin/env python3
"""LIPRO 控制命令 —— 分组下发，带 dry-run 门与写后回读。

端点与请求体全部溯源自上游 Exlany/lipro-hass（2026-10 实读，非文档推测）：

    PATH_SEND_GROUP_COMMAND = "/app/oauth/api/v2/user/control/device/group/command.do"
    body = {
        "command":     "<CMD_*>",
        "deviceId":    "<serial>",     # 注意：分组命令这里传的是 serial，不是 deviceId 数字
        "deviceType":  "<hex>",        # to_device_type_hex(device_type)
        "iotName":     "<iotName>",    # 实测灯为 20X1 / 风扇灯为 21F1
        "properties":  [{"key":..,"value":..}],
        "skuId":       "",
        "hasMacRule":  False,
        "groupId":     "<serial>",
    }
    来源 const/api.py:26 · command_api_service.py:build_command_request_body

命令与属性常量（const/entities.py 实读）：

    CMD_CHANGE_STATE="CHANGE_STATE"  CMD_POWER_ON="POWER_ON"  CMD_POWER_OFF="POWER_OFF"
    PROP_POWER_STATE="powerState"    PROP_BRIGHTNESS="brightness"
    PROP_TEMPERATURE="temperature"   PROP_FAN_ONOFF="fanOnoff"   ← 小写 o
    PROP_FAN_GEAR="fanGear"          PROP_FAN_MODE="fanMode"

⚠️ PROP_FAN_ONOFF 是 "fanOnoff"（小写 o），而分组状态回读里出现的是 "fanOnOff"
    （大写 O）—— 两者是不同字段：写用小写，回读大写在分组层、小写也在子设备层。
    写错大小写云端不报错但不生效，故统一走 PROP_* 常量。

安全设计：
  * 默认 dry-run，只打印将要发的请求体，不发
  * --apply 才真发
  * 写后必回读 state.do 比对目标值，不靠响应自称成功
  * 不重试：写操作不幂等（skill 已记此约束）
  * 凭据只从环境变量读，不落盘、不进 argv
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time

API_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lipro_api.py")

PATH_SEND_GROUP_COMMAND = "/app/oauth/api/v2/user/control/device/group/command.do"

CMD_CHANGE_STATE = "CHANGE_STATE"
CMD_POWER_ON = "POWER_ON"
CMD_POWER_OFF = "POWER_OFF"

P_STATE = "powerState"
P_BRIGHT = "brightness"
P_TEMP = "temperature"
P_FAN_ONOFF = "fanOnoff"
P_FAN_GEAR = "fanGear"
P_FAN_MODE = "fanMode"


def login_cached(api):
    """取 access_token。

    ⚠️ 本项目**不做任何持久化存储**，因此没有 refresh_token 可复用
       （它是一次性的，本来靠存储层轮换）。每次都走账号密码登录。
    """
    return api.cmd_login()


def load_api():
    spec = importlib.util.spec_from_file_location("lipro_api", API_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 本 skill 只支持两类灯 —— 都是 2026-10-18 实发验证过的：
#   普通灯   physicalModel=light    deviceType=ff000001
#   风扇灯   physicalModel=fanLight deviceType=ff000004
# ⚠️ 上游 const/device_types.py 里还有 curtain / panel / heater / sensor / outlet /
#    deskLamp / gateway 等，本项目**刻意不实现** —— 没验证过，不猜。
#    遇到未支持的设备必须报错，不要按 type 兜底猜一个值发出去。
DT_LED        = "ff000001"   # 普通灯
DT_FAN_LIGHT  = "ff000004"   # 风扇灯

SUPPORTED_PHYSICAL = ("light", "fanLight")

# physicalModel → deviceType（权威来源；上游注释明说 physicalModel 是唯一可靠来源）
PHYSICAL_TO_DT = {
    "light": DT_LED,
    "fanLight": DT_FAN_LIGHT,
}

# type（Mesh 协议分类）→ deviceType，仅在 physicalModel 缺失时兜底。
# ⚠️ 上游原注释警告：type 不可靠，例 "type=9: Fan light (NOT desk lamp!)"。
#    type=1 实测对应普通灯；type=9 对应风扇灯 —— 但这两个值在没有 physicalModel
#    时无法区分「风扇灯」和「台灯」，**保留 9 有把台灯误判成风扇灯的风险**。
#    宁可让调用方补 physicalModel，也不猜。
TYPE_FALLBACK = {
    1: DT_LED,        # light（实测：普通灯 type 为 1）
}


def dev_type_hex(device):
    """deviceType 归一化。优先 physicalModel；缺失才退回 type。

    未支持的设备类型直接报错 —— 宁可拒绝，也不猜一个 deviceType 发到云端。
    """
    pm = device.get("physicalModel")
    if pm in PHYSICAL_TO_DT:
        return PHYSICAL_TO_DT[pm]
    dt = device.get("type")
    if isinstance(dt, int) and dt in TYPE_FALLBACK:
        return TYPE_FALLBACK[dt]
    if isinstance(dt, str) and dt.strip().lower() in (DT_LED, DT_FAN_LIGHT):
        return dt.strip().lower()
    raise SystemExit(
        "✗ 不支持的设备类型：physicalModel=%r type=%r\n"
        "  本 skill 只支持 %s（均已实发验证）。\n"
        "  窗帘/面板/传感器/插座/台灯等未实现 —— 需要的话先实发验证再补。"
        % (pm, dt, " / ".join(SUPPORTED_PHYSICAL))
    )


def build_body(device, command, properties):
    return {
        "command": command,
        "deviceId": device["serial"],
        "deviceType": dev_type_hex(device),
        "iotName": device.get("iotName", ""),
        "properties": [{"key": k, "value": str(v)} for k, v in properties.items()],
        "skuId": "",
        "hasMacRule": False,
        "groupId": device["serial"],
    }


PATH_GROUP_STATE = "/app/oauth/api/v2/user/query/devices/group/state.do"


def readback(api, token, serial, keys=None):
    """写后/写前回读分组属性，返回 {key: value}。

    ⚠️ 2026-10-18 修：原来调 api.cmd_group_state()，但那个函数**只 print 且返回 None**
    （lipro_api.py:cmd_group_state 末行是 print(json.dumps(...))），
    于是 unwrap(None) → AttributeError 崩在写前，把「命令还没发」变成一个 traceback。
    现在直接调 iot_request 自己解析，不依赖那个函数的副作用。
    """
    result = api.iot_request(PATH_GROUP_STATE, {"groupIdList": [serial]}, token)
    data = api.unwrap(result)
    for g in (data if isinstance(data, list) else [data]):
        if isinstance(g, dict) and g.get("groupId") == serial:
            props = {p["key"]: p["value"] for p in (g.get("properties") or [])
                     if isinstance(p, dict) and "key" in p}
            # 子设备层也带 fanOnoff（分组层是 fanOnOff，大小写不同），一并合并
            for dev in (g.get("devices") or []):
                for p in (dev.get("properties") or []):
                    if isinstance(p, dict) and "key" in p:
                        props.setdefault(p["key"], p["value"])
            return props
    return {}


def main():
    ap = argparse.ArgumentParser(description="LIPRO 分组控制（默认 dry-run）")
    ap.add_argument("serial", nargs="?", help="分组 serial，形如 mesh_group_<数字>（用 --list 查看）")
    ap.add_argument("set", nargs="*", metavar="KEY=VAL",
                    help="属性对，如 brightness=80 / fanGear=3 / fanOnoff=1")
    ap.add_argument("--command", default=CMD_CHANGE_STATE, choices=[CMD_CHANGE_STATE, CMD_POWER_ON, CMD_POWER_OFF])
    ap.add_argument("--list", action="store_true", help="只列设备与 serial")
    ap.add_argument("--apply", action="store_true", help="真发。缺省只打印请求体")
    ap.add_argument("--no-readback", action="store_true")
    ap.add_argument("--wait", type=int, default=15, help="写后轮询等待秒数（默认 15）")
    a = ap.parse_args()


    # POWER_ON/OFF 自动补 powerState —— 必须在这里（props 组装之前），
    # 放到 --list 之后会太晚：那时 props 已经是空 dict，实测请求体 properties: []。
    # --list 的「必须给 KEY=VAL」校验另在 --list 分支之后做。
    if not a.set and a.command in (CMD_POWER_ON, CMD_POWER_OFF):
        a.set = ["powerState=" + ("1" if a.command == CMD_POWER_ON else "0")]

    props = {}
    for kv in a.set:
        if "=" not in kv:
            sys.exit(f"✗ 参数格式应为 KEY=VAL，收到 {kv!r}")
        k, v = kv.split("=", 1)
        props[k] = v

    api = load_api()
    token = login_cached(api)

    r = api.smart_home_request("/v1/user/index.do", {}, access_token=token)
    devices = api.unwrap(r).get("devices", [])

    if a.list:
        print("%-16s %-12s %-6s %-10s %s" % ("serial", "名称", "房间", "类型", "deviceId"))
        for d in devices:
            print("%-16s %-12s %-6s %-10s %s" % (
                d.get("serial", ""), d.get("deviceName", ""), d.get("roomName", ""),
                d.get("physicalModel", ""), d.get("deviceId", "")))

        return 0

    if not a.set:
        sys.exit("✗ 需要至少一个 KEY=VAL（或用 --command POWER_ON/POWER_OFF）")
    if not a.serial:
        sys.exit("✗ 需要 serial。用 --list 查看。")

    dev = next((d for d in devices if d.get("serial") == a.serial), None)
    if dev is None:
        sys.exit(f"✗ 找不到 serial={a.serial}；现有：{[d['serial'] for d in devices]}")

    body = build_body(dev, a.command, props)

    print("=" * 70)
    print("目标设备 :", dev["deviceName"], "|", dev["roomName"], "|", dev["physicalModel"])
    print("serial   :", dev["serial"], "| deviceId:", dev["deviceId"])
    print("端点     :", PATH_SEND_GROUP_COMMAND)
    print("请求体   :", json.dumps(body, ensure_ascii=False, indent=2))
    print("=" * 70)

    if not a.apply:
        print(">>> DRY-RUN，未发送。加 --apply 真发。")
        return 0

    before = {} if a.no_readback else readback(api, token, a.serial)
    if before:
        print(">>> 写前:", {k: before.get(k) for k in props})

    resp = api.iot_request(PATH_SEND_GROUP_COMMAND, body, access_token=token)
    print(">>> 响应:", json.dumps(resp, ensure_ascii=False)[:400])

    if a.no_readback:
        return 0

    # 写后轮询：设备状态经云端→Mesh 同步有延迟，实测 2s 不够（首轮回读读到旧值，
    # 云端已返回 pushSuccess: true 但物理未变）。
    # 最多等 --wait 秒（默认 15），每 1.5s 查一次，全部命中即返回。
    deadline = time.time() + a.wait
    attempts = 0
    after, bad = {}, []
    while True:
        after = readback(api, token, a.serial)
        attempts += 1
        bad = [(k, want, after.get(k)) for k, want in props.items()
               if after.get(k) != want]
        if not bad:
            break
        if time.time() >= deadline:
            break
        time.sleep(1.5)

    print(">>> 写后:", {k: after.get(k) for k in props},
          f"(轮询 {attempts} 次 / {a.wait}s 上限)")

    if bad:
        print()
        print("✗ 未生效（已等满时限，可能设备离线或命令被拒）:")
        for k, w, g in bad:
            print(f"    {k}: 期望 {w} 实得 {g}")
        print()
        print("  排查线索：该字段可能只在另一层更新 —— 分组层用 fanOnOff（大写 O），")
        print("  子设备层用 fanOnoff（小写 o）。回读会同时取两层，比对前先确认写的是哪个。")
        return 1
    print("✓ 全部生效")
    for k, want in props.items():
        print(f"    {k}: {want}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
