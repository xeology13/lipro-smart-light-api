---
name: lipro-smart-light-api
description: Control LIPRO smart lights via reverse-engineered cloud API.
version: 2.0.0
author: xeology13
last_verified: 2026-10（真凭据实测跑通鉴权 + 状态查询；本文「实测」标记均为该次验证）
license: MIT
metadata:
  tags: [smart-home, lipro, ble-mesh, reverse-engineering]
---

# LIPRO 智能灯逆向云 API

## When to Use

触发条件：
- 需要**程序化控制** LIPRO / 类太阳光 智能灯（开关、亮度、色温、定时）
- 问 LIPRO 灯**能不能接入** HomeKit / 米家 / Home Assistant / Matter
- **局域网里认不出灯**（灯是 BLE Mesh 节点，不开 TCP 端口，mDNS 不广播）
- 要写**不依赖 Home Assistant** 的独立控制脚本（免装 HA）

不适用：Hue / Aqara / Yeelight 等其他品牌（用对应厂商的官方集成）。

## 免 HA 的客户端

`scripts/lipro_api.py` —— 纯 stdlib，不依赖 Home Assistant，提供登录 / 设备查询 /
分组状态读取，以及 token 刷新与请求签发能力。**控制命令**由 `scripts/lipro_ctl.py`
构造并下发（见下方「控制命令」）。

> ⚠️ **「签名算法已实测通过」这句话在 v1.0 是误导性的** —— 那只验证了 MD5 公式自洽
> （与源码常量独立手算一致），**从未验证过真实 HTTP 层能通**。v2.0 才真跑通（见下方「四个实测坑」）。
> 教训：纯函数算得对 ≠ 端到端能用。写「实测通过」前必须真发一次请求。

```bash
cd <本仓库目录>
export LIPRO_PHONE=手机号
read -rs LIPRO_PASSWORD && export LIPRO_PASSWORD   # 静默输入，不回显不进 history

# 推荐：直接调用函数，token 只走内存，不落盘不进环境变量
python3 - <<'EOF'
import importlib.util
spec = importlib.util.spec_from_file_location("api", "scripts/lipro_api.py")
api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
tok = api.cmd_login()                 # 返回 token 值（v1.0 的 CLI 丢弃了这个返回值）
api.cmd_group_state(tok, api.cmd_group_serials(tok))   # 分组状态
EOF
```

**凭据铁律**：只走环境变量，永不落盘、永不进 argv、永不进 shell history。
登录本身无副作用但会记账号登录日志；云端有限流，失败后等几分钟再试。

**走直连，不要走代理**：出口 IP 必须与 App 登录地一致（本机直连=广州移动 IPv6）。
走代理出口是境外段（45.131.71.227），可能触发异地登录风控。
`export -n HTTP_PROXY HTTPS_PROXY ALL_PROXY` 或在脚本里 `unset`。

## 写控制的三大约束（扩控制时必读，（2026-10））

这三条比「控制逻辑本身」更值钱，且是**验收项**，不是附属细节。

### 1. 写操作不幂等，禁止盲目重试

`power` / `brightness` 是**状态跃迁**。场景：退避重试时第一次其实成功了但响应丢了 → 重发就是**二次确认** → 灯切回上一状态。

→ **必须先 `state` 读回来自证，再决定要不要补发。** 不能拿到超时/5xx 就无脑重发。
→ 幂等只对「设定为某状态」成立（`power=on` 重发无害），**对「切换」不成立**（toggle、调光增量、重试补发）。

### 2. 读操作 ≠ 天然安全

每次 `devices` / `state` 都要**重新签名**（`nonce` 毫秒时间戳 + token + body），签名本身可能就在被风控的计数里。
所以**退避对读操作同样有价值**，不能因为「无副作用」就不做退避。

> ⚠️ 本节说的「读操作」仅指 `devices` / `state` 这两个具体调用 ——
> 本项目**同时具备控制能力**（见上方「免 HA 的客户端」与「控制命令」两节）。

