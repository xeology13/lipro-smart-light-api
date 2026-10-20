#!/usr/bin/env python3
"""LIPRO 云 API 只读客户端 —— 反编译自 Exlany/lipro-hass (MIT)。

只做读操作：登录 / 列设备 / 查状态。不含任何写(控制)能力。
凭据从环境变量读，不落盘、不进 argv、不进 shell history。

用法:
    export LIPRO_PHONE=1xxxxxxxxxx
    read -rs LIPRO_PASSWORD && export LIPRO_PASSWORD
    python3 lipro_api.py devices
    python3 lipro_api.py state <deviceId>
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

# ── 常量（逆向自官方 App，见 skill lipro-smart-light-api）──
SMART_HOME_URL = "https://api-hilbert.lipro.com"
SMART_HOME_SIGN_KEY = "*Hilbert$@q9g"
IOT_URL = "https://api-mlink.lipro.com"
IOT_SIGN_KEY = "19ff9eb20f818bc45ab216d0d67f"
MERCHANT_CODE = "LP0002"
APP_VERSION_NAME = "2.24.3"
APP_VERSION_CODE = "20240003"
USER_AGENT = "okhttp/4.12.0"

# phone_id 是客户端自生成的 UUID4，每次会话固定即可
PHONE_ID = os.environ.get("LIPRO_PHONE_ID") or "00000000-0000-4000-8000-000000000000"


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def ts_ms() -> int:
    return int(time.time() * 1000)


def smart_home_sign(phone_id: str) -> str:
    return md5(f"{phone_id}{SMART_HOME_SIGN_KEY}")


def iot_sign(access_token: str, nonce: int, body: str) -> str:
    return md5(f"{access_token}{nonce}{MERCHANT_CODE}{body.strip()}{IOT_SIGN_KEY}")


def _post(url: str, body: str, content_type: str, headers: dict, timeout: int = 30) -> dict:
    """统一出口：body 是已编码好的字符串，content_type 由调用方决定。

    Smart Home 面 = application/x-www-form-urlencoded（源码 request_gateway.py:132
    硬编码 CONTENT_TYPE_FORM）；IoT 面 = application/json，因为它的签名覆盖
    JSON body 原文。发错 Content-Type 会让网关把请求当成签名不匹配，返回
    HTTP 404 + API_SIGN_ERROR —— 状态码与语义完全脱节，别拿 404 当"路径错"。
    """
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", USER_AGENT)
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def smart_home_request(path: str, data: dict, access_token: str | None = None) -> dict:
    """Smart Home 面（api-hilbert）。表单字段进 urlencoded body。

    业务错误码在 JSON 的 code 字段；调用方必须读 body 的 errorCode，不能只看
    HTTP 状态 —— 签名失败会伪装成 404。
    """
    nonce = ts_ms()
    payload = {
        "sign": smart_home_sign(PHONE_ID),
        "optFrom": PHONE_ID,
        "optAt": nonce,
        "vn": APP_VERSION_NAME,
        "vc": APP_VERSION_CODE,
    }
    payload.update(data)
    if access_token is not None:
        payload["accessToken"] = access_token
    return _post(
        SMART_HOME_URL + path,
        urllib.parse.urlencode(payload),
        "application/x-www-form-urlencoded",
        {},
    )


def iot_request(path: str, data: dict, access_token: str) -> dict:
    """IoT 面（api-mlink），签名覆盖 JSON body 原文，故此面用 JSON body。"""
    nonce = ts_ms()
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return _post(
        IOT_URL + path,
        body,
        "application/json",
        {
            "i-access-token": access_token,
            "merchant-code": MERCHANT_CODE,
            "nonce": str(nonce),
            "sign": iot_sign(access_token, nonce, body),
        },
    )


def unwrap(result: dict) -> dict:
    """剥信封: 判定成功码后取业务数据。

    ⚠️ 成功码有三个形态，必须都认：
      - 数字 0 / 200（Smart Home 面，const/api.py RESPONSE_SUCCESS_CODES）
      - 字符串 "0000"（IoT 面实测返回，字符串！数字比较恒不等）
      - 缺省 None
    早先只认数字，导致 IoT 面所有成功响应被判成「API 错误 code=0000 msg=success」。
    另外 IoT 面的数据键是 data（见上游 auth_recovery.py:316），
    Smart Home 面才是 value/typedValue。
    """
    code = result.get("code", result.get("resultCode"))
    code_num = None
    if code is not None:
        try:
            code_num = int(str(code).strip())
        except (TypeError, ValueError):
            code_num = None
    if code_num not in (0, 200, None):
        raise SystemExit(f"API 错误 code={code} msg={result.get('msg') or result.get('message')}")
    for key in ("value", "typedValue", "data"):
        if key in result:
            return result[key] or {}
    return result


# ── 命令 ──
_LAST_REFRESH_TOKEN = ""   # 2026-10：cmd_login 时捕获，cmd_refresh 时用。只在内存，不落盘。


def cmd_login() -> str:
    phone = os.environ.get("LIPRO_PHONE")
    password = os.environ.get("LIPRO_PASSWORD")
    if not phone or not password:
        raise SystemExit("需要环境变量 LIPRO_PHONE / LIPRO_PASSWORD")
    result = smart_home_request(
        "/v1/user/token/login-by-password.do",
        {"phone": phone, "password": md5(password)},
    )
    data = unwrap(result)
    # 云端返回的是 snake_case：access_token / refresh_token。
    # 早先这里只查驼峰 refreshToken，导致 refresh 恒为「无」的误报。
    tok = data.get("access_token") or data.get("accessToken")
    if not tok:
        raise SystemExit(f"登录失败，未拿到 token: {json.dumps(result)[:400]}")
    rtk = data.get("refresh_token") or data.get("refreshToken")
    print(f"✅ 登录成功  userId={data.get('userId')} bizId={data.get('bizId')}", file=sys.stderr)
    print(f"   access_token 长度={len(tok)}  refresh={'有' if rtk else '无'}", file=sys.stderr)
    print(f"   字段: {sorted(data.keys())}", file=sys.stderr)
    # 2026-10：rtk 之前只用于这行 print 就丢了（与早先 export token 同类断链）。
    # 挂到模块全局供 cmd_refresh 用；只存内存，不打印内容。
    globals()["_LAST_REFRESH_TOKEN"] = rtk or ""
    return tok


def cmd_devices(token: str) -> None:
    result = smart_home_request("/v1/user/index.do", {}, access_token=token)
    data = unwrap(result)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])


def cmd_state(token: str, device_ids: list[str]) -> None:
    """单灯状态。body_key 是 deviceIdList（复数），值必须是数组。"""
    result = iot_request(
        "/app/oauth/api/v1/user/query/devices/state.do",
        {"deviceIdList": [str(x) for x in device_ids]},
        token,
    )
    print(json.dumps(unwrap(result), ensure_ascii=False, indent=2)[:4000])


def cmd_group_state(token: str, group_ids: list[str]) -> None:
    """Mesh 分组状态。

    ⚠️ groupIdList 的值是 **serial**（形如 `mesh_group_<数字>`），不是 deviceId
    （形如 `<数字>`）。上游 snapshot_support.py:138 取的是
    device.iot_device_id，即 serial；identifiers.py 校验 ^mesh_group_\\d+$。
    传错会得到 140013「未找到群组，请复位后重新配网」—— 该提示有误导性，
    真实原因是 ID 空间不对，不是设备要重新配网。

    body_key 是 groupIdList（复数），值必须是数组。
    上游用 binary-split fallback：批量查出错就二分重试到单元素，故单次最多可传全部。
    """
    result = iot_request(
        "/app/oauth/api/v2/user/query/devices/group/state.do",
        {"groupIdList": [str(x) for x in group_ids]},
        token,
    )
    print(json.dumps(unwrap(result), ensure_ascii=False, indent=2)[:12000])


def cmd_group_serials(token: str) -> list[str]:
    """取账号下所有 Mesh 分组的 serial，供 cmd_group_state 使用。"""
    result = smart_home_request("/v1/user/index.do", {}, access_token=token)
    data = unwrap(result)
    return [d["serial"] for d in data.get("devices", []) if d.get("isGroup")]


def cmd_connect_state(token: str, device_ids: list[str]) -> None:
    """在线状态。同样要 deviceIdList 数组。

    ⚠️ 此端点要 IoT 设备 ID（<prefix> + 12 位 hex），不是数字 deviceId，
    也不是 mesh 分组 serial。全分组账号下这里很可能返回 {}。
    """
    result = iot_request(
        "/app/oauth/api/v1/user/query/devices/connect-state.do",
        {"deviceIdList": [str(x) for x in device_ids]},
        token,
    )
    print(json.dumps(unwrap(result), ensure_ascii=False, indent=2)[:3000])


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    cmd = sys.argv[1]
    token = os.environ.get("LIPRO_TOKEN")
    if cmd == "login":
        cmd_login()
    elif cmd == "devices":
        if not token:
            raise SystemExit("需要 LIPRO_TOKEN（先跑 login）")
        cmd_devices(token)
    elif cmd == "state":
        if not token or len(sys.argv) < 3:
            raise SystemExit("用法: ... state <deviceId>")
        cmd_state(token, sys.argv[2])
    else:
        raise SystemExit(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()


def cmd_refresh(refresh_token: str | None = None) -> str:
    """用 refresh_token 换新 access_token，返回 access_token。

    端点溯源自上游 Exlany/lipro-hass（auth_service.py:135-151）：
        POST /v1/user/token/refresh.do
        body: {"refreshToken": <rtk>, "model": "HomeAssistant"}
        require_auth=False

    实测：传假 refresh_token → code=1202 AUTH_REFRESH_TOKEN_ERROR（说明端点与格式正确）。
    失败抛 SystemExit，调用方回退账号密码重登。
    """
    rtk = refresh_token or _LAST_REFRESH_TOKEN
    if not rtk:
        raise SystemExit(2)   # code=2 明确表示失败
    result = smart_home_request(
        "/v1/user/token/refresh.do",
        {"refreshToken": rtk, "model": "HomeAssistant"},
    )
    data = unwrap(result)
    tok = data.get("access_token") or data.get("accessToken")
    if not tok:
        raise SystemExit(3)   # code=3 明确表示失败
    new_rtk = data.get("refresh_token") or data.get("refreshToken")
    if new_rtk:
        globals()["_LAST_REFRESH_TOKEN"] = new_rtk
    print(f"\u267b\ufe0f  刷新成功  access_token 长度={len(tok)}  "
          f"新 refresh_token={'有' if new_rtk else '无(沿用旧)'}")
    return tok
