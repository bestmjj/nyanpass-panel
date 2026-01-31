"""
Nyanpass 面板服务
提供对 Nyanpass 面板服务的监控和管理功能
"""

import os
import json
import secrets
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
import urllib.request
import urllib.parse
import re
import pytz
import sys
import ipaddress


class NyanpassPanel:
    """Nyanpass Panel 主类，封装了所有功能"""

    def __init__(self, config):
        """初始化 Nyanpass Panel 应用"""
        self.app = Flask(__name__)
        self.CONFIG_FILE = config
        self.scheduler = None
        
        # 强制所有 print 输出到 stderr
        sys.stdout = sys.stderr

        self.app.secret_key = secrets.token_hex(16)
        self.app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
        
        # 设置装饰器
        self.require_auth = self._create_auth_decorator()
        
        # 注册路由
        self._register_routes()

    def _register_routes(self):
        """注册 Flask 路由"""
        self.app.add_url_rule('/login', 'login', self.login, methods=['GET', 'POST'])
        self.app.add_url_rule('/logout', 'logout', self.logout)
        self.app.add_url_rule('/', 'index', self.require_auth(self.index))
        self.app.add_url_rule('/api/config', 'get_config', self.require_auth(self.get_config), methods=['GET'])
        self.app.add_url_rule('/api/config', 'update_config', self.require_auth(self.update_config), methods=['POST'])
        self.app.add_url_rule('/api/run/<job_id>', 'trigger_run', self.require_auth(self.trigger_run), methods=['POST'])
        self.app.add_url_rule('/api/domains/<job_id>/<rule_id>', 'manage_rule_domains', self.require_auth(self.manage_rule_domains), methods=['GET', 'POST', 'DELETE'])

    def _create_auth_decorator(self):
        """创建认证装饰器"""
        def decorator(f):
            def wrapper(*args, **kwargs):
                if 'logged_in' not in session:
                    return redirect(url_for('login'))
                return f(*args, **kwargs)
            wrapper.__name__ = f.__name__
            return wrapper
        return decorator

    def load_config(self):
        """加载配置文件"""
        if os.path.exists(self.CONFIG_FILE):
            with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {"jobs": {}}

    def save_config(self, config):
        """保存配置到文件"""
        #if "timezone" not in config:
        #    config["timezone"] = "Asia/Shanghai"
        with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def create_scheduler(self, timezone):
        """创建后台任务调度器"""
        return BackgroundScheduler(
            jobstores={'default': MemoryJobStore()},
            executors={'default': ThreadPoolExecutor(10)},
            timezone=timezone
        )

    def format_user_info(self, user_data):
        """格式化用户信息显示"""
        username = user_data.get("username", "未知")
        group_name = user_data.get("group_name", "未知")
        plan_name = user_data.get("plan_name", "未知")
        expire_ts = user_data.get("expire", 0)
        if expire_ts > 0:
            expire_dt = datetime.utcfromtimestamp(expire_ts / 1000.0)
            expire_str = expire_dt.strftime("%Y/%m/%d %H:%M:%S")
        else:
            expire_str = "永久有效"
        renew_price = user_data.get("renew_price", "0")
        GiB = 1024 ** 3
        traffic_used = user_data.get("traffic_used", 0) / GiB
        traffic_enable = user_data.get("traffic_enable", 1) / GiB
        max_rules = user_data.get("max_rules", 0)
        speed_bps = user_data.get("speed_limit", 0) 
        speed_mbps = round((speed_bps / 1_000_000)*8)
        balance = user_data.get("balance", "0")
        return (
            f"用户名：{username}\n"
            f"用户组：{group_name}\n"
            f"套餐：{plan_name}\n"
            f"套餐失效：{expire_str}\n"
            f"续费价格：{renew_price} 元\n"
            f"流量：{traffic_used:.2f} GiB / {traffic_enable:.2f} GiB\n"
            f"最大规则数：{max_rules}\n"
            f"速率限制：{speed_mbps} Mbps\n"
            f"钱包余额：{balance} 元"
        )

    def get_forward_rules(self, nya_host, token, device_groups_map):
        """获取转发规则列表"""
        url = f"{nya_host.rstrip('/')}/api/v1/user/forward?page=1&size=100"
        
        # 添加完整的请求头以模拟浏览器请求
        headers = {
            "Authorization": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json",
            "Origin": nya_host,
            "Referer": f"{nya_host}/",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                data = json.load(res)
            if data.get("code") != 0:
                raise Exception(f"获取转发规则失败: {data.get('msg', 'unknown')}")
            rules = []
            for item in data.get("data", []):
                try:
                    config = json.loads(item.get("config", "{}"))
                    dest_list = config.get("dest", [])
                    dest_str = ", ".join(dest_list)
                except:
                    dest_str = "解析失败"
                traffic_gib = item.get("traffic_used", 0) / (1024 ** 3)
                dgi = item.get("device_group_in")
                device_group_info = device_groups_map.get(dgi) if device_groups_map else None
                dgi_name = device_group_info["name"] if device_group_info else f"ID {dgi}"
                dgi_connect = device_group_info.get("connect_host", "") if device_group_info else ""
                rules.append({
                    "id": item["id"],
                    "name": item["name"],
                    "listen_port": item["listen_port"],
                    "dest": dest_str,
                    "status": item["status"],
                    "traffic_gib": round(traffic_gib, 2),
                    "updated_at": item.get("display_updated_at", ""),
                    "device_group_in": dgi,
                    "device_group_name": dgi_name,
                    "device_group_connect": dgi_connect
                })
            return rules
        except urllib.error.HTTPError as e:
            if e.code == 403:
                error_details = e.read().decode('utf-8')
                print(f"获取转发规则失败: HTTP 403 禁止访问，详情: {error_details}")
                raise Exception(f"获取转发规则失败: HTTP 403 禁止访问")
            else:
                print(f"获取转发规则失败: HTTP {e.code} {e.reason}")
                raise Exception(f"获取转发规则失败: HTTP {e.code} {e.reason}")
        except Exception as e:
            print(f"获取转发规则失败: {e}")
            raise Exception(f"获取转发规则失败: {e}")

    def get_traffic_statistic(self, nya_host, token):
        """获取流量统计数据"""
        url = f"{nya_host.rstrip('/')}/api/v1/user/statistic"
        
        # 添加完整的请求头以模拟浏览器请求
        headers = {
            "Authorization": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json",
            "Origin": nya_host,
            "Referer": f"{nya_host}/",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                data = json.load(res)
            if data.get("code") == 0:
                return data.get("data", {})
        except urllib.error.HTTPError as e:
            if e.code == 403:
                error_details = e.read().decode('utf-8')
                print(f"[Stat] 获取流量统计失败: HTTP 403 禁止访问，详情: {error_details}")
            else:
                print(f"[Stat] 获取流量统计失败: HTTP {e.code} {e.reason}")
        except Exception as e:
            print(f"[Stat] 获取流量统计失败: {e}")
        return {}

    def send_telegram_message(self, bot_token, chat_id, message):
        """发送 Telegram 消息通知"""
        if not bot_token or not chat_id:
            return False
        # ✅ 关键修复：去除首尾空白
        bot_token = bot_token.strip()
        chat_id = str(chat_id).strip()
        if not bot_token or not chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as res:
                result = json.load(res)
            return result.get("ok", False)
        except Exception as e:
            token_preview = bot_token.strip()[:10] + "..." if len(bot_token) > 10 else bot_token
            print(f"[Telegram] 发送失败 (token预览: {token_preview}): {e}")
            return False

    def update_dns_record(self, cf_token, zone_id, name, ip):
        """
        更新 Cloudflare DNS A 记录。
        返回: (success: bool, message: str, changed: bool)
            - success: 操作是否成功（包括"已是最新"）
            - message: 日志信息
            - changed: IP 是否实际发生了变更（用于决定是否发通知）
        """
        try:
            # 查询现有 DNS 记录
            dns_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A&name={urllib.parse.quote(name)}"
            dns_req = urllib.request.Request(dns_url, headers={"Authorization": f"Bearer {cf_token}"})
            try:
                with urllib.request.urlopen(dns_req, timeout=30) as res:
                    dns_data = json.load(res)
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    return False, f"CF API 403 Error: Check Cloudflare Token permissions", False
                else:
                    return False, f"CF API Error {e.code}: {e.reason}", False
            
            if not (dns_data.get("success") and dns_data.get("result")):
                return False, f"Could not find DNS record: {name}", False

            record = dns_data["result"][0]
            current_ip = record.get("content", "")
            
            if current_ip == ip:
                return True, f"✓ {name} is up to date: {ip}", False

            # IP 不同，执行更新
            update_data = json.dumps({
                "type": "A",
                "name": name,
                "content": ip,
                "ttl": 120,
                "proxied": False
            }).encode()
            update_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record['id']}"
            update_req = urllib.request.Request(
                update_url,
                data=update_data,
                method="PUT",
                headers={"Authorization": f"Bearer {cf_token}", "Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(update_req, timeout=30) as res:
                    result = json.load(res)
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    return False, f"Failed to update DNS record: 403 Forbidden, check Cloudflare Token permissions", False
                else:
                    error_body = getattr(e, 'read', lambda: b'Unknown error')()
                    try:
                        error_data = json.loads(error_body.decode('utf-8'))
                        errors = str(error_data)
                    except:
                        errors = str(error_body)
                    return False, f"Failed to update DNS record: HTTP {e.code} {e.reason}, Details: {errors}", False

            if result.get("success"):
                return True, f"Updated {name} -> {ip}", True
            else:
                errors = result.get("errors", "Unknown error")
                return False, f"Update failed: {errors}", False

        except Exception as e:
            return False, f"Exception: {e}", False
    def run_job(self, job_id, job):
        """
        执行定时任务的主要函数
        包括登录、获取用户信息、获取转发规则、更新DNS记录等操作
        """
        tz = self.scheduler.timezone
        log_lines = []
        def log(msg):
            now = datetime.now(tz)
            line = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
            log_lines.append(line)
            print(line)
        try:

            def login(host, username, password, headers):
                """登录面板"""
                data = json.dumps({"username": username, "password": password}).encode()
                
                # 添加更完整的浏览器样式请求头
                full_headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    "Accept": "application/json",
                    "Origin": job.get("nya_host", "https://nya.trp.sh").strip().rstrip("/"),
                    "Referer": f"{job.get('nya_host', 'https://nya.trp.sh').strip().rstrip('/')}/",
                    **headers  # 包含原始的headers
                }
                
                req = urllib.request.Request(host, data=data, headers=full_headers)
                try:
                    with urllib.request.urlopen(req, timeout=30) as res:
                        response_data = res.read().decode('utf-8')
                        response_json = json.loads(response_data)
                        
                        # 检查响应是否包含错误代码
                        if response_json.get("code") != 0:  # 假设0表示成功
                            error_code = response_json.get("code")
                            error_msg = response_json.get("message", "Unknown error")
                            raise Exception(f"登录失败: API返回错误代码 {error_code} - {error_msg}")
                            
                        token = response_json["data"]
                        return token
                except urllib.error.HTTPError as e:
                    if e.code == 403:
                        error_details = e.read().decode('utf-8')
                        log(f"Nyanpass面板返回HTTP 403错误，详情: {error_details}")
                        
                        # 检查是否是错误代码1010
                        if "1010" in error_details:
                            raise Exception(f"登录失败: API返回错误代码1010，这通常表示访问被拒绝，可能需要启用API访问权限或存在CSRF保护")
                        else:
                            raise Exception(f"登录失败: HTTP 403 禁止访问，可能是请求被防火墙或反机器人系统拦截")
                    elif e.code == 401:
                        raise Exception(f"登录失败: HTTP 401 认证失败，请检查用户名和密码是否正确")
                    else:
                        error_details = e.read().decode('utf-8')
                        raise Exception(f"Login failed: HTTP {e.code} {e.reason}, details: {error_details}")
            
            def get_device_groups(host, token):
                """获取设备组"""
                try:
                    # 添加完整的请求头以模拟浏览器请求
                    headers = {
                        "Authorization": token,
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                        "Accept": "application/json",
                        "Origin": job.get("nya_host", "https://nya.trp.sh").strip().rstrip("/"),
                        "Referer": f"{job.get('nya_host', 'https://nya.trp.sh').strip().rstrip('/')}/",
                    }
                    req_dev = urllib.request.Request(host, headers=headers)
                    with urllib.request.urlopen(req_dev, timeout=30) as res:
                        dev_data = json.load(res)["data"]
                    return dev_data
                except urllib.error.HTTPError as e:
                    if e.code == 403:
                        error_details = e.read().decode('utf-8')
                        log(f"获取设备组失败: HTTP 403 禁止访问，详情: {error_details}")
                        
                        # 检查是否是错误代码1010
                        if "1010" in error_details:
                            raise Exception(f"获取设备组失败: API返回错误代码1010，这通常表示访问被拒绝，可能需要启用API访问权限或存在CSRF保护")
                        else:
                            raise Exception(f"获取设备组失败: HTTP 403 禁止访问，API令牌可能权限不足或已过期")
                    else:
                        error_details = e.read().decode('utf-8')
                        raise Exception(f"获取设备组失败: HTTP {e.code} {e.reason}, details: {error_details}")

            def get_user_info(host, token):
                """获取用户信息"""
                try:
                    # 添加完整的请求头以模拟浏览器请求
                    headers = {
                        "Authorization": token,
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                        "Accept": "application/json",
                        "Origin": job.get("nya_host", "https://nya.trp.sh").strip().rstrip("/"),
                        "Referer": f"{job.get('nya_host', 'https://nya.trp.sh').strip().rstrip('/')}/",
                    }
                    req = urllib.request.Request(host, headers=headers)
                    with urllib.request.urlopen(req, timeout=30) as res:
                        user_info = json.load(res)["data"]
                    return user_info
                except urllib.error.HTTPError as e:
                    if e.code == 403:
                        error_details = e.read().decode('utf-8')
                        log(f"获取用户信息失败: HTTP 403 禁止访问，详情: {error_details}")
                        
                        # 检查是否是错误代码1010
                        if "1010" in error_details:
                            raise Exception(f"获取用户信息失败: API返回错误代码1010，这通常表示访问被拒绝，可能需要启用API访问权限或存在CSRF保护")
                        else:
                            raise Exception(f"获取用户信息失败: HTTP 403 禁止访问，API令牌可能权限不足或已过期")
                    else:
                        error_details = e.read().decode('utf-8')
                        raise Exception(f"获取用户信息失败: HTTP {e.code} {e.reason}, details: {error_details}")
            # 获取面板地址
            nya_host = job.get("nya_host", "https://nya.trp.sh").strip().rstrip("/")

            # 获取 API 路径
            api = "api/v1"
            # login 路径
            login_uri = f"{nya_host}/{api}/auth/login"
            # 设备组路径
            device_groups_uri = f"{nya_host}/{api}/user/devicegroup"
            # 用户信息路径
            user_info_uri = f"{nya_host}/{api}/user/info"

            # 登录面板
            headers = {"Content-Type": "application/json"}
            token = login(login_uri, job["username"], job["password"], headers)
            if not token:
                raise Exception(f"{nya_host} 登录失败")
            log(f"{nya_host.removeprefix('https://')} 登录成功")

            # 获取设备组
            dev_data = get_device_groups(device_groups_uri, token)
            device_groups_map = {item["id"]: item for item in dev_data}

            # 获取用户信息
            user_info = get_user_info(user_info_uri, token)
            
            # 获取流量统计
            stat_data = self.get_traffic_statistic(nya_host, token)
            # 今日流量统计
            traffic_today = stat_data.get("traffic_today", 0)
            # 昨日流量统计
            traffic_yesterday = stat_data.get("traffic_yesterday", 0)

            # 格式化字节单位显示
            def format_bytes(bytes_val):
                """格式化字节单位显示"""
                if bytes_val < 1024 ** 2:
                    return f"{bytes_val / (1024**1):.2f} KiB"
                elif bytes_val < 1024 ** 3:
                    return f"{bytes_val / (1024**2):.2f} MiB"
                else:
                    return f"{bytes_val / (1024**3):.2f} GiB"

            # 格式化流量信息
            stat_info = (
                f"今日流量：{format_bytes(traffic_today)}\n"
                f"昨日流量：{format_bytes(traffic_yesterday)}"
            )
            log("流量统计: " + stat_info.replace('\n', ' | '))
            # 格式化用户信息
            formatted_info = self.format_user_info(user_info)
            full_user_info = formatted_info + "\n" + stat_info
            log("用户信息:")
            for line in formatted_info.split('\n'):
                log("  " + line)
            # 获取转发规则
            forward_rules = self.get_forward_rules(nya_host, token, device_groups_map)
            log(f"获取到 {len(forward_rules)} 条转发规则")

            # === 仅通过规则域名更新 DNS（无主域名）===
            cf_token = job.get("cf_token")
            if cf_token:
                log("开始 Cloudflare DNS 同步（仅规则域名）...")

                config_current = self.load_config()
                job_current = config_current.get("jobs", {}).get(job_id, {})
                rule_domains_raw = job_current.get("rule_domains", {})
                if not isinstance(rule_domains_raw, dict):
                    log(f"警告: job 的 rule_domains 不是字典（类型: {type(rule_domains_raw)}），跳过 DNS 更新")
                    rule_domains = {}
                else:
                    rule_domains = rule_domains_raw
                all_domains = []
                for domains in rule_domains.values():
                    all_domains.extend(domains)
                
                if not all_domains:
                    log("无规则域名，跳过 DNS 更新")
                else:
                    sample_domain = all_domains[0]
                    parts = sample_domain.split('.')
                    zone_name = '.'.join(parts[-2:]) if len(parts) >= 2 else sample_domain

                    try:
                        zone_url = f"https://api.cloudflare.com/client/v4/zones?name={urllib.parse.quote(zone_name)}"
                        zone_req = urllib.request.Request(zone_url, headers={"Authorization": f"Bearer {cf_token}"})
                        with urllib.request.urlopen(zone_req, timeout=30) as res:
                            zone_data = json.load(res)
                        if zone_data.get("success") and zone_data["result"]:
                            zone_id = zone_data["result"][0]["id"]
                            log(f"Zone: {zone_name}, ID: {zone_id}")
                        else:
                            log(f"未找到 Zone ID for {zone_name}")
                            zone_id = None
                    except Exception as e:
                        log(f"获取 Zone ID 失败: {e}")
                        zone_id = None

                if zone_id:
                    updated_records = []
                    for rule in forward_rules:
                        rule_id = str(rule["id"])
                        dgi = rule.get("device_group_in")
                        if dgi is None:
                            continue
                        dg = device_groups_map.get(dgi)
                        if not dg or not dg.get("connect_host"):
                            log(f"规则 {rule_id} 的设备组 {dgi} 无 connect_host，跳过")
                            continue

                        # 创建正则表达式，匹配 ipv4 地址
                        # 匹配 IPv4 地址的正则表达式
                        ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
                        ips = dg["connect_host"].strip()
                        # 查找第一个匹配的ip
                        for candidate in re.findall(ip_pattern, ips):
                            try:
                                rule_ip = str(ipaddress.IPv4Address(candidate))
                                #print(str(ip))
                                break
                            except ipaddress.AddressValueError:
                                continue
                        # rule_ip = dg["connect_host"].strip()
                        domains = rule_domains.get(rule_id, [])
                        if not domains:
                            continue
                        log(f"规则 {rule_id} 使用 IP {rule_ip}，更新域名: {', '.join(domains)}")
                        for domain_name in domains:
                            success, msg, changed = self.update_dns_record(cf_token, zone_id, domain_name, rule_ip)
                            log(f"  → {msg}")
                            if changed:
                                updated_records.append((domain_name, rule_ip))

                    if updated_records and job.get("telegram_bot_token") and job.get("telegram_chat_id"):
                        tg_token = job["telegram_bot_token"]
                        tg_chat_id = job["telegram_chat_id"]
                        unique_updates = {}
                        for name, ip in updated_records:
                            unique_updates[name] = ip
                        items = list(unique_updates.items())[:10]
                        details = "\n".join([f"  • <code>{name}</code> → {ip}" for name, ip in items])
                        if len(unique_updates) > 10:
                            details += f"\n  • ... 等共 {len(unique_updates)} 个记录"
                        msg = (
                            f"⚠️ <b>IEPL DNS 已更新</b>\n"
                            f"时间: {datetime.now(self.scheduler.timezone).strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"详情:\n{details}"
                        )
                        if self.send_telegram_message(tg_token, tg_chat_id, msg):
                            log("Telegram 通知已发送")
                        else:
                            log("Telegram 通知发送失败")
            else:
                log("未配置 Cloudflare Token，跳过 DNS 更新")

            req_logout = urllib.request.Request(
                f"{nya_host}/api/v1/auth/logout", 
                method="POST", 
                headers={
                    "Authorization": token,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    "Accept": "application/json",
                    "Origin": nya_host,
                    "Referer": f"{nya_host}/",
                }
            )
            try:
                urllib.request.urlopen(req_logout, timeout=5)
                log("已登出")
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    log("登出失败: HTTP 403 禁止访问，可能因API令牌权限问题")
                else:
                    log(f"登出失败: HTTP {e.code} {e.reason}")
            except Exception as e:
                log(f"登出时出现其他错误: {str(e)}")

            config = self.load_config()
            if job_id in config["jobs"]:
                config["jobs"][job_id]["user_info"] = full_user_info
                config["jobs"][job_id]["forward_rules"] = forward_rules
                config["jobs"][job_id]["device_groups"] = dev_data
                config["jobs"][job_id]["last_log"] = "\n".join(log_lines)
                config["jobs"][job_id]["last_run"] = datetime.now(tz).isoformat()
                self.save_config(config)

        except Exception as e:
            log(f"错误: {str(e)}")
            config = self.load_config()
            if job_id in config["jobs"]:
                config["jobs"][job_id]["last_log"] = "\n".join(log_lines)
                self.save_config(config)

    def start_scheduler(self):
        """启动任务调度器"""
        config = self.load_config()
        tz_name = config.get("timezone", "Asia/Shanghai")
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.timezone("Asia/Shanghai")
        if self.scheduler is not None:
            if self.scheduler.running:
                self.scheduler.shutdown()
            self.scheduler = None
        self.scheduler = self.create_scheduler(tz)
        for job_id, job in config.get("jobs", {}).items():
            if job.get("enabled", True) and job.get("interval_minutes", 15) > 0:
                self.scheduler.add_job(
                    func=self.run_job,
                    trigger="interval",
                    minutes=job["interval_minutes"],
                    args=[job_id, job],
                    id=job_id,
                    replace_existing=True
                )
        if not self.scheduler.running:
            self.scheduler.start()

    def login(self):
        """处理用户登录请求"""
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            config = self.load_config()
            auth_config = config.get("auth", {})
            stored_user = auth_config.get("username")
            stored_pass = auth_config.get("password")
            if stored_user and stored_pass and username == stored_user and password == stored_pass:
                session.permanent = True
                session['logged_in'] = True
                return redirect(url_for('index'))
            else:
                return self.render_login_page(error="用户名或密码错误")
        else:
            if 'logged_in' in session:
                return redirect(url_for('index'))
            return self.render_login_page()

    def render_login_page(self, error=None):
        """渲染登录页面"""
        error_html = f'<div class="error">{error}</div>' if error else ''
        html = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>登录 - IEPL 配置面板</title>
            <style>
                body {{ 
                    font-family: system-ui; 
                    background: #f5f5f5; 
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    height: 100vh; 
                    margin: 0; 
                }}
                .login-box {{ 
                    background: white; 
                    padding: 30px; 
                    border-radius: 8px; 
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
                    width: 320px; 
                }}
                .login-box h2 {{ 
                    margin-top: 0; 
                    color: #333; 
                }}
                input {{ 
                    width: 100%; 
                    padding: 10px; 
                    margin: 8px 0; 
                    border: 1px solid #ddd; 
                    border-radius: 4px; 
                    box-sizing: border-box; 
                }}
                button {{ 
                    width: 100%; 
                    padding: 10px; 
                    background: #0d6efd; 
                    color: white; 
                    border: none; 
                    border-radius: 4px; 
                    cursor: pointer; 
                    font-size: 16px; 
                }}
                button:hover {{ 
                    background: #0b5ed7; 
                }}
                .error {{ 
                    color: #dc3545; 
                    margin: 10px 0; 
                }}
            </style>
        </head>
        <body>
            <div class="login-box">
                <h2>🔐 登录</h2>
                {error_html}
                <form method="post">
                    <input type="text" name="username" placeholder="用户名" required autofocus>
                    <input type="password" name="password" placeholder="密码" required>
                    <button type="submit">登录</button>
                </form>
            </div>
        </body>
        </html>
        '''
        return html

    def logout(self):
        """用户登出"""
        session.pop('logged_in', None)
        return redirect(url_for('login'))

    def index(self):
        """主页路由"""
        return send_from_directory('static', 'index.html')

    def get_config(self):
        """获取配置信息 API"""
        config = self.load_config()
        safe_jobs = {}
        for k, v in config.get("jobs", {}).items():
            safe_jobs[k] = {**v}
            if "password" in safe_jobs[k]: safe_jobs[k]["password"] = "********"
            if "cf_token" in safe_jobs[k]: safe_jobs[k]["cf_token"] = "********"
            if "telegram_bot_token" in safe_jobs[k]: safe_jobs[k]["telegram_bot_token"] = "********"
        return jsonify({
            "auth": {
                "username": config.get("auth", {}).get("username", ""),
                "password": "********" if config.get("auth", {}).get("password") else ""
            },
            "timezone": config.get("timezone", "Asia/Shanghai"),
            "jobs": safe_jobs
        })

    def update_config(self):
        """更新配置信息 API"""
        data = request.json
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400
        config = self.load_config()
        if "auth" in data:
            config["auth"] = data["auth"]
        if "timezone" in data:
            config["timezone"] = data["timezone"]
        
        orig_jobs = config.get("jobs", {})
        new_jobs = {}
        for job_id, job in data.get("jobs", {}).items():
            orig_job = orig_jobs.get(job_id, {})
            
            # 恢复敏感字段
            if job.get("password") == "********":
                job["password"] = orig_job.get("password", "")
            if job.get("cf_token") == "********":
                job["cf_token"] = orig_job.get("cf_token", "")
            if job.get("telegram_bot_token") == "********":
                job["telegram_bot_token"] = orig_job.get("telegram_bot_token", "")
            
            #  关键修复：始终保留 rule_domains（不管前端是否发送）
            job["rule_domains"] = orig_job.get("rule_domains", {})
            
            new_jobs[job_id] = job
        
        config["jobs"] = new_jobs
        self.save_config(config)
        self.start_scheduler()
        return jsonify({"status": "saved"})

    def trigger_run(self, job_id):
        """手动触发运行指定任务"""
        config = self.load_config()
        if job_id not in config.get("jobs", {}):
            return jsonify({"error": "Job not found"}), 404
        job = config["jobs"][job_id]
        import threading
        threading.Thread(target=self.run_job, args=(job_id, job)).start()
        return jsonify({"status": "started"})

    def manage_rule_domains(self, job_id, rule_id):
        """管理规则对应的域名"""
        config = self.load_config()
        if job_id not in config.get("jobs", {}):
            return jsonify({"error": "Job not found"}), 404
        
        job = config["jobs"][job_id]
        if "rule_domains" not in job or not isinstance(job["rule_domains"], dict):
            job["rule_domains"] = {}
            self.save_config(config)
        
        if request.method == 'GET':
            rule_domains = job.get("rule_domains", {})
            if not isinstance(rule_domains, dict):
                rule_domains = {}
                job["rule_domains"] = rule_domains
                self.save_config(config)
            domains = rule_domains.get(rule_id, [])
            return jsonify({"domains": domains})
        
        elif request.method == 'POST':
            data = request.json
            if not data or "domains" not in data:
                return jsonify({"error": "Invalid data"}), 400
            domains = data["domains"]
            if not isinstance(domains, list):
                return jsonify({"error": "domains must be a list"}), 400
            domain_re = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z]{2,})+$')
            invalid = [d for d in domains if not isinstance(d, str) or not domain_re.match(d)]
            if invalid:
                return jsonify({"error": "invalid domains", "invalid": invalid}), 400
            if len(domains) > 500:
                return jsonify({"error": "too many domains"}), 400
            if not isinstance(job.get("rule_domains"), dict):
                job["rule_domains"] = {}
            job["rule_domains"][rule_id] = domains
            self.save_config(config)
            return jsonify({"status": "saved", "domains": domains})
        
        elif request.method == 'DELETE':
            if not isinstance(job.get("rule_domains"), dict):
                job["rule_domains"] = {}
            if rule_id in job["rule_domains"]:
                del job["rule_domains"][rule_id]
                self.save_config(config)
            return jsonify({"status": "deleted"})

    def initialize_config(self):
        """初始化配置文件"""
        # 判断配置文件是否存在，不存在则初始化
        if not os.path.exists(self.CONFIG_FILE):
            # 生成随机初始用户名、密码
            initial_username = "" + secrets.token_hex(6)
            initial_password = secrets.token_urlsafe(16)
            # 默认时区
            timezone = "Asia/Shanghai"
            print(f"  未找到 {self.CONFIG_FILE}，已创建初始配置文件 {self.CONFIG_FILE}，请修改 auth 部分后重启！")
            initial_config = {
                "auth": {
                "username": initial_username,
                "password": initial_password
                },
                "timezone": timezone,
                "jobs": {}
            }
            self.save_config(initial_config)
            print(" 已创建初始配置文件 config.json，请修改 auth 部分后重启！")

    def run(self):
        """运行应用"""
        self.initialize_config()
        self.start_scheduler()
        try:
            self.app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
        except KeyboardInterrupt:
            print("Shutting down...")
        finally:
            if self.scheduler is not None and self.scheduler.running:
                self.scheduler.shutdown()