### 3. 限流要可观测

被限流时要能区分「被限流了」和「卡住了」—— 输出层要能看出限流发生，而不是只看到一个挂住的脚本。

**验收项**：①指数退避+抖动+上限 ②写操作幂等策略（读回自证而非直接重发）③限流触发时的可观测输出

## 官方无开放平台

`Exlany/lipro-hass` (MIT) 是**目前唯一**能程序化控制 LIPRO 灯的开源方案。**官方没有开放平台**——lipro 官网只有《第三方信息共享清单》，仅列天猫精灵 IoT 一个对接方。

## 核心事实（源码实读，非文档）

**云端双域名**
```
Smart Home : https://api-hilbert.lipro.com
IoT(控制)  : https://api-mlink.lipro.com
固件清单    : https://lipro-share.lany.me
```

**签名常量**（逆向自官方 App；上游 Exlany/lipro-hass 公开了同值常量，MIT）
```python
SMART_HOME_SIGN_KEY = "*Hilbert$@q9g"
IOT_SIGN_KEY        = "19ff9eb20f818bc45ab216d0d67f"
MERCHANT_CODE       = "LP0002"
MQTT_AES_KEY        = "fprd#huy1n!&d8d6"   # 解密 MQTT 凭据
```

**签名算法**（`core/api/transport_signing.py`，两套都是 MD5）
```python
# Smart Home
sign = md5(f"{phone_id}{SMART_HOME_SIGN_KEY}")
# IoT ← 控灯走这条
sign = md5(f"{access_token}{nonce_ms}{MERCHANT_CODE}{body.strip()}{IOT_SIGN_KEY}")
```
`nonce` = 毫秒时间戳。body 签名前**必须 strip()**。

**请求头**：`i-access-token` / `merchant-code` / `nonce` / `sign` / `User-Agent: okhttp/4.12.0`（伪装安卓 App）

**Content-Type ⚠️ 两个面不同，别搞反**（实测 2026-10-18）
```
Smart Home 面 (api-hilbert)  application/x-www-form-urlencoded   ← 表单编码
IoT 面       (api-mlink)    application/json                    ← 签名覆盖 JSON body 原文
```
源码依据：上游 `request_gateway.py` 硬编码 `CONTENT_TYPE_FORM = "application/x-www-form-urlencoded"`。

> **踩坑实录**：v1.0 脚本两个面都发 `application/json`，登录直接 **HTTP 404**。
> 更坑的是 —— 网关把「body 编码不对」误判成「签名不对」，返回
> `HTTP 404 + {code:"0000", errorCode:"API_SIGN_ERROR"}`。
> **HTTP 状态码与语义完全脱节，404 看起来像「路径写错」，实际是 Content-Type 写错。**
> 对照实验：同签名同路径，JSON → 404/API_SIGN_ERROR；form → 200/业务错误码。

**成功码有三种形态，必须都认**（实测 2026-10-18）
```
数字 0      Smart Home 面（上游 const/api.py: RESPONSE_SUCCESS_CODES = frozenset((0, 200))）
字符串 "0000"  IoT 面实测返回 ← 字符串！与数字 0 比较恒不等
缺省 None
```
> **踩坑实录**：v1.0 写 `if code not in (0, 200)`，导致 IoT 面**所有成功响应**
> 被判成 `API 错误 code=0000 msg=success`。**错误信息自己写着 success**，却当错误抛。
> 解法：`int(str(code).strip())` 归一化后再比。

**响应数据键也不同**
```
Smart Home 面 → value / typedValue
IoT 面       → data
```

