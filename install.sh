#!/usr/bin/env bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;36m'
PLAIN='\033[0m'

# 1. Check root permissions
if [ "$(id -u)" != "0" ]; then
    echo -e "${RED}错误: 必须以 root 权限运行此脚本。请使用: sudo bash $0${PLAIN}"
    exit 1
fi

# 2. Check OS distribution and set package manager
OS_TYPE=""
PKG_MGR=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_TYPE=$ID
fi

case "$OS_TYPE" in
    ubuntu|debian)
        PKG_MGR="apt-get"
        export DEBIAN_FRONTEND=noninteractive
        ;;
    alpine)
        PKG_MGR="apk"
        ;;
    centos|rhel|rocky|almalinux|fedora|ol|amzn)
        if command -v dnf >/dev/null 2>&1; then
            PKG_MGR="dnf"
        else
            PKG_MGR="yum"
        fi
        ;;
    *)
        echo -e "${RED}错误: 不支持的操作系统 ($OS_TYPE)！目前仅支持 Ubuntu/Debian/Alpine/CentOS/RHEL/Rocky/AlmaLinux/Fedora/OracleLinux/AmazonLinux。${PLAIN}"
        exit 1
        ;;
esac

echo -e "${BLUE}==========================================================${PLAIN}"
echo -e "${BLUE}        欢迎使用 AimiliVPN 一键源码部署与管理脚本${PLAIN}"
echo -e "${BLUE}==========================================================${PLAIN}"

# 3. Configure GitHub Repository URL
# Default to this fork (wangyuyan666/aimili-vpngate)
DEFAULT_USER="wangyuyan666"
DEFAULT_REPO="aimili-vpngate"

# Allow custom repository override via command line arguments
GITHUB_USER="${1:-${DEFAULT_USER}}"
GITHUB_REPO="${2:-${DEFAULT_REPO}}"

GITHUB_URL="https://github.com/${GITHUB_USER}/${GITHUB_REPO}.git"

echo -e "\n${YELLOW}[1/4] 正在安装系统基础依赖...${PLAIN}"
if [ "$PKG_MGR" = "apt-get" ]; then
    echo -e "  -> 正在运行 apt-get update 更新软件源清单..."
    apt-get update -q || true
    echo -e "  -> 正在运行 apt-get install 安装基础依赖包..."
    apt-get install -y openvpn curl git ca-certificates iptables iproute2 psmisc python3
elif [ "$PKG_MGR" = "apk" ]; then
    echo -e "  -> 正在运行 apk update 更新软件源清单..."
    apk update || true
    echo -e "  -> 正在运行 apk add 安装基础依赖包..."
    # bash is required for this script itself and some internal logic
    apk add openvpn curl git ca-certificates iptables iproute2 psmisc python3 bash
elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
    echo -e "  -> 正在运行 $PKG_MGR 安装基础依赖包..."
    if [ "$OS_TYPE" != "fedora" ] && [ "$OS_TYPE" != "amzn" ]; then
        echo -e "     -> 正在安装 EPEL 软件源 (以支持 openvpn)..."
        $PKG_MGR install -y epel-release || true
    fi
    # Try installing packages. Note: iproute or iproute2
    $PKG_MGR install -y openvpn curl git ca-certificates iptables iproute psmisc python3 || \
    $PKG_MGR install -y openvpn curl git ca-certificates iptables iproute2 psmisc python3
fi

# 4. Clone or pull the repository
INSTALL_DIR="/opt/aimilivpn"
# 默认部署分支（在 bate 分支设为 bate；在 main 分支设为 main）
DEFAULT_DEPLOY_BRANCH="main"

# 自动检测本地已安装版本当前所在的分支
CURRENT_BRANCH=""
if [ -d "${INSTALL_DIR}/.git" ]; then
    CURRENT_BRANCH=$(cd "${INSTALL_DIR}" && git rev-parse --abbrev-ref HEAD 2>/dev/null)
fi
DEPLOY_BRANCH="${CURRENT_BRANCH:-$DEFAULT_DEPLOY_BRANCH}"

