import os
import json
import secrets
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, render_template_string, session, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
import urllib.request
import urllib.parse
import time
import re
import concurrent.futures
import pytz
import sys

app = Flask(__name__)
CONFIG_FILE = 'config.json'
scheduler = None

# 强制所有 print 输出到 stderr
sys.stdout = sys.stderr

app.secret_key = secrets.token_hex(16)
app.permanent_session_lifetime = timedelta(minutes=30)

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return {"jobs": {}}

def save_config(config):
    if "timezone" not in config:
        config["timezone"] = "Asia/Shanghai"
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def create_scheduler(timezone):
    return BackgroundScheduler(
        jobstores={'default': MemoryJobStore()},
        executors={'default': ThreadPoolExecutor(10)},
        timezone=timezone
    )

def require_auth(f):
    def wrapper(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

def format_user_info(user_data):
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

def get_forward_rules(nya_host, token, device_groups_map):
    url = f"{nya_host.rstrip('/')}/api/v1/user/forward?page=1&size=100"
    req = urllib.request.Request(url, headers={"Authorization": token})
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
            "device_group_connect": dgi_connect,
        })
    return rules

def get_traffic_statistic(nya_host, token):
    url = f"{nya_host.rstrip('/')}/api/v1/user/statistic"
    req = urllib.request.Request(url, headers={"Authorization": token})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.load(res)
        if data.get("code") == 0:
            return data.get("data", {})
    except Exception as e:
        print(f"[Stat] 获取流量统计失败: {e}")
    return {}

def send_telegram_message(bot_token, chat_id, message):
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

def update_dns_record(cf_token, zone_id, name, ip):
    """
    更新 Cloudflare DNS A 记录。
    返回: (success: bool, message: str, changed: bool)
        - success: 操作是否成功（包括“已是最新”）
        - message: 日志信息
        - changed: IP 是否实际发生了变更（用于决定是否发通知）
    """
    try:
        # 查询现有 DNS 记录
        dns_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A&name={urllib.parse.quote(name)}"
        dns_req = urllib.request.Request(dns_url, headers={"Authorization": f"Bearer {cf_token}"})
        with urllib.request.urlopen(dns_req, timeout=30) as res:
            dns_data = json.load(res)
        
        if not (dns_data.get("success") and dns_data.get("result")):
            return False, f"未找到 DNS 记录: {name}", False

        record = dns_data["result"][0]
        current_ip = record.get("content", "")
        
        if current_ip == ip:
            return True, f"✓ {name} 已是最新的: {ip}", False

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
        with urllib.request.urlopen(update_req, timeout=30) as res:
            result = json.load(res)
        
        if result.get("success"):
            return True, f"已更新 {name} → {ip}", True
        else:
            errors = result.get("errors", "未知错误")
            return False, f"更新失败: {errors}", False

    except Exception as e:
        return False, f"异常: {e}", False

def run_job(job_id, job):
    tz = scheduler.timezone
    log_lines = []
    def log(msg):
        now = datetime.now(tz)
        line = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        log_lines.append(line)
        print(line)
    try:
        nya_host = job.get("nya_host", "https://nya.trp.sh").strip().rstrip("/")
        data = json.dumps({"username": job["username"], "password": job["password"]}).encode()
        req = urllib.request.Request(f"{nya_host}/api/v1/auth/login", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as res:
            token = json.load(res)["data"]
        log("登录成功")
        req_dev = urllib.request.Request(f"{nya_host}/api/v1/user/devicegroup", headers={"Authorization": token})
        with urllib.request.urlopen(req_dev, timeout=30) as res:
            dev_data = json.load(res)["data"]
        device_groups_map = {item["id"]: item for item in dev_data}
        req2 = urllib.request.Request(f"{nya_host}/api/v1/user/info", headers={"Authorization": token})
        with urllib.request.urlopen(req2, timeout=30) as res:
            user_info = json.load(res)["data"]
        
        stat_data = get_traffic_statistic(nya_host, token)
        traffic_today = stat_data.get("traffic_today", 0)
        traffic_yesterday = stat_data.get("traffic_yesterday", 0)
        def format_bytes(bytes_val):
            if bytes_val < 1024 ** 2:
                return f"{bytes_val / (1024**1):.2f} KiB"
            elif bytes_val < 1024 ** 3:
                return f"{bytes_val / (1024**2):.2f} MiB"
            else:
                return f"{bytes_val / (1024**3):.2f} GiB"

        stat_info = (
            f"今日流量：{format_bytes(traffic_today)}\n"
            f"昨日流量：{format_bytes(traffic_yesterday)}"
        )
        log("流量统计: " + stat_info.replace('\n', ' | '))
        formatted_info = format_user_info(user_info)
        full_user_info = formatted_info + "\n" + stat_info
        log("用户信息:")
        for line in formatted_info.split('\n'):
            log("  " + line)
        forward_rules = get_forward_rules(nya_host, token, device_groups_map)
        log(f"获取到 {len(forward_rules)} 条转发规则")

        # === 仅通过规则域名更新 DNS（无主域名）===
        cf_token = job.get("cf_token")
        if cf_token:
            log("开始 Cloudflare DNS 同步（仅规则域名）...")

            config_current = load_config()
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
                    rule_ip = dg["connect_host"].strip()
                    domains = rule_domains.get(rule_id, [])
                    if not domains:
                        continue
                    log(f"规则 {rule_id} 使用 IP {rule_ip}，更新域名: {', '.join(domains)}")
                    for domain_name in domains:
                        success, msg, changed = update_dns_record(cf_token, zone_id, domain_name, rule_ip)
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
                        f"时间: {datetime.now(scheduler.timezone).strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"详情:\n{details}"
                    )
                    if send_telegram_message(tg_token, tg_chat_id, msg):
                        log("Telegram 通知已发送")
                    else:
                        log("Telegram 通知发送失败")
        else:
            log("未配置 Cloudflare Token，跳过 DNS 更新")

        req4 = urllib.request.Request(f"{nya_host}/api/v1/auth/logout", method="POST", headers={"Authorization": token})
        urllib.request.urlopen(req4, timeout=5)
        log("已登出")

        config = load_config()
        if job_id in config["jobs"]:
            config["jobs"][job_id]["user_info"] = full_user_info
            config["jobs"][job_id]["forward_rules"] = forward_rules
            config["jobs"][job_id]["device_groups"] = dev_data
            config["jobs"][job_id]["last_log"] = "\n".join(log_lines)
            config["jobs"][job_id]["last_run"] = datetime.now(tz).isoformat()
            save_config(config)

    except Exception as e:
        log(f"错误: {str(e)}")
        config = load_config()
        if job_id in config["jobs"]:
            config["jobs"][job_id]["last_log"] = "\n".join(log_lines)
            save_config(config)