**端点**
```
登录      /v1/user/token/login-by-password.do
刷新token  /v1/user/token/refresh.do
设备列表   /v1/user/index.do
产品配置   /v1/iot/product/index/list.do
控制      /app/oauth/api/v1/user/control/device/command.do
分组控制   /app/oauth/api/v2/user/control/device/group/command.do
状态      /app/oauth/api/v1/user/query/devices/state.do
分组状态   /app/oauth/api/v2/user/query/devices/group/state.do
在线状态   /app/oauth/api/v1/user/query/devices/connect-state.do
功率      /app/oauth/api/v1/user/query/device/power-info.do
命令结果   /app/oauth/api/v1/user/control/device/query-command-result.do
BLE 网关   /app/oauth/api/v1/ble/list.do
MQTT配置   /app/oauth/api/v2/user/control/device/get_aliyun_mqtt_config.do
定时       .../device/schedule/{get,addOrUpdate,delete}.do
BLE定时    .../device/bleSchedule/{get,addOrUpdate,delete}.do
```

**MQTT 实时通道**（阿里云，状态推送）
```
broker: post-cn-li93yvd5304.mqtt.aliyuncs.com:8883
QoS 2, keepalive 60
topic 前缀: Topic_Device_State
凭据不固定，须调 get_aliyun_mqtt_config.do 取回并用 MQTT_AES_KEY 解密
```

## 能控什么（⚠️ v1.0 此节严重错误，v2.0 实测修正）

> **v1.0 原话「只有 power/brightness/color_temp 三个字段」是错的。**
> 那是照抄源码静态 grep 的结论，**没实测过**。真凭据实读 `group/state.do` 返回后修正为：
> `properties`，实际字段远超三个。

**实测字段清单**（来自 `group/state.do` 真实返回，2026-10-18）

```
所有分组共有
  powerState          开关（1=开）
  brightness          亮度 0-100
  temperature         色温 0-100
  fadeState           渐变开关
  gearList            档位表，形如 [{"temperature":50,"brightness":50}, ...]
  switchGearOnReboot  重启后恢复上次档位
  ip / wifi_ssid / wifi_rssi / connectState / version / mac / deviceInfo
  beepSwitch          提示音
  latestSyncTimestamp, saveJson, deviceName, net_type, rcList

fanLight 额外（风扇灯）
  fanMode             风扇模式
  fanGear             风扇档位 ← 实测 8/9 档，不同灯不同值
  fanOnOff / fanOnoff 风扇独立开关 ← 灯亮着风扇也可以单独关
  brightnessDecimal   亮度小数位

light 台灯额外
  seatSwitch / seatTime(300000ms)   坐姿提醒
  ldrAutoSwitch       光感自动
  timerCtrl / schedule 定时
```

**仍然成立的部分**：无 RGB / 无特效。LIPRO 走全光谱护眼，静态 grep `rgb|hs|xy|effect` 零命中确实为真。

**某些型号色温被锁死**
```
gearList: [{"temperature":50,...},{"temperature":50,...},{"temperature":50,...}]
```
三档全是 50 → **该型号不支持调色温**，只能调亮度。写控制时别给它发 temperature。

## 设备型号对照（反编译自 App `res/raw/device_models.txt` v2.24.3）

设备类型是十六进制码：
```
ff000001 LED(灯)      ff000002 窗帘        ff000003 开关面板
ff000004 风扇灯        ff000005 吸顶灯      ff000006 插座
ff000007 浴霸          ff000008 人体传感器  ff000009 台灯
ff00000a 门磁         ff00000b 网关
```

⚠️ **源码自带警告，认型号必须遵守**：
> API 返回的 `type` 字段是 **Mesh 协议分类**，**不是设备功能类型**。实测 `type=6` 是灯带（不是插座）、`type=9` 是风扇灯（不是台灯）。**只有 `physicalModel` 字段可靠**，`type` 仅作最后兜底。

`iotName` → 型号（部分）：
```
20X1 客厅吸顶灯E2 Max    23x1 客厅吸顶灯E2      23x2 客厅吸顶灯E2 Pro
23x3 卧室吸顶灯E2        24x1 智能吸顶灯A1 Pro   24x2 客厅吸顶灯A1 Pro
24x3 智能吸顶灯A1 Air    24x4 客厅吸顶灯E2S Pro
T2P4/T2P5/T2P6 筒灯射灯
```

⚠️ **这张表不全** —— 反编译自 App v2.24.3 的 `device_models.txt`，实测中遇到过
**不在表里**的型号（例如风扇灯、护眼台灯）：
```
21F1  风扇灯   ← 不在表里
22Z1  护眼台灯 ← 不在表里
```
遇到表外型号时，**用 `physicalModel` 判断能力**（light / fanLight），
别因为查不到型号就当不认识。

## 硬限制（README 原列，非推测）

- **只走云，不支持本地控制** —— 断网 = 灯失控
- 设备必须**先在 lipro 智家 App 里配对**
- **网关设备不单独出实体**
- **频繁操作触发云端限流**
- token 过期是**估算值**（源码标注 conservative）：access ~2h(7200s) 提前 300s 刷新；refresh ~30d；连续 401 折半至最低 30min

**错误码**
```
140004 设备离线(源: 反编译 ServerErrorCode)   140013 设备不存在
140014 设备升级中                          140101 无权限
250001 设备忙，稍后重试                      100000 参数非法
401/2001/2002 鉴权失败                     20002/1202 refresh token 过期
```

## 局域网识别实战

**灯大概率扫不出来** —— LIPRO 灯是 **BLE Mesh 节点**：有 IP（WiFi 连路由器）但**不监听任何 TCP 端口**，mDNS 也不广播。实测 5 台魅族 OUI 设备全部 0 开放端口。

判定顺序：
1. **先确认 App 里的设备名/型号** —— 比任何网络嗅探都准，能直接对上上面的对照表
2. 路由器 DHCP 租约表拿 MAC → 查 OUI
3. OUI 用 `https://api.macvendors.com/<MAC大写>` 在线核，**别猜厂商**

**已知 OUI**
```
5c:cd:7c  MEIZU Technology          18:c2:3c  Lumi United(绿米/Aqara)
c0:f5:35  AMPAK Technology           fc:df:00  美的
64:9e:31  Beijing Xiaomi Mobile      00:11:32  Synology
```

⚠️ **本地置位 MAC**（`70:48:f7` `1e:3d:bd` `4e:e9:06` `4e:1b:15` 等）查 OUI 无意义 —— 随机化后无真实厂商，别浪费工夫。

## 踩过的坑

- **别信「在 ARP 不在 DHCP = 智能灯」** —— 华硕 Merlin 的租约表在 `/var/lib/misc/dnsmasq.leases`，**不是** `dhcpd.leases`。查错路径会得出「设备不走 DHCP」的错误结论
- **路由器可能全路径返回 200** —— 未登录时所有路径返回同一个跳转页（华硕 `/Main_Login.asp`），软 200 会让路径探测全假阳性。**必须比对响应体大小**
- **~~`MeizuLight` ≠ LIPRO~~（已推翻，见下方「品牌归属」）** —— 当时因只有 hostname 而不敢认定品牌归属，后经 CNAME 证实同属魅族系
- **`.15` / `.154` 看着可疑但不是灯** —— `.15` 指纹 `WWW-Authenticate: Basic realm="Kodi"` 是媒体服务器；`.154` OUI 是小米
- 该 repo **4 star / 0 fork / 12 open issue**，个人项目，出问题基本没人帮。最新 release v1.0.3，要求 HA ≥ 2026.9.4

## 品牌归属（2026-10-18 定案）

`dig +short api-hilbert.lipro.com` → **CNAME: `mce-gateway.flyme.com`**
`flyme` = 魅族操作系统，`mce` = 魅族云基础设施。

→ **LIPRO 云后端就是魅族的基础设施，两者是同一套技术体系**（尽管 lipro.com 是独立站点、独立品牌）。
所以灯在网络上表现为魅族 OUI / 自报 `MeizuLight` 是**正常现象**，不是巧合。

## 设备清单的读取方式（不含任何真实设备信息）

⚠️ **本仓库不记录任何真实设备清单** —— serial、deviceId、灯名、房间、MAC、IP 全部不写入文档。
每次运行都从云端实时拉取：