echo -e "\n${YELLOW}[2/4] 正在从 GitHub 部署源代码到 ${INSTALL_DIR} (目标分支: ${DEPLOY_BRANCH})...${PLAIN}"
if [ -f "${INSTALL_DIR}/.local_dev" ]; then
    echo -e "${GREEN}检测到本地开发模式 (.local_dev)，跳过 git pull/reset 保持本地修改。${PLAIN}"
else
    if [ -d "${INSTALL_DIR}" ]; then
        echo -e "  -> 目录 ${INSTALL_DIR} 已存在，正在更新并强制覆盖本地源码..."
        cd "${INSTALL_DIR}"
        git fetch --all || true
        git checkout "${DEPLOY_BRANCH}" || git checkout -b "${DEPLOY_BRANCH}" "origin/${DEPLOY_BRANCH}" || true
        echo -e "  -> 正在强制重置本地源码至 origin/${DEPLOY_BRANCH} ..."
        if git reset --hard "origin/${DEPLOY_BRANCH}"; then
            echo -e "${GREEN}  -> 源码更新成功！${PLAIN}"
        else
            if git pull origin "${DEPLOY_BRANCH}"; then
                echo -e "${GREEN}  -> 源码更新成功！${PLAIN}"
            else
                echo -e "${YELLOW}  -> 警告: git pull/reset 失败，将保留当前本地源码并继续安装。${PLAIN}"
            fi
        fi
    else
        echo -e "  -> 正在克隆 GitHub 仓库 ${GITHUB_URL} (分支: ${DEPLOY_BRANCH}) ..."
        if git clone -b "${DEPLOY_BRANCH}" "${GITHUB_URL}" "${INSTALL_DIR}"; then
            echo -e "${GREEN}  -> 克隆成功！${PLAIN}"
        else
            echo -e "  -> 尝试默认克隆..."
            if git clone "${GITHUB_URL}" "${INSTALL_DIR}"; then
                cd "${INSTALL_DIR}"
                git checkout "${DEPLOY_BRANCH}" || git checkout -b "${DEPLOY_BRANCH}" "origin/${DEPLOY_BRANCH}" || true
                echo -e "${GREEN}  -> 克隆成功！${PLAIN}"
            else
                echo -e "${RED}  -> 错误: 无法克隆仓库 ${GITHUB_URL}，请检查网络！${PLAIN}"
                exit 1
            fi
        fi
    fi
fi

# 5. Configure Service
echo -e "\n${YELLOW}[3/4] 正在配置系统服务...${PLAIN}"
if command -v systemctl >/dev/null 2>&1; then
    echo -e "  -> 检测到 systemd，正在创建服务配置 /etc/systemd/system/aimilivpn.service ..."
    # Migrate away from the old unit location (admin units belong in /etc/systemd/system)
    rm -f /lib/systemd/system/aimilivpn.service
    cat > /etc/systemd/system/aimilivpn.service <<EOF
[Unit]
Description=AimiliVPN OpenVPN Manager with HTTP/SOCKS5 Proxy
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 vpngate_manager.py
Restart=always
RestartSec=5
EnvironmentFile=-/etc/default/aimilivpn

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable aimilivpn.service
elif command -v rc-service >/dev/null 2>&1; then
    echo -e "  -> 检测到 OpenRC，正在创建服务配置 /etc/init.d/aimilivpn ..."
    cat > /etc/init.d/aimilivpn <<EOF
#!/sbin/openrc-run

description="AimiliVPN OpenVPN Manager with HTTP/SOCKS5 Proxy"
command="/usr/bin/python3"
command_args="${INSTALL_DIR}/vpngate_manager.py"
command_background="yes"
directory="${INSTALL_DIR}"
pidfile="/run/aimilivpn.pid"

if [ -f /etc/default/aimilivpn ]; then
    set -a
    . /etc/default/aimilivpn
    set +a
fi

depend() {
    need net
    after firewall
}
EOF
    chmod +x /etc/init.d/aimilivpn
    rc-update add aimilivpn default
else
    echo -e "${YELLOW}警告: 未能检测到 systemd 或 OpenRC，请手动管理服务。${PLAIN}"
fi

# 6. Configure global command shortcut "ml"
echo -e "\n${YELLOW}[4/4] 正在创建全局命令快捷接口 'ml'...${PLAIN}"
echo -e "  -> 正在写入管理脚本 /usr/bin/ml ..."
if [ -f "${INSTALL_DIR}/ml.py" ]; then
    install -m 0755 "${INSTALL_DIR}/ml.py" /usr/bin/ml
else
    echo -e "${YELLOW}警告: 未找到 ${INSTALL_DIR}/ml.py，跳过 ml 命令更新 (保留现有 /usr/bin/ml)。${PLAIN}"
fi

# 7. Configure Custom parameters (First-time installation check)
AUTH_FILE="${INSTALL_DIR}/vpngate_data/ui_auth.json"
PROXY_ENV_FILE="/etc/default/aimilivpn"
mkdir -p "${INSTALL_DIR}/vpngate_data"

# Returns 0 if the TCP port is already taken by another process (bind test on IPv4+IPv6 wildcard)
port_in_use() {
    python3 - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
in_use = False
for af, addr in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
    try:
        s = socket.socket(af, socket.SOCK_STREAM)
    except OSError:
        continue
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((addr, port))
    except OSError:
        in_use = True
    finally:
        s.close()
sys.exit(0 if in_use else 1)
PY
}

# Echoes the first free port at or after $1 (scans up to +100)
find_free_port() {
    local p="$1"
    local limit=$((p + 100))
    while [ "$p" -le "$limit" ] && [ "$p" -le 65535 ]; do
        if ! port_in_use "$p"; then
            echo "$p"
            return 0
        fi
        p=$((p + 1))
    done
    echo "$1"
    return 1
}

is_custom="n"
if [ ! -f "$AUTH_FILE" ]; then
    if [ -t 0 ]; then
        echo -e "\n${YELLOW}检测到是首次安装，是否需要自定义配置网页端参数（端口/安全后缀/登录账号密码）？${PLAIN}"
        read -p "是否自定义配置？[y/N]: " is_custom
    else
        echo -e "\n${YELLOW}检测到是非交互式/无TTY环境安装，已自动跳过网页端参数自定义配置，采用默认随机参数部署。${PLAIN}"
    fi
    
    # Initialize defaults; avoid clashing with services already on this machine (e.g. Xray/Nginx)
    UI_PORT=$(find_free_port 8787)
    PROXY_PORT=$(find_free_port 7928)
    if [ "$UI_PORT" != "8787" ]; then
        echo -e "${YELLOW}提示: 端口 8787 已被其他进程占用，管理端口自动调整为 ${UI_PORT}。${PLAIN}"
    fi
    if [ "$PROXY_PORT" != "7928" ]; then
        echo -e "${YELLOW}提示: 端口 7928 已被其他进程占用，代理出站端口自动调整为 ${PROXY_PORT}。${PLAIN}"
    fi
    # generate random secret suffix (12 chars alphanumeric)
    SECRET_PATH=$(python3 -c "import random, string; print(''.join(random.choices(string.ascii_letters + string.digits, k=12)))")
    # generate random password
    UI_PASSWORD=$(python3 -c "
import random, string
chars = string.ascii_letters + string.digits
while True:
    pwd = ''.join(random.choices(chars, k=12))
    if any(c.islower() for c in pwd) and any(c.isupper() for c in pwd) and any(c.isdigit() for c in pwd):
        print(pwd)
        break
")
    UI_USERNAME=$(python3 -c "
import random, string
chars = string.ascii_letters + string.digits
while True:
    uname = ''.join(random.choices(chars, k=12))
    if uname[0].isalpha() and any(c.islower() for c in uname) and any(c.isupper() for c in uname) and any(c.isdigit() for c in uname):
        print(uname)
        break
")

    if [[ "$is_custom" =~ ^[Yy]$ ]]; then
        # Step-by-step custom inputs
        # 1. Custom port
        while true; do
            read -p "请输入自定义管理端口 [1-65535, 默认 ${UI_PORT}]: " input_port
            if [ -z "$input_port" ]; then
                break
            fi
            if [[ "$input_port" =~ ^[0-9]+$ ]] && [ "$input_port" -ge 1 ] && [ "$input_port" -le 65535 ]; then
                if port_in_use "$input_port"; then
                    echo -e "${RED}输入错误: 端口 ${input_port} 已被其他进程占用，请换一个端口。${PLAIN}"
                    continue
                fi
                UI_PORT=$input_port
                break
            else
                echo -e "${RED}输入错误: 端口必须是 1 到 65535 之间的数字！${PLAIN}"
            fi
        done
        
        # 2. Custom suffix
        while true; do
            read -p "请输入网页登录自定义安全后缀 [字母与数字组合, 默认随机]: " input_suffix
            if [ -z "$input_suffix" ]; then
                break
            fi
            if [[ "$input_suffix" =~ ^[A-Za-z0-9]+$ ]]; then
                SECRET_PATH=$input_suffix
                break
            else
                echo -e "${RED}输入错误: 后缀仅能由英文字母和数字组成！${PLAIN}"
            fi
        done
        
        # 3. Custom login username and password
        read -p "请输入登录账号 [默认 $UI_USERNAME]: " input_user
        if [ -n "$input_user" ]; then
            UI_USERNAME=$input_user
        fi
        
        while true; do
            read -p "请输入登录密码 [默认随机生成, 建议包含字母、数字与符号]: " input_pass
            if [ -z "$input_pass" ]; then
                break
            fi
            if [ ${#input_pass} -ge 4 ]; then
                UI_PASSWORD=$input_pass
                break
            else
                echo -e "${RED}输入错误: 密码长度不能少于 4 位！${PLAIN}"
            fi
        done
    fi

    # Write config JSON. Values are passed as argv to avoid breaking Python code
    # when username/password contain quotes, backslashes, or shell metacharacters.
    python3 - "$AUTH_FILE" "$UI_PORT" "$SECRET_PATH" "$UI_USERNAME" "$UI_PASSWORD" "$PROXY_PORT" <<'PY'
import json
import os
import sys

auth_file, ui_port, secret_path, username, password, proxy_port = sys.argv[1:7]
cfg = {
    "host": "::",
    "port": int(ui_port),
    "proxy_port": int(proxy_port),
    "secret_path": secret_path,
    "username": username,
    "password": password,
}
with open(auth_file, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
os.chmod(auth_file, 0o600)
PY
fi
# Config file holds the panel password in cleartext — keep it root-only even on upgrades
chmod 600 "$AUTH_FILE" 2>/dev/null || true

# 7.5 Configure public proxy listener and mandatory proxy credentials.
# Existing LOCAL_PROXY_USER / LOCAL_PROXY_PASS are preserved (env file first, then
# the runtime-generated local_proxy_auth.json so upgrades don't rotate credentials);
# listener host is forced to 0.0.0.0 so external clients can reach the proxy.
# NOTE: keep this heredoc Python 3.8 compatible (no dict[...] annotations).
python3 - "$PROXY_ENV_FILE" "$AUTH_FILE" "${INSTALL_DIR}/vpngate_data/local_proxy_auth.json" <<'PY'
import json
import os
import re
import secrets
import string
import sys
from pathlib import Path

env_file = Path(sys.argv[1])
auth_file = Path(sys.argv[2])
proxy_auth_json = Path(sys.argv[3])

def parse_env(path):
    result = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (len(value) >= 2) and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                # Undo quote_env escaping so values round-trip across reruns.
                value = re.sub(r"\\(.)", r"\1", value)
        result[key] = value
    return result

def random_alnum(length):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

def quote_env(value):
    # Single quotes: literal for both shell sourcing (OpenRC) and systemd
    # EnvironmentFile. Values containing a single quote fall back to double
    # quotes with best-effort escaping (systemd does not expand $ anyway).
    if "'" not in value:
        return "'" + value + "'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return '"' + escaped + '"'

env = parse_env(env_file)
proxy_port = "7928"
try:
    cfg = json.loads(auth_file.read_text(encoding="utf-8"))
    proxy_port = str(int(cfg.get("proxy_port", 7928)))
except Exception:
    pass

json_user = ""
json_pass = ""
try:
    if proxy_auth_json.exists():
        data = json.loads(proxy_auth_json.read_text(encoding="utf-8"))
        json_user = str(data.get("username") or "")
        json_pass = str(data.get("password") or "")
except Exception:
    pass

proxy_user = (
    env.get("LOCAL_PROXY_USER") or env.get("LOCAL_PROXY_USERNAME")
    or json_user or ("proxy" + random_alnum(6))
)
proxy_pass = (
    env.get("LOCAL_PROXY_PASS") or env.get("LOCAL_PROXY_PASSWORD")
    or json_pass or random_alnum(20)
)

content = "\n".join([
    "# AimiliVPN runtime environment",
    "# HTTP/SOCKS5 proxy listens on all IPv4 interfaces and requires authentication.",
    "# Set LOCAL_PROXY_USER and LOCAL_PROXY_PASS both to empty values to disable auth (not recommended).",
    "LOCAL_PROXY_HOST=0.0.0.0",
    "LOCAL_PROXY_PORT=" + proxy_port,
    "LOCAL_PROXY_USER=" + quote_env(proxy_user),
    "LOCAL_PROXY_PASS=" + quote_env(proxy_pass),
    "",
])
env_file.parent.mkdir(parents=True, exist_ok=True)
env_file.write_text(content, encoding="utf-8")
os.chmod(env_file, 0o600)
PY

# 8. Start service
# 8.5 Optimize network parameters (rp_filter for policy routing)
# Kernel uses max(conf/all, conf/<iface>) for rp_filter, so setting conf/default=2
# is enough: every tun interface created later inherits loose mode (2), while the
# strictness of existing physical interfaces and the global "all" value stay untouched.
echo -e "\n正在优化网络参数 (为新建接口配置反向路径过滤 rp_filter=2 以支持策略路由)..."
if [ -d "/etc/sysctl.d" ]; then
    cat > /etc/sysctl.d/99-aimilivpn.conf <<EOF
net.ipv4.conf.default.rp_filter = 2
EOF
    sysctl -p /etc/sysctl.d/99-aimilivpn.conf >/dev/null 2>&1 || true
else
    # Fallback to appending to /etc/sysctl.conf
    if ! grep -q "net.ipv4.conf.default.rp_filter" /etc/sysctl.conf; then
        echo "" >> /etc/sysctl.conf
        echo "net.ipv4.conf.default.rp_filter = 2" >> /etc/sysctl.conf
    else
        sed -i 's/net.ipv4.conf.default.rp_filter\s*=\s*[0-9]/net.ipv4.conf.default.rp_filter = 2/g' /etc/sysctl.conf
    fi
    sysctl -p >/dev/null 2>&1 || true
fi
# Apply immediately (prefer native proc write for BusyBox/Alpine compatibility)
echo "2" > /proc/sys/net/ipv4/conf/default/rp_filter 2>/dev/null || sysctl -w net.ipv4.conf.default.rp_filter=2 >/dev/null 2>&1 || true
# Existing tun interfaces (upgrade while connected) still need loose mode right now
if [ -d "/proc/sys/net/ipv4/conf" ]; then
    for dev_dir in /proc/sys/net/ipv4/conf/tun*; do
        [ -d "$dev_dir" ] || continue
        dev_name=$(basename "$dev_dir")
        echo "2" > "/proc/sys/net/ipv4/conf/${dev_name}/rp_filter" 2>/dev/null || sysctl -w net.ipv4.conf.${dev_name}.rp_filter=2 >/dev/null 2>&1 || true
    done
fi

echo -e "\n正在启动 AimiliVPN 服务并初始化网络..."
if command -v systemctl >/dev/null 2>&1; then
    systemctl restart aimilivpn.service || true
elif command -v rc-service >/dev/null 2>&1; then
    rc-service aimilivpn restart || true
fi

# Wait and poll for node loading and active connection
echo -e "\n正在等待 AimiliVPN 首次获取节点并建立加密通道 (此过程可能需要 5-30 秒)..."
ACTIVE_ID=""
LAST_MSG=""
for i in {1..90}; do
    if [ -f "${INSTALL_DIR}/vpngate_data/state.json" ]; then
        ACTIVE_ID=$(python3 -c "import json; print(json.load(open('${INSTALL_DIR}/vpngate_data/state.json')).get('active_openvpn_node_id', ''))" 2>/dev/null || echo "")
        IS_CONN=$(python3 -c "import json; print(json.load(open('${INSTALL_DIR}/vpngate_data/state.json')).get('is_connecting', False))" 2>/dev/null || echo "False")
        CUR_MSG=$(python3 -c "import json; print(json.load(open('${INSTALL_DIR}/vpngate_data/state.json')).get('last_check_message', ''))" 2>/dev/null || echo "")
        
        if [ "$IS_CONN" = "False" ] || [ "$IS_CONN" = "false" ]; then
            if [ -n "$ACTIVE_ID" ]; then
                echo -e "  -> ${GREEN}[已就绪]${PLAIN} 首次节点连接成功，活动节点: ${GREEN}$ACTIVE_ID${PLAIN}"
                break
            else
                if [ -n "$CUR_MSG" ] && [ "$CUR_MSG" != "$LAST_MSG" ]; then
                    echo -e "  -> 提示: ${YELLOW}${CUR_MSG}${PLAIN}"
                    LAST_MSG="$CUR_MSG"
                fi
            fi
        else
            if [ -n "$CUR_MSG" ] && [ "$CUR_MSG" != "$LAST_MSG" ]; then
                echo -e "  -> 状态: ${YELLOW}${CUR_MSG}${PLAIN}"
                LAST_MSG="$CUR_MSG"
            fi
        fi
    else
        echo -n "."
    fi
    sleep 1
done
if [ -z "$ACTIVE_ID" ]; then
    echo -e "  -> ${YELLOW}[加载超时]${PLAIN} 首次节点获取或连接超时，将在后台继续尝试..."
fi

SECRET_PATH="EJsW2EeBo9lY"
USERNAME="未配置"
PASSWORD="未配置"
UI_PORT=8787
PROXY_PORT=7928
PROXY_HOST="0.0.0.0"
PROXY_USER="未配置"
PROXY_PASS="未配置"
AUTH_FILE="${INSTALL_DIR}/vpngate_data/ui_auth.json"
if [ -f "$AUTH_FILE" ]; then
    SECRET_PATH=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('secret_path', 'EJsW2EeBo9lY'))" 2>/dev/null || echo "EJsW2EeBo9lY")
    USERNAME=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('username', '未配置'))" 2>/dev/null || echo "未配置")
    PASSWORD=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('password', '未配置'))" 2>/dev/null || echo "未配置")
    UI_PORT=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('port', 8787))" 2>/dev/null || echo "8787")
    PROXY_PORT=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('proxy_port', 7928))" 2>/dev/null || echo "7928")
