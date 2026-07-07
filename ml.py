#!/usr/bin/env python3
import sys
import os
import socket
import subprocess
import time
import tty
import termios
import shutil

INSTALL_DIR = "/opt/aimilivpn"
LOG_FILE = "/opt/aimilivpn/vpngate_data/vpngate.log"

def generate_random_password():
    import random
    import string
    chars = string.ascii_letters + string.digits
    while True:
        pwd = "".join(random.choices(chars, k=12))
        if any(c.islower() for c in pwd) and any(c.isupper() for c in pwd) and any(c.isdigit() for c in pwd):
            return pwd

def generate_random_suffix():
    import random
    import string
    return "".join(random.choices(string.ascii_letters + string.digits, k=12))

def load_ui_cfg():
    import json
    path = "/opt/aimilivpn/vpngate_data/ui_auth.json"
    cfg = {"host": "::", "port": 8787, "secret_path": "EJsW2EeBo9lY", "password": ""}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    cfg[k] = v
        except Exception:
            pass
    return cfg

def save_ui_cfg(cfg):
    import json
    path = "/opt/aimilivpn/vpngate_data/ui_auth.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return True
    except Exception:
        return False

def load_proxy_auth_cfg():
    import json
    env_path = "/etc/default/aimilivpn"
    data_path = "/opt/aimilivpn/vpngate_data/local_proxy_auth.json"
    cfg = {"host": "0.0.0.0", "username": "", "password": ""}
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    value = value.strip().strip("'\"")
                    if key == "LOCAL_PROXY_HOST":
                        cfg["host"] = value or cfg["host"]
                    elif key in ("LOCAL_PROXY_USER", "LOCAL_PROXY_USERNAME"):
                        cfg["username"] = value
                    elif key in ("LOCAL_PROXY_PASS", "LOCAL_PROXY_PASSWORD"):
                        cfg["password"] = value
        except Exception:
            pass
    if (not cfg["username"] or not cfg["password"]) and os.path.exists(data_path):
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg["username"] = cfg["username"] or str(data.get("username") or "")
            cfg["password"] = cfg["password"] or str(data.get("password") or "")
        except Exception:
            pass
    return cfg

def load_state():
    import json
    path = "/opt/aimilivpn/vpngate_data/state.json"
    state = {"active_openvpn_node_id": "", "last_check_message": "", "is_connecting": False}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    state[k] = v
        except Exception:
            pass
    return state

def get_active_node_info():
    import json
    paths = [
        "/opt/aimilivpn/vpngate_data/nodes.json",
        "/opt/aimilivpn/vpngate_data/nodes_vpnbook.json",
    ]
    state = load_state()
    active_id = state.get("active_openvpn_node_id")
    if not active_id:
        return None, None
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                nodes = json.load(f)
                for n in nodes:
                    if n.get("id") == active_id:
                        ip = n.get("ip") or n.get("remote_host")
                        loc = n.get("location") or n.get("country") or "未知"
                        return ip, loc
        except Exception:
            pass
    return None, None

def ping_ip(ip):
    if not ip:
        return None
    try:
        # Run standard linux ping command with 1 packet and 2 seconds timeout
        res = subprocess.run(["ping", "-c", "1", "-W", "2", ip], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            out = res.stdout
            lines = out.splitlines()
            for line in lines:
                if "rtt" in line or "min/avg" in line:
                    parts = line.split("=")[1].strip().split("/")
                    if len(parts) >= 2:
                        avg_rtt = float(parts[1])
                        return f"{int(avg_rtt)} ms"
            return "已响应"
        else:
            return "检测超时"
    except Exception:
        return "无法连接"

def get_public_ip():
    path = "/opt/aimilivpn/vpngate_data/public_ip.txt"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                ip = f.read().strip()
                if ip:
                    return ip
        except Exception:
            pass
    import urllib.request
    # IPv4 first: the proxy listens on 0.0.0.0 (IPv4 only), so an IPv6 answer
    # would point at an unreachable address. Dual-stack / IPv6 as fallback.
    for api_url in ["https://api.ipify.org", "https://api64.ipify.org", "https://api6.ipify.org"]:
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=2) as r:
                ip = r.read().decode().strip()
                if ip:
                    try:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(ip)
                    except Exception:
                        pass
                    return ip
        except Exception:
            pass
    return "您的服务器公网IP"