```bash
bash scripts/lipro_ctl.sh --list      # 输出即你自己账号下的设备
```

### 读到的字段结构（格式说明，非真实数据）

```
serial          mesh_group_<数字>     ← IoT 面 group/state.do 用这个
deviceId        <数字>                ← Smart Home 面用这个
physicalModel   light | fanLight
type            <整数>                ← Mesh 协议分类，⚠️ 不可靠，见坑 18
iotName         <短码>                ← 同型号之间会重名，不能用来路由
roomName        <字符串>
isGroup         true
```

### ⚠️ ID 空间对照（最容易搞混的一处）

| 字段 | 形态 | 哪个 API 面用 |
|---|---|---|
| `deviceId` | `<数字>` | Smart Home 面（`/app/oauth/api/v2/u/...`） |
| `serial` | `mesh_group_<数字>` | IoT 面（`group/state.do` 的 `groupIdList`） |
| `iotName` | `<短码>` | 请求体必带，但**不是路由依据** |

**传错 ID 空间会得到 `140013 未找到群组，请复位后重新配网`。**
⚠️ **该提示有误导性** —— 真实原因只是 ID 空间不对，与设备硬件无关，
不要照着提示去复位/重配网。源码依据：
```
snapshot_support.py       assembly.group_ids.append(device.iot_device_id)
identifiers.py             _MESH_GROUP_ID_PATTERN = ^mesh_group_\d+$
```

### 每盏灯自己就是 Mesh 网关

云端返回的分组里有 `groupRoot=true` + `hasBindGateway=true`，但
**`gatewayDeviceId` 指向该分组自己的 `deviceId`**，且 `address` 是 3/4/5/7/8（Mesh 地址）。

→ **没有独立的网关设备实体，每盏灯各自是 Mesh 节点。**
→ 这解释了为什么 App 里找不到网关入口。

### 路由：按 `deviceId`，与 `iotName` 无关

⚠️ `iotName` 在**同型号设备之间会重名**（同型号共用一个短码）。
云端按 `deviceId` 定位目标，因此 `iotName` 重名**不影响路由**。
**不要把 `iotName` 当成设备唯一标识去查/去比对。**

---

## 控制命令

```bash
CTL=scripts/lipro_ctl.sh
$CTL --list                                    # 列设备（只读，不写）
$CTL <serial> fanOnoff=1                        # dry-run，只打印请求体
$CTL <serial> fanOnoff=1 --apply                # 真发 + 写后轮询比对
$CTL <serial> brightness=60 temperature=30 --apply   # 多属性可同批
$CTL <serial> --command POWER_OFF --apply       # 整灯关（自动补 powerState=0）
$CTL <serial> --wait 20                         # 写后轮询上限秒（默认 15）
```

### 已实发验证的属性

| 属性 | 普通灯 `light` | 风扇灯 `fanLight` |
|---|---|---|
| `brightness` | ✅ | ✅ |
| `temperature` | ✅ | ✅ |
| `POWER_ON` / `POWER_OFF` | ✅ | ✅ |
| `fanOnoff` | 不适用 | ✅ |
| `fanGear` | 不适用 | ✅（档位值受设备自身 `gearList` 约束） |

⚠️ 上表是**属性层面**的验证结论，不是某台具体设备的结论 ——
每台灯的可用属性请以 `--list` 返回的 `physicalModel` 和实读为准。

### 写操作的三条铁律

1. **默认 dry-run**，不加 `--apply` 绝不下发。
2. **写后必须轮询回读**，不能只信云端返回（见坑 6）。
3. **写窗口内不要发任何其他命令** —— 网关会把并发写入当成新状态
   （见坑 10：发完 `POWER_OFF` 后补一条亮度恢复，灯会被"重新打开"）。

### 关灯态下不能调属性

灯关着时写 `brightness` / `temperature` 会被**网关丢弃**（回读不变）。
必须先 `POWER_ON`，**等设备状态更新**，再分两步调属性 ——
`POWER_ON` 和属性写**同批下发会互相干扰**，也会失败。

---

## ⚠️ 十条坑（2026-10-18 全部踩过；8/10 是无声失败）

### 坑 6（最贵）：云端说成功 ≠ 灯真的动了
```
下发 POWER_OFF → code=0000, pushSuccess=true, 回读 powerState: 1→0  ← 三处都说成功
物理          → 灯还亮着
```
**`pushSuccess: true` 和回读都不是物理真相。** 云端只在 `pushSuccess` 明确为
false 时才判失败 —— 报 true 时不会降级重试。
**写后必须由人眼确认，不能只看回读。**

### 坑 7：推断对 ≠ 结论对（我的思维错误）
我推断「POWER_OFF 可能生效了，但被 7 秒后的亮度命令重新打开」——
这个推断**后来被证实是对的**，但我当时把它当结论说了「POWER_OFF 无效」。
**真因是我自己的恢复命令毁了验证。**
> 规则：说「所以是 X」之前先说「我怀疑是 X，等一个判据」。

### 坑 8：关灯态下属性写会被丢弃
```
灯关着时发 brightness=100 → 回读仍是 3，命令被设备忽略
先 POWER_ON 再 brightness=100 → ✅ 生效
```
**开灯和调属性必须分两步，不能合成一批。** 网关把属性写入视为「开机动作」。

### 坑 9：写后回读要轮询，不能等固定 2 秒
设备状态经云端→Mesh 同步有延迟。实测 2 秒读到旧值；改成每 1.5s 查一次、
最多 `--wait` 秒才可靠。轮询命中次数 3~13 次都属正常。

### 坑 10：写窗口内不要发任何其他命令
验证关灯时，我 7 秒后发 `brightness=100 temperature=100` 去「恢复」，
**把刚关好的灯又打开了**，还据此得出「命令无效」的错误结论。
> 规则：验证写操作时，发完就静置观察，别急着「恢复」。

### 坑 11：`cmd_group_state()` 只 print 且返回 None
它末行是 `print(json.dumps(...))`，拿它的返回值去 `unwrap()` 必崩
（`AttributeError: NoneType`）。要读状态自己调 `iot_request` +
`PATH_GROUP_STATE` 解析。

### 坑 12：`SystemExit(0)` 是成功，别当失败
```python
try:
    tok = a.cmd_refresh(rt)
    sys.exit(0)          # ← 被下面的 except SystemExit 抓走，当成失败
except SystemExit as e:
    ...回退密码登录     # ← 刷新成功了却白跑一次
```
必须看 `e.code in (0, None)` 才算失败。

### 坑 13：函数返回值走 stdout，诊断必须走 stderr
`tok=$(lipro_login_cached)` 会把函数内所有 `echo` 一起抓走。
实测 token 长度变成 **417/562**（应为 68）。所有诊断一律 `>&2`。
python 侧同理：诊断 `print(..., file=sys.stderr)`，`stdout` 只回 `__RT__`/`__TOK__`。

### 坑 14：secret 绝不能作 argv
`python3 - "$api_py" "$rt"` → 凭据作为 argv 出现在 `ps aux`。
用环境变量传：`LIPRO_API_PY="$api_py" LIPRO_RT="$rt" python3 <<'EOF'`。
**注意 bash 里不能写 Python docstring `"""..."""`**，bash 会当命令执行报
`command not found`。

### 坑 15：source 静默要靠 if 包住整个 case
`source` 时若函数定义写在 `return` 之后，永远不会被定义。
正确做法：把 `case "${1:-}" in ... esac` 整块用
`if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then ... fi` 包住。

### 坑 16：补值/校验的插入位置（今天犯了 3 次）
`a.set` 的自动补值必须在 `props = {}` **之前**；
「需要 KEY=VAL」的校验必须在 `--list` 分支**之后**。
两处需求冲突 → 拆成两处代码，别指望一块代码同时满足。

---

## 凭据：只走命令行，不做任何持久化

```bash
export LIPRO_PHONE=<手机号>       # 11 位
export LIPRO_PASSWORD='<密码>'    # 16 位，建议加单引号
bash scripts/lipro_ctl.sh --list
```

**本项目不做任何持久化存储** —— 不写任何文件、不接系统钥匙串、不接第三方密码管理器。
进程退出后凭据即消失。

未设置环境变量时，若当前是交互式终端会自动提示输入（密码 `read -rs` 不回显）；
**非交互环境（agent / CI / ssh）会直接报错退出，不会卡死等输入。**

### 代价：没有 refresh_token 可复用

登录接口会同时返回 `access_token` 和 `refresh_token`。
**本项目不存 refresh_token**，所以每条命令都重新走一次账号密码登录，
而不是用 rt 刷新（实测 rt 刷新约 0.5s、账号密码登录约 1.0s）。

| 方案 | 认证路径 | 耗时 | 代价 |
|---|---|---|---|
| 本项目（不持久化） | 每次账号密码登录 | ~1.0s/条 | 高频调用更易撞风控 |
| 存 rt | rt 刷新 | ~0.5s/条 | 需持久化一个一次性凭据 |

⚠️ rt 是**一次性**的（每次刷新即轮换），要存就必须每次刷新后回写。
**高频调用时建议在 shell 层做缓存**（如 `direnv`），而不是在本项目里存。

刷新端点（参考上游 `auth_service.py`，实测可用）：
```
POST /v1/user/token/refresh.do
body: {"refreshToken": rtk, "model": "HomeAssistant"}
```

### 密码绝不能出现在这些地方

| 位置 | 是否安全 | 原因 |
|---|---|---|
| `export LIPRO_PASSWORD` | ✅ | 只在本进程环境；history 记的是命令本身 |
| `read -rs` 交互输入 | ✅ | 不回显、不进 argv |
| 命令行参数 `--password xxx` | ❌ | `ps aux` 全机可见 |
| 写进项目内 `.env` | ❌ | 会进 git |
| 写进 `~/.zshrc` | ⚠️ | 明文落盘且被所有子进程继承，需确保权限 `600` |

⚠️ 长度校验**不能用 bash `${#var}`** —— 它按字节计（中文 6 字 = 18 字节），
会被误判成「长度不对」。统一走 `python3` 按 Unicode 字符数校验，且走管道不进 argv：
```bash
printf '%s' "$pw" | /usr/bin/python3 -c 'import sys; print(len(sys.stdin.read()))'
```

### 坑 17：bash 变量紧跟全角标点 → `unbound variable`（跨机才暴露）

```bash
LC_CTYPE=C.UTF-8 src=K; echo "$src，"     → $src 被吃掉，set -u 下炸
LC_CTYPE=C.UTF-8 src=K; echo "${src}，"   → 正常
LC_ALL=C            src=K; echo "$src，"   → 正常（非 UTF-8 locale 不吃这个字节）
```
bash 在 UTF-8 locale 下把全角逗号首字节 `0xEF` 当成变量名的一部分 → 去找 `src\xEF`。
**修法一律加花括号 `${var}`**，不依赖调用方 locale。
⚠️ **Air 上 locale 是 `C` 恰好躲过，长期以为「脚本没问题」是错觉 —— mini 部署时才炸。**
⚠️ 排查手法：全量扫 `$var` 后紧跟非 ASCII 的位置，别只修报错那两处。

### 坑 18：脚本路径硬编码 `smart-home/` → 换安装位置静默失败

```bash
# 原写法（Air 上一直能用）
CTL="$HOME/.hermes/skills/smart-home/lipro-smart-light-api/scripts/lipro_ctl.py"
# 改后（装在哪层都能跑）
_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
CTL="$_SELF_DIR/lipro_ctl.py"
```
⚠️ **mini 是扁平结构** `~/.hermes/skills/<name>/`（无分类子目录），Air 是 `skills/smart-home/<name>/`。
部署到新机器后**必须真跑一次**才算数 —— 部署成功、sha256 全对、语法全过，一跑就报错。

