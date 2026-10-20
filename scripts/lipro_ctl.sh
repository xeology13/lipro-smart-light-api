#!/usr/bin/env bash
# LIPRO 控制命令包装 —— 凭据只来自环境变量或交互输入，默认 dry-run
#
# 用法：
#   lipro_ctl.sh <serial> KEY=VAL [KEY=VAL ...]            # dry-run，只打印请求体
#   lipro_ctl.sh <serial> KEY=VAL --apply                 # 真发 + 写后回读
#   lipro_ctl.sh --list                                   # 列出你账号下的设备
#
# 设备清单不在此硬编码 —— 每次从云端实时拉取。参考字段结构见 SKILL.md。
#
# 可控属性（const/entities.py 实读）：
#   灯   powerState brightness temperature fadeState
#   风扇 fanOnoff(小写o!) fanGear fanMode
set -uo pipefail
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

# ---- 路径：从脚本自身位置推导（skill 装在哪一层都能跑）----
_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
CTL="$_SELF_DIR/lipro_ctl.py"


# ---- 凭据：统一走 lipro_cred.sh（环境变量优先，无则交互提示）----
CRED="$_SELF_DIR/lipro_cred.sh"
if [ ! -f "$CRED" ]; then echo "✗ 找不到 $CRED"; exit 1; fi
# shellcheck source=/dev/null
source "$CRED"
if ! lipro_load_creds; then echo "✗ 凭据加载失败"; exit 1; fi
# ----------------------------------------------------------------export LIPRO_PASSWORD LIPRO_PHONE

if [ "${1:-}" = "--list" ]; then
    exec /usr/bin/python3 "$CTL" --list
fi

/usr/bin/python3 "$CTL" "$@"
rc=$?
unset LIPRO_PASSWORD LIPRO_PHONE
exit $rc