fi
if [ -f "$PROXY_ENV_FILE" ]; then
    PROXY_HOST=$(python3 - "$PROXY_ENV_FILE" <<'PY' 2>/dev/null || echo "0.0.0.0"
import sys
from pathlib import Path
key = "LOCAL_PROXY_HOST"
for raw in Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore").splitlines():
    line = raw.strip()
    if line.startswith(key + "="):
        val = line.split("=", 1)[1].strip().strip("'\"")
        print(val or "0.0.0.0")
        break
else:
    print("0.0.0.0")
PY
)
    PROXY_USER=$(python3 - "$PROXY_ENV_FILE" <<'PY' 2>/dev/null || echo "未配置"
import sys
from pathlib import Path
keys = ("LOCAL_PROXY_USER", "LOCAL_PROXY_USERNAME")
env = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore").splitlines():
    line = raw.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")
print(next((env[k] for k in keys if env.get(k)), "未配置"))
PY
)
    PROXY_PASS=$(python3 - "$PROXY_ENV_FILE" <<'PY' 2>/dev/null || echo "未配置"
import sys
from pathlib import Path
keys = ("LOCAL_PROXY_PASS", "LOCAL_PROXY_PASSWORD")
env = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore").splitlines():
    line = raw.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")
print(next((env[k] for k in keys if env.get(k)), "未配置"))
PY
)
fi