### 坑 19：报「长度」时插了值 —— `$t` vs `${#t}`（差一个 `#`）

凭据状态诊断把 **16 位密码明文**打进了 stdout：
```bash
echo "凭据就绪（密码 $t 字符）"    # $t    → 明文
echo "凭据就绪（密码 ${#t} 字符）" # ${#t} → 长度
```
⚠️ **为什么测试发现不了**：`$t` 恰好非空，脚本行为「正常」、不报错、退出码 0，
输出句式「密码 xxx 字符」读起来完全合理 —— **不逐字比对就发现不了**。
stdout 会进 agent 对话上下文（Telegram 场景即进聊天记录）。

**排查手法（当 lint 用）**：
```bash
grep -nE '\$\{?(pw|password|passwd|tok|token|rt|ph|t)\}?' *.sh | grep -E 'echo|printf'
```
逐行判定，**判据是「有没有进诊断文案」，不是「有没有进 stdout」**：
| 情形 | 判定 |
|---|---|
| `printf '%s' "$tok"` 作函数返回值 | ✅ 合法，token 只流经管道 |
| `echo "…密码 $t …"` 意图报长度 | ❌ 必须 `${#t}` |
| 诊断文案里插值任何 secret | ❌ 移到 stderr 也不够，stderr 同样进上下文 |

⚠️ 「函数靠 stdout 返回 secret」（`t=$(get_pw)` 这个模式本身安全），
但**任何把它的结果直接丢进 echo 的写法都会泄** —— 改这类代码时重点看这条路径。

**自检必须带对照组**：只断言「新版无明文」是恒真检查。
跑一次修复前的写法确认断言能抓到（本次已做：对照组含明文、新版不含）。

### 坑 20：装好了 ≠ 能跑（跨机部署必真跑一次）

mini 是扁平结构 `~/.hermes/skills/<name>/`，Air 是 `skills/smart-home/<name>/`。
脚本曾硬编码 `smart-home/` 这一层 —— **部署成功、5 文件 sha256 全对、
`bash -n`/`py_compile` 全过，一跑就报「找不到 lipro_cred.sh」**。
```bash
_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
```
⚠️ 改完一端记得同步另一端，**再逐个核对 sha256** —— 漏同步是核对时才发现的。

## 验证状态

| 能力 | 状态 |
|---|---|
| 登录 / 设备列表 / 分组状态读取 | ✅ 实发验证 |
| `brightness` / `temperature` | ✅ 实发验证（两类灯） |
| `POWER_ON` / `POWER_OFF` | ✅ 实发验证，静置 25s 不回弹 |
| `fanOnoff` / `fanGear` | ✅ 实发验证（fanLight） |
| 同 `iotName` 多设备零串扰 | ✅ 实发验证（`deviceId` 路由） |
| 写后是否每次真生效 | ⚠️ **必须人眼确认**，云端判据不可信（坑 6） |

- ✅ 凭据零落盘：不写任何文件、不接系统钥匙串、不接密码管理器，只认环境变量/交互输入
- ✅ 零落盘、零 Touch ID、零第三方凭据服务调用
- ✅ 脚本零硬编码：设备清单每次实时拉，换账号只需改环境变量

### 换账号

```bash
export LIPRO_PHONE=<新手机号>
export LIPRO_PASSWORD='<新密码>'
```

因为没有任何持久化存储，**换账号只需改环境变量**，
不存在「旧 refresh_token 串到旧账号」这类问题。

### 换机器

脚本零硬编码 —— 设备清单每次从云端实时拉取，
路径从脚本自身位置推导，装在任何目录都能跑。

⚠️ 每台机器都要各自设置环境变量（凭据不进仓库、不跨机共享）。

token 时效（源码估算，非实测）：access ~2h；refresh ~30d。
实测登录返回同时含 `access_token` 与 `refresh_token`（snake_case）。
