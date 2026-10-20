## 说明

- 本仓库是**独立重写**的 LIPRO 云端 API 客户端。请勿提交来自官方 App
  安装包的代码片段或反编译产物。
- 接口路径、字段语义与协议常量参考了 [Exlany/lipro-hass](https://github.com/Exlany/lipro-hass)
  （MIT License, Copyright (c) 2025 Exlany）。若你的改动包含其内容，
  请在 PR 描述中说明并遵循其 MIT 许可条款。

## 变更类型

- [ ] 文档 / 措辞
- [ ] 缺陷修复
- [ ] 新功能
- [ ] 重构

## 验证

- [ ] `bash -n scripts/*.sh` 通过
- [ ] `python3 -m py_compile scripts/*.py` 通过
- [ ] 已实跑验证（写操作请用 `--apply` 并说明验证方式）
- [ ] 改动不引入任何真实凭据、设备 serial / deviceId、灯名、房间名、局域网 IP