# Get VPS public IP
echo -e "正在获取 VPS 公网 IP..."
# Force IPv4: the proxy listens on 0.0.0.0 (IPv4 only) and unbracketed IPv6
# literals would produce invalid proxy URLs below. Dual-stack as last resort.
PUBLIC_IP=$(curl -4 -s --max-time 3 https://api.ipify.org || curl -4 -s --max-time 3 https://ifconfig.me || curl -4 -s --max-time 3 https://icanhazip.com || curl -s --max-time 3 https://api64.ipify.org || echo "您的服务器公网IP")
echo -n "$PUBLIC_IP" > "${INSTALL_DIR}/vpngate_data/public_ip.txt"

# Get VPS public IPv6
echo -e "正在获取 VPS 公网 IPv6..."
PUBLIC_IPV6=$(curl -6 -s --max-time 3 https://api.ipify.org || curl -6 -s --max-time 3 https://ifconfig.me || curl -6 -s --max-time 3 https://icanhazip.com || echo "")

echo -e "\n${GREEN}==========================================================${PLAIN}"
echo -e "${GREEN}             AimiliVPN 源码一键部署已完成！${PLAIN}"
echo -e "${GREEN}==========================================================${PLAIN}"
echo -e "  * 网页控制面板:  ${BLUE}http://${PUBLIC_IP}:${UI_PORT}/${SECRET_PATH}/${PLAIN}"
if [ -n "$PUBLIC_IPV6" ]; then
    echo -e "  * 网页控制面板(IPv6):  ${BLUE}http://[${PUBLIC_IPV6}]:${UI_PORT}/${SECRET_PATH}/${PLAIN}"
fi
echo -e "  * 网页管理账号:  ${YELLOW}${USERNAME}${PLAIN}"
echo -e "  * 网页管理密码:  ${YELLOW}${PASSWORD}${PLAIN}"
echo -e "  * HTTP/SOCKS5 代理监听:  ${YELLOW}${PROXY_HOST}:${PROXY_PORT}${PLAIN}"
echo -e "  * 代理账号:        ${YELLOW}${PROXY_USER}${PLAIN}"
echo -e "  * 代理密码:        ${YELLOW}${PROXY_PASS}${PLAIN}"
echo -e "  * HTTP 代理地址:   ${BLUE}http://${PROXY_USER}:${PROXY_PASS}@${PUBLIC_IP}:${PROXY_PORT}/${PLAIN}"
echo -e "  * SOCKS5 代理地址: ${BLUE}socks5://${PROXY_USER}:${PROXY_PASS}@${PUBLIC_IP}:${PROXY_PORT}/${PLAIN}"
echo -e " --------------------------------------------------------"
echo -e "  * 快速状态指令:   ${YELLOW}ml status${PLAIN}  或  ${YELLOW}ml${PLAIN}"
echo -e "  * 查看实时日志:   ${YELLOW}ml logs${PLAIN}"
echo -e "  * 停止服务:       ${YELLOW}ml stop${PLAIN}"
echo -e "  * 重启服务:       ${YELLOW}ml restart${PLAIN}"
echo -e "=========================================================="
UI_HOST_CFG=$(python3 -c "import json; print(json.load(open('$AUTH_FILE')).get('host', '::'))" 2>/dev/null || echo "::")
if [ "$UI_HOST_CFG" = "::" ] || [ "$UI_HOST_CFG" = "0.0.0.0" ]; then
    echo -e "${YELLOW}[安全提示] 管理面板为明文 HTTP 且当前绑定公网地址 (${UI_HOST_CFG})，账号密码可能被网络中间人截获。${PLAIN}"
    echo -e "${YELLOW}           更安全的方式: 运行 'ml web' 改绑 127.0.0.1，然后通过 SSH 隧道访问:${PLAIN}"
    echo -e "${YELLOW}           ssh -L ${UI_PORT}:127.0.0.1:${UI_PORT} root@${PUBLIC_IP}${PLAIN}"
fi
echo
