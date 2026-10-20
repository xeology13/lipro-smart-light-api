# 参与贡献

感谢你考虑为这个项目做贡献。

## 提交 Issue 前

请先搜一下有没有同类 Issue。提新 Issue 时：

- **不要贴出你的手机号、密码、token、serial、deviceId、灯名或房间名。**
  诊断命令（`lipro_cred.sh status`）只输出长度而非明文，但你手工粘贴的
  终端日志可能包含这些 —— 贴之前请检查一遍。
- 描述问题时附上：操作系统、Python 版本、复现步骤、实际输出、期望结果。

## 提交 Pull Request

1. Fork 后从 `main` 切分支。
2. 改动保持最小，不要顺手重构无关代码。
3. 自查清单：
   - `bash -n scripts/*.sh` 通过
   - `python3 -m py_compile scripts/*.py` 通过
   - 已实跑验证过（写操作请加 `--apply`，并说明你怎么确认生效）
   - **没有引入任何真实凭据或设备识别信息**（serial / deviceId / 灯名 / 房间 / 局域网 IP）
4. PR 描述里说明：改了什么、为什么、怎么验证的。

## 代码来源声明

本仓库是**独立重写**的 LIPRO 云端 API 客户端。接口路径、字段语义与部分协议
常量参考了 [Exlany/lipro-hass](https://github.com/Exlany/lipro-hass)
（MIT License, Copyright (c) 2025 Exlany），遵循其 MIT 许可条款。

若你的 PR 包含来自该项目的代码，请：
- 在 PR 描述中**明确说明**来源与许可依据；
- 保留上游的版权与许可声明；
- 不要提交**官方 App 安装包的代码片段或反编译产物**。

## 许可

你的贡献将以 MIT License 授权，与本仓库现有许可一致（见 [LICENSE](LICENSE)）。
