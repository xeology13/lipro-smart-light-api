#!/usr/bin/env bash
# LIPRO 凭据模块 —— 只认命令行环境变量 / 交互式输入，不做任何持久化
#
# 唯一来源（二选一）：
#   1. 环境变量 LIPRO_PHONE / LIPRO_PASSWORD（推荐，可写进 ~/.zshrc）
#   2. 交互式 read -rs（未设环境变量时自动提示，不回显）
#
# ⚠️ 本模块**不写任何持久化存储** —— 不落盘、不接系统钥匙串、不接第三方密码管理器。
#    进程退出后凭据即消失。
#    代价：refresh_token 也无处存放，每条命令都会重新走一次账号密码登录。
#
# 安全约束：密码只存在于 shell 变量与当前进程的环境里。
#   绝不 echo / print / 进 argv / 写明文文件 / 进 shell history。无 set -x。
set -uo pipefail

EXPECT_PW_LEN=16                        # 实测字段长度（lipro_item_shape.sh）

# 按 Unicode 字符数校验长度（stdin 进，不进 argv）
_len_check() { printf '%s' "$1" | /usr/bin/python3 -c 'import sys; print(len(sys.stdin.read()))'; }

lipro_load_creds() {
    local src="" pw="" ph="" n=0

    if [ -n "${LIPRO_PASSWORD:-}" ]; then
        src="环境变量"
    else
        # 非交互环境（agent / CI / ssh）无法提示，直接报错退出
        if [ ! -t 0 ]; then
            echo "✗ 未设置 LIPRO_PASSWORD，且当前不是交互式终端，无法提示输入。" >&2
            echo "  请先 export：export LIPRO_PHONE=<手机号> LIPRO_PASSWORD=<密码>" >&2
            return 1
        fi
        echo ">>> 未设置环境变量，请手动输入（密码不回显）"
        printf "手机号："; read -r LIPRO_PHONE
        printf "密码（不回显）："; read -rs LIPRO_PASSWORD; echo
        export LIPRO_PHONE
        src="交互输入"
    fi

    # 形状断言：按 Unicode 字符数，不按字节
    n=$(_len_check "$LIPRO_PASSWORD")
    if [ "$n" -ne "$EXPECT_PW_LEN" ]; then
        echo "✗ 凭据来自[$src]，密码长度 $n 字符 ≠ 期望 $EXPECT_PW_LEN —— 拒绝提交" >&2
        echo "  请检查 LIPRO_PASSWORD 是否设错（终端里可能带引号或空格）" >&2
        return 1
    fi
    if [ -z "${LIPRO_PHONE:-}" ]; then echo "✗ 缺手机号（需设置 LIPRO_PHONE）" >&2; return 1; fi

    export LIPRO_PHONE LIPRO_PASSWORD
    echo "✓ 凭据就绪（来源：${src}，密码 $n 字符）" >&2
    return 0
}

# 取得可用的 access_token。每次都走账号密码登录 —— 没有持久化存储就没有 rt 可用。
# 返回值走 stdout（供 $(...) 捕获），所有诊断走 stderr。
# ⚠️ 这里不能用 bash docstring（"""..."""），bash 会把里面当命令执行并报
#    "command not found" —— 2026-10-18 实测踩过。
lipro_login_cached() {
    local api_py
    api_py="$(dirname "${BASH_SOURCE[0]}")/lipro_api.py"
    # ⚠️ 本函数返回值走 stdout（$(...) 捕获）。所有诊断一律走 stderr，
    #    否则诊断文本会被当成 token 一起抓走（2026-10-18 实测踩过：token 长度 417/562）。
    if [ ! -f "$api_py" ]; then echo "✗ 找不到 $api_py" >&2; return 1; fi

    local login_out
    login_out=$(LIPRO_API_PY="$api_py" /usr/bin/python3 <<'PYEOF'
import importlib.util, os, sys
api_py = os.environ["LIPRO_API_PY"]
spec = importlib.util.spec_from_file_location("lipro_api", api_py)
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
tok = a.cmd_login()
print("__TOK__" + tok)
PYEOF
)
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "$login_out" | sed 's/^/    /' >&2
        echo "✗ LIPRO 登录失败" >&2; return 1
    fi
    local tok
    tok=$(printf '%s\n' "$login_out" | grep '^__TOK__' | tail -1 | sed 's/^__TOK__//')
    if [ -z "$tok" ]; then echo "✗ 未取到 access_token" >&2; return 1; fi
    printf '%s' "$tok"
    return 0
}

# 只有「直接执行本脚本」时才走下面的命令分发；
# 被 source 时（$0 != BASH_SOURCE[0]）整块跳过，只留函数定义。
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    case "${1:-}" in
    status)
        if [ -n "${LIPRO_PASSWORD:-}" ]; then echo "  LIPRO_PASSWORD : 已设置，长度 ${#LIPRO_PASSWORD} 字节"
        else echo "  LIPRO_PASSWORD : <未设置>"; fi
        if [ -n "${LIPRO_PHONE:-}" ]; then echo "  LIPRO_PHONE    : 已设置"
        else echo "  LIPRO_PHONE    : <未设置>"; fi
        echo
        echo "  持久化存储: 无（不落盘 / 不接系统钥匙串 / 不接密码管理器）"
        echo "  未设置时会退回交互式输入（仅限交互式终端）"
        ;;
    *)
        cat <<'USAGE'
lipro_cred.sh —— LIPRO 凭据模块（被 lipro_ctl.sh 自动 source，一般无需直接执行）

  用法：export LIPRO_PHONE=<手机号> LIPRO_PASSWORD=<密码>
        bash lipro_cred.sh status    看环境变量是否已设置（绝不显示明文）

  本模块不做任何持久化：不落盘、不接系统钥匙串、不接密码管理器。
USAGE
        ;;
    esac
fi