def check_port_listening(port):
    for host, family in [("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)]:
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
            s.settimeout(0.2)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            pass
    return False

def get_service_pid(service_name="aimilivpn.service"):
    # 1. Prefer systemd's own record of the main process
    if shutil.which("systemctl"):
        try:
            res = subprocess.run(
                ["systemctl", "show", "-p", "MainPID", "--value", service_name],
                capture_output=True, text=True, timeout=2)
            pid = res.stdout.strip()
            if pid.isdigit() and pid != "0" and os.path.isdir(os.path.join("/proc", pid)):
                return pid
        except Exception:
            pass
    # 2. OpenRC pidfile
    try:
        with open("/run/aimilivpn.pid", "r") as f:
            pid = f.read().strip()
            if pid.isdigit() and os.path.isdir(os.path.join("/proc", pid)):
                return pid
    except Exception:
        pass
    # 3. Fallback: strict /proc scan — argv0 must be an interpreter/binary and
    #    vpngate_manager.py must be a standalone argument (avoids matching
    #    editors, tail, grep, etc. that merely mention the file name)
    try:
        for pid_dir in os.listdir('/proc'):
            if not pid_dir.isdigit():
                continue
            try:
                with open(os.path.join('/proc', pid_dir, 'cmdline'), 'rb') as f:
                    argv = [a.decode("utf-8", "replace") for a in f.read().split(b'\x00') if a]
                if len(argv) >= 2 and "python" in os.path.basename(argv[0]).lower():
                    if any(os.path.basename(a) == "vpngate_manager.py" for a in argv[1:]):
                        return pid_dir
            except Exception:
                continue
    except Exception:
        pass
    return None

def check_service_active(service_name="aimilivpn.service"):
    return get_service_pid(service_name) is not None

def check_openvpn_process():
    try:
        for pid_dir in os.listdir('/proc'):
            if pid_dir.isdigit():
                try:
                    with open(os.path.join('/proc', pid_dir, 'cmdline'), 'r') as f:
                        cmd = f.read().replace('\x00', ' ')
                        if 'openvpn' in cmd and ('/opt/aimilivpn/vpngate_data' in cmd or '/opt/aimilivpn/vpngate_data/configs' in cmd):
                            return True
                except Exception:
                    continue
    except Exception:
        pass
    return False

def get_display_width(s):
    import re
    ansi_escape = re.compile(r'\x1b\[[0-9;]*[mGKH]')
    s_clean = ansi_escape.sub('', s)
    width = 0
    for char in s_clean:
        if ord(char) > 127:
            width += 2
        else:
            width += 1
    return width

def format_line(label, value, target_width=26):
    prefix = "  ● "
    w = get_display_width(label)
    padding = " " * max(0, target_width - w)
    return f"{prefix}{label}{padding}:  {value}"

def print_line(text=""):
    print(f"{text}\033[K")

def print_status():
    cfg = load_ui_cfg()
    ui_port = cfg.get("port", 8787)
    secret_path = cfg.get("secret_path", "EJsW2EeBo9lY")
    proxy_port = cfg.get("proxy_port", 7928)
    state = load_state()
    is_connecting = state.get("is_connecting", False)
    
    gateway_ok = check_port_listening(proxy_port)
    service_ok = check_service_active("aimilivpn.service")
    openvpn_ok = check_openvpn_process()
    pid = get_service_pid("aimilivpn.service")
    
    active_ip, active_loc = get_active_node_info()
    latency = state.get("active_node_latency", "测试中...") if active_ip else "无活动连接"
    
    green = "\033[1;32m"
    red = "\033[1;31m"
    reset = "\033[0m"
    bold = "\033[1m"
    yellow = "\033[1;33m"
    
    backend_status = f"{green}[已激活] (PID: {pid}){reset}" if (service_ok and pid) else f"{red}[未启动]{reset}"
    
    if is_connecting:
        gateway_status = f"{yellow}[切换中...]{reset}"
        openvpn_status = f"{yellow}[{state.get('active_node_latency') or '连接中'}...]{reset}"
    else:
        gateway_status = f"{green}[已激活]{reset}" if gateway_ok else f"{red}[未启动]{reset}"
        openvpn_status = f"{green}[已连接]{reset}" if openvpn_ok else f"{red}[未连接]{reset}"
    
    print_line("=======================================================")
    print_line(f"               {bold}AimiliVPN 管理终端 v2.0{reset}                  ")
    print_line("=======================================================")
    print_line("【核心服务状态】")
    print_line(format_line(f"代理网关 (Port {proxy_port})", gateway_status))
    print_line(format_line(f"管理后台 (Port {ui_port})", backend_status))
    print_line(format_line("连接核心 (OpenVPN)", openvpn_status))
    
    host_cfg = cfg.get("host", "::")
    if host_cfg in ("127.0.0.1", "localhost"):
        login_ip = "127.0.0.1"
    elif host_cfg == "::1":
        login_ip = "[::1]"
    elif host_cfg == "::":
        login_ip = get_public_ip()
    else:
        login_ip = f"[{host_cfg}]" if ":" in host_cfg else host_cfg
    print_line(format_line("网页登录地址", f"{yellow}http://{login_ip}:{ui_port}/{secret_path}/{reset}"))
    print_line(format_line("网页管理账号", cfg.get("username", "未配置")))
    curr_pwd = cfg.get("password", "")
    masked_pwd = curr_pwd if len(curr_pwd) <= 4 else curr_pwd[:3] + "********" + curr_pwd[-2:]
    print_line(format_line("网页管理密码", masked_pwd))
    proxy_auth = load_proxy_auth_cfg()
    proxy_pwd = proxy_auth.get("password", "")
    masked_proxy_pwd = proxy_pwd if len(proxy_pwd) <= 4 else proxy_pwd[:3] + "********" + proxy_pwd[-2:]
    print_line(format_line("代理监听地址", f"{proxy_auth.get('host', '0.0.0.0')}:{proxy_port}"))
    print_line(format_line("代理账号", proxy_auth.get("username") or "未配置"))
    print_line(format_line("代理密码", masked_proxy_pwd or "未配置"))
    print_line()
    print_line("【活动节点状态】")
    if is_connecting:
        connecting_msg = state.get('last_check_message') or '正在建立加密隧道并验证路由规则...'
        print_line(format_line("节点状态", f"{yellow}{connecting_msg}{reset}"))
    elif active_ip:
        proxy_ip = state.get("proxy_ip", "-")
        proxy_latency = state.get("proxy_latency_ms", 0)
        proxy_ok = state.get("proxy_ok", False)
        
        print_line(format_line("节点 IP (入口)", active_ip))
        print_line(format_line("节点地区", active_loc))
        print_line(format_line("节点延迟 (直连测试)", latency))
        if proxy_ok and proxy_ip and proxy_ip != "-":
            print_line(format_line("出口 IP (出站)", proxy_ip))
            print_line(format_line("本地代理延迟", f"{proxy_latency} ms" if proxy_latency else "检测中..."))
        else:
            proxy_err = state.get("proxy_error") or "检测中/未就绪"
            print_line(format_line("出口 IP (出站)", f"{red}[不可用 - {proxy_err}]{reset}"))
    else:
        print_line(format_line("节点状态", "无活动连接"))
    print_line()
    local_proxy = state.get("local_proxy", f"http://0.0.0.0:{proxy_port}")
    import urllib.parse
    try:
        parsed = urllib.parse.urlsplit(local_proxy)
        proxy_host = parsed.hostname or "127.0.0.1"
        proxy_port = parsed.port or proxy_port
    except Exception:
        proxy_host = "127.0.0.1"
        proxy_port = proxy_port
    
    if proxy_host == "::":
        proxy_addr = "127.0.0.1"
    elif proxy_host == "0.0.0.0":
        proxy_addr = get_public_ip()
        # Cached public_ip.txt may hold an IPv6 literal (older installs):
        # bracket it so the printed URL stays parseable.
        if ":" in proxy_addr:
            proxy_addr = f"[{proxy_addr}]"
    elif ":" in proxy_host:
        proxy_addr = f"[{proxy_host}]"
    else:
        proxy_addr = proxy_host

    print_line("【使用方法】")
    auth_prefix = ""
    if proxy_auth.get("username") or proxy_auth.get("password"):
        auth_prefix = f"{proxy_auth.get('username', '')}:{proxy_auth.get('password', '')}@"
    print_line(f"  export http_proxy=http://{auth_prefix}{proxy_addr}:{proxy_port}")
    print_line(f"  export https_proxy=http://{auth_prefix}{proxy_addr}:{proxy_port}")
    print_line(f"  # 也可用于 SOCKS5: socks5://{auth_prefix}{proxy_addr}:{proxy_port}")
    print_line("=======================================================")

def run_service_cmd(cmd):
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", cmd, "aimilivpn.service"])
    elif shutil.which("rc-service"):
        subprocess.run(["rc-service", "aimilivpn", cmd])
    else:
        print("未检测到支持的服务管理器 (systemd/OpenRC)")

def start_service():
    print("正在启动 AimiliVPN 服务...", flush=True)
    run_service_cmd("start")
    print("已发送启动指令。")
    time.sleep(1)

def stop_service():
    print("正在停止 AimiliVPN 服务...", flush=True)
    run_service_cmd("stop")
    print("已发送停止指令。")
    time.sleep(1)

def restart_service():
    print("正在重启 AimiliVPN 服务...", flush=True)
    run_service_cmd("restart")
    print("已发送重启指令。")
    time.sleep(1)

def show_logs():
    print("正在查看 AimiliVPN 日志 (按 Ctrl+C 退出)...", flush=True)
    if os.path.exists(LOG_FILE):
        try:
            subprocess.run(["tail", "-f", "-n", "50", LOG_FILE])
        except KeyboardInterrupt:
            pass
    else:
        print(f"日志文件不存在: {LOG_FILE}")
        time.sleep(2)

def update_service():
    print("正在获取远程更新并检测版本...", flush=True)
    if os.path.exists(INSTALL_DIR):
        try:
            os.chdir(INSTALL_DIR)
            if not os.path.exists(".git"):
                print("错误: 当前安装目录不是 Git 仓库，无法通过 Git 更新。")
                time.sleep(3)
                return
            
            # Fetch remote origin updates
            subprocess.run(["git", "fetch", "--all"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Detect remote branch (prefer current local branch, fallback to origin/main or origin/master)
            curr = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
            branch = curr.stdout.strip() if curr.returncode == 0 else ""
            if not branch or branch == "HEAD":
                branch = "main"
                for b in ["main", "master"]:
                    chk = subprocess.run(["git", "rev-parse", "--verify", f"origin/{b}"], capture_output=True, text=True)
                    if chk.returncode == 0:
                        branch = b
                        break
            
            local_commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            remote_commit = subprocess.run(["git", "rev-parse", f"origin/{branch}"], capture_output=True, text=True).stdout.strip()
            
            if local_commit == remote_commit:
                print("\n【版本状态】当前已是最新版本，无需更新！")
                override = input("是否强制重新拉取代码并覆盖安装？(y/N): ").strip().lower()
                if override != 'y':
                    print("已取消更新。")
                    time.sleep(1.5)
                    return
            else:
                print(f"\n【检测到更新】本地版本: {local_commit[:8]}，远程最新版本: {remote_commit[:8]}")
                confirm = input("是否确认开始更新并重启服务？(Y/n): ").strip().lower()
                if confirm not in ('', 'y', 'yes'):
                    print("已取消更新。")
                    time.sleep(1.5)
                    return
            
            print(f"\n正在强制重置本地代码至 origin/{branch} ...", flush=True)
            subprocess.run(["git", "reset", "--hard", f"origin/{branch}"], check=True)
            
            # Clean up python cache files
            print("正在清理 Python 缓存 (pycache)...", flush=True)
            subprocess.run(["find", ".", "-type", "d", "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"], check=False)
            
            print("代码拉取成功，正在重新运行安装脚本...", flush=True)
            subprocess.run(["bash", "install.sh"])
            print("更新已完成！")
            time.sleep(2)
        except Exception as e:
            print(f"更新失败: {e}")
            time.sleep(4)
    else:
        print(f"未找到安装目录: {INSTALL_DIR}")
        time.sleep(2)

def cleanup_policy_routing():
    """Remove ip rules and routes in table 100 left behind by the service."""
    for _ in range(10):
        res = subprocess.run(["ip", "rule", "del", "table", "100"], capture_output=True)
        if res.returncode != 0:
            break
    subprocess.run(["ip", "route", "flush", "table", "100"], capture_output=True)

def cleanup_resolv_conf(path="/etc/resolv.conf"):
    """Remove the DNS fallback block appended by the service to /etc/resolv.conf."""
    begin_marker = "# aimilivpn-dns-fallback-begin"
    end_marker = "# aimilivpn-dns-fallback-end"
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        cleaned = []
        skipping = False
        changed = False
        for line in lines:
            stripped = line.strip()
            if stripped == begin_marker:
                skipping = True
                changed = True
                continue
            if stripped == end_marker:
                skipping = False
                continue
            if not skipping:
                cleaned.append(line)
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(cleaned)
            print(f"已还原 {path} 中由本服务追加的 DNS 配置。")
    except Exception as e:
        print(f"还原 {path} 失败: {e}")

def uninstall_service():
    confirm = input("确定要完全卸载 AimiliVPN 吗？(y/N): ")
    if confirm.lower() == 'y':
        print("正在完全卸载 AimiliVPN...", flush=True)
        stop_service()
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "disable", "aimilivpn.service"])
            for unit_path in ("/etc/systemd/system/aimilivpn.service",
                              "/lib/systemd/system/aimilivpn.service"):
                try:
                    os.unlink(unit_path)
                except Exception:
                    pass
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
        elif shutil.which("rc-service"):
            subprocess.run(["rc-update", "del", "aimilivpn"])
            try:
                os.unlink("/etc/init.d/aimilivpn")
            except Exception:
                pass
        # Clean up system-level changes made at install time / runtime
        cleanup_policy_routing()
        cleanup_resolv_conf()
        try:
            os.unlink("/etc/sysctl.d/99-aimilivpn.conf")
            print("已删除 /etc/sysctl.d/99-aimilivpn.conf (rp_filter 调整将在重启后恢复系统默认)。")
        except Exception:
            pass
        try:
            os.unlink("/usr/bin/ml")
        except Exception:
            pass
        subprocess.run(["rm", "-rf", INSTALL_DIR])
        print("AimiliVPN 已卸载！")
        sys.exit(0)
    else:
        print("已取消卸载。")
        time.sleep(1)

def ask_restart():
    ans = input("配置已保存。是否立即重启服务生效？(Y/n): ").strip().lower()
    if ans in ('', 'y', 'yes'):
        print("正在重启 AimiliVPN 服务...", flush=True)
        restart_service()
        print("服务已重启。")
        time.sleep(1.5)

def configure_web():
    cfg = load_ui_cfg()
    while True:
        print("\033[H\033[J", end="")
        print("=======================================================")
        print("               网页绑定与地址后缀配置                  ")
        print("=======================================================")
        print(f"  [1] 切换绑定地址 (当前: {cfg.get('host', '0.0.0.0')})")
        print(f"  [2] 随机重置安全后缀 (当前: {cfg.get('secret_path', '')})")
        print("  [3] 返回主菜单")
        print("=======================================================")
        print("请直接输入数字键 [1-3] 快速执行：", end="", flush=True)
        
        key = getch()
        if key == '1':
            print("\033[H\033[J", end="")
            print("选择网页登录绑定地址：")
            print("  1. 仅允许本地 IPv4 登录 (127.0.0.1 - 更安全)")
            print("  2. 允许 IPv4 公网登录 (0.0.0.0)")
            print("  3. 允许 IPv4 & IPv6 双栈公网登录 (:: - 推荐)")
            print("  4. 仅允许本地 IPv6 登录 (::1)")
            sel = input("请选择 (1/2/3/4, 默认3): ").strip()
            if sel == '1':
                cfg['host'] = "127.0.0.1"
            elif sel == '2':
                cfg['host'] = "0.0.0.0"
            elif sel == '4':
                cfg['host'] = "::1"
            else:
                cfg['host'] = "::"
            save_ui_cfg(cfg)
            print(f"绑定地址已更新为: {cfg['host']}")
            if cfg['host'] in ("0.0.0.0", "::"):
                print("\033[1;33m[安全提示] 管理页面为明文 HTTP，公网绑定时账号密码可能被网络中间人截获。\033[0m")
                print("\033[1;33m           更安全的方式: 绑定 127.0.0.1 并通过 SSH 隧道访问，例如:\033[0m")
                print(f"\033[1;33m           ssh -L {cfg.get('port', 8787)}:127.0.0.1:{cfg.get('port', 8787)} root@服务器IP\033[0m")
            ask_restart()
            break
        elif key == '2':
            print("\033[H\033[J", end="")
            new_path = generate_random_suffix()
            cfg['secret_path'] = new_path
            save_ui_cfg(cfg)
            print("安全登录后缀已随机重置成功！")
            print(f"您的全新安全登录后缀为: {new_path}")
            display_host = cfg['host']
            if ":" in display_host:
                display_host = f"[{display_host}]"
            print(f"新的访问路径为: http://{display_host}:{cfg['port']}/{new_path}/")
            ask_restart()
            break
        elif key == '3' or key == 'q' or key == '\x03':
            break

def configure_port():
    cfg = load_ui_cfg()
    while True:
        print("\033[H\033[J", end="")
        print("=======================================================")
        print("                      端口配置菜单                     ")
        print("=======================================================")
        print(f"1) 网页管理端口: {cfg.get('port', 8787)}")
        print(f"2) 代理出站端口: {cfg.get('proxy_port', 7928)}")
        print("3) 返回主菜单")
        print("-------------------------------------------------------")
        key = input("请选择操作 (1-3): ").strip()
        if key == '1':
            try:
                val = input("请输入新的网页管理端口 (1-65535, 按回车取消): ").strip()
                if val:
                    port = int(val)
                    if 1 <= port <= 65535:
                        if port == int(cfg.get('proxy_port', 7928)):
                            print("错误: 网页管理端口不能与代理出站端口相同。")
                            time.sleep(2)
                            continue
                        cfg['port'] = port
                        save_ui_cfg(cfg)
                        print(f"网页管理端口已更新为: {port}")
                        ask_restart()
                    else:
                        print("错误: 端口范围必须在 1 至 65535 之间。")
                        time.sleep(2)
            except ValueError:
                print("错误: 输入必须是数字。")
                time.sleep(2)
        elif key == '2':
            try:
                val = input("请输入新的代理出站端口 (1024-65535, 按回车取消): ").strip()
                if val:
                    port = int(val)
                    if 1024 <= port <= 65535:
                        if port == int(cfg.get('port', 8787)):
                            print("错误: 代理出站端口不能与网页管理端口相同。")
                            time.sleep(2)
                            continue
                        cfg['proxy_port'] = port
                        save_ui_cfg(cfg)
                        print(f"代理出站端口已更新为: {port}")
                        ask_restart()
                    else:
                        print("错误: 端口范围必须在 1024 至 65535 之间。")
                        time.sleep(2)
            except ValueError:
                print("错误: 输入必须是数字。")
                time.sleep(2)
        elif key == '3' or key == 'q' or key == '\x03':
            break

def configure_credentials():
    cfg = load_ui_cfg()
    while True:
        print("\033[H\033[J", end="")
        print("=======================================================")
        print("                    管理账号密码管理                   ")
        print("=======================================================")
        curr_uname = cfg.get('username', '未配置')
        curr_pwd = cfg.get('password', '')
        masked_pwd = curr_pwd if len(curr_pwd) <= 4 else curr_pwd[:3] + "********" + curr_pwd[-2:]
        print(f"当前管理账号: {curr_uname}")
        print(f"当前管理密码: {masked_pwd}")
        print("  [1] 自定义修改账号密码")
        print("  [2] 随机重置安全密码")
        print("  [3] 返回主菜单")
        print("=======================================================")
        print("请直接输入数字键 [1-3] 快速执行：", end="", flush=True)
        
        key = getch()
        if key == '1':
            print("\033[H\033[J", end="")
            new_uname = input(f"请输入新管理账号 (回车默认 {curr_uname}): ").strip()
            if not new_uname:
                new_uname = curr_uname
            new_pwd = input("请输入新管理密码 (不能为空): ").strip()
            if not new_pwd:
                print("错误: 密码不能为空！")
                time.sleep(2)
                continue
            cfg['username'] = new_uname
            cfg['password'] = new_pwd
            save_ui_cfg(cfg)
            print("账号密码修改成功！")
            print(f"您的新管理账号: {new_uname}")
            print(f"您的新管理密码: {new_pwd}")
            input("\n按任意键返回菜单...")
        elif key == '2':
            print("\033[H\033[J", end="")
            new_pwd = generate_random_password()
            cfg['password'] = new_pwd
            save_ui_cfg(cfg)
            print("密码随机重置成功！")
            print(f"您的全新12位安全密码为: {new_pwd}")
            print("密码已保存在本地，不需要重启服务，刷新浏览器即可登录。")
            input("\n按任意键返回菜单...")
        elif key == '3' or key == 'q' or key == '\x03':
            break

def getch():
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        ch = sys.stdin.read(1)
        return ch if ch else "q"
    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch if ch else "q"

def getch_timeout(timeout=1.0):
    import select
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        try:
            r, _, _ = select.select([sys.stdin], [], [], timeout)
            if r:
                ch = sys.stdin.read(1)
                if not ch:
                    time.sleep(timeout)
                    return None
                return ch
        except Exception:
            time.sleep(timeout)
        return None
    try:
        tty.setraw(fd)
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if r:
            ch = sys.stdin.read(1)
            if not ch:
                return None
            return ch
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def get_status_state():
    cfg = load_ui_cfg()
    state = load_state()
    proxy_port = cfg.get("proxy_port", 7928)
    return (
        cfg.get("port", 8787),
        cfg.get("secret_path", "EJsW2EeBo9lY"),
        cfg.get("username", "未配置"),
        cfg.get("password", ""),
        cfg.get("host", "0.0.0.0"),
        state.get("is_connecting", False),
        state.get("active_openvpn_node_id", ""),
        state.get("last_check_message", ""),
        state.get("active_node_latency", ""),
        state.get("proxy_ip", "-"),
        state.get("proxy_latency_ms", 0),
        state.get("proxy_ok", False),
        check_port_listening(proxy_port),
        check_service_active("aimilivpn.service"),
        check_openvpn_process(),
        get_service_pid("aimilivpn.service")
    )

def main():
    if os.geteuid() != 0:
        print("错误: 必须以 root 权限运行此命令。")
        sys.exit(1)
        
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "start":
            start_service()
        elif cmd == "stop":
            stop_service()
        elif cmd == "restart":
            restart_service()
        elif cmd == "status":
            print("\033[?1049h\033[?25l\033[H\033[J", end="", flush=True)
            try:
                while True:
                    print("\033[H", end="")
                    print_status()
                    print_line("\n\033[1;33m提示: 当前为静态页面。按 [回车键/Enter] 手动刷新状态，按 [q] 或 [Ctrl+C] 退出...\033[0m")
                    print("\033[J", end="", flush=True)
                    
                    key = getch()
                    if key in ('q', 'Q', '\x03'):
                        break
                    if key in ('\r', '\n', '\x0a', '\x0d'):
                        continue
            except KeyboardInterrupt:
                pass
            finally:
                print("\033[?1049l\033[?25h", end="", flush=True)
        elif cmd == "logs":
            show_logs()
        elif cmd == "update":
            update_service()
        elif cmd == "uninstall":
            uninstall_service()
        elif cmd == "web":
            configure_web()
        elif cmd == "port":
            configure_port()
        elif cmd == "password":
            configure_credentials()
        else:
            print("未知命令。可用命令: start, stop, restart, status, logs, update, uninstall, web, port, password")
        sys.exit(0)
        
    options = {
        '1': ("启动服务 (ml start)", start_service),
        '2': ("停止服务 (ml stop)", stop_service),
        '3': ("重启服务 (ml restart)", restart_service),
        '4': ("日志监控 (ml logs)", show_logs),
        '5': ("网页配置 (ml web)", configure_web),
        '6': ("端口配置 (ml port)", configure_port),
        '7': ("账号密码 (ml password)", configure_credentials),
        '8': ("一键更新 (ml update)", update_service),
        '9': ("完全卸载 (ml uninstall)", uninstall_service),
        '0': ("退出终端", None)
    }
    
    # Enter alternate buffer and hide cursor
    print("\033[?1049h\033[?25l\033[H\033[J", end="", flush=True)
    try:
        need_redraw = True
        while True:
            if need_redraw:
                print("\033[H", end="")
                print_status()
                
                bold = "\033[1m"
                reset = "\033[0m"
                green = "\033[1;32m"
                
                print_line(f"【{bold}终端指令菜单栏{reset}】")
                for key in sorted(options.keys()):
                    if key == '0':
                        continue
                    name, _ = options[key]
                    print_line(f"  {green}[{key}]{reset} {name}")
                print_line(f"  {green}[0]{reset} {options['0'][0]}")
                print_line("=======================================================")
                print_line("提示: 当前为静态页面。按 [回车键/Enter] 手动刷新状态。")
                print("请直接输入数字键 [0-9] 快速选择执行：\033[K", end="", flush=True)
                print("\033[J", end="", flush=True)
                need_redraw = False
                
            try:
                key = getch()
            except KeyboardInterrupt:
                break
                
            if key == '\x03' or key == 'q' or key == 'Q':
                break
                
            if key == '0':
                break
                
            if key in ('\r', '\n', '\x0a', '\x0d'):
                need_redraw = True
                continue
                
            if key in options:
                name, func = options[key]
                if func is None:
                    break
                    
                # Temporarily restore normal terminal scrollback and show cursor
                print("\033[?1049l\033[?25h", end="", flush=True)
                print(f"正在执行: {name}...\n")
                
                try:
                    func()
                except Exception as e:
                    print(f"执行出错: {e}")
                    
                if func not in (start_service, stop_service, restart_service,
                                configure_web, configure_port, configure_credentials, show_logs, update_service):
                    input("\n操作已完成，按回车键返回主菜单...")
                    
                # Re-enter alternate buffer and hide cursor
                print("\033[?1049h\033[?25l\033[H\033[J", end="", flush=True)
                need_redraw = True
    finally:
        # Exit alternate buffer and show cursor on exit
        print("\033[?1049l\033[?25h", end="", flush=True)

if __name__ == "__main__":
    main()