def start_scheduler():
    global scheduler
    config = load_config()
    tz_name = config.get("timezone", "Asia/Shanghai")
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.timezone("Asia/Shanghai")
    if scheduler is not None:
        if scheduler.running:
            scheduler.shutdown()
        scheduler = None
    scheduler = create_scheduler(tz)
    for job_id, job in config.get("jobs", {}).items():
        if job.get("enabled", True) and job.get("interval_minutes", 15) > 0:
            scheduler.add_job(
                func=run_job,
                trigger="interval",
                minutes=job["interval_minutes"],
                args=[job_id, job],
                id=job_id,
                replace_existing=True
            )
    if not scheduler.running:
        scheduler.start()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        config = load_config()
        auth_config = config.get("auth", {})
        stored_user = auth_config.get("username")
        stored_pass = auth_config.get("password")
        if stored_user and stored_pass and username == stored_user and password == stored_pass:
            session.permanent = True
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_login_page(error="用户名或密码错误")
    else:
        if 'logged_in' in session:
            return redirect(url_for('index'))
        return render_login_page()

def render_login_page(error=None):
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

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
@require_auth
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/config', methods=['GET'])
@require_auth
def get_config():
    config = load_config()
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

@app.route('/api/config', methods=['POST'])
@require_auth
def update_config():
    data = request.json
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    config = load_config()
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
        
        # ✅ 关键修复：始终保留 rule_domains（不管前端是否发送）
        job["rule_domains"] = orig_job.get("rule_domains", {})
        
        new_jobs[job_id] = job
    
    config["jobs"] = new_jobs
    save_config(config)
    start_scheduler()
    return jsonify({"status": "saved"})

@app.route('/api/run/<job_id>', methods=['POST'])
@require_auth
def trigger_run(job_id):
    config = load_config()
    if job_id not in config.get("jobs", {}):
        return jsonify({"error": "Job not found"}), 404
    job = config["jobs"][job_id]
    import threading
    threading.Thread(target=run_job, args=(job_id, job)).start()
    return jsonify({"status": "started"})

@app.route('/api/domains/<job_id>/<rule_id>', methods=['GET', 'POST', 'DELETE'])
@require_auth
def manage_rule_domains(job_id, rule_id):
    config = load_config()
    if job_id not in config.get("jobs", {}):
        return jsonify({"error": "Job not found"}), 404
    
    job = config["jobs"][job_id]
    if "rule_domains" not in job or not isinstance(job["rule_domains"], dict):
        job["rule_domains"] = {}
        save_config(config)
    
    if request.method == 'GET':
        rule_domains = job.get("rule_domains", {})
        if not isinstance(rule_domains, dict):
            rule_domains = {}
            job["rule_domains"] = rule_domains
            save_config(config)
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
        save_config(config)
        return jsonify({"status": "saved", "domains": domains})
    
    elif request.method == 'DELETE':
        if not isinstance(job.get("rule_domains"), dict):
            job["rule_domains"] = {}
        if rule_id in job["rule_domains"]:
            del job["rule_domains"][rule_id]
            save_config(config)
        return jsonify({"status": "deleted"})

if __name__ == '__main__':
    if not os.path.exists(CONFIG_FILE):
        initial_config = {
            "auth": {
                "username": "admin",
                "password": "change_this_password"
            },
            "timezone": "Asia/Shanghai",
            "jobs": {}
        }
        save_config(initial_config)
        print("✅ 已创建初始配置文件 config.json，请修改 auth 部分后重启！")
    start_scheduler()
    try:
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown()