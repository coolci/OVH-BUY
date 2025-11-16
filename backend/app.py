import os
import time
import json
import logging
import uuid
import threading
import shutil
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import ovh
from ovh.exceptions import APIError as OvhAPIError
import re
import traceback
import requests
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

# 加载 .env 文件
load_dotenv()

# 导入API认证中间件
from api_auth_middleware import init_api_auth

# 导入服务器监控器
from server_monitor import ServerMonitor

# Data storage directories
DATA_DIR = "data"
CACHE_DIR = "cache"
LOGS_DIR = "logs"

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Configure logging with UTF-8 encoding to support emoji and Unicode characters
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "app.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Set UTF-8 encoding for StreamHandler (All platforms)
import sys
# Force UTF-8 encoding for console output on all platforms
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

app = Flask(__name__)
# Enable CORS for all routes with API security headers support
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "allow_headers": ["Content-Type", "Authorization", "X-API-Key", "X-Request-Time"]
    }
})

# 初始化API密钥验证
init_api_auth(app)

# Data storage files (organized in data directory)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOGS_FILE = os.path.join(DATA_DIR, "logs.json")
QUEUE_FILE = os.path.join(DATA_DIR, "queue.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
SERVERS_FILE = os.path.join(DATA_DIR, "servers.json")
SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "subscriptions.json")
CONFIG_SNIPER_FILE = os.path.join(DATA_DIR, "config_sniper_tasks.json")
VPS_SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "vps_subscriptions.json")

config = {
    "appKey": "",
    "appSecret": "",
    "consumerKey": "",
    "endpoint": "ovh-eu",
    "tgToken": "",
    "tgChatId": "",
    "iam": "go-ovh-ie",
    "zone": "IE",
}

logs = []
queue = []
purchase_history = []
server_plans = []
stats = {
    "activeQueues": 0,
    "totalServers": 0,
    "availableServers": 0,
    "purchaseSuccess": 0,
    "purchaseFailed": 0,
    "queueProcessorRunning": True,  # 队列处理器状态（守护线程，始终运行）
    "monitorRunning": False  # 监控器运行状态
}

# 服务器列表缓存
server_list_cache = {
    "data": [],
    "timestamp": None,
    "cache_duration": 2 * 60 * 60  # 缓存2小时
}

# 自动刷新缓存的后台线程标志
auto_refresh_running = False

# 初始化监控器（需要在函数定义后才能传入函数引用）
monitor = None

# 全局删除任务ID集合（用于立即停止后台线程处理）
deleted_task_ids = set()

# 配置绑定狙击任务
config_sniper_tasks = []
config_sniper_running = False

# VPS 监控相关
vps_subscriptions = []
vps_monitor_running = False
vps_monitor_thread = None
vps_check_interval = 60  # VPS检查间隔（秒）

# Load data from files if they exist
def load_data():
    global config, logs, queue, purchase_history, server_plans, stats, config_sniper_tasks, vps_subscriptions, vps_check_interval
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except json.JSONDecodeError:
            print(f"警告: {CONFIG_FILE}文件格式不正确，使用默认值")
    
    if os.path.exists(LOGS_FILE):
        try:
            with open(LOGS_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:  # 确保文件不是空的
                    logs = json.loads(content)
                    original_count = len(logs)
                    # 限制日志数量为1000条（保留最新的）
                    if len(logs) > 1000:
                        logs = logs[-1000:]
                        # 立即保存限制后的日志回文件
                        try:
                            with open(LOGS_FILE, 'w', encoding='utf-8') as f:
                                json.dump(logs, f, ensure_ascii=False, indent=2)
                            print(f"日志文件已限制为1000条（原{original_count}条）")
                        except Exception as e:
                            print(f"警告: 保存限制后的日志文件失败: {e}")
                else:
                    print(f"警告: {LOGS_FILE}文件为空，使用空列表")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"警告: {LOGS_FILE}文件格式不正确或编码错误，使用空列表: {e}")
    
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:  # 确保文件不是空的
                    queue = json.loads(content)
                else:
                    print(f"警告: {QUEUE_FILE}文件为空，使用空列表")
        except json.JSONDecodeError:
            print(f"警告: {QUEUE_FILE}文件格式不正确，使用空列表")
    
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:  # 确保文件不是空的
                    purchase_history = json.loads(content)
                else:
                    print(f"警告: {HISTORY_FILE}文件为空，使用空列表")
        except json.JSONDecodeError:
            print(f"警告: {HISTORY_FILE}文件格式不正确，使用空列表")
    
    if os.path.exists(SERVERS_FILE):
        try:
            with open(SERVERS_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:  # 确保文件不是空的
                    server_plans = json.loads(content)
                    # 将文件数据同步到缓存
                    server_list_cache["data"] = server_plans
                    server_list_cache["timestamp"] = time.time()
                    print(f"已从文件加载 {len(server_plans)} 台服务器，并同步到缓存")
                else:
                    print(f"警告: {SERVERS_FILE}文件为空，使用空列表")
        except json.JSONDecodeError:
            print(f"警告: {SERVERS_FILE}文件格式不正确，使用空列表")
    
    # 加载订阅数据（需要monitor已初始化）
    if os.path.exists(SUBSCRIPTIONS_FILE):
        try:
            with open(SUBSCRIPTIONS_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    subscriptions_data = json.loads(content)
                    # 确保monitor已初始化
                    if monitor is None:
                        print(f"警告: monitor未初始化，跳过订阅数据加载")
                    else:
                        # 恢复订阅到监控器
                        if 'subscriptions' in subscriptions_data:
                            for sub in subscriptions_data['subscriptions']:
                                monitor.add_subscription(
                                    sub['planCode'],
                                    sub.get('datacenters', []),
                                    sub.get('notifyAvailable', True),
                                    sub.get('notifyUnavailable', False),
                                    sub.get('serverName'),  # 恢复服务器名称
                                    sub.get('lastStatus', {}),  # ✅ 恢复上次状态，避免重复通知
                                    sub.get('history', []),  # ✅ 恢复历史记录
                                    sub.get('autoOrder', False),  # ✅ 恢复自动下单标记
                                    sub.get('quantity', 1)  # ✅ 恢复下单数量，默认为1
                                )
                        # 恢复已知服务器列表
                        if 'known_servers' in subscriptions_data:
                            monitor.known_servers = set(subscriptions_data['known_servers'])
                        # 检查间隔全局强制为5秒（忽略配置文件中的值）
                        monitor.check_interval = 5
                        print(f"检查间隔已强制设置为: 5秒（全局固定值）")
                        print(f"已加载 {len(monitor.subscriptions)} 个订阅")
                else:
                    print(f"警告: {SUBSCRIPTIONS_FILE}文件为空")
        except json.JSONDecodeError:
            print(f"警告: {SUBSCRIPTIONS_FILE}文件格式不正确")
    
    # 加载配置绑定狙击任务
    if os.path.exists(CONFIG_SNIPER_FILE):
        try:
            with open(CONFIG_SNIPER_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    config_sniper_tasks.clear()
                    config_sniper_tasks.extend(json.loads(content))
                    print(f"已加载 {len(config_sniper_tasks)} 个配置绑定狙击任务")
                else:
                    print(f"警告: {CONFIG_SNIPER_FILE}文件为空")
        except json.JSONDecodeError:
            print(f"警告: {CONFIG_SNIPER_FILE}文件格式不正确")
    
    # 加载VPS订阅数据
    if os.path.exists(VPS_SUBSCRIPTIONS_FILE):
        try:
            with open(VPS_SUBSCRIPTIONS_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    vps_subscriptions.clear()
                    vps_subscriptions.extend(data.get('subscriptions', []))
                    vps_check_interval = data.get('check_interval', 60)
                    print(f"已加载 {len(vps_subscriptions)} 个VPS订阅")
                else:
                    print(f"警告: {VPS_SUBSCRIPTIONS_FILE}文件为空")
        except json.JSONDecodeError:
            print(f"警告: {VPS_SUBSCRIPTIONS_FILE}文件格式不正确")
    
    # Update stats
    update_stats()
    
    logging.info("Data loaded from files")

# Save data to files
def save_data():
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        flush_logs()  # 使用批量刷新函数
        with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(purchase_history, f, ensure_ascii=False, indent=2)
        with open(SERVERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(server_plans, f, ensure_ascii=False, indent=2)
        logging.info("Data saved to files")
    except Exception as e:
        logging.error(f"保存数据时出错: {str(e)}")
        print(f"保存数据时出错: {str(e)}")
        # 尝试单独保存每个文件
        try_save_file(CONFIG_FILE, config)
        try_save_file(LOGS_FILE, logs)
        try_save_file(QUEUE_FILE, queue)
        try_save_file(HISTORY_FILE, purchase_history)
        try_save_file(SERVERS_FILE, server_plans)

# 尝试保存单个文件
def try_save_file(filename, data):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"成功保存 {filename}")
    except Exception as e:
        print(f"保存 {filename} 时出错: {str(e)}")

# 保存配置绑定狙击任务
def save_config_sniper_tasks():
    try:
        with open(CONFIG_SNIPER_FILE, 'w', encoding='utf-8') as f:
            json.dump(config_sniper_tasks, f, indent=2, ensure_ascii=False)
        logging.info(f"已保存 {len(config_sniper_tasks)} 个配置绑定狙击任务")
    except Exception as e:
        logging.error(f"保存配置狙击任务时出错: {str(e)}")

# 保存VPS订阅数据
def save_vps_subscriptions():
    try:
        data = {
            'subscriptions': vps_subscriptions,
            'check_interval': vps_check_interval
        }
        with open(VPS_SUBSCRIPTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logging.info(f"已保存 {len(vps_subscriptions)} 个VPS订阅")
    except Exception as e:
        logging.error(f"保存VPS订阅时出错: {str(e)}")

# 日志缓冲区：批量写入以提高性能
log_write_counter = 0
LOG_WRITE_THRESHOLD = 10  # 每10条日志写一次文件

# Add a log entry
def add_log(level, message, source="system"):
    global logs, log_write_counter
    log_entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "message": message,
        "source": source
    }
    logs.append(log_entry)
    
    # Keep logs at a reasonable size (last 1000 entries)
    if len(logs) > 1000:
        logs = logs[-1000:]
    
    # 批量写入：每N条或ERROR级别立即写入
    log_write_counter += 1
    should_write = (log_write_counter >= LOG_WRITE_THRESHOLD) or (level == "ERROR")
    
    if should_write:
        try:
            # 确保写入文件时日志数量不超过1000条
            logs_to_write = logs[-1000:] if len(logs) > 1000 else logs
            with open(LOGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(logs_to_write, f, ensure_ascii=False, indent=2)
            # 如果日志被截断，更新内存中的logs
            if len(logs) > 1000:
                logs = logs_to_write
            log_write_counter = 0
        except Exception as e:
            logging.error(f"写入日志文件失败: {str(e)}")
    
    # Also print to console
    if level == "ERROR":
        logging.error(f"[{source}] {message}")
    elif level == "WARNING":
        logging.warning(f"[{source}] {message}")
    else:
        logging.info(f"[{source}] {message}")

# 强制写入所有日志到文件
def flush_logs():
    global logs, log_write_counter
    try:
        # 确保日志数量不超过1000条
        if len(logs) > 1000:
            logs = logs[-1000:]
        with open(LOGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
        log_write_counter = 0
        logging.info("日志已强制刷新到文件")
    except Exception as e:
        logging.error(f"强制写入日志文件失败: {str(e)}")

# Update statistics
def update_stats():
    global stats, monitor
    # 活跃队列 = 所有未完成的队列项（running + pending），不包括已完成或失败的
    active_count = sum(1 for item in queue if item["status"] in ["running", "pending", "paused"])
    available_count = 0
    
    # Count available servers
    for server in server_plans:
        for dc in server["datacenters"]:
            if dc["availability"] not in ["unavailable", "unknown"]:
                available_count += 1
                break
    
    success_count = sum(1 for item in purchase_history if item["status"] == "success")
    failed_count = sum(1 for item in purchase_history if item["status"] == "failed")
    
    # 检查监控器运行状态
    monitor_running = False
    if monitor:
        monitor_running = monitor.running
    
    stats = {
        "activeQueues": active_count,
        "totalServers": len(server_plans),
        "availableServers": available_count,
        "purchaseSuccess": success_count,
        "purchaseFailed": failed_count,
        "queueProcessorRunning": True,  # 队列处理器作为守护线程始终运行
        "monitorRunning": monitor_running  # 监控器实际运行状态
    }

# Helper: 根据endpoint配置获取API基础URL
def get_api_base_url():
    """
    根据用户的endpoint配置返回对应的API基础URL
    
    Returns:
        str: API基础URL (如 https://api.us.ovhcloud.com)
    """
    endpoint_urls = {
        'ovh-eu': 'https://eu.api.ovh.com',
        'ovh-us': 'https://api.us.ovhcloud.com',
        'ovh-ca': 'https://ca.api.ovh.com'
    }
    return endpoint_urls.get(config.get('endpoint', 'ovh-eu'), 'https://eu.api.ovh.com')

# Initialize OVH client
def get_ovh_client():
    global config
    if not config["appKey"] or not config["appSecret"] or not config["consumerKey"]:
        add_log("ERROR", "Missing OVH API credentials")
        return None
    
    try:
        client = ovh.Client(
            endpoint=config["endpoint"],
            application_key=config["appKey"],
            application_secret=config["appSecret"],
            consumer_key=config["consumerKey"]
        )
        return client
    except Exception as e:
        add_log("ERROR", f"Failed to initialize OVH client: {str(e)}")
        return None

# 监控器专用：获取所有配置组合的可用性
def check_server_availability_with_configs(plan_code):
    """
    获取服务器所有配置组合的可用性（用于监控器）
    
    返回格式：
    {
        "config_key": {
            "memory": "ram-64g",
            "storage": "softraid-2x4000sa",
            "datacenters": {"gra": "available", "rbx": "unavailable", ...}
        },
        ...
    }
    """
    client = get_ovh_client()
    if not client:
        return {}
    
    try:
        add_log("INFO", f"[配置监控] 查询 {plan_code} 的所有配置组合...", "monitor")
        availabilities = client.get('/dedicated/server/datacenter/availabilities', planCode=plan_code)
        
        if not availabilities or len(availabilities) == 0:
            add_log("WARNING", f"[配置监控] 未获取到 {plan_code} 的可用性数据", "monitor")
            return {}
        
        add_log("INFO", f"[配置监控] OVH API 返回 {len(availabilities)} 个配置组合", "monitor")
        
        # 构建配置级别的可用性数据
        result = {}
        for item in availabilities:
            memory = item.get("memory", "N/A")
            storage = item.get("storage", "N/A")
            fqn = item.get("fqn", "")
            
            # 使用 fqn 作为唯一key
            config_key = fqn
            
            # 收集该配置在各个数据中心的可用性
            datacenters = {}
            for dc in item.get("datacenters", []):
                dc_name = dc.get("datacenter")
                availability = dc.get("availability", "unknown")
                
                if dc_name:
                    datacenters[dc_name] = availability
            
            # 尝试查找匹配的API2选项代码（用于价格查询）
            api2_options = []
            try:
                # 使用standardize_config查找匹配的选项
                memory_std = standardize_config(memory) if memory != "N/A" else None
                storage_std = standardize_config(storage) if storage != "N/A" else None
                
                if memory_std or storage_std:
                    # 查找catalog中匹配的选项
                    catalog = client.get(f'/order/catalog/public/eco?ovhSubsidiary={config["zone"]}')
                    for plan in catalog.get("plans", []):
                        if plan.get("planCode") == plan_code:
                            addon_families = plan.get("addonFamilies", [])
                            
                            for family in addon_families:
                                family_name = family.get("name", "").lower()
                                addons = family.get("addons", [])
                                
                                if family_name == "memory" and memory_std:
                                    for addon in addons:
                                        if standardize_config(addon) == memory_std:
                                            if addon not in api2_options:
                                                api2_options.append(addon)
                                
                                elif family_name == "storage" and storage_std:
                                    for addon in addons:
                                        if standardize_config(addon) == storage_std:
                                            if addon not in api2_options:
                                                api2_options.append(addon)
                            break  # 找到匹配的plan后退出
            except Exception as e:
                add_log("WARNING", f"[配置监控] 查找API2选项代码失败: {str(e)}", "monitor")
            
            result[config_key] = {
                "memory": memory,
                "storage": storage,
                "datacenters": datacenters,
                "fqn": fqn,
                "options": api2_options  # 添加匹配的API2选项代码
            }
            
            add_log("INFO", f"[配置监控] 配置: {memory} + {storage}, 数据中心数: {len(datacenters)}", "monitor")
        
        add_log("INFO", f"[配置监控] 成功获取 {len(result)} 个配置组合的可用性", "monitor")
        return result
        
    except Exception as e:
        add_log("ERROR", f"[配置监控] 获取配置可用性失败: {str(e)}", "monitor")
        add_log("ERROR", f"错误详情: {traceback.format_exc()}", "monitor")
        return {}

# Check availability of servers
def check_server_availability(plan_code, options=None):
    client = get_ovh_client()
    if not client:
        return None
    
    try:
        # 调用OVH API获取所有配置组合的可用性
        # planCode 原样传递给 OVH API（包括 -v1 等后缀）
        add_log("INFO", f"查询 {plan_code} 的可用性...")
        availabilities = client.get('/dedicated/server/datacenter/availabilities', planCode=plan_code)
        
        # 记录 OVH API 返回的数据
        add_log("INFO", f"OVH API 返回 {len(availabilities) if availabilities else 0} 个配置组合")
        if availabilities and len(availabilities) > 0:
            fqn_list = [item.get('fqn', 'N/A') for item in availabilities[:3]]  # 只记录前3个
            add_log("INFO", f"配置示例: {fqn_list}")
        
        # 如果没有返回数据
        if not availabilities or len(availabilities) == 0:
            add_log("WARNING", f"未获取到 {plan_code} 的可用性数据")
            return {}
        
        # 如果用户选择了自定义配置，需要精确匹配
        if options and len(options) > 0:
            add_log("INFO", f"查询 {plan_code} 的配置选项可用性: {options}")
            
            # 从 options 中提取内存和存储配置
            memory_option = None
            storage_option = None
            
            for opt in options:
                opt_lower = opt.lower()
                
                # 匹配内存配置
                if 'ram-' in opt_lower or 'memory' in opt_lower:
                    memory_option = opt
                    add_log("INFO", f"识别内存配置: {opt}")
                # 匹配存储配置
                elif 'softraid-' in opt_lower or 'hybrid' in opt_lower or 'disk' in opt_lower or 'nvme' in opt_lower or 'raid' in opt_lower:
                    storage_option = opt
                    add_log("INFO", f"识别存储配置: {opt}")
            
            add_log("INFO", f"提取配置 - 内存: {memory_option}, 存储: {storage_option}")
            
            # 遍历所有配置组合，找到匹配的
            matched_config = None
            for item in availabilities:
                item_memory = item.get("memory")
                item_storage = item.get("storage")
                item_fqn = item.get("fqn")
                
                add_log("INFO", f"检查配置: {item_fqn}")
                add_log("INFO", f"  OVH内存: {item_memory}, OVH存储: {item_storage}")
                
                # 匹配逻辑：需要处理型号后缀
                # 前端传递：ram-16g-24skstor01
                # OVH返回：ram-16g
                # 匹配：前端值.startswith(OVH值)
                
                memory_match = True
                if memory_option:
                    if item_memory:
                        # 提取关键部分进行匹配
                        # 前端：ram-16g-24skstor01 -> ram-16g
                        # OVH：ram-16g-ecc-2133 -> ram-16g
                        # 策略：提取前两段（如 ram-16g）进行比较
                        
                        user_memory_parts = memory_option.split('-')[:2]  # ['ram', '16g']
                        ovh_memory_parts = item_memory.split('-')[:2]     # ['ram', '16g']
                        
                        user_memory_key = '-'.join(user_memory_parts)  # 'ram-16g'
                        ovh_memory_key = '-'.join(ovh_memory_parts)    # 'ram-16g'
                        
                        memory_match = (user_memory_key == ovh_memory_key)
                        add_log("INFO", f"  内存匹配: '{memory_option}' ({user_memory_key}) vs '{item_memory}' ({ovh_memory_key}) = {memory_match}")
                    else:
                        memory_match = False
                        add_log("INFO", f"  内存匹配: OVH无内存字段 = False")
                else:
                    # 用户没有选择内存配置，允许任何内存
                    memory_match = True
                    add_log("INFO", f"  内存匹配: 用户未选内存，允许匹配 = True")
                
                storage_match = True
                if storage_option:
                    if item_storage:
                        # 对于存储，直接使用前缀匹配（因为存储格式比较一致）
                        # 前端：hybridsoftraid-4x4000sa-1x500nvme-24skstor
                        # OVH：hybridsoftraid-4x4000sa-1x500nvme
                        storage_match = storage_option.startswith(item_storage)
                        add_log("INFO", f"  存储匹配: '{storage_option}'.startswith('{item_storage}') = {storage_match}")
                    else:
                        storage_match = False
                        add_log("INFO", f"  存储匹配: OVH无存储字段 = False")
                else:
                    # 用户没有选择存储配置，允许任何存储
                    storage_match = True
                    add_log("INFO", f"  存储匹配: 用户未选存储，允许匹配 = True")
                
                add_log("INFO", f"  最终匹配结果: memory={memory_match}, storage={storage_match}")
                
                if memory_match and storage_match:
                    matched_config = item
                    add_log("INFO", f"✅ 找到匹配配置: {item_fqn}")
                    break
                else:
                    add_log("INFO", f"❌ 不匹配，继续下一个")
            
            # 如果找到匹配的配置
            if matched_config:
                result = {}
                for dc in matched_config.get("datacenters", []):
                    datacenter_name = dc.get("datacenter")
                    availability = dc.get("availability", "unknown")
                    
                    if datacenter_name:
                        if not availability or availability == "unknown":
                            result[datacenter_name] = "unknown"
                        elif availability == "unavailable":
                            result[datacenter_name] = "unavailable"
                        else:
                            result[datacenter_name] = availability

                add_log("INFO", f"配置 {matched_config.get('fqn')} 的可用性: {result}")
                return result
            else:
                # 没找到匹配的配置
                add_log("WARNING", f"❌ 未找到匹配的配置组合！请求: {options}")
                add_log("INFO", f"可用的配置组合: {[item.get('fqn') for item in availabilities]}")
                return {}
        
        else:
            # 没有指定配置，返回第一个（默认配置）
            default_config = availabilities[0]
            default_fqn = default_config.get("fqn")
            add_log("INFO", f"使用默认配置: {default_fqn}")
            
            result = {}
            for dc in default_config.get("datacenters", []):
                datacenter_name = dc.get("datacenter")
                availability = dc.get("availability", "unknown")
                
                if datacenter_name:
                    if not availability or availability == "unknown":
                        result[datacenter_name] = "unknown"
                    elif availability == "unavailable":
                        result[datacenter_name] = "unavailable"
                    else:
                        result[datacenter_name] = availability
            
            add_log("INFO", f"默认配置 {default_fqn} 的可用性: {result}")
            return result
            
    except Exception as e:
        add_log("ERROR", f"Failed to check availability for {plan_code}: {str(e)}")
        add_log("ERROR", f"Traceback: {traceback.format_exc()}")
        return None
# Purchase server
def purchase_server(queue_item):
    client = get_ovh_client()
    if not client:
        return False
    
    cart_id = None # Initialize cart_id to None
    item_id = None # Initialize item_id to None
    
    try:
        # Check availability first
        add_log("INFO", f"开始为 {queue_item['planCode']} 在 {queue_item['datacenter']} 的购买流程，选项: {queue_item.get('options')}", "purchase")
        availabilities = client.get('/dedicated/server/datacenter/availabilities', planCode=queue_item["planCode"])
        
        found_available = False
        for item in availabilities:
            datacenters = item.get("datacenters", [])
            
            for dc_info in datacenters:
                # 转换数据中心代码以便比较（API返回的是OVH API代码，如ynm）
                api_dc_from_queue = _convert_display_dc_to_api_dc(queue_item["datacenter"])
                if dc_info.get("datacenter") == api_dc_from_queue and dc_info.get("availability") not in ["unavailable", "unknown"]:
                    found_available = True
                    break
            
            if found_available:
                break
        
        if not found_available:
            add_log("INFO", f"服务器 {queue_item['planCode']} 在数据中心 {queue_item['datacenter']} 当前无货", "purchase")
            # Even if not available, we might want to record this attempt in history if it's the first one
            # For now, returning False will prevent history update here, purchase_server is called in a loop by queue processor
            return False
        
        # Create cart
        add_log("INFO", f"为区域 {config['zone']} 创建购物车", "purchase")
        cart_result = client.post('/order/cart', ovhSubsidiary=config["zone"])
        cart_id = cart_result["cartId"]
        add_log("INFO", f"购物车创建成功，ID: {cart_id}", "purchase")
        
        # Add base item to cart using /eco endpoint
        add_log("INFO", f"添加基础商品 {queue_item['planCode']} 到购物车 (使用 /eco)", "purchase")
        item_payload = {
            "planCode": queue_item["planCode"],
            "pricingMode": "default",
            "duration": "P1M",  # 1 month
            "quantity": 1
        }
        item_result = client.post(f'/order/cart/{cart_id}/eco', **item_payload)
        item_id = item_result["itemId"] # This is the itemId for the base server
        add_log("INFO", f"基础商品添加成功，项目 ID: {item_id}", "purchase")
        
        # Configure item (datacenter, OS, region)
        add_log("INFO", f"为项目 {item_id} 设置必需配置", "purchase")
        # 转换数据中心代码（前端显示代码 → OVH API代码）
        api_datacenter = _convert_display_dc_to_api_dc(queue_item["datacenter"])
        dc_lower = api_datacenter.lower()
        region = None
        EU_DATACENTERS = ['gra', 'rbx', 'sbg', 'eri', 'lim', 'waw', 'par', 'fra', 'lon']
        CANADA_DATACENTERS = ['bhs']
        US_DATACENTERS = ['vin', 'hil']
        APAC_DATACENTERS = ['syd', 'sgp', 'ynm']  # ynm是孟买的OVH API代码 

        if any(dc_lower.startswith(prefix) for prefix in EU_DATACENTERS): region = "europe"
        elif any(dc_lower.startswith(prefix) for prefix in CANADA_DATACENTERS): region = "canada"
        elif any(dc_lower.startswith(prefix) for prefix in US_DATACENTERS): region = "usa"
        elif any(dc_lower.startswith(prefix) for prefix in APAC_DATACENTERS): region = "apac"

        configurations_to_set = {
            "dedicated_datacenter": api_datacenter,  # 使用转换后的数据中心代码
            "dedicated_os": "none_64.en" 
        }
        if region:
            configurations_to_set["region"] = region
        else:
            add_log("WARNING", f"无法为数据中心 {dc_lower} 推断区域，可能导致配置失败", "purchase")
            try:
                required_configs_list = client.get(f'/order/cart/{cart_id}/item/{item_id}/requiredConfiguration')
                if any(conf.get("label") == "region" and conf.get("required") for conf in required_configs_list):
                    raise Exception("必需的区域配置无法确定。")
            except Exception as rc_err:
                 add_log("WARNING", f"获取必需配置失败或区域为必需但未确定: {rc_err}", "purchase")

        for label, value in configurations_to_set.items():
            if value is None: continue
            add_log("INFO", f"配置项目 {item_id}: 设置必需项 {label} = {value}", "purchase")
            client.post(f'/order/cart/{cart_id}/item/{item_id}/configuration',
                       label=label,
                       value=str(value))
            add_log("INFO", f"成功设置必需项: {label} = {value}", "purchase")

        user_requested_options = queue_item.get("options", [])
        if user_requested_options:
            add_log("INFO", f"📦 处理用户请求的硬件选项（{len(user_requested_options)}个）: {user_requested_options}", "purchase")
            filtered_hardware_options = []
            for option_plan_code in user_requested_options:
                if not option_plan_code or not isinstance(option_plan_code, str):
                    add_log("WARNING", f"跳过无效的选项值: {option_plan_code}", "purchase")
                    continue
                opt_lower = option_plan_code.lower()
                if any(skip_term in opt_lower for skip_term in [
                    "windows-server", "sql-server", "cpanel-license", "plesk-",
                    "-license-", "os-", "control-panel", "panel", "license", "security"
                ]):
                    add_log("INFO", f"跳过非硬件/许可证选项: {option_plan_code}", "purchase")
                    continue
                filtered_hardware_options.append(option_plan_code)
            
            if filtered_hardware_options:
                add_log("INFO", f"过滤后的硬件选项计划代码: {filtered_hardware_options}", "purchase")
                try:
                    add_log("INFO", f"获取购物车 {cart_id} 中与基础商品 {queue_item['planCode']} 兼容的 Eco 硬件选项...", "purchase")
                    available_eco_options = client.get(f'/order/cart/{cart_id}/eco/options', planCode=queue_item['planCode'])
                    add_log("INFO", f"找到 {len(available_eco_options)} 个可用的 Eco 硬件选项。", "purchase")
                    added_options_count = 0
                    for wanted_option_plan_code in filtered_hardware_options:
                        option_added_successfully = False
                        for avail_opt in available_eco_options:
                            avail_opt_plan_code = avail_opt.get("planCode")
                            if not avail_opt_plan_code:
                                continue
                            if avail_opt_plan_code == wanted_option_plan_code:
                                add_log("INFO", f"找到匹配的 Eco 选项: {avail_opt_plan_code} (匹配用户请求: {wanted_option_plan_code})", "purchase")
                                try:
                                    option_payload_eco = {
                                        "itemId": item_id, 
                                        "planCode": avail_opt_plan_code, 
                                        "duration": avail_opt.get("duration", "P1M"),
                                        "pricingMode": avail_opt.get("pricingMode", "default"),
                                        "quantity": 1
                                    }
                                    add_log("INFO", f"准备添加 Eco 选项: {option_payload_eco}", "purchase")
                                    client.post(f'/order/cart/{cart_id}/eco/options', **option_payload_eco)
                                    add_log("INFO", f"成功添加 Eco 选项: {avail_opt_plan_code} 到购物车 {cart_id}", "purchase")
                                    added_options_count += 1
                                    option_added_successfully = True
                                    break 
                                except ovh.exceptions.APIError as add_opt_error:
                                    add_log("WARNING", f"添加 Eco 选项 {avail_opt_plan_code} 失败: {add_opt_error}", "purchase")
                                except Exception as general_add_opt_error:
                                    add_log("WARNING", f"添加 Eco 选项 {avail_opt_plan_code} 时发生未知错误: {general_add_opt_error}", "purchase")
                        if not option_added_successfully:
                             add_log("WARNING", f"用户请求的硬件选项 {wanted_option_plan_code} 未在可用Eco选项中找到或添加失败。", "purchase")
                    add_log("INFO", f"共成功添加 {added_options_count} 个硬件选项。", "purchase")
                except ovh.exceptions.APIError as get_opts_error:
                    add_log("ERROR", f"获取 Eco 硬件选项列表失败: {get_opts_error}", "purchase")
                except Exception as e:
                    add_log("ERROR", f"处理 Eco 硬件选项时发生未知错误: {e}", "purchase")
            else:
                add_log("INFO", "用户未请求有效的硬件选项，或所有请求的选项都是非硬件类型。", "purchase")
        else:
            add_log("INFO", "⚠️ 用户未提供任何硬件选项，将使用默认配置下单", "purchase")

        add_log("INFO", f"绑定购物车 {cart_id}", "purchase")
        client.post(f'/order/cart/{cart_id}/assign')
        add_log("INFO", "购物车绑定成功", "purchase")
        
        # 获取购物车摘要以提取价格信息
        price_info = None
        try:
            add_log("INFO", f"获取购物车 {cart_id} 摘要以提取价格信息", "purchase")
            cart_summary = client.get(f'/order/cart/{cart_id}/summary')
            
            if cart_summary and isinstance(cart_summary, dict):
                prices_field = cart_summary.get("prices")
                if isinstance(prices_field, dict):
                    with_tax_obj = prices_field.get("withTax")
                    without_tax_obj = prices_field.get("withoutTax")
                    tax_obj = prices_field.get("tax")
                    
                    # 提取货币代码
                    currency_code = None
                    if isinstance(with_tax_obj, dict):
                        currency_code = with_tax_obj.get("currencyCode")
                    if not currency_code:
                        currency_code = prices_field.get("currencyCode", "EUR")
                    
                    # 安全提取价格值
                    with_tax = None
                    without_tax = None
                    tax = None
                    
                    if with_tax_obj is not None:
                        with_tax = with_tax_obj.get("value") if isinstance(with_tax_obj, dict) else with_tax_obj
                    if without_tax_obj is not None:
                        without_tax = without_tax_obj.get("value") if isinstance(without_tax_obj, dict) else without_tax_obj
                    if tax_obj is not None:
                        tax = tax_obj.get("value") if isinstance(tax_obj, dict) else tax_obj
                    
                    if with_tax is not None or without_tax is not None:
                        price_info = {
                            "withTax": with_tax,
                            "withoutTax": without_tax,
                            "tax": tax,
                            "currencyCode": currency_code
                        }
                        add_log("INFO", f"成功提取价格信息: 含税={with_tax} {currency_code}, 不含税={without_tax} {currency_code}", "purchase")
        except Exception as price_error:
            add_log("WARNING", f"获取价格信息时出错: {str(price_error)}，将继续结账流程", "purchase")
        
        add_log("INFO", f"对购物车 {cart_id} 执行结账", "purchase")
        checkout_payload = {
            "autoPayWithPreferredPaymentMethod": False, 
            "waiveRetractationPeriod": True
        }
        checkout_result = client.post(f'/order/cart/{cart_id}/checkout', **checkout_payload)
        
        order_id_val = checkout_result.get("orderId", "")
        order_url_val = checkout_result.get("url", "")
        
        # Update or create purchase history entry for SUCCESS
        existing_history_entry = next((h for h in purchase_history if h.get("taskId") == queue_item["id"]), None)
        current_time_iso = datetime.now().isoformat()

        if existing_history_entry:
            existing_history_entry["status"] = "success"
            existing_history_entry["orderId"] = order_id_val
            existing_history_entry["orderUrl"] = order_url_val
            existing_history_entry["errorMessage"] = None # Clear previous error on success
            existing_history_entry["purchaseTime"] = current_time_iso
            existing_history_entry["attemptCount"] = queue_item["retryCount"]
            existing_history_entry["options"] = queue_item.get("options", [])
            if price_info:
                existing_history_entry["price"] = price_info
            add_log("INFO", f"更新抢购历史(成功) 任务ID: {queue_item['id']}", "purchase")
        else:
            history_entry = {
                "id": str(uuid.uuid4()),
                "taskId": queue_item["id"],
                "planCode": queue_item["planCode"],
                "datacenter": queue_item["datacenter"],
                "options": queue_item.get("options", []),
                "status": "success",
                "orderId": order_id_val,
                "orderUrl": order_url_val,
                "errorMessage": None,
                "purchaseTime": current_time_iso,
                "attemptCount": queue_item["retryCount"]
            }
            if price_info:
                history_entry["price"] = price_info
            purchase_history.append(history_entry)
            add_log("INFO", f"创建抢购历史(成功) 任务ID: {queue_item['id']}", "purchase")
        
        save_data()
        update_stats()
        
        add_log("INFO", f"成功购买 {queue_item['planCode']} 在 {queue_item['datacenter']} (订单ID: {order_id_val}, URL: {order_url_val})", "purchase")

        # 发送 Telegram 成功通知
        if config.get("tgToken") and config.get("tgChatId"):
            success_message = (
                f"🎉 OVH 服务器抢购成功！🎉\n\n"
                f"服务器型号 (Plan Code): {queue_item['planCode']}\n"
                f"数据中心: {queue_item['datacenter']}\n"
                f"订单 ID: {order_id_val}\n"
                f"订单链接: {order_url_val}\n"
            )
            options_list = queue_item.get("options", [])
            if options_list:
                options_str = ", ".join(options_list)
                success_message += f"自定义配置: {options_str}\n"
            
            success_message += f"\n抢购任务ID: {queue_item['id']}"
            
            send_telegram_msg(success_message)
            add_log("INFO", f"已为订单 {order_id_val} 发送 Telegram 成功通知。", "purchase")
        else:
            add_log("INFO", "未配置 Telegram Token 或 Chat ID，跳过成功通知发送。", "purchase")

        return True
    
    except ovh.exceptions.APIError as api_e:
        error_msg = str(api_e)
        add_log("ERROR", f"购买 {queue_item['planCode']} 时发生 OVH API 错误: {error_msg}", "purchase")
        if cart_id: add_log("ERROR", f"错误发生时的购物车ID: {cart_id}", "purchase")
        if item_id: add_log("ERROR", f"错误发生时的基础商品ID: {item_id}", "purchase")
        
        # Update or create purchase history entry for API FAILURE
        existing_history_entry = next((h for h in purchase_history if h.get("taskId") == queue_item["id"]), None)
        current_time_iso = datetime.now().isoformat()

        if existing_history_entry:
            existing_history_entry["status"] = "failed"
            existing_history_entry["orderId"] = None
            existing_history_entry["orderUrl"] = None
            existing_history_entry["errorMessage"] = error_msg
            existing_history_entry["purchaseTime"] = current_time_iso
            existing_history_entry["attemptCount"] = queue_item["retryCount"]
            existing_history_entry["options"] = queue_item.get("options", [])
            add_log("INFO", f"更新抢购历史(API失败) 任务ID: {queue_item['id']}", "purchase")
        else:
            history_entry = {
                "id": str(uuid.uuid4()),
                "taskId": queue_item["id"],
                "planCode": queue_item["planCode"],
                "datacenter": queue_item["datacenter"],
                "options": queue_item.get("options", []),
                "status": "failed",
                "orderId": None,
                "orderUrl": None,
                "errorMessage": error_msg,
                "purchaseTime": current_time_iso,
                "attemptCount": queue_item["retryCount"]
            }
            purchase_history.append(history_entry)
            add_log("INFO", f"创建抢购历史(API失败) 任务ID: {queue_item['id']}", "purchase")

        save_data()
        update_stats()
        return False

    except Exception as e:
        error_msg = str(e)
        add_log("ERROR", f"购买 {queue_item['planCode']} 时发生未知错误: {error_msg}", "purchase")
        add_log("ERROR", f"完整错误堆栈: {traceback.format_exc()}", "purchase")
        if cart_id: add_log("ERROR", f"错误发生时的购物车ID: {cart_id}", "purchase")
        if item_id: add_log("ERROR", f"错误发生时的基础商品ID: {item_id}", "purchase")

        # Update or create purchase history entry for GENERAL FAILURE
        existing_history_entry = next((h for h in purchase_history if h.get("taskId") == queue_item["id"]), None)
        current_time_iso = datetime.now().isoformat()

        if existing_history_entry:
            existing_history_entry["status"] = "failed"
            existing_history_entry["orderId"] = None
            existing_history_entry["orderUrl"] = None
            existing_history_entry["errorMessage"] = error_msg
            existing_history_entry["purchaseTime"] = current_time_iso
            existing_history_entry["attemptCount"] = queue_item["retryCount"]
            existing_history_entry["options"] = queue_item.get("options", [])
            add_log("INFO", f"更新抢购历史(通用失败) 任务ID: {queue_item['id']}", "purchase")
        else:
            history_entry = {
                "id": str(uuid.uuid4()),
                "taskId": queue_item["id"],
                "planCode": queue_item["planCode"],
                "datacenter": queue_item["datacenter"],
                "options": queue_item.get("options", []),
                "status": "failed",
                "orderId": None,
                "orderUrl": None,
                "errorMessage": error_msg,
                "purchaseTime": current_time_iso,
                "attemptCount": queue_item["retryCount"]
            }
            purchase_history.append(history_entry)
            add_log("INFO", f"创建抢购历史(通用失败) 任务ID: {queue_item['id']}", "purchase")
        
        save_data()
        update_stats()
        return False

# Process queue items
def process_queue():
    global deleted_task_ids
    while True:
        # 在循环开始时检查队列是否为空
        if not queue:
            # 队列为空时，清理已完成的删除标记（避免内存泄漏）
            if deleted_task_ids:
                deleted_task_ids.clear()
                add_log("DEBUG", "队列为空，清理删除标记集合", "queue")
            time.sleep(1)
            continue
        
        # ✅ 清理已从队列移除的删除标记（避免内存泄漏）
        current_queue_ids = {item["id"] for item in queue}
        removed_ids = deleted_task_ids - current_queue_ids
        if removed_ids:
            deleted_task_ids -= removed_ids
            add_log("DEBUG", f"清理 {len(removed_ids)} 个已从队列移除的删除标记", "queue")
            
        # 复制一份并按优先级排序：quickOrder 优先，其次按创建时间
        items_to_process = sorted(
            list(queue),
            key=lambda it: (
                0 if it.get("quickOrder") else 1,                 # quickOrder 优先
                -int(datetime.fromisoformat(it.get("createdAt")).timestamp()) if it.get("createdAt") else 0  # 越新越先
            )
        )
        for item in items_to_process:
            # 优先检查：任务是否在删除集合中（前端删除时立即生效）
            if item["id"] in deleted_task_ids:
                add_log("INFO", f"任务 {item['id']} 已被标记为删除，跳过处理", "queue")
                continue
            
            # 次要检查：在处理前检查项目是否仍在原始队列中（通过ID检查）
            item_still_exists = any(q_item["id"] == item["id"] for q_item in queue)
            if not item_still_exists:
                add_log("INFO", f"任务 {item['id']} 已从队列中移除，跳过处理", "queue")
                # 添加到删除集合，避免重复处理
                deleted_task_ids.add(item["id"])
                continue
            
            if item["status"] == "running":
                current_time = time.time()
                last_check_time = item.get("lastCheckTime", 0)
                
                # 如果是首次尝试 (lastCheckTime为0) 或者到达重试间隔
                if last_check_time == 0 or (current_time - last_check_time >= item["retryInterval"]):
                    # 最后检查：任务是否被标记删除
                    if item["id"] in deleted_task_ids:
                        add_log("INFO", f"任务 {item['id']} 在执行前被标记删除", "queue")
                        continue
                    
                    # 再次检查任务是否还在队列中（可能在等待期间被删除）
                    if not any(q_item["id"] == item["id"] for q_item in queue):
                        add_log("INFO", f"任务 {item['id']} 在处理前被移除", "queue")
                        deleted_task_ids.add(item["id"])
                        continue
                    
                    if last_check_time == 0:
                        add_log("INFO", f"首次尝试任务 {item['id']}: {item['planCode']} 在 {item['datacenter']}", "queue")
                    else:
                        add_log("INFO", f"重试检查任务 {item['id']} (尝试次数: {item['retryCount'] + 1}): {item['planCode']} 在 {item['datacenter']}", "queue")
                    
                    # 更新检查时间和重试计数
                    item["lastCheckTime"] = current_time
                    item["retryCount"] += 1
                    item["updatedAt"] = datetime.now().isoformat()
                    
                    # 尝试购买
                    if purchase_server(item):
                        item["status"] = "completed"
                        item["updatedAt"] = datetime.now().isoformat()
                        log_message_verb = "首次尝试购买成功" if item["retryCount"] == 1 else f"重试购买成功 (尝试次数: {item['retryCount']})"
                        add_log("INFO", f"{log_message_verb}: {item['planCode']} 在 {item['datacenter']} (ID: {item['id']})", "queue")
                    else:
                        log_message_verb = "首次尝试购买失败或服务器暂无货" if item["retryCount"] == 1 else f"重试购买失败或服务器仍无货 (尝试次数: {item['retryCount']})"
                        add_log("INFO", f"{log_message_verb}: {item['planCode']} 在 {item['datacenter']} (ID: {item['id']})。将根据重试间隔再次尝试。", "queue")
                    
                    save_data() # 保存队列状态
                    update_stats() # 更新统计信息
        
        time.sleep(1) # 每秒检查一次队列

# Start queue processing thread
def start_queue_processor():
    thread = threading.Thread(target=process_queue)
    thread.daemon = True
    thread.start()

# 自动刷新缓存的后台线程
def auto_refresh_cache_loop():
    """自动刷新服务器列表缓存（每2小时）"""
    global auto_refresh_running, server_list_cache, server_plans
    
    auto_refresh_running = True
    add_log("INFO", "服务器列表自动刷新已启动（每2小时更新一次）", "auto_refresh")
    
    while auto_refresh_running:
        try:
            # 每2小时刷新一次
            time.sleep(2 * 60 * 60)  # 2小时
            
            # 检查是否配置了API
            if not get_ovh_client():
                add_log("WARNING", "未配置API，跳过自动刷新", "auto_refresh")
                continue
            
            add_log("INFO", "开始自动刷新服务器列表...", "auto_refresh")
            
            # 从API加载服务器列表
            api_servers = load_server_list()
            
            if api_servers and len(api_servers) > 0:
                # 更新缓存和全局变量
                server_plans = api_servers
                server_list_cache["data"] = api_servers
                server_list_cache["timestamp"] = time.time()
                save_data()
                update_stats()
                
                add_log("INFO", f"自动刷新完成：已更新 {len(server_plans)} 台服务器", "auto_refresh")
            else:
                add_log("WARNING", "自动刷新失败：API返回空数据", "auto_refresh")
                
        except Exception as e:
            add_log("ERROR", f"自动刷新缓存时出错: {str(e)}", "auto_refresh")
            add_log("ERROR", f"完整错误堆栈: {traceback.format_exc()}", "auto_refresh")

# Start auto refresh cache thread
def start_auto_refresh_cache():
    """启动自动刷新缓存的线程"""
    global auto_refresh_running
    
    # 防止重复启动
    if auto_refresh_running:
        add_log("WARNING", "自动刷新缓存已在运行，跳过重复启动", "auto_refresh")
        return
    
    thread = threading.Thread(target=auto_refresh_cache_loop)
    thread.daemon = True
    thread.start()
    add_log("INFO", "自动刷新缓存线程已启动", "auto_refresh")
# Load server list from OVH API
def load_server_list():
    global config
    client = get_ovh_client()
    if not client:
        return []
    
    try:
        # 保存完整的API原始响应
        try:
            # 尝试获取并保存原始目录响应
            catalog = client.get(f'/order/catalog/public/eco?ovhSubsidiary={config["zone"]}')
            with open(os.path.join(CACHE_DIR, "ovh_catalog_raw.json"), "w", encoding='utf-8') as f:
                json.dump(catalog, f, ensure_ascii=False, indent=2)
            add_log("INFO", "已保存完整的API原始响应")
        except Exception as e:
            add_log("WARNING", f"保存API原始响应时出错: {str(e)}")
        
        # Get server models
        catalog = client.get(f'/order/catalog/public/eco?ovhSubsidiary={config["zone"]}')
        plans = []
        
        # 创建一个计数器，记录硬件信息提取成功的服务器数量
        hardware_info_counter = {
            "total": 0,
            "cpu_success": 0,
            "memory_success": 0,
            "storage_success": 0,
            "bandwidth_success": 0
        }
        
        for plan in catalog.get("plans", []):
            plan_code = plan.get("planCode")
            if not plan_code:
                continue
            
            hardware_info_counter["total"] += 1
            
            # Get availability
            availabilities = client.get('/dedicated/server/datacenter/availabilities', planCode=plan_code)
            datacenters = []
            
            for item in availabilities:
                for dc in item.get("datacenters", []):
                    datacenters.append({
                        "datacenter": dc.get("datacenter"),
                        "availability": dc.get("availability", "unknown")
                    })
            
            # 添加数据中心的名称和区域信息
            for dc in datacenters:
                dc_code = dc.get("datacenter", "").lower()[:3]  # 取前三个字符作为数据中心代码
                
                # 根据代码设置名称和区域
                if dc_code == "gra":
                    dc["dcName"] = "格拉夫尼茨"
                    dc["region"] = "法国"
                elif dc_code == "sbg":
                    dc["dcName"] = "斯特拉斯堡"
                    dc["region"] = "法国"
                elif dc_code == "rbx":
                    dc["dcName"] = "鲁贝"
                    dc["region"] = "法国"
                elif dc_code == "bhs":
                    dc["dcName"] = "博阿尔诺"
                    dc["region"] = "加拿大"
                elif dc_code == "hil":
                    dc["dcName"] = "俄勒冈"
                    dc["region"] = "美国西部"
                elif dc_code == "vin":
                    dc["dcName"] = "弗吉尼亚"
                    dc["region"] = "美国东部"
                elif dc_code == "lim":
                    dc["dcName"] = "利马索尔"
                    dc["region"] = "塞浦路斯"
                elif dc_code == "sgp":
                    dc["dcName"] = "新加坡"
                    dc["region"] = "新加坡"
                elif dc_code == "syd":
                    dc["dcName"] = "悉尼"
                    dc["region"] = "澳大利亚"
                elif dc_code == "waw":
                    dc["dcName"] = "华沙"
                    dc["region"] = "波兰"
                elif dc_code == "fra":
                    dc["dcName"] = "法兰克福"
                    dc["region"] = "德国"
                elif dc_code == "lon":
                    dc["dcName"] = "伦敦"
                    dc["region"] = "英国"
                elif dc_code == "eri":
                    dc["dcName"] = "厄斯沃尔"
                    dc["region"] = "英国"
                else:
                    dc["dcName"] = dc.get("datacenter", "未知")
                    dc["region"] = "未知"
            
            # Extract server details
            default_options = []
            available_options = []
            
            # 创建初始服务器信息对象 - 确保在解析特定字段前就已创建
            server_info = {
                "planCode": plan_code,
                "name": plan.get("invoiceName", ""),
                "description": plan.get("description", ""),
                "cpu": "N/A",
                "memory": "N/A",
                "storage": "N/A",
                "bandwidth": "N/A",
                "vrackBandwidth": "N/A",
                "datacenters": datacenters,
                "defaultOptions": default_options,
                "availableOptions": available_options
            }
            
            # 保存服务器详细数据，以便于调试
            try:
                # 创建一个目录来存储服务器数据
                server_data_dir = os.path.join(CACHE_DIR, "servers", plan_code)
                os.makedirs(server_data_dir, exist_ok=True)
                
                # 保存详细的plan数据
                with open(os.path.join(server_data_dir, "plan_data.json"), "w", encoding='utf-8') as f:
                    json.dump(plan, f, ensure_ascii=False, indent=2)
                
                # 保存addonFamilies数据，如果存在
                if plan.get("addonFamilies") and isinstance(plan.get("addonFamilies"), list):
                    with open(os.path.join(server_data_dir, "addonFamilies.json"), "w", encoding='utf-8') as f:
                        json.dump(plan.get("addonFamilies"), f, ensure_ascii=False, indent=2)
                
                add_log("INFO", f"已保存服务器{plan_code}的详细数据用于调试")
            except Exception as e:
                add_log("WARNING", f"保存服务器详细数据时出错: {str(e)}")
            
            # 处理特殊系列处理逻辑
            special_server_processed = False
            try:
                # 检查是否为SYSLE系列服务器
                if "sysle" in plan_code.lower():
                    add_log("INFO", f"检测到SYSLE系列服务器: {plan_code}")
                    
                    # 尝试从plan_code提取信息
                    # 通常SYSLE的格式为"25sysle021"，可能包含CPU型号或配置信息
                    # 根据不同型号添加更具体的CPU信息
                    if "011" in plan_code:
                        server_info["cpu"] = "SYSLE 011系列 (入门级服务器CPU)"
                    elif "021" in plan_code:
                        server_info["cpu"] = "SYSLE 021系列 (中端服务器CPU)"
                    elif "031" in plan_code:
                        server_info["cpu"] = "SYSLE 031系列 (高端服务器CPU)"
                    else:
                        server_info["cpu"] = "SYSLE系列CPU"
                    
                    # 获取服务器显示名称和描述，可能包含CPU信息
                    display_name = plan.get("displayName", "")
                    invoice_name = plan.get("invoiceName", "")
                    description = plan.get("description", "")
                    
                    # 检查名称中是否包含具体CPU型号信息
                    found_cpu = False
                    for name in [display_name, invoice_name, description]:
                        if not name:
                            continue
                            
                        # 查找CPU型号关键词
                        cpu_keywords = ["i7-", "i9-", "i5-", "xeon", "epyc", "ryzen"]
                        for keyword in cpu_keywords:
                            if keyword.lower() in name.lower():
                                # 提取包含CPU型号的部分
                                start_pos = name.lower().find(keyword.lower())
                                end_pos = min(start_pos + 30, len(name))  # 提取最多30个字符
                                cpu_info = name[start_pos:end_pos].split(",")[0].strip()
                                server_info["cpu"] = cpu_info
                                add_log("INFO", f"从关键词中提取SYSLE CPU型号: {cpu_info} 给 {plan_code}")
                                found_cpu = True
                                break
                        
                        if found_cpu:
                            break
                    
                    # 尝试寻找更具体的信息
                    # 保存原始数据以便分析
                    try:
                        debug_file = os.path.join(CACHE_DIR, f"sysle_server_{plan_code}.json")
                        with open(debug_file, "w", encoding='utf-8') as f:
                            json.dump(plan, f, ensure_ascii=False, indent=2)
                        add_log("INFO", f"已保存SYSLE服务器{plan_code}的原始数据到cache目录")
                    except Exception as e:
                        add_log("WARNING", f"保存SYSLE服务器数据时出错: {str(e)}")
                    
                    special_server_processed = True
                
                # 检查是否为SK系列服务器
                elif "sk" in plan_code.lower():
                    add_log("INFO", f"检测到SK系列服务器: {plan_code}")
                    
                    # 获取服务器显示名称和描述，可能包含CPU信息
                    display_name = plan.get("displayName", "")
                    invoice_name = plan.get("invoiceName", "")
                    description = plan.get("description", "")
                    
                    # 检查名称中是否包含具体CPU型号信息
                    found_cpu = False
                    for name in [display_name, invoice_name, description]:
                        if not name:
                            continue
                            
                        # 查找典型的CPU信息格式，例如"KS-A | Intel i7-6700k"
                        if "|" in name:
                            parts = name.split("|")
                            if len(parts) > 1:
                                cpu_part = parts[1].strip()
                                if "intel" in cpu_part.lower() or "amd" in cpu_part.lower() or "xeon" in cpu_part.lower() or "i7" in cpu_part.lower():
                                    server_info["cpu"] = cpu_part
                                    add_log("INFO", f"从名称中提取CPU型号: {cpu_part} 给 {plan_code}")
                                    found_cpu = True
                        
                        # 直接查找CPU型号关键词
                        cpu_keywords = ["i7-", "i9-", "i5-", "xeon", "epyc", "ryzen"]
                        for keyword in cpu_keywords:
                            if keyword.lower() in name.lower():
                                # 提取包含CPU型号的部分
                                start_pos = name.lower().find(keyword.lower())
                                end_pos = min(start_pos + 30, len(name))  # 提取最多30个字符
                                cpu_info = name[start_pos:end_pos].split(",")[0].strip()
                                server_info["cpu"] = cpu_info
                                add_log("INFO", f"从关键词中提取CPU型号: {cpu_info} 给 {plan_code}")
                                found_cpu = True
                                break
                        
                        if found_cpu:
                            break
                    
                    # 如果没有找到详细的CPU型号，使用默认值
                    if not found_cpu:
                        server_info["cpu"] = "SK系列专用CPU"
                    
                    # 尝试寻找更具体的信息
                    # 保存原始数据以便分析
                    try:
                        debug_file = os.path.join(CACHE_DIR, f"sk_server_{plan_code}.json")
                        with open(debug_file, "w", encoding='utf-8') as f:
                            json.dump(plan, f, ensure_ascii=False, indent=2)
                        add_log("INFO", f"已保存SK服务器{plan_code}的原始数据到cache目录")
                    except Exception as e:
                        add_log("WARNING", f"保存SK服务器数据时出错: {str(e)}")
                    
                    special_server_processed = True
                
                # 添加更多特殊系列处理...
                
                # 确保所有服务器都有CPU信息
                if server_info["cpu"] == "N/A":
                    add_log("INFO", f"服务器 {plan_code} 无法从API提取CPU信息，尝试从名称提取")
                    
                    # 尝试从名称中提取CPU信息
                    display_name = plan.get("displayName", "")
                    invoice_name = plan.get("invoiceName", "")
                    description = plan.get("description", "")
                    
                    found_cpu = False
                    for name in [display_name, invoice_name, description]:
                        if not name:
                            continue
                            
                        # 检查是否有CPU型号信息
                        cpu_keywords = ["i7-", "i9-", "i5-", "xeon", "epyc", "ryzen", "processor", "cpu"]
                        for keyword in cpu_keywords:
                            if keyword.lower() in name.lower():
                                # 提取包含CPU型号的部分
                                start_pos = name.lower().find(keyword.lower())
                                end_pos = min(start_pos + 30, len(name))  # 提取最多30个字符
                                cpu_info = name[start_pos:end_pos].split(",")[0].strip()
                                server_info["cpu"] = cpu_info
                                add_log("INFO", f"从名称关键词中提取CPU型号: {cpu_info} 给 {plan_code}")
                                found_cpu = True
                                break
                        
                        if found_cpu:
                            break
                    
                    # 如果仍然没有找到CPU信息，使用默认值
                    if not found_cpu:
                        if "sysle" in plan_code.lower():
                            server_info["cpu"] = "SYSLE系列专用CPU"
                        elif "rise" in plan_code.lower():
                            server_info["cpu"] = "RISE系列专用CPU"
                        elif "game" in plan_code.lower():
                            server_info["cpu"] = "GAME系列专用CPU"
                        else:
                            server_info["cpu"] = "专用服务器CPU"
            except Exception as e:
                add_log("WARNING", f"处理特殊系列服务器时出错: {str(e)}")
                add_log("WARNING", f"错误详情: {traceback.format_exc()}")
                
                # 出错时也确保有默认CPU信息
                if server_info["cpu"] == "N/A":
                    server_info["cpu"] = "专用服务器CPU"
            
            # 如果是特殊处理的服务器，记录日志
            if special_server_processed:
                add_log("INFO", f"已对服务器 {plan_code} 应用特殊处理逻辑")
            
            # 获取服务器名称和描述，确保它们不为空
            if not server_info["name"] and plan.get("displayName"):
                server_info["name"] = plan.get("displayName")
            
            if not server_info["description"] and plan.get("displayName"):
                server_info["description"] = plan.get("displayName")
            
            # 尝试从服务器名称标签中提取CPU信息
            # 例如"KS-A | Intel i7-6700k"格式
            if server_info["cpu"] == "N/A" or "系列" in server_info["cpu"]:
                try:
                    display_name = plan.get("displayName", "")
                    invoice_name = plan.get("invoiceName", "")
                    
                    for name in [display_name, invoice_name]:
                        if not name or "|" not in name:
                            continue
                            
                        parts = name.split("|")
                        if len(parts) > 1:
                            cpu_part = parts[1].strip()
                            if "intel" in cpu_part.lower() or "amd" in cpu_part.lower() or "xeon" in cpu_part.lower() or "i7" in cpu_part.lower():
                                server_info["cpu"] = cpu_part
                                add_log("INFO", f"从服务器名称标签中提取CPU: {cpu_part} 给 {plan_code}")
                                break
                except Exception as e:
                    add_log("WARNING", f"从名称提取CPU时出错: {str(e)}")
            
            # 获取推荐配置和可选配置 - 使用多种方法处理不同格式
            try:
                # 方法 1: 检查plan.default.options
                if plan.get("default") and isinstance(plan.get("default"), dict) and plan.get("default").get("options"):
                    for default_opt in plan.get("default").get("options"):
                        if isinstance(default_opt, dict):
                            option_code = default_opt.get("planCode")
                            option_name = default_opt.get("description", option_code)
                            
                            if option_code:
                                default_options.append({
                                    "label": option_name,
                                    "value": option_code
                                })
                
                # 方法 2: 检查plan.addons
                if plan.get("addons") and isinstance(plan.get("addons"), list):
                    for addon in plan.get("addons"):
                        if not isinstance(addon, dict):
                            continue
                            
                        addon_plan_code = addon.get("planCode")
                        if not addon_plan_code:
                            continue
                        
                        # 跳过已经在默认选项中的配置
                        if any(opt["value"] == addon_plan_code for opt in default_options):
                            continue
                        
                        # 添加到可选配置列表
                        available_options.append({
                            "label": addon.get("description", addon_plan_code),
                            "value": addon_plan_code
                        })
                
                # 方法 3: 检查plan.product.options
                if plan.get("product") and isinstance(plan.get("product"), dict) and plan.get("product").get("options"):
                    product_options = plan.get("product").get("options")
                    if isinstance(product_options, list):
                        for product_opt in product_options:
                            if not isinstance(product_opt, dict):
                                continue
                                
                            option_code = product_opt.get("planCode")
                            option_name = product_opt.get("description", option_code)
                            
                            if option_code and not any(opt["value"] == option_code for opt in available_options) and not any(opt["value"] == option_code for opt in default_options):
                                available_options.append({
                                    "label": option_name,
                                    "value": option_code
                                })
                
                # 方法 4: 尝试从plan.addonFamilies中提取硬件信息
                printed_example = False
                try:
                    if plan.get("addonFamilies") and isinstance(plan.get("addonFamilies"), list):
                        # 尝试保存完整的addonFamilies数据用于更深入分析
                        try:
                            debug_file = os.path.join(CACHE_DIR, f"addonFamilies_{plan_code}.json")
                            with open(debug_file, "w", encoding='utf-8') as f:
                                json.dump(plan.get("addonFamilies"), f, ensure_ascii=False, indent=2)
                            add_log("INFO", f"已保存服务器 {plan_code} 的addonFamilies数据到cache目录")
                        except Exception as e:
                            add_log("WARNING", f"保存addonFamilies数据时出错: {str(e)}")
                        
                        # 打印一个完整的addonFamilies示例用于调试
                        if len(plan.get("addonFamilies")) > 0 and not printed_example:
                            try:
                                add_log("INFO", f"addonFamilies示例: {json.dumps(plan.get('addonFamilies')[0], indent=2)}")
                                printed_example = True
                            except Exception as e:
                                add_log("WARNING", f"无法序列化addonFamilies示例: {str(e)}")
                        
                        # 尝试保存所有带宽相关的选项用于调试
                        try:
                            bandwidth_options = []
                            for family in plan.get("addonFamilies"):
                                family_name = family.get("name", "").lower()
                                if ("bandwidth" in family_name or "traffic" in family_name or "network" in family_name):
                                    bandwidth_options.append({
                                        "family": family.get("name"),
                                        "default": family.get("default"),
                                        "addons": family.get("addons")
                                    })
                            
                            if bandwidth_options:
                                debug_file = os.path.join(CACHE_DIR, f"bandwidth_options_{plan_code}.json")
                                with open(debug_file, "w", encoding='utf-8') as f:
                                    json.dump(bandwidth_options, f, ensure_ascii=False, indent=2)
                                add_log("INFO", f"已保存{plan_code}的带宽选项到cache目录")
                        except Exception as e:
                            add_log("WARNING", f"保存带宽选项时出错: {str(e)}")
                        
                        # 重置可选配置列表
                        temp_available_options = []
                        
                        # 提取addonFamilies信息
                        for family in plan.get("addonFamilies"):
                            if not isinstance(family, dict):
                                add_log("WARNING", f"addonFamily不是字典类型: {family}")
                                continue
                                
                            family_name = family.get("name", "").lower()  # 注意: 在API响应中是'name'而不是'family'
                            default_addon = family.get("default")  # 获取默认选项
                            
                            # 提取可选配置
                            if family.get("addons") and isinstance(family.get("addons"), list):
                                for addon_code in family.get("addons"):
                                    # 在API响应中，addons是字符串数组而不是对象数组
                                    if not isinstance(addon_code, str):
                                        continue
                            
                                    # 标记是否为默认选项
                                    is_default = (addon_code == default_addon)
                                    
                                    # 从addon_code解析描述信息
                                    addon_desc = addon_code
                                    
                                    # 过滤掉许可证相关选项
                                    if (
                                        # Windows许可证
                                        "windows-server" in addon_code.lower() or
                                        # SQL Server许可证
                                        "sql-server" in addon_code.lower() or
                                        # cPanel许可证
                                        "cpanel-license" in addon_code.lower() or
                                        # Plesk许可证
                                        "plesk-" in addon_code.lower() or
                                        # 其他常见许可证
                                        "-license-" in addon_code.lower() or
                                        # 操作系统选项
                                        addon_code.lower().startswith("os-") or
                                        # 控制面板
                                        "control-panel" in addon_code.lower() or
                                        "panel" in addon_code.lower()
                                    ):
                                        # 跳过许可证类选项
                                        continue
                            
                                    if addon_code:
                                        temp_available_options.append({
                                            "label": addon_desc,
                                            "value": addon_code,
                                            "family": family_name,
                                            "isDefault": is_default
                                        })
                                        
                                        # 如果是默认选项，添加到默认选项列表
                                        if is_default:
                                            default_options.append({
                                                "label": addon_desc,
                                                "value": addon_code
                                            })
                            
                            # 根据family名称设置对应的硬件信息
                            if family_name and family.get("addons") and isinstance(family.get("addons"), list):
                                # 获取默认选项的值
                                default_value = family.get("default")
                                
                                # CPU信息
                                if ("cpu" in family_name or "processor" in family_name) and server_info["cpu"] == "N/A":
                                    if default_value:
                                        server_info["cpu"] = default_value
                                        add_log("INFO", f"从addonFamilies默认选项提取CPU: {default_value} 给 {plan_code}")
                                        
                                        # 尝试从CPU选项中提取更详细信息
                                        try:
                                            # 记录CPU选项的完整列表，方便调试
                                            if family.get("addons") and isinstance(family.get("addons"), list):
                                                cpu_options = []
                                                for cpu_addon in family.get("addons"):
                                                    if isinstance(cpu_addon, str):
                                                        cpu_options.append(cpu_addon)
                                                
                                                if cpu_options:
                                                    add_log("INFO", f"服务器 {plan_code} 的CPU选项: {', '.join(cpu_options)}")
                                                    
                                                    # 保存到文件以便更详细分析
                                                    try:
                                                        debug_file = os.path.join(CACHE_DIR, f"cpu_options_{plan_code}.json")
                                                        with open(debug_file, "w") as f:
                                                            json.dump({"options": cpu_options, "default": default_value}, f, indent=2)
                                                    except Exception as e:
                                                        add_log("WARNING", f"保存CPU选项时出错: {str(e)}")
                                        except Exception as e:
                                            add_log("WARNING", f"解析CPU选项时出错: {str(e)}")
                                
                                # 内存信息
                                elif ("memory" in family_name or "ram" in family_name) and server_info["memory"] == "N/A":
                                    if default_value:
                                        # 尝试提取内存大小
                                        ram_size = ""
                                        ram_match = re.search(r'ram-(\d+)g', default_value, re.IGNORECASE)
                                        if ram_match:
                                            ram_size = f"{ram_match.group(1)} GB"
                                            server_info["memory"] = ram_size
                                            add_log("INFO", f"从addonFamilies默认选项提取内存: {ram_size} 给 {plan_code}")
                                        else:
                                            server_info["memory"] = default_value
                                            add_log("INFO", f"从addonFamilies默认选项提取内存(原始值): {default_value} 给 {plan_code}")
                                
                                # 存储信息
                                elif ("storage" in family_name or "disk" in family_name or "drive" in family_name or "ssd" in family_name or "hdd" in family_name) and server_info["storage"] == "N/A":
                                    if default_value:
                                        # 尝试匹配混合RAID格式
                                        hybrid_storage_match = re.search(r'hybridsoftraid-(\d+)x(\d+)(sa|ssd|hdd)-(\d+)x(\d+)(nvme|ssd|hdd)', default_value, re.IGNORECASE)
                                        if hybrid_storage_match:
                                            count1 = hybrid_storage_match.group(1)
                                            size1 = hybrid_storage_match.group(2)
                                            type1 = hybrid_storage_match.group(3).upper()
                                            count2 = hybrid_storage_match.group(4)
                                            size2 = hybrid_storage_match.group(5)
                                            type2 = hybrid_storage_match.group(6).upper()
                                            server_info["storage"] = f"混合RAID {count1}x {size1}GB {type1} + {count2}x {size2}GB {type2}"
                                            add_log("INFO", f"从addonFamilies默认选项提取混合存储: {server_info['storage']} 给 {plan_code}")
                                        else:
                                            # 尝试从存储代码中提取信息
                                            storage_match = re.search(r'(raid|softraid)-(\d+)x(\d+)(ssd|hdd|nvme|sa)', default_value, re.IGNORECASE)
                                            if storage_match:
                                                raid_type = storage_match.group(1).upper()
                                                count = storage_match.group(2)
                                                size = storage_match.group(3)
                                                type_str = storage_match.group(4).upper()
                                                server_info["storage"] = f"{raid_type} {count}x {size}GB {type_str}"
                                                add_log("INFO", f"从addonFamilies默认选项提取存储: {server_info['storage']} 给 {plan_code}")
                                            else:
                                                server_info["storage"] = default_value
                                                add_log("INFO", f"从addonFamilies默认选项提取存储(原始值): {default_value} 给 {plan_code}")
                                
                                # 带宽信息
                                elif ("bandwidth" in family_name or "traffic" in family_name or "network" in family_name) and server_info["bandwidth"] == "N/A":
                                    if default_value:
                                        add_log("DEBUG", f"处理带宽选项: {default_value}")
                                        
                                        # 格式1: traffic-5tb-100-24sk-apac (带宽限制和流量限制)
                                        traffic_bw_match = re.search(r'traffic-(\d+)(tb|gb|mb)-(\d+)', default_value, re.IGNORECASE)
                                        if traffic_bw_match:
                                            size = traffic_bw_match.group(1)
                                            unit = traffic_bw_match.group(2).upper()
                                            bw_value = traffic_bw_match.group(3)
                                            server_info["bandwidth"] = f"{bw_value} Mbps / {size} {unit}流量"
                                            add_log("INFO", f"从addonFamilies默认选项提取带宽和流量: {server_info['bandwidth']} 给 {plan_code}")
                                        
                                        # 格式2: traffic-5tb (仅流量限制)
                                        elif re.search(r'traffic-(\d+)(tb|gb|mb)$', default_value, re.IGNORECASE):
                                            simple_traffic_match = re.search(r'traffic-(\d+)(tb|gb|mb)', default_value, re.IGNORECASE)
                                            size = simple_traffic_match.group(1)
                                            unit = simple_traffic_match.group(2).upper()
                                            server_info["bandwidth"] = f"{size} {unit}流量"
                                            add_log("INFO", f"从addonFamilies默认选项提取流量: {server_info['bandwidth']} 给 {plan_code}")
                                        
                                        # 格式3: bandwidth-100 (仅带宽限制)
                                        elif re.search(r'bandwidth-(\d+)', default_value, re.IGNORECASE):
                                            bandwidth_match = re.search(r'bandwidth-(\d+)', default_value, re.IGNORECASE)
                                            bw_value = int(bandwidth_match.group(1))
                                            if bw_value >= 1000:
                                                server_info["bandwidth"] = f"{bw_value/1000:.1f} Gbps".replace(".0 ", " ")
                                            else:
                                                server_info["bandwidth"] = f"{bw_value} Mbps"
                                            add_log("INFO", f"从addonFamilies默认选项提取带宽: {server_info['bandwidth']} 给 {plan_code}")
                                        
                                        # 格式4: traffic-unlimited (无限流量)
                                        elif "traffic-unlimited" in default_value.lower() or "unlimited" in default_value.lower():
                                            # 检查是否有带宽限制
                                            bw_match = re.search(r'(\d+)', default_value)
                                            if bw_match:
                                                bw_value = int(bw_match.group(1))
                                                server_info["bandwidth"] = f"{bw_value} Mbps / 无限流量"
                                            else:
                                                server_info["bandwidth"] = "无限流量"
                                            add_log("INFO", f"从addonFamilies默认选项提取带宽: {server_info['bandwidth']} 给 {plan_code}")
                                        
                                        # 格式5: bandwidth-guarantee (保证带宽)
                                        elif "guarantee" in default_value.lower() or "guaranteed" in default_value.lower():
                                            bw_guarantee_match = re.search(r'(\d+)', default_value)
                                            if bw_guarantee_match:
                                                bw_value = int(bw_guarantee_match.group(1))
                                                server_info["bandwidth"] = f"{bw_value} Mbps (保证带宽)"
                                                add_log("INFO", f"从addonFamilies默认选项提取保证带宽: {server_info['bandwidth']} 给 {plan_code}")
                                            else:
                                                server_info["bandwidth"] = "保证带宽"
                                                add_log("INFO", f"从addonFamilies默认选项提取保证带宽(无具体值) 给 {plan_code}")
                                        
                                        # 格式6: vrack-bandwidth (内部网络带宽)
                                        elif "vrack" in default_value.lower():
                                            vrack_bw_match = re.search(r'vrack-bandwidth-(\d+)', default_value, re.IGNORECASE)
                                            if vrack_bw_match:
                                                bw_value = int(vrack_bw_match.group(1))
                                                if bw_value >= 1000:
                                                    server_info["vrackBandwidth"] = f"{bw_value/1000:.1f} Gbps".replace(".0 ", " ")
                                                else:
                                                    server_info["vrackBandwidth"] = f"{bw_value} Mbps"
                                                add_log("INFO", f"从addonFamilies默认选项提取内部网络带宽: {server_info['vrackBandwidth']} 给 {plan_code}")
                                        
                                        # 无法识别的格式，使用原始值
                                        else:
                                            server_info["bandwidth"] = default_value
                                            add_log("INFO", f"从addonFamilies默认选项提取带宽(原始值): {default_value} 给 {plan_code}")
                        
                        # 将处理好的可选配置添加到服务器信息中
                        if temp_available_options:
                            available_options = temp_available_options
                
                except Exception as e:
                    add_log("ERROR", f"解析addonFamilies时出错: {str(e)}")
                    add_log("ERROR", f"错误详情: {traceback.format_exc()}")
                
                # 方法 5: 检查plan.pricings中的配置项
                if plan.get("pricings") and isinstance(plan.get("pricings"), dict):
                    for pricing_key, pricing_value in plan.get("pricings").items():
                        if isinstance(pricing_value, dict) and pricing_value.get("options"):
                            for option_code, option_details in pricing_value.get("options").items():
                                # 跳过已经在其他列表中的项目
                                if any(opt["value"] == option_code for opt in default_options) or any(opt["value"] == option_code for opt in available_options):
                                    continue
                                
                                option_label = option_code
                                if isinstance(option_details, dict) and option_details.get("description"):
                                    option_label = option_details.get("description")
                                
                                available_options.append({
                                    "label": option_label,
                                    "value": option_code
                                })
                
                # 记录找到的选项数量
                add_log("INFO", f"找到 {len(default_options)} 个默认选项和 {len(available_options)} 个可选配置用于 {plan_code}")
                
            except Exception as e:
                add_log("WARNING", f"解析 {plan_code} 选项时出错: {str(e)}")
            
            # 解析方法 1: 尝试从properties中提取硬件详情
            try:
                if plan.get("details") and plan.get("details").get("properties"):
                    for prop in plan.get("details").get("properties"):
                        # 添加类型检查，确保prop是字典类型
                        if not isinstance(prop, dict):
                            add_log("WARNING", f"属性项不是字典类型: {prop}")
                            continue
                            
                        prop_name = prop.get("name", "").lower()
                        value = prop.get("value", "N/A")
                        
                        if value and value != "N/A":
                            if any(cpu_term in prop_name for cpu_term in ["cpu", "processor"]):
                                server_info["cpu"] = value
                                add_log("INFO", f"从properties提取CPU: {value} 给 {plan_code}")
                            elif any(mem_term in prop_name for mem_term in ["memory", "ram"]):
                                server_info["memory"] = value
                                add_log("INFO", f"从properties提取内存: {value} 给 {plan_code}")
                            elif any(storage_term in prop_name for storage_term in ["storage", "disk", "hdd", "ssd"]):
                                server_info["storage"] = value
                                add_log("INFO", f"从properties提取存储: {value} 给 {plan_code}")
                            elif "bandwidth" in prop_name:
                                if any(private_term in prop_name for private_term in ["vrack", "private", "internal"]):
                                    server_info["vrackBandwidth"] = value
                                    add_log("INFO", f"从properties提取vRack带宽: {value} 给 {plan_code}")
                                else:
                                    server_info["bandwidth"] = value
                                    add_log("INFO", f"从properties提取带宽: {value} 给 {plan_code}")
            except Exception as e:
                add_log("WARNING", f"解析 {plan_code} 属性时出错: {str(e)}")
            # 解析方法 2: 尝试从名称中提取信息
            try:
                server_name = server_info["name"]
                server_desc = server_info["description"] if server_info["description"] else ""
                
                # 保存原始数据用于调试
                try:
                    debug_file = os.path.join(CACHE_DIR, f"server_details_{plan_code}.json")
                    with open(debug_file, "w") as f:
                        json.dump({
                            "name": server_name,
                            "description": server_desc,
                            "planCode": plan_code
                        }, f, indent=2)
                except Exception as e:
                    add_log("WARNING", f"保存服务器详情时出错: {str(e)}")
                
                # 检查是否为KS/RISE系列服务器，它们通常使用 "KS-XX | CPU信息" 格式
                if "|" in server_name:
                    parts = server_name.split("|")
                    if len(parts) > 1 and server_info["cpu"] == "N/A":
                        cpu_part = parts[1].strip()
                        server_info["cpu"] = cpu_part
                        add_log("INFO", f"从服务器名称提取CPU: {cpu_part} 给 {plan_code}")
                        
                        # 尝试从CPU部分提取更多信息
                        if "core" in cpu_part.lower():
                            # 例如: "4 Core, 8 Thread, xxxx"
                            core_parts = cpu_part.split(",")
                            if len(core_parts) > 1:
                                server_info["cpu"] = core_parts[0].strip()
                
                # 提取CPU型号信息
                if server_info["cpu"] == "N/A":
                    # 尝试匹配常见的CPU关键词
                    cpu_keywords = ["i7-", "i9-", "ryzen", "xeon", "epyc", "cpu", "intel", "amd", "processor"]
                    full_text = f"{server_name} {server_desc}".lower()
                    
                    for keyword in cpu_keywords:
                        if keyword in full_text.lower():
                            # 找到关键词的位置
                            pos = full_text.lower().find(keyword)
                            if pos >= 0:
                                # 提取关键词周围的文本
                                start = max(0, pos - 5)
                                end = min(len(full_text), pos + 25)
                                cpu_text = full_text[start:end]
                                
                                # 尝试清理提取的文本
                                cpu_text = re.sub(r'[^\w\s\-,.]', ' ', cpu_text)
                                cpu_text = ' '.join(cpu_text.split())
                                
                                if cpu_text:
                                    server_info["cpu"] = cpu_text
                                    add_log("INFO", f"从文本中提取CPU关键字: {cpu_text} 给 {plan_code}")
                                    break
                
                # 从服务器名称中提取内存信息
                if server_info["memory"] == "N/A":
                    # 寻找内存关键词
                    mem_match = None
                    mem_patterns = [
                        r'(\d+)\s*GB\s*RAM', 
                        r'RAM\s*(\d+)\s*GB',
                        r'(\d+)\s*G\s*RAM',
                        r'RAM\s*(\d+)\s*G',
                        r'(\d+)\s*GB'
                    ]
                    
                    full_text = f"{server_name} {server_desc}"
                    for pattern in mem_patterns:
                        match = re.search(pattern, full_text, re.IGNORECASE)
                        if match:
                            mem_match = match
                            break
                    
                    if mem_match:
                        memory_size = mem_match.group(1)
                        server_info["memory"] = f"{memory_size} GB"
                        add_log("INFO", f"从文本中提取内存: {server_info['memory']} 给 {plan_code}")
                
                # 从服务器名称中提取存储信息
                if server_info["storage"] == "N/A":
                    # 寻找存储关键词
                    storage_patterns = [
                        r'(\d+)\s*[xX]\s*(\d+)\s*GB\s*(SSD|HDD|NVMe)',
                        r'(\d+)\s*(SSD|HDD|NVMe)\s*(\d+)\s*GB',
                        r'(\d+)\s*TB\s*(SSD|HDD|NVMe)',
                        r'(\d+)\s*(SSD|HDD|NVMe)'
                    ]
                    
                    full_text = f"{server_name} {server_desc}"
                    for pattern in storage_patterns:
                        match = re.search(pattern, full_text, re.IGNORECASE)
                        if match:
                            if match.lastindex == 3:  # 匹配了第一种模式
                                count = match.group(1)
                                size = match.group(2)
                                disk_type = match.group(3).upper()
                                server_info["storage"] = f"{count}x {size}GB {disk_type}"
                            elif match.lastindex == 2:  # 匹配了最后一种模式
                                size = match.group(1)
                                disk_type = match.group(2).upper()
                                server_info["storage"] = f"{size} {disk_type}"
                            
                            add_log("INFO", f"从文本中提取存储: {server_info['storage']} 给 {plan_code}")
                            break
            except Exception as e:
                add_log("WARNING", f"解析 {plan_code} 服务器名称时出错: {str(e)}")
                add_log("WARNING", f"错误详情: {traceback.format_exc()}")
            
            # 解析方法 3: 尝试从产品配置中提取信息
            try:
                if plan.get("product") and isinstance(plan.get("product"), dict) and plan.get("product").get("configurations"):
                    configs = plan.get("product").get("configurations")
                    if not isinstance(configs, list):
                        add_log("WARNING", f"产品配置不是列表类型: {configs}")
                        configs = []
                        
                    for config in configs:
                        # 添加类型检查，确保config是字典类型
                        if not isinstance(config, dict):
                            add_log("WARNING", f"产品配置项不是字典类型: {config}")
                            continue
                            
                        config_name = config.get("name", "").lower()
                        value = config.get("value")
                        
                        if value:
                            if any(cpu_term in config_name for cpu_term in ["cpu", "processor"]):
                                server_info["cpu"] = value
                                add_log("INFO", f"从产品配置提取CPU: {value} 给 {plan_code}")
                            elif any(mem_term in config_name for mem_term in ["memory", "ram"]):
                                server_info["memory"] = value
                                add_log("INFO", f"从产品配置提取内存: {value} 给 {plan_code}")
                            elif any(storage_term in config_name for storage_term in ["storage", "disk", "hdd", "ssd"]):
                                server_info["storage"] = value
                                add_log("INFO", f"从产品配置提取存储: {value} 给 {plan_code}")
                            elif "bandwidth" in config_name:
                                server_info["bandwidth"] = value
                                add_log("INFO", f"从产品配置提取带宽: {value} 给 {plan_code}")
            except Exception as e:
                add_log("WARNING", f"解析 {plan_code} 产品配置时出错: {str(e)}")
                add_log("WARNING", f"错误详情: {traceback.format_exc()}")
            
            # 解析方法 4: 尝试从description解析信息
            try:
                description = plan.get("description", "")
                if description:
                    parts = description.split(",")
                    for part in parts:
                        part = part.strip().lower()
                        
                        # 检查每个部分是否包含硬件信息
                        if server_info["cpu"] == "N/A" and any(cpu_term in part for cpu_term in ["cpu", "core", "i7", "i9", "xeon", "epyc", "ryzen"]):
                            server_info["cpu"] = part
                            add_log("INFO", f"从描述提取CPU: {part} 给 {plan_code}")
                            
                        if server_info["memory"] == "N/A" and any(mem_term in part for mem_term in ["ram", "gb", "memory"]):
                            server_info["memory"] = part
                            add_log("INFO", f"从描述提取内存: {part} 给 {plan_code}")
                            
                        if server_info["storage"] == "N/A" and any(storage_term in part for storage_term in ["hdd", "ssd", "nvme", "storage", "disk"]):
                            server_info["storage"] = part
                            add_log("INFO", f"从描述提取存储: {part} 给 {plan_code}")
                            
                        if server_info["bandwidth"] == "N/A" and "bandwidth" in part:
                            server_info["bandwidth"] = part
                            add_log("INFO", f"从描述提取带宽: {part} 给 {plan_code}")
            except Exception as e:
                add_log("WARNING", f"解析 {plan_code} 描述时出错: {str(e)}")
            
            # 解析方法 5: 从pricing获取信息
            try:
                if plan.get("pricing") and isinstance(plan.get("pricing"), dict) and plan.get("pricing").get("configurations"):
                    pricing_configs = plan.get("pricing").get("configurations")
                    if not isinstance(pricing_configs, list):
                        add_log("WARNING", f"价格配置不是列表类型: {pricing_configs}")
                        pricing_configs = []
                        
                    for price_config in pricing_configs:
                        # 添加类型检查，确保price_config是字典类型
                        if not isinstance(price_config, dict):
                            add_log("WARNING", f"价格配置项不是字典类型: {price_config}")
                            continue
                            
                        config_name = price_config.get("name", "").lower()
                        value = price_config.get("value")
                        
                        if value:
                            if "processor" in config_name and server_info["cpu"] == "N/A":
                                server_info["cpu"] = value
                                add_log("INFO", f"从pricing配置提取CPU: {value} 给 {plan_code}")
                            elif "memory" in config_name and server_info["memory"] == "N/A":
                                server_info["memory"] = value
                                add_log("INFO", f"从pricing配置提取内存: {value} 给 {plan_code}")
                            elif "storage" in config_name and server_info["storage"] == "N/A":
                                server_info["storage"] = value
                                add_log("INFO", f"从pricing配置提取存储: {value} 给 {plan_code}")
            except Exception as e:
                add_log("WARNING", f"解析 {plan_code} pricing配置时出错: {str(e)}")
                add_log("WARNING", f"错误详情: {traceback.format_exc()}")
            
            # 清理提取的数据以确保格式一致
            # 对于CPU，添加一些基本信息如果只有核心数
            if server_info["cpu"] != "N/A" and server_info["cpu"].isdigit():
                server_info["cpu"] = f"{server_info['cpu']} 核心"
            
            # 更新服务器信息中的配置选项
            server_info["defaultOptions"] = default_options
            server_info["availableOptions"] = available_options
            
            # 更新硬件信息计数器
            if server_info["cpu"] != "N/A":
                hardware_info_counter["cpu_success"] += 1
            if server_info["memory"] != "N/A":
                hardware_info_counter["memory_success"] += 1
            if server_info["storage"] != "N/A":
                hardware_info_counter["storage_success"] += 1
            if server_info["bandwidth"] != "N/A":
                hardware_info_counter["bandwidth_success"] += 1
            
            plans.append(server_info)
        
        # 记录硬件信息提取的成功率
        total = hardware_info_counter["total"]
        if total > 0:
            cpu_rate = (hardware_info_counter["cpu_success"] / total) * 100
            memory_rate = (hardware_info_counter["memory_success"] / total) * 100
            storage_rate = (hardware_info_counter["storage_success"] / total) * 100
            bandwidth_rate = (hardware_info_counter["bandwidth_success"] / total) * 100
            
            add_log("INFO", f"服务器硬件信息提取成功率: CPU={cpu_rate:.1f}%, 内存={memory_rate:.1f}%, "
                           f"存储={storage_rate:.1f}%, 带宽={bandwidth_rate:.1f}%")
        
        return plans
    except Exception as e:
        add_log("ERROR", f"Failed to load server list: {str(e)}")
        add_log("ERROR", f"错误详情: {traceback.format_exc()}")
        return []

# 保存完整的API原始响应用于调试分析
def save_raw_api_response(client, zone):
    try:
        # 使用cache目录存储API响应
        api_responses_dir = os.path.join(CACHE_DIR, "api_responses")
        os.makedirs(api_responses_dir, exist_ok=True)
        
        # 获取目录并保存
        catalog = client.get(f'/order/catalog/public/eco?ovhSubsidiary={zone}')
        with open(os.path.join(api_responses_dir, "catalog_response.json"), "w") as f:
            json.dump(catalog, f, indent=2)
        
        add_log("INFO", "已保存目录API原始响应到cache目录")
        
        # 获取可用的服务器列表
        available_servers = client.get('/dedicated/server/datacenter/availabilities')
        with open(os.path.join(api_responses_dir, "availability_response.json"), "w") as f:
            json.dump(available_servers, f, indent=2)
        
        add_log("INFO", "已保存可用性API原始响应到cache目录")
        
        # 尝试获取一些具体服务器的详细信息
        if available_servers and len(available_servers) > 0:
            for i, server in enumerate(available_servers[:5]):  # 只获取前5个服务器的信息
                server_code = server.get("planCode")
                if server_code:
                    try:
                        server_details = client.get(f'/order/catalog/formatted/eco?planCode={server_code}&ovhSubsidiary={zone}')
                        with open(os.path.join(api_responses_dir, f"server_details_{server_code}.json"), "w") as f:
                            json.dump(server_details, f, indent=2)
                        add_log("INFO", f"已保存服务器{server_code}的详细API响应到cache目录")
                    except Exception as e:
                        add_log("WARNING", f"获取服务器{server_code}详细信息时出错: {str(e)}")
        
    except Exception as e:
        add_log("WARNING", f"保存API原始响应时出错: {str(e)}")

#移植过来的 send_telegram_msg 函数，适配 app.py 的 config
def send_telegram_msg(message: str, reply_markup=None):
    """
    发送Telegram消息
    
    Args:
        message: 消息文本
        reply_markup: 可选的内联键盘（InlineKeyboardMarkup格式）
    """
    # 使用 app.py 的全局 config 字典
    tg_token = config.get("tgToken")
    tg_chat_id = config.get("tgChatId")

    if not tg_token:
        add_log("WARNING", "Telegram消息未发送: Bot Token未在config中设置")
        return False
    
    if not tg_chat_id:
        add_log("WARNING", "Telegram消息未发送: Chat ID未在config中设置")
        return False
    
    add_log("INFO", f"准备发送Telegram消息，ChatID: {tg_chat_id}, TokenLength: {len(tg_token)}")
    
    url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
    payload = {
        "chat_id": tg_chat_id,
        "text": message
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    headers = {"Content-Type": "application/json"}

    try:
        add_log("INFO", f"发送HTTP请求到Telegram API: {url[:45]}...")
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        add_log("INFO", f"Telegram API响应: 状态码={response.status_code}")
        
        if response.status_code == 200:
            try:
                response_data = response.json()
                add_log("INFO", f"Telegram响应数据: {response_data}")
                add_log("INFO", "成功发送消息到Telegram")
                return True
            except Exception as json_error: # Changed from json.JSONDecodeError to generic Exception for wider catch, or could add 'import json'
                add_log("ERROR", f"解析Telegram响应JSON时出错: {str(json_error)}")
                return False # Explicitly return False here
        else:
            add_log("ERROR", f"发送消息到Telegram失败: 状态码={response.status_code}, 响应={response.text}")
            return False
    except requests.exceptions.Timeout:
        add_log("ERROR", "发送Telegram消息超时")
        return False
    except requests.exceptions.RequestException as e:
        add_log("ERROR", f"发送Telegram消息时发生网络错误: {str(e)}")
        return False
    except Exception as e:
        add_log("ERROR", f"发送Telegram消息时发生未预期错误: {str(e)}")
        add_log("ERROR", f"错误详情: {traceback.format_exc()}")
        return False

# 初始化服务器监控器
def init_monitor():
    """初始化监控器"""
    global monitor
    monitor = ServerMonitor(
        check_availability_func=check_server_availability_with_configs,  # 使用配置级别的监控
        send_notification_func=send_telegram_msg,
        add_log_func=add_log
    )
    return monitor

# 保存订阅数据
def save_subscriptions():
    """保存订阅数据到文件"""
    global monitor
    try:
        # 确保monitor已初始化
        if monitor is None:
            add_log("WARNING", "monitor未初始化，无法保存订阅数据", "monitor")
            return
        # 检查间隔全局强制为5秒，保存时也固定为5秒
        monitor.check_interval = 5
        subscriptions_data = {
            "subscriptions": monitor.subscriptions,
            "known_servers": list(monitor.known_servers),
            "check_interval": 5  # 全局固定为5秒
        }
        with open(SUBSCRIPTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(subscriptions_data, f, ensure_ascii=False, indent=2)
        add_log("INFO", "订阅数据已保存（检查间隔固定为5秒）", "monitor")
    except Exception as e:
        add_log("ERROR", f"保存订阅数据失败: {str(e)}", "monitor")

# Routes
@app.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(config)

@app.route('/api/settings', methods=['POST'])
def save_settings():
    global config
    data = request.json
    
    # Store previous TG settings to check if they changed
    prev_tg_token = config.get("tgToken")
    prev_tg_chat_id = config.get("tgChatId")

    # Update config
    config = {
        "appKey": data.get("appKey", ""),
        "appSecret": data.get("appSecret", ""),
        "consumerKey": data.get("consumerKey", ""),
        "endpoint": data.get("endpoint", "ovh-eu"),
        "tgToken": data.get("tgToken", ""),
        "tgChatId": data.get("tgChatId", ""),
        "iam": data.get("iam", "go-ovh-ie"),
        "zone": data.get("zone", "IE")
    }
    
    # Auto-generate IAM if not set
    if not config["iam"]:
        config["iam"] = f"go-ovh-{config['zone'].lower()}"
    
    save_data()
    add_log("INFO", "API settings updated in config.json") # Clarified log message

    # Check if Telegram settings are present and if they have changed or were just set
    current_tg_token = config.get("tgToken")
    current_tg_chat_id = config.get("tgChatId")

    if current_tg_token and current_tg_chat_id:
        # Send test message if token or chat id is newly set or changed
        if (current_tg_token != prev_tg_token) or (current_tg_chat_id != prev_tg_chat_id) or not prev_tg_token or not prev_tg_chat_id :
            add_log("INFO", f"Telegram Token或Chat ID已更新/设置。尝试发送Telegram测试消息到 Chat ID: {current_tg_chat_id}")
            test_message_content = "OVH Phantom Sniper: Telegram 通知已成功配置 (来自 app.py 测试)"
            test_result = send_telegram_msg(test_message_content) # Call the移植过来的 function
            if test_result:
                add_log("INFO", "Telegram 测试消息发送成功。")
            else:
                add_log("WARNING", "Telegram 测试消息发送失败。请检查 Token 和 Chat ID 以及后端日志。")
        else:
            add_log("INFO", "Telegram 配置未更改，跳过测试消息。")
    else:
        add_log("INFO", "未配置 Telegram Token 或 Chat ID，跳过测试消息。")
    
    return jsonify({"status": "success"})

@app.route('/api/verify-auth', methods=['POST'])
def verify_auth():
    client = get_ovh_client()
    if not client:
        return jsonify({"valid": False})
    
    try:
        # Try a simple API call to check authentication
        client.get("/me")
        return jsonify({"valid": True})
    except Exception as e:
        add_log("ERROR", f"Authentication verification failed: {str(e)}")
        return jsonify({"valid": False})

@app.route('/api/endpoint-config', methods=['OPTIONS', 'GET'])
def get_endpoint_config():
    """获取当前的 OVH API endpoint 配置"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    return jsonify({
        "endpoint": config.get("endpoint", "ovh-eu"),
        "zone": config.get("zone", "IE")
    })

@app.route('/api/logs', methods=['GET'])
def get_logs():
    # 先刷新日志到文件，确保返回最新数据
    flush_logs()
    return jsonify(logs)

@app.route('/api/logs/flush', methods=['POST'])
def force_flush_logs():
    """强制刷新日志到文件"""
    flush_logs()
    return jsonify({"status": "success", "message": "日志已刷新"})

@app.route('/api/logs', methods=['DELETE'])
def clear_logs():
    global logs
    logs = []
    flush_logs()  # 立即写入空日志
    add_log("INFO", "Logs cleared")
    return jsonify({"status": "success"})

@app.route('/api/queue', methods=['GET'])
def get_queue():
    return jsonify(queue)

@app.route('/api/queue', methods=['POST'])
def add_queue_item():
    data = request.json
    
    queue_item = {
        "id": str(uuid.uuid4()),
        "planCode": data.get("planCode", ""),
        "datacenter": data.get("datacenter", ""),
        "options": data.get("options", []),
        "status": "running",  # 直接设置为 running
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
        "retryInterval": data.get("retryInterval", 30),
        "retryCount": 0, # 初始化为0, process_queue的首次检查会处理
        "lastCheckTime": 0 # 初始化为0, process_queue的首次检查会处理
    }
    
    queue.append(queue_item)
    save_data()
    update_stats()
    
    add_log("INFO", f"添加任务 {queue_item['id']} ({queue_item['planCode']} 在 {queue_item['datacenter']}) 到队列并立即启动 (状态: running)")
    return jsonify({"status": "success", "id": queue_item["id"]})

@app.route('/api/queue/<id>', methods=['DELETE'])
def remove_queue_item(id):
    global queue, deleted_task_ids
    item = next((item for item in queue if item["id"] == id), None)
    if item:
        # 立即标记为删除（后台线程会检查这个集合）
        deleted_task_ids.add(id)
        add_log("INFO", f"标记任务 {id} 为删除，后台线程将立即停止处理", "system")
        
        # 从队列中移除
        queue = [item for item in queue if item["id"] != id]
        save_data()
        update_stats()
        add_log("INFO", f"Removed {item['planCode']} from queue (ID: {id})", "system")
    
    return jsonify({"status": "success"})

@app.route('/api/queue/clear', methods=['DELETE'])
def clear_all_queue():
    global queue, deleted_task_ids
    count = len(queue)
    
    # 立即标记所有任务为删除（后台线程会检查这个集合）
    for item in queue:
        deleted_task_ids.add(item["id"])
    
    add_log("INFO", f"标记 {count} 个任务为删除，后台线程将立即停止处理")
    
    # 强制清空队列
    queue.clear()  # 使用clear()方法确保列表被清空
    
    # 立即保存到文件
    save_data()
    
    # 强制再次确认文件已写入
    try:
        with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        add_log("INFO", f"强制清空队列文件: {QUEUE_FILE}")
    except Exception as e:
        add_log("ERROR", f"清空队列文件时出错: {str(e)}")
    
    update_stats()
    add_log("INFO", f"Cleared all queue items ({count} items removed)")
    return jsonify({"status": "success", "count": count})

@app.route('/api/queue/<id>/status', methods=['PUT'])
def update_queue_status(id):
    data = request.json
    item = next((item for item in queue if item["id"] == id), None)
    
    if item:
        item["status"] = data.get("status", "pending")
        item["updatedAt"] = datetime.now().isoformat()
        save_data()
        update_stats()
        
        add_log("INFO", f"Updated {item['planCode']} status to {item['status']}")
    
    return jsonify({"status": "success"})

@app.route('/api/purchase-history', methods=['GET'])
def get_purchase_history():
    return jsonify(purchase_history)

@app.route('/api/purchase-history', methods=['DELETE'])
def clear_purchase_history():
    global purchase_history
    purchase_history = []
    save_data()
    update_stats()
    add_log("INFO", "Purchase history cleared")
    return jsonify({"status": "success"})

# 监控相关API
@app.route('/api/monitor/subscriptions', methods=['GET'])
def get_subscriptions():
    """获取订阅列表"""
    return jsonify(monitor.subscriptions)

@app.route('/api/monitor/subscriptions', methods=['POST'])
def add_subscription():
    """添加订阅"""
    data = request.json
    plan_code = data.get("planCode")
    datacenters = data.get("datacenters", [])
    notify_available = data.get("notifyAvailable", True)
    notify_unavailable = data.get("notifyUnavailable", False)
    auto_order = data.get("autoOrder", False)
    quantity = data.get("quantity", 1)  # 获取下单数量，默认为1
    
    if not plan_code:
        return jsonify({"status": "error", "message": "缺少planCode参数"}), 400
    
    # 从 server_plans 中获取服务器名称
    server_name = None
    try:
        server_info = next((s for s in server_plans if s.get("planCode") == plan_code), None)
        if server_info:
            server_name = server_info.get("name")
            add_log("INFO", f"找到服务器名称: {server_name} ({plan_code})", "monitor")
        else:
            add_log("WARNING", f"未找到服务器 {plan_code} 的名称信息", "monitor")
    except Exception as e:
        add_log("WARNING", f"获取服务器名称失败: {str(e)}", "monitor")
    
    monitor.add_subscription(plan_code, datacenters, notify_available, notify_unavailable, server_name, None, None, auto_order, quantity)
    save_subscriptions()
    
    # 如果监控未运行，自动启动
    if not monitor.running:
        monitor.start()
        add_log("INFO", "添加订阅后自动启动监控")
    
    add_log("INFO", f"添加服务器订阅: {plan_code} ({server_name or '未知名称'})")
    return jsonify({"status": "success", "message": f"已订阅 {plan_code}"})

@app.route('/api/monitor/subscriptions/batch-add-all', methods=['OPTIONS', 'POST'])
def batch_add_all_servers():
    """批量添加所有服务器到监控（全机房监控）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    global server_plans
    
    if not server_plans or len(server_plans) == 0:
        return jsonify({"status": "error", "message": "服务器列表为空，请先刷新服务器列表"}), 400
    
    data = request.json or {}
    notify_available = data.get("notifyAvailable", True)
    notify_unavailable = data.get("notifyUnavailable", False)
    auto_order = data.get("autoOrder", False)  # ✅ 获取自动下单参数
    
    added_count = 0
    skipped_count = 0
    errors = []
    
    # 获取当前已订阅的服务器列表（避免重复添加）
    existing_plan_codes = {s.get("planCode") for s in monitor.subscriptions if s.get("planCode")}
    
    for server in server_plans:
        plan_code = server.get("planCode")
        if not plan_code:
            continue
        
        # 检查是否已订阅
        if plan_code in existing_plan_codes:
            skipped_count += 1
            continue
        
        try:
            # 获取服务器名称
            server_name = server.get("name")
            
            # 添加订阅（datacenters=[] 表示监控所有机房）
            monitor.add_subscription(
                plan_code, 
                datacenters=[],  # 空列表表示监控所有机房
                notify_available=notify_available,
                notify_unavailable=notify_unavailable,
                server_name=server_name,
                auto_order=auto_order,  # ✅ 传递自动下单参数
                quantity=1  # 批量添加时默认数量为1
            )
            added_count += 1
            add_log("DEBUG", f"批量添加订阅: {plan_code} ({server_name or '未知名称'})", "monitor")
        except Exception as e:
            error_msg = f"{plan_code}: {str(e)}"
            errors.append(error_msg)
            add_log("WARNING", f"批量添加订阅失败 {error_msg}", "monitor")
    
    # 保存订阅
    save_subscriptions()
    
    # 如果监控未运行，自动启动
    if not monitor.running:
        monitor.start()
        add_log("INFO", "批量添加订阅后自动启动监控", "monitor")
    
    message = f"已添加 {added_count} 个服务器到监控（全机房监控）"
    if skipped_count > 0:
        message += f"，跳过 {skipped_count} 个已订阅的服务器"
    if errors:
        message += f"，{len(errors)} 个失败"
    
    add_log("INFO", f"批量添加订阅完成: {message}", "monitor")
    
    return jsonify({
        "status": "success",
        "added": added_count,
        "skipped": skipped_count,
        "errors": errors,
        "message": message
    })

@app.route('/api/monitor/subscriptions/<plan_code>', methods=['DELETE'])
def remove_subscription(plan_code):
    """删除订阅"""
    success = monitor.remove_subscription(plan_code)
    
    if success:
        save_subscriptions()
        add_log("INFO", f"删除服务器订阅: {plan_code}")
        return jsonify({"status": "success", "message": f"已取消订阅 {plan_code}"})
    else:
        return jsonify({"status": "error", "message": "订阅不存在"}), 404

@app.route('/api/monitor/subscriptions/clear', methods=['DELETE'])
def clear_subscriptions():
    """清空所有订阅"""
    count = monitor.clear_subscriptions()
    save_subscriptions()
    
    add_log("INFO", f"清空所有订阅 ({count} 项)")
    return jsonify({"status": "success", "count": count, "message": f"已清空 {count} 个订阅"})

@app.route('/api/monitor/subscriptions/<plan_code>/history', methods=['GET'])
def get_subscription_history(plan_code):
    """获取订阅的历史记录"""
    subscription = next((s for s in monitor.subscriptions if s["planCode"] == plan_code), None)
    
    if not subscription:
        return jsonify({"status": "error", "message": "订阅不存在"}), 404
    
    history = subscription.get("history", [])
    # 返回倒序（最新的在前），使用切片避免修改原数组
    reversed_history = history[::-1]
    
    return jsonify({
        "status": "success",
        "planCode": plan_code,
        "history": reversed_history
    })

@app.route('/api/monitor/start', methods=['POST'])
def start_monitor():
    """启动监控"""
    success = monitor.start()
    
    if success:
        add_log("INFO", "用户启动服务器监控")
        return jsonify({"status": "success", "message": "监控已启动"})
    else:
        return jsonify({"status": "info", "message": "监控已在运行中"})

@app.route('/api/monitor/stop', methods=['POST'])
def stop_monitor():
    """停止监控"""
    success = monitor.stop()
    
    if success:
        add_log("INFO", "用户停止服务器监控")
        return jsonify({"status": "success", "message": "监控已停止"})
    else:
        return jsonify({"status": "info", "message": "监控未运行"})

@app.route('/api/monitor/status', methods=['GET'])
def get_monitor_status():
    """获取监控状态"""
    status = monitor.get_status()
    return jsonify(status)

@app.route('/api/monitor/interval', methods=['PUT'])
def set_monitor_interval():
    """设置监控间隔（已禁用，全局固定为5秒）"""
    # 检查间隔全局固定为5秒，不允许修改
    return jsonify({"status": "info", "message": "检查间隔已全局固定为5秒，无法修改"}), 200
@app.route('/api/telegram/set-webhook', methods=['POST'])
def set_telegram_webhook():
    """
    设置 Telegram Bot Webhook
    """
    try:
        data = request.json or {}
        webhook_url = data.get('webhook_url')
        
        tg_token = config.get("tgToken")
        if not tg_token:
            return jsonify({"success": False, "error": "未配置 Telegram Bot Token"}), 400
        
        if not webhook_url:
            return jsonify({"success": False, "error": "缺少 webhook_url 参数"}), 400
        
        # 验证 URL 格式
        if not webhook_url.startswith("http://") and not webhook_url.startswith("https://"):
            return jsonify({"success": False, "error": "Webhook URL 必须以 http:// 或 https:// 开头"}), 400
        
        # 确保 URL 指向正确的端点
        if not webhook_url.endswith("/api/telegram/webhook"):
            # 如果用户只提供了基础 URL，自动添加端点路径
            if webhook_url.endswith("/"):
                webhook_url = webhook_url.rstrip("/") + "/api/telegram/webhook"
            else:
                webhook_url = webhook_url + "/api/telegram/webhook"
        
        add_log("INFO", f"正在设置 Telegram Webhook: {webhook_url}", "telegram")
        
        # 调用 Telegram API 设置 webhook
        set_url = f"https://api.telegram.org/bot{tg_token}/setWebhook"
        params = {"url": webhook_url}
        
        try:
            response = requests.post(set_url, params=params, timeout=10)
            result = response.json()
            
            if result.get("ok"):
                add_log("INFO", f"✅ Telegram Webhook 设置成功: {webhook_url}", "telegram")
                
                # 获取 webhook 信息进行验证
                get_url = f"https://api.telegram.org/bot{tg_token}/getWebhookInfo"
                info_response = requests.get(get_url, timeout=10)
                info_result = info_response.json()
                
                webhook_info = {}
                if info_result.get("ok"):
                    webhook_info = info_result.get("result", {})
                
                return jsonify({
                    "success": True,
                    "message": "Webhook 设置成功",
                    "webhook_url": webhook_url,
                    "webhook_info": webhook_info
                })
            else:
                error_msg = result.get("description", "未知错误")
                add_log("ERROR", f"Telegram Webhook 设置失败: {error_msg}", "telegram")
                return jsonify({
                    "success": False,
                    "error": f"设置失败: {error_msg}"
                }), 400
                
        except requests.exceptions.RequestException as e:
            add_log("ERROR", f"请求 Telegram API 失败: {str(e)}", "telegram")
            return jsonify({
                "success": False,
                "error": f"请求失败: {str(e)}"
            }), 500
        
    except Exception as e:
        add_log("ERROR", f"设置 Telegram Webhook 时出错: {str(e)}", "telegram")
        import traceback
        add_log("ERROR", f"错误详情: {traceback.format_exc()}", "telegram")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/telegram/get-webhook-info', methods=['GET'])
def get_telegram_webhook_info():
    """
    获取当前 Telegram Bot Webhook 信息
    """
    try:
        tg_token = config.get("tgToken")
        if not tg_token:
            return jsonify({"success": False, "error": "未配置 Telegram Bot Token"}), 400
        
        get_url = f"https://api.telegram.org/bot{tg_token}/getWebhookInfo"
        
        try:
            response = requests.get(get_url, timeout=10)
            result = response.json()
            
            if result.get("ok"):
                webhook_info = result.get("result", {})
                return jsonify({
                    "success": True,
                    "webhook_info": webhook_info
                })
            else:
                return jsonify({
                    "success": False,
                    "error": result.get("description", "未知错误")
                }), 400
                
        except requests.exceptions.RequestException as e:
            add_log("ERROR", f"请求 Telegram API 失败: {str(e)}", "telegram")
            return jsonify({
                "success": False,
                "error": f"请求失败: {str(e)}"
            }), 500
        
    except Exception as e:
        add_log("ERROR", f"获取 Webhook 信息时出错: {str(e)}", "telegram")
        return jsonify({"success": False, "error": str(e)}), 500

def parse_telegram_order_message(text):
    """
    解析Telegram下单消息格式
    格式：plancode [datacenter] [quantity] [options]
    
    支持的模式：
    1. 24sk202 - 只有plancode，使用所有可用配置和所有可用机房，数量默认为1
    2. 24sk202 rbx 1 - plancode + 机房 + 数量
    3. 24sk202 1 - plancode + 数量，不指定机房
    4. 24sk202 rbx 1 ram-64g-ecc-2133-24sk20,softraid-2x450nvme-24sk20 - 完整格式
    5. 24sk202 1 ram-64g-ecc-2133-24sk20,softraid-2x450nvme-24sk20 - plancode + 数量 + 可选参数，不指定机房
    
    Returns:
        dict: {
            "planCode": str,
            "datacenter": str or None,  # None表示所有机房
            "quantity": int,  # 默认为1
            "options": list or None  # None表示所有可用配置
        }
    """
    if not text or not text.strip():
        return None
    
    parts = text.strip().split()
    if len(parts) == 0:
        return None
    
    result = {
        "planCode": parts[0],
        "datacenter": None,
        "quantity": 1,
        "options": None
    }
    
    # 从第二个部分开始解析
    remaining_parts = parts[1:] if len(parts) > 1 else []
    
    if not remaining_parts:
        # 模式1：只有plancode
        return result
    
    # 查找可选参数部分（包含逗号的部分，可能是最后一个或倒数第二个）
    options_start_idx = None
    for i, part in enumerate(remaining_parts):
        if ',' in part:
            # 找到包含逗号的部分，这是可选参数
            options_start_idx = i
            break
    
    # 如果有可选参数，先提取出来
    if options_start_idx is not None:
        # 合并从options_start_idx开始的所有部分
        options_text = ' '.join(remaining_parts[options_start_idx:])
        result["options"] = [opt.strip() for opt in options_text.split(',') if opt.strip()]
        # 移除已处理的部分
        remaining_parts = remaining_parts[:options_start_idx]
    
    # 解析剩余部分（datacenter 和 quantity）
    # 可能的组合：
    # - [datacenter] [quantity]
    # - [quantity]
    # - [datacenter]
    
    if len(remaining_parts) == 1:
        # 只有一个部分，可能是datacenter或quantity
        part = remaining_parts[0]
        if part.isdigit():
            result["quantity"] = int(part)
        elif len(part) >= 3 and len(part) <= 4 and part.isalpha() and part.islower():
            result["datacenter"] = part
    elif len(remaining_parts) == 2:
        # 两个部分，可能是 [datacenter] [quantity] 或 [quantity] [datacenter]
        part1, part2 = remaining_parts[0], remaining_parts[1]
        
        # 检查第一个是否是datacenter（3-4个小写字母）
        if len(part1) >= 3 and len(part1) <= 4 and part1.isalpha() and part1.islower():
            result["datacenter"] = part1
            if part2.isdigit():
                result["quantity"] = int(part2)
        # 或者第一个是数量，第二个是datacenter（不太常见，但支持）
        elif part1.isdigit():
            result["quantity"] = int(part1)
            if len(part2) >= 3 and len(part2) <= 4 and part2.isalpha() and part2.islower():
                result["datacenter"] = part2
    
    return result

def process_telegram_order(plan_code, datacenter=None, quantity=1, options=None):
    """
    处理Telegram下单请求
    规则：可用配置 × 可用机房 × 指定数量 = 总订单数
    
    Args:
        plan_code: 服务器型号
        datacenter: 指定机房（None表示所有可用机房）
        quantity: 每个配置×每个机房的数量
        options: 指定配置选项（None表示所有可用配置）
    
    Returns:
        dict: {
            "success": bool,
            "message": str,
            "total_orders": int,
            "created_orders": int
        }
    """
    try:
        client = get_ovh_client()
        if not client:
            return {
                "success": False,
                "message": "OVH API客户端未初始化",
                "total_orders": 0,
                "created_orders": 0
            }
        
        # 获取所有配置组合的可用性
        availability_by_config = check_server_availability_with_configs(plan_code)
        if not availability_by_config:
            return {
                "success": False,
                "message": f"无法获取 {plan_code} 的可用性信息",
                "total_orders": 0,
                "created_orders": 0
            }
        
        # 过滤配置
        configs_to_order = []
        if options:
            # 用户指定了配置选项，需要匹配
            for config_key, config_data in availability_by_config.items():
                config_options = config_data.get("options", [])
                # 检查用户指定的选项是否匹配
                if set(options).issubset(set(config_options)):
                    configs_to_order.append((config_key, config_data))
        else:
            # 使用所有可用配置
            configs_to_order = list(availability_by_config.items())
        
        if not configs_to_order:
            return {
                "success": False,
                "message": f"未找到匹配的配置（指定选项: {options}）",
                "total_orders": 0,
                "created_orders": 0
            }
        
        # 收集所有有货的数据中心
        available_datacenters = set()
        for config_key, config_data in configs_to_order:
            dc_map = config_data.get("datacenters", {})
            for dc, status in dc_map.items():
                if status and status not in ["unavailable", "unknown"]:
                    available_datacenters.add(dc)
        
        if not available_datacenters:
            return {
                "success": False,
                "message": f"所有配置在所有机房都无货",
                "total_orders": 0,
                "created_orders": 0
            }
        
        # 如果指定了机房，只使用指定的机房
        if datacenter:
            if datacenter not in available_datacenters:
                return {
                    "success": False,
                    "message": f"指定机房 {datacenter} 无货",
                    "total_orders": 0,
                    "created_orders": 0
                }
            datacenters_to_order = [datacenter]
        else:
            datacenters_to_order = list(available_datacenters)
        
        # 计算总订单数
        total_orders = len(configs_to_order) * len(datacenters_to_order) * quantity
        
        # 先收集所有需要创建的订单项（不立即添加到队列）
        orders_to_create = []
        for config_key, config_data in configs_to_order:
            config_options = config_data.get("options", [])
            dc_map = config_data.get("datacenters", {})
            
            for dc in datacenters_to_order:
                # 检查该配置在该机房是否有货
                if dc_map.get(dc) in ["unavailable", "unknown"]:
                    continue
                
                # 为每个数据中心创建 quantity 个订单
                for i in range(quantity):
                    queue_item = {
                        "id": str(uuid.uuid4()),
                        "planCode": plan_code,
                        "datacenter": dc,
                        "options": config_options,
                        "status": "running",
                        "createdAt": datetime.now().isoformat(),
                        "updatedAt": datetime.now().isoformat(),
                        "retryInterval": 30,
                        "retryCount": 0,
                        "lastCheckTime": 0,
                        "fromTelegram": True  # 标记来自Telegram
                    }
                    orders_to_create.append(queue_item)
        
        # 并发处理订单创建（每批10单）
        BATCH_SIZE = 10
        created_orders = 0
        queue_lock = threading.Lock()  # 用于保护队列操作的锁
        
        def add_order_to_queue(order_item):
            """将订单添加到队列（线程安全）"""
            with queue_lock:
                queue.append(order_item)
                return True
        
        # 分批并发处理
        total_batches = (len(orders_to_create) + BATCH_SIZE - 1) // BATCH_SIZE
        add_log("INFO", f"开始并发创建订单: 总数={len(orders_to_create)}, 批次大小={BATCH_SIZE}, 总批次数={total_batches}", "telegram")
        
        for batch_idx in range(total_batches):
            start_idx = batch_idx * BATCH_SIZE
            end_idx = min(start_idx + BATCH_SIZE, len(orders_to_create))
            batch_orders = orders_to_create[start_idx:end_idx]
            
            # 使用线程池并发处理当前批次
            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                futures = [executor.submit(add_order_to_queue, order_item) for order_item in batch_orders]
                batch_created = sum(1 for future in as_completed(futures) if future.result())
                created_orders += batch_created
            
            add_log("INFO", f"批次 {batch_idx + 1}/{total_batches} 完成: 本批次创建 {len(batch_orders)} 个订单", "telegram")
        
        if created_orders > 0:
            save_data()
            update_stats()
            add_log("INFO", f"并发创建订单完成: 共创建 {created_orders}/{total_orders} 个订单", "telegram")
        
        return {
            "success": True,
            "message": f"已创建 {created_orders}/{total_orders} 个订单",
            "total_orders": total_orders,
            "created_orders": created_orders
        }
        
    except Exception as e:
        add_log("ERROR", f"处理Telegram下单时出错: {str(e)}", "telegram")
        import traceback
        add_log("ERROR", f"错误详情: {traceback.format_exc()}", "telegram")
        return {
            "success": False,
            "message": f"下单失败: {str(e)}",
            "total_orders": 0,
            "created_orders": 0
        }

@app.route('/api/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """
    Telegram webhook端点，处理内联键盘按钮回调和普通消息
    """
    try:
        data = request.json
        
        # 处理callback_query（按钮点击）
        if data.get("callback_query"):
            callback_query = data["callback_query"]
            callback_data = callback_query.get("data", "")
            message = callback_query.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            message_id = message.get("message_id")
            from_user = callback_query.get("from", {})
            user_id = from_user.get("id")
            
            add_log("INFO", f"收到Telegram回调: user_id={user_id}, callback_data={callback_data[:50]}...", "telegram")
            
            # 解析callback_data（可能是JSON或base64编码的）
            import base64
            import json
            
            try:
                # 检查是否是base64编码
                if callback_data.startswith("b64:"):
                    # 提取base64部分并解码
                    base64_part = callback_data[4:]  # 去掉 "b64:" 前缀
                    try:
                        # base64解码可能会因为截断而失败，需要添加padding
                        # base64字符串长度必须是4的倍数，不足的用'='补足
                        missing_padding = len(base64_part) % 4
                        if missing_padding:
                            base64_part += '=' * (4 - missing_padding)
                        callback_data_decoded = base64.b64decode(base64_part).decode('utf-8')
                        callback_data_obj = json.loads(callback_data_decoded)
                    except (base64.binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as decode_error:
                        add_log("WARNING", f"base64解码失败（可能是数据被截断）: {str(decode_error)}, base64_len={len(callback_data[4:])}", "telegram")
                        # 如果解码失败，尝试从部分数据中提取关键信息
                        # 这通常意味着数据在传输过程中被截断
                        add_log("WARNING", f"callback_data（前100字符）: {callback_data[:100]}", "telegram")
                        return jsonify({"ok": False, "error": "Callback data decoding failed (possibly truncated)"}), 400
                else:
                    callback_data_obj = json.loads(callback_data)
            except json.JSONDecodeError as e:
                add_log("ERROR", f"解析callback_data JSON失败: {str(e)}, data={callback_data[:100]}", "telegram")
                return jsonify({"ok": False, "error": "Invalid callback data format"}), 400
            except Exception as e:
                add_log("ERROR", f"解析callback_data时发生未知错误: {str(e)}, data={callback_data[:100]}", "telegram")
                return jsonify({"ok": False, "error": "Invalid callback data"}), 400
            
            # 支持短字段名（a, p, d, o）和长字段名（action, planCode, datacenter, options）
            action = callback_data_obj.get("a") or callback_data_obj.get("action")
            
            if action == "add_to_queue":
                # 优先使用UUID机制（新）
                message_uuid = callback_data_obj.get("u") or callback_data_obj.get("uuid")
                
                if message_uuid and monitor and hasattr(monitor, 'message_uuid_cache'):
                    # UUID机制：从缓存恢复完整配置 - 使用锁保护并发访问
                    cached_config = None
                    cache_valid = False
                    
                    # ✅ 使用锁保护缓存读取和删除操作
                    if hasattr(monitor, '_cache_lock'):
                        with monitor._cache_lock:
                            if message_uuid in monitor.message_uuid_cache:
                                cached_config = monitor.message_uuid_cache[message_uuid]
                                cache_timestamp = cached_config.get("timestamp", 0)
                                current_time = time.time()
                                
                                # 检查缓存是否过期
                                if current_time - cache_timestamp < monitor.message_uuid_cache_ttl:
                                    cache_valid = True
                                else:
                                    # 缓存过期，删除
                                    del monitor.message_uuid_cache[message_uuid]
                                    add_log("WARNING", f"UUID缓存已过期: {message_uuid}", "telegram")
                    else:
                        # 兼容旧版本（无锁）
                        if message_uuid in monitor.message_uuid_cache:
                            cached_config = monitor.message_uuid_cache[message_uuid]
                            cache_timestamp = cached_config.get("timestamp", 0)
                            current_time = time.time()
                            if current_time - cache_timestamp < monitor.message_uuid_cache_ttl:
                                cache_valid = True
                            else:
                                del monitor.message_uuid_cache[message_uuid]
                                add_log("WARNING", f"UUID缓存已过期: {message_uuid}", "telegram")
                    
                    if cache_valid and cached_config:
                        plan_code = cached_config.get("planCode")
                        datacenter = cached_config.get("datacenter")
                        options = cached_config.get("options", [])
                        
                        add_log("INFO", f"✅ 从UUID缓存恢复配置: UUID={message_uuid}, {plan_code}@{datacenter}, options={options}", "telegram")
                        
                        # 添加到抢购队列
                        queue_item = {
                            "id": str(uuid.uuid4()),
                            "planCode": plan_code,
                            "datacenter": datacenter,
                            "options": options,
                            "status": "running",
                            "createdAt": datetime.now().isoformat(),
                            "updatedAt": datetime.now().isoformat(),
                            "retryInterval": 30,
                            "retryCount": 0,
                            "lastCheckTime": 0,
                            "fromTelegram": True  # 标记来自Telegram
                        }
                        
                        queue.append(queue_item)
                        save_data()
                        update_stats()
                        
                        options_str = ", ".join(options) if options else "无（默认配置）"
                        add_log("INFO", f"Telegram用户 {user_id} 通过UUID按钮添加到队列: {plan_code}@{datacenter}, 配置选项: {options_str}", "telegram")
                        
                        # 回复确认消息
                        tg_token = config.get("tgToken")
                        if tg_token:
                            confirm_message = f"✅ 已添加到抢购队列！\n\n型号: {plan_code}\n机房: {datacenter.upper()}\n配置: {options_str}\n\n系统将自动尝试下单。"
                            answer_url = f"https://api.telegram.org/bot{tg_token}/answerCallbackQuery"
                            send_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
                            
                            # 先回答callback（显示loading提示）
                            requests.post(answer_url, json={
                                "callback_query_id": callback_query.get("id"),
                                "text": "已添加到队列！",
                                "show_alert": False
                            }, timeout=5)
                            
                            # 发送确认消息
                            requests.post(send_url, json={
                                "chat_id": chat_id,
                                "text": confirm_message,
                                "reply_to_message_id": message_id
                            }, timeout=5)
                        
                        return jsonify({"ok": True})
                    elif cached_config is None:
                        add_log("WARNING", f"UUID未找到 in cache: {message_uuid}", "telegram")
                
                # 降级到旧机制（兼容性）：直接从callback_data提取
                plan_code = callback_data_obj.get("p") or callback_data_obj.get("planCode")
                datacenter = callback_data_obj.get("d") or callback_data_obj.get("datacenter")
                # 优先使用短字段名 o，如果不存在则使用长字段名 options
                if "o" in callback_data_obj:
                    options = callback_data_obj.get("o", [])
                else:
                    options = callback_data_obj.get("options", [])
                
                # 确保 options 是列表类型
                if not isinstance(options, list):
                    options = []
                
                # 如果 callback_data 中没有 options 或 options 为空，尝试从监控器的缓存中恢复
                if not options and plan_code and datacenter and monitor:
                    cache_key = f"{plan_code}|{datacenter}"
                    # ✅ 使用锁保护options缓存访问
                    if hasattr(monitor, 'options_cache'):
                        cached_data = None
                        cache_valid = False
                        
                        if hasattr(monitor, '_cache_lock'):
                            with monitor._cache_lock:
                                if cache_key in monitor.options_cache:
                                    cached_data = monitor.options_cache[cache_key]
                                    cache_timestamp = cached_data.get("timestamp", 0)
                                    current_time = time.time()
                                    if current_time - cache_timestamp < 24 * 3600:  # 24小时有效期
                                        cache_valid = True
                                    else:
                                        # 缓存过期，删除
                                        del monitor.options_cache[cache_key]
                                        add_log("WARNING", f"options缓存已过期: {cache_key}", "telegram")
                        else:
                            # 兼容旧版本（无锁）
                            if cache_key in monitor.options_cache:
                                cached_data = monitor.options_cache[cache_key]
                                cache_timestamp = cached_data.get("timestamp", 0)
                                current_time = time.time()
                                if current_time - cache_timestamp < 24 * 3600:
                                    cache_valid = True
                                else:
                                    del monitor.options_cache[cache_key]
                                    add_log("WARNING", f"options缓存已过期: {cache_key}", "telegram")
                        
                        if cache_valid and cached_data:
                            options = cached_data.get("options", [])
                            add_log("INFO", f"✅ 从缓存恢复 options: {cache_key} = {options}", "telegram")
                
                if not plan_code or not datacenter:
                    return jsonify({"ok": False, "error": "Missing planCode or datacenter"}), 400
                
                # 添加到抢购队列
                queue_item = {
                    "id": str(uuid.uuid4()),
                    "planCode": plan_code,
                    "datacenter": datacenter,
                    "options": options,  # 确保使用恢复后的 options
                    "status": "running",
                    "createdAt": datetime.now().isoformat(),
                    "updatedAt": datetime.now().isoformat(),
                    "retryInterval": 30,
                    "retryCount": 0,
                    "lastCheckTime": 0,
                    "fromTelegram": True  # 标记来自Telegram
                }
                
                queue.append(queue_item)
                save_data()
                update_stats()
                
                options_str = ", ".join(options) if options else "无（默认配置）"
                add_log("INFO", f"Telegram用户 {user_id} 通过按钮添加到队列（旧机制）: {plan_code}@{datacenter}, 配置选项: {options_str}", "telegram")
                
                # 回复确认消息
                tg_token = config.get("tgToken")
                if tg_token:
                    confirm_message = f"✅ 已添加到抢购队列！\n\n型号: {plan_code}\n机房: {datacenter.upper()}\n配置: {', '.join(options) if options else '默认配置'}\n\n系统将自动尝试下单。"
                    answer_url = f"https://api.telegram.org/bot{tg_token}/answerCallbackQuery"
                    send_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
                    
                    # 先回答callback（显示loading提示）
                    requests.post(answer_url, json={
                        "callback_query_id": callback_query.get("id"),
                        "text": "已添加到队列！",
                        "show_alert": False
                    }, timeout=5)
                    
                    # 发送确认消息
                    requests.post(send_url, json={
                        "chat_id": chat_id,
                        "text": confirm_message,
                        "reply_to_message_id": message_id
                    }, timeout=5)
                
                return jsonify({"ok": True})
            
            else:
                add_log("WARNING", f"未知的action: {action}", "telegram")
                return jsonify({"ok": False, "error": f"Unknown action: {action}"}), 400
        
        # 处理普通消息（支持特定格式的下单消息）
        elif data.get("message"):
            message = data["message"]
            text = message.get("text", "").strip()
            chat_id = message.get("chat", {}).get("id")
            message_id = message.get("message_id")
            from_user = message.get("from", {})
            user_id = from_user.get("id")
            username = from_user.get("username", "未知用户")
            
            add_log("INFO", f"收到Telegram普通消息: user_id={user_id}, username={username}, text={text[:100]}", "telegram")
            
            # 解析消息，检查是否是下单格式
            order_info = parse_telegram_order_message(text)
            
            if order_info:
                # 这是下单消息
                plan_code = order_info["planCode"]
                datacenter = order_info["datacenter"]
                quantity = order_info["quantity"]
                options = order_info["options"]
                
                add_log("INFO", f"解析下单消息: planCode={plan_code}, datacenter={datacenter}, quantity={quantity}, options={options}", "telegram")
                
                # 处理下单
                result = process_telegram_order(plan_code, datacenter, quantity, options)
                
                # 发送回复消息
                tg_token = config.get("tgToken")
                if tg_token:
                    if result["success"]:
                        reply_text = (
                            f"✅ 下单成功！\n\n"
                            f"型号: {plan_code}\n"
                            f"机房: {datacenter.upper() if datacenter else '所有可用机房'}\n"
                            f"数量: {quantity}\n"
                            f"配置: {', '.join(options) if options else '所有可用配置'}\n\n"
                            f"已创建: {result['created_orders']}/{result['total_orders']} 个订单\n"
                            f"系统将自动尝试下单。"
                        )
                    else:
                        reply_text = f"❌ 下单失败\n\n{result['message']}"
                    
                    send_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
                    try:
                        requests.post(send_url, json={
                            "chat_id": chat_id,
                            "text": reply_text,
                            "reply_to_message_id": message_id
                        }, timeout=10)
                    except Exception as e:
                        add_log("ERROR", f"发送Telegram回复消息失败: {str(e)}", "telegram")
                
                return jsonify({"ok": True})
            else:
                # 不是下单格式的消息，忽略
                add_log("DEBUG", "消息不是下单格式，忽略", "telegram")
                return jsonify({"ok": True})
        
        return jsonify({"ok": True})
        
    except Exception as e:
        add_log("ERROR", f"处理Telegram webhook时出错: {str(e)}", "telegram")
        import traceback
        add_log("ERROR", f"错误详情: {traceback.format_exc()}", "telegram")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/monitor/test-notification', methods=['POST'])
def test_notification():
    """测试Telegram通知"""
    try:
        test_message = (
            "🔔 服务器监控测试通知\n\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "✅ Telegram通知配置正常！"
        )
        
        result = send_telegram_msg(test_message)
        
        if result:
            add_log("INFO", "Telegram测试通知发送成功", "monitor")
            return jsonify({"status": "success", "message": "测试通知已发送，请检查Telegram"})
        else:
            add_log("WARNING", "Telegram测试通知发送失败", "monitor")
            return jsonify({"status": "error", "message": "发送失败，请检查Telegram配置和日志"}), 500
    except Exception as e:
        add_log("ERROR", f"测试通知异常: {str(e)}", "monitor")
        return jsonify({"status": "error", "message": f"发送异常: {str(e)}"}), 500

@app.route('/api/servers', methods=['GET'])
def get_servers():
    global server_plans, server_list_cache
    show_api_servers = request.args.get('showApiServers', 'false').lower() == 'true'
    force_refresh = request.args.get('forceRefresh', 'false').lower() == 'true'
    
    # 标记是否使用了过期缓存
    using_expired_cache = False
    cache_age_minutes = 0
    
    # 检查缓存是否有效
    cache_valid = False
    if server_list_cache["timestamp"] is not None:
        cache_age = time.time() - server_list_cache["timestamp"]
        cache_age_minutes = int(cache_age / 60)
        cache_valid = cache_age < server_list_cache["cache_duration"]
    
    # 如果缓存有效且不是强制刷新，使用缓存
    if cache_valid and not force_refresh:
        add_log("INFO", f"使用缓存的服务器列表 (缓存时间: {cache_age_minutes} 分钟前)")
        server_plans = server_list_cache["data"]
    elif show_api_servers and get_ovh_client():
        # 缓存失效或强制刷新，从API重新加载
        add_log("INFO", "正在从OVH API重新加载服务器列表...")
        api_servers = load_server_list()
        if api_servers and len(api_servers) > 0:  # 确保返回有效数据
            server_plans = api_servers
            # 更新缓存
            server_list_cache["data"] = api_servers
            server_list_cache["timestamp"] = time.time()
            save_data()
            update_stats()
            add_log("INFO", f"从OVH API加载了 {len(server_plans)} 台服务器，已更新缓存")
            
            # 记录硬件信息统计
            cpu_count = sum(1 for s in server_plans if s["cpu"] != "N/A")
            memory_count = sum(1 for s in server_plans if s["memory"] != "N/A")
            storage_count = sum(1 for s in server_plans if s["storage"] != "N/A")
            bandwidth_count = sum(1 for s in server_plans if s["bandwidth"] != "N/A")
            
            add_log("INFO", f"服务器硬件信息统计: CPU={cpu_count}/{len(server_plans)}, 内存={memory_count}/{len(server_plans)}, "
                   f"存储={storage_count}/{len(server_plans)}, 带宽={bandwidth_count}/{len(server_plans)}")
        else:
            # API返回空数据或调用失败，尝试使用旧的缓存
            add_log("WARNING", f"从OVH API加载服务器列表失败或返回空数据")
            if server_list_cache["data"] and len(server_list_cache["data"]) > 0:
                # 内存缓存有数据，使用过期缓存
                server_plans = server_list_cache["data"]
                using_expired_cache = True
                add_log("WARNING", f"⚠️ OVH API 调用失败，使用过期缓存数据（{cache_age_minutes} 分钟前，共 {len(server_plans)} 台服务器）")
            elif len(server_plans) > 0:
                # 全局变量有数据（可能是从文件加载的），使用全局变量
                using_expired_cache = True
                add_log("WARNING", f"⚠️ OVH API 调用失败，使用全局服务器数据（可能过期，共 {len(server_plans)} 台服务器）")
            else:
                # 完全没有数据，返回错误
                add_log("ERROR", "❌ OVH API 调用失败且没有缓存数据可用！")
                return jsonify({
                    "error": "No data available",
                    "message": "无法获取服务器列表：OVH API 调用失败且没有缓存数据"
                }), 503
    elif not cache_valid and server_list_cache["data"]:
        # 缓存过期但未认证或未配置 OVH API，使用过期缓存
        using_expired_cache = True
        add_log("WARNING", f"⚠️ 缓存已过期（{cache_age_minutes} 分钟前）但未配置 OVH API，使用过期缓存数据")
        server_plans = server_list_cache["data"]
    
    # 确保返回的服务器对象具有所有必要字段
    validated_servers = []
    
    for server in server_plans:
        # 确保每个字段都有合理的默认值
        validated_server = {
            "planCode": server.get("planCode", "未知"),
            "name": server.get("name", "未命名服务器"),
            "description": server.get("description", ""),
            "cpu": server.get("cpu", "N/A"),
            "memory": server.get("memory", "N/A"),
            "storage": server.get("storage", "N/A"),
            "bandwidth": server.get("bandwidth", "N/A"),
            "vrackBandwidth": server.get("vrackBandwidth", "N/A"),
            "defaultOptions": server.get("defaultOptions", []),
            "availableOptions": server.get("availableOptions", []),
            "datacenters": server.get("datacenters", [])
        }
        
        # 确保数组类型的字段是有效的数组
        if not isinstance(validated_server["defaultOptions"], list):
            validated_server["defaultOptions"] = []
        
        if not isinstance(validated_server["availableOptions"], list):
            validated_server["availableOptions"] = []
        
        if not isinstance(validated_server["datacenters"], list):
            validated_server["datacenters"] = []
        
        validated_servers.append(validated_server)
    
    # 计算下一次自动刷新的时间
    next_refresh_time = None
    if server_list_cache["timestamp"]:
        next_refresh_time = server_list_cache["timestamp"] + server_list_cache["cache_duration"]
    
    # 返回服务器列表和缓存信息
    response_data = {
        "servers": validated_servers,
        "cacheInfo": {
            "cached": cache_valid,
            "usingExpiredCache": using_expired_cache,  # 标记是否使用过期缓存
            "cacheAgeMinutes": cache_age_minutes,  # 缓存年龄（分钟）
            "timestamp": server_list_cache["timestamp"],
            "cacheAge": int(time.time() - server_list_cache["timestamp"]) if server_list_cache["timestamp"] else None,
            "cacheDuration": server_list_cache["cache_duration"],
            "nextAutoRefresh": next_refresh_time,
            "autoRefreshEnabled": True
        }
    }
    
    # 如果使用了过期缓存，在响应头中添加警告
    response = jsonify(response_data)
    if using_expired_cache:
        response.headers['X-Cache-Warning'] = f'Using expired cache ({cache_age_minutes} minutes old)'
    
    return response

@app.route('/api/availability/<path:plan_code>', methods=['GET', 'POST', 'OPTIONS'])
def get_availability(plan_code):
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    # 支持GET（URL参数）和POST（请求体）两种方式
    if request.method == 'POST':
        data = request.json or {}
        options = data.get('options', [])
        if isinstance(options, str):
            # 如果是字符串，分割为列表
            options = [opt.strip() for opt in options.split(',') if opt.strip()]
    else:
        # GET请求：获取配置选项参数（逗号分隔的字符串）
        options_str = request.args.get('options', '')
        options = [opt.strip() for opt in options_str.split(',') if opt.strip()] if options_str else []
    
    add_log("DEBUG", f"查询可用性: plan_code={plan_code}, method={request.method}, options数量={len(options)}", "availability")
    
    availability = check_server_availability(plan_code, options)
    if availability:
        return jsonify(availability)
    else:
        add_log("WARNING", f"未找到 {plan_code} 的可用性数据", "availability")
        return jsonify({}), 404

def _convert_display_dc_to_api_dc(datacenter):
    """
    将前端显示的数据中心代码转换为OVH API代码
    例如：前端显示 "mum"，但OVH API使用 "ynm"
    """
    if not datacenter:
        return 'gra'
    dc_map = {
        'mum': 'ynm',  # 孟买：前端用mum，OVH API用ynm
    }
    dc_lower = datacenter.lower()
    return dc_map.get(dc_lower, dc_lower)
def _get_server_price_internal(plan_code, datacenter='gra', options=None):
    """
    内部函数：获取配置后的服务器价格（不实际下单）
    
    Args:
        plan_code: 服务器型号
        datacenter: 数据中心，默认'gra'
        options: addon列表，例如 ['ram-64g-ecc-3200-24rise', 'softraid-2x960nvme-24rise']
    
    Returns:
        dict: {
            "success": bool,
            "price": {...} or None,
            "error": str or None
        }
    """
    global config
    
    if options is None:
        options = []
    
    # 转换数据中心代码（前端显示代码 → OVH API代码）
    api_datacenter = _convert_display_dc_to_api_dc(datacenter)
    
    client = get_ovh_client()
    if not client:
        return {"success": False, "error": "未配置OVH API密钥", "price": None}
    
    cart_id = None
    try:
        add_log("INFO", f"查询 {plan_code} 的配置价格，数据中心: {api_datacenter} (原始: {datacenter}), 选项: {options}", "price")
        
        # 1. 创建购物车
        cart_result = client.post('/order/cart', ovhSubsidiary=config["zone"])
        cart_id = cart_result["cartId"]
        add_log("DEBUG", f"购物车创建成功，ID: {cart_id}", "price")
        
        # 2. 添加基础商品
        item_payload = {
            "planCode": plan_code,
            "pricingMode": "default",
            "duration": "P1M",
            "quantity": 1
        }
        item_result = client.post(f'/order/cart/{cart_id}/eco', **item_payload)
        item_id = item_result["itemId"]
        add_log("DEBUG", f"基础商品添加成功，项目 ID: {item_id}", "price")
        
        # 3. 设置必需配置（数据中心、操作系统、区域）
        dc_lower = api_datacenter.lower()  # 使用转换后的数据中心代码
        region = None
        EU_DATACENTERS = ['gra', 'rbx', 'sbg', 'eri', 'lim', 'waw', 'par', 'fra', 'lon']
        CANADA_DATACENTERS = ['bhs']
        US_DATACENTERS = ['vin', 'hil']
        APAC_DATACENTERS = ['syd', 'sgp', 'ynm']  # ynm是孟买的OVH API代码
        
        if any(dc_lower.startswith(prefix) for prefix in EU_DATACENTERS): 
            region = "europe"
        elif any(dc_lower.startswith(prefix) for prefix in CANADA_DATACENTERS): 
            region = "canada"
        elif any(dc_lower.startswith(prefix) for prefix in US_DATACENTERS): 
            region = "usa"
        elif any(dc_lower.startswith(prefix) for prefix in APAC_DATACENTERS): 
            region = "apac"
        
        configurations_to_set = {
            "dedicated_datacenter": api_datacenter,  # 使用转换后的数据中心代码
            "dedicated_os": "none_64.en"
        }
        if region:
            configurations_to_set["region"] = region
        
        for label, value in configurations_to_set.items():
            if value is None:
                continue
            try:
                client.post(f'/order/cart/{cart_id}/item/{item_id}/configuration',
                           label=label,
                           value=str(value))
                add_log("DEBUG", f"设置配置: {label} = {value}", "price")
            except Exception as e:
                add_log("WARNING", f"设置配置 {label} 失败: {str(e)}", "price")
        
        # 4. 添加用户选择的addons
        if options and isinstance(options, list):
            try:
                available_eco_options = client.get(f'/order/cart/{cart_id}/eco/options', planCode=plan_code)
                add_log("DEBUG", f"找到 {len(available_eco_options)} 个可用选项", "price")
                
                added_options = []
                for wanted_option in options:
                    for avail_opt in available_eco_options:
                        if avail_opt.get("planCode") == wanted_option:
                            try:
                                option_payload = {
                                    "itemId": item_id,
                                    "planCode": wanted_option,
                                    "duration": avail_opt.get("duration", "P1M"),
                                    "pricingMode": avail_opt.get("pricingMode", "default"),
                                    "quantity": 1
                                }
                                client.post(f'/order/cart/{cart_id}/eco/options', **option_payload)
                                added_options.append(wanted_option)
                                add_log("DEBUG", f"成功添加选项: {wanted_option}", "price")
                                break
                            except Exception as e:
                                add_log("WARNING", f"添加选项 {wanted_option} 失败: {str(e)}", "price")
                                break
                
                add_log("INFO", f"共添加 {len(added_options)} 个选项: {added_options}", "price")
            except Exception as e:
                add_log("WARNING", f"获取或添加选项失败: {str(e)}", "price")
        
        # 5. 绑定购物车
        try:
            client.post(f'/order/cart/{cart_id}/assign')
            add_log("DEBUG", "购物车绑定成功", "price")
        except Exception as e:
            add_log("WARNING", f"绑定购物车失败（可能不需要）: {str(e)}", "price")
        
        # 6. 获取购物车详情和价格
        cart_info = client.get(f'/order/cart/{cart_id}')
        cart_summary = client.get(f'/order/cart/{cart_id}/summary')
        
        # 验证返回值的类型
        if not isinstance(cart_info, dict):
            add_log("WARNING", f"购物车info返回类型异常: {type(cart_info)}, 值: {cart_info}", "price")
            cart_info = {}
        if not isinstance(cart_summary, dict):
            add_log("WARNING", f"购物车summary返回类型异常: {type(cart_summary)}, 值: {cart_summary}", "price")
            cart_summary = {}
        
        # 提取价格信息
        price_info = {
            "pricingMode": "default",
            "prices": {
                "withTax": None,
                "withoutTax": None,
                "tax": None,
                "currencyCode": None
            },
            "items": []
        }
        
        # 从summary中提取总价（安全访问）
        if cart_summary and isinstance(cart_summary, dict):
            prices_field = cart_summary.get("prices")
            # 确保 prices_field 是字典类型，如果不是则使用空字典
            if isinstance(prices_field, dict):
                prices_dict = prices_field
            elif prices_field is not None:
                # 如果prices不是字典（可能是整数或其他类型），记录警告并使用空字典
                add_log("WARNING", f"购物车summary的prices字段类型异常: {type(prices_field)}，值: {prices_field}，预期dict", "price")
                prices_dict = {}
            else:
                prices_dict = {}
            
            if isinstance(prices_dict, dict):
                with_tax_obj = prices_dict.get("withTax")
                without_tax_obj = prices_dict.get("withoutTax")
                tax_obj = prices_dict.get("tax")
                
                # 安全提取值（支持字典或直接是值）
                if with_tax_obj is not None:
                    price_info["prices"]["withTax"] = with_tax_obj.get("value") if isinstance(with_tax_obj, dict) else with_tax_obj
                if without_tax_obj is not None:
                    price_info["prices"]["withoutTax"] = without_tax_obj.get("value") if isinstance(without_tax_obj, dict) else without_tax_obj
                if tax_obj is not None:
                    price_info["prices"]["tax"] = tax_obj.get("value") if isinstance(tax_obj, dict) else tax_obj
                
                # 提取货币代码
                if isinstance(with_tax_obj, dict):
                    currency_from_with_tax = with_tax_obj.get("currencyCode")
                    if currency_from_with_tax:
                        price_info["prices"]["currencyCode"] = currency_from_with_tax
            
                if not price_info["prices"]["currencyCode"]:
                    price_info["prices"]["currencyCode"] = prices_dict.get("currencyCode", "EUR") if isinstance(prices_dict, dict) else "EUR"
            elif prices_dict is not None:
                # 如果prices不是字典，可能是其他类型，记录警告
                add_log("WARNING", f"购物车summary的prices字段类型异常: {type(prices_dict)}", "price")
        
        # 提取每个商品的价格（安全访问）
        cart_items = []
        if cart_info and isinstance(cart_info, dict):
            items_field = cart_info.get("items")
            # 确保items字段是列表类型
            if isinstance(items_field, list):
                cart_items = items_field
            elif items_field is not None:
                add_log("WARNING", f"购物车items字段类型异常: {type(items_field)}，预期list，跳过商品详情提取", "price")
        elif cart_info is not None:
            add_log("WARNING", f"购物车info类型异常: {type(cart_info)}，预期dict", "price")
        
        for item in cart_items:
            if not isinstance(item, dict):
                # 静默跳过非字典类型的项目（可能是API返回格式问题）
                continue
            item_prices = item.get("prices", {})
            if not isinstance(item_prices, dict):
                continue
                
            # 安全提取价格值（支持字典或直接值）
            with_tax_obj = item_prices.get("withTax")
            without_tax_obj = item_prices.get("withoutTax")
            tax_obj = item_prices.get("tax")
            
            # 安全提取价格值（支持字典或直接是值）
            with_tax_value = None
            without_tax_value = None
            tax_value = None
            currency_code = None
            
            if with_tax_obj is not None:
                with_tax_value = with_tax_obj.get("value") if isinstance(with_tax_obj, dict) else with_tax_obj
                if isinstance(with_tax_obj, dict):
                    currency_code = with_tax_obj.get("currencyCode")
            
            if without_tax_obj is not None:
                without_tax_value = without_tax_obj.get("value") if isinstance(without_tax_obj, dict) else without_tax_obj
            
            if tax_obj is not None:
                tax_value = tax_obj.get("value") if isinstance(tax_obj, dict) else tax_obj
            
            # 如果没有从withTax中获取货币代码，尝试从item_prices获取
            if not currency_code:
                currency_code = item_prices.get("currencyCode") if isinstance(item_prices, dict) else None
            if not currency_code:
                currency_code = "EUR"  # 默认值
            
            item_price_data = {
                "itemId": item.get("itemId"),
                "planCode": item.get("planCode"),
                "description": item.get("description"),
                "prices": {
                    "withTax": with_tax_value,
                    "withoutTax": without_tax_value,
                    "tax": tax_value,
                    "currencyCode": currency_code
                }
            }
            
            price_info["items"].append(item_price_data)
        
        add_log("INFO", f"价格查询成功: 总价含税={price_info['prices']['withTax']} {price_info['prices']['currencyCode']}", "price")
        
        # 清理购物车（删除）
        try:
            client.delete(f'/order/cart/{cart_id}')
            add_log("DEBUG", "临时购物车已清理", "price")
        except Exception as e:
            add_log("WARNING", f"清理购物车失败（不影响结果）: {str(e)}", "price")
        
        return {
            "success": True,
            "planCode": plan_code,
            "datacenter": datacenter,
            "options": options,
            "price": price_info
        }
        
    except ovh.exceptions.APIError as api_e:
        error_msg = str(api_e)
        # 判断是否是配置不可用的错误
        if "is not available in" in error_msg:
            add_log("WARNING", f"配置在指定数据中心不可用: {error_msg}", "price")
            error_msg = f"该配置在指定数据中心不可用"
        else:
            add_log("ERROR", f"查询价格时发生OVH API错误: {error_msg}", "price")
        
        # 尝试清理购物车
        if cart_id:
            try:
                client = get_ovh_client()
                if client:
                    client.delete(f'/order/cart/{cart_id}')
            except:
                pass
        
        return {
            "success": False,
            "error": error_msg,
            "price": None
        }
    
    except Exception as e:
        error_msg = str(e)
        error_traceback = traceback.format_exc()
        add_log("ERROR", f"查询价格时发生错误: {error_msg}", "price")
        add_log("ERROR", f"错误堆栈: {error_traceback}", "price")
        
        # 尝试清理购物车
        if cart_id:
            try:
                client = get_ovh_client()
                if client:
                    client.delete(f'/order/cart/{cart_id}')
            except:
                pass
        
        return {
            "success": False,
            "error": error_msg,
            "price": None
        }

@app.route('/api/servers/<path:plan_code>/price', methods=['OPTIONS', 'POST'])
def get_server_price(plan_code):
    """获取配置后的服务器价格（不实际下单）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    data = request.json or {}
    datacenter = data.get('datacenter', 'gra')
    options = data.get('options', [])
    
    # 调用内部函数
    result = _get_server_price_internal(plan_code, datacenter, options)
    
    if result.get("success"):
        return jsonify(result)
    else:
        status_code = 401 if "未配置OVH API密钥" in result.get("error", "") else 500
        return jsonify(result), status_code

@app.route('/api/internal/monitor/price', methods=['POST'])
def get_monitor_price():
    """
    内部API：用于订阅监控器获取价格（不需要API密钥验证）
    这个端点只接受来自本地进程的调用
    """
    try:
        # 安全检查：确保请求来自本地
        client_ip = request.remote_addr
        if client_ip not in ['127.0.0.1', '::1', 'localhost']:
            add_log("WARNING", f"[monitor price API] 拒绝非本地请求: {client_ip}", "price")
            return jsonify({"success": False, "error": "此API仅限本地访问"}), 403
        
        data = request.json or {}
        plan_code = data.get('plan_code')
        datacenter = data.get('datacenter', 'gra')
        options = data.get('options', [])
        
        if not plan_code:
            return jsonify({"success": False, "error": "缺少 plan_code 参数"}), 400
        
        # 调用内部函数获取价格
        result = _get_server_price_internal(plan_code, datacenter, options)
        
        return jsonify(result)
        
    except Exception as e:
        add_log("ERROR", f"[monitor price API] 获取价格异常: {str(e)}", "price")
        import traceback
        add_log("ERROR", f"[monitor price API] 异常详情: {traceback.format_exc()}", "price")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    update_stats()
    return jsonify(stats)

@app.route('/api/cache/info', methods=['GET'])
def get_cache_info():
    """获取缓存信息"""
    cache_info = {
        "backend": {
            "hasCachedData": len(server_list_cache["data"]) > 0,
            "timestamp": server_list_cache["timestamp"],
            "cacheAge": int(time.time() - server_list_cache["timestamp"]) if server_list_cache["timestamp"] else None,
            "cacheDuration": server_list_cache["cache_duration"],
            "serverCount": len(server_list_cache["data"]),
            "cacheValid": False
        },
        "storage": {
            "dataDir": DATA_DIR,
            "cacheDir": CACHE_DIR,
            "logsDir": LOGS_DIR,
            "files": {
                "config": os.path.exists(CONFIG_FILE),
                "servers": os.path.exists(SERVERS_FILE),
                "logs": os.path.exists(LOGS_FILE),
                "queue": os.path.exists(QUEUE_FILE),
                "history": os.path.exists(HISTORY_FILE)
            }
        }
    }
    
    # 检查缓存是否有效
    if server_list_cache["timestamp"]:
        cache_age = time.time() - server_list_cache["timestamp"]
        cache_info["backend"]["cacheValid"] = cache_age < server_list_cache["cache_duration"]
    
    return jsonify(cache_info)

@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    """清除后端缓存"""
    global server_list_cache, server_plans
    
    cache_type = request.json.get('type', 'all') if request.json else 'all'
    cleared = []
    
    if cache_type in ['all', 'memory']:
        # 清除内存缓存
        server_list_cache["data"] = []
        server_list_cache["timestamp"] = None
        server_plans = []
        cleared.append('memory')
        add_log("INFO", "已清除内存缓存")
    
    if cache_type in ['all', 'files']:
        # 清除缓存文件
        try:
            if os.path.exists(SERVERS_FILE):
                os.remove(SERVERS_FILE)
                cleared.append('servers_file')
            
            # 清除API调试缓存
            cache_files = ['ovh_catalog_raw.json']
            for cache_file in cache_files:
                cache_path = os.path.join(CACHE_DIR, cache_file)
                if os.path.exists(cache_path):
                    os.remove(cache_path)
                    cleared.append(cache_file)
            
            # 清除服务器详细缓存目录
            servers_cache_dir = os.path.join(CACHE_DIR, 'servers')
            if os.path.exists(servers_cache_dir):
                shutil.rmtree(servers_cache_dir)
                cleared.append('servers_cache_dir')
            
            add_log("INFO", f"已清除缓存文件: {', '.join(cleared)}")
        except Exception as e:
            add_log("ERROR", f"清除缓存文件时出错: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500
    
    return jsonify({
        "status": "success",
        "cleared": cleared,
        "message": f"已清除缓存: {', '.join(cleared)}"
    })

# 确保所有必要的文件都存在
def ensure_files_exist():
    # 检查并创建日志文件
    if not os.path.exists(LOGS_FILE):
        with open(LOGS_FILE, 'w', encoding='utf-8') as f:
            f.write('[]')
        print(f"已创建空的 {LOGS_FILE} 文件")
    
    # 检查并创建队列文件
    if not os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
            f.write('[]')
        print(f"已创建空的 {QUEUE_FILE} 文件")
    
    # 检查并创建历史记录文件
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            f.write('[]')
        print(f"已创建空的 {HISTORY_FILE} 文件")
    
    # 检查并创建服务器信息文件
    if not os.path.exists(SERVERS_FILE):
        with open(SERVERS_FILE, 'w', encoding='utf-8') as f:
            f.write('[]')
        print(f"已创建空的 {SERVERS_FILE} 文件")
    
    # 检查并创建配置文件
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"已创建默认 {CONFIG_FILE} 文件")

# ==================== 配置绑定狙击系统 ====================

def standardize_config(config_str):
    """标准化配置字符串，提取核心参数用于匹配"""
    if not config_str:
        return ""
    
    normalized = config_str.lower().strip()
    
    # 第一步：移除所有型号后缀
    model_patterns = [
        r'-\d+skl[a-e]\d{2}(-v\d+)?',  # -24sklea01, -24sklea01-v1
        r'-\d+sk\d+',                   # -24sk502
        r'-\d+rise\d*',                 # -24rise, -24rise012
        r'-\d+sys\w*',                  # -24sys, -24sysgame01
        r'-\d+risegame\d*',             # -24risegame01
        r'-\d+risestor',                # -24risestor
        r'-\d+skgame\d*',               # -24skgame01
        r'-\d+ska\d*',                  # -24ska01
        r'-\d+skstor\d*',               # -24skstor01
        r'-\d+sysstor',                 # -24sysstor
        r'game\d*',                     # game01, game02
        r'stor\d*',                     # stor
        r'-ks\d+',                      # -ks40
        r'-rise',                       # -rise
        r'-\d+sysle\d+',                # -25sysle012
        r'-\d+skb\d+',                  # -25skb01
        r'-\d+skc\d+',                  # -25skc01
        r'-\d+sk\d+b',                  # -24sk60b
        r'-v\d+',                       # -v1
        r'-[a-z]{3}$',                  # -gra, -sgp (机房后缀)
    ]
    
    for pattern in model_patterns:
        normalized = re.sub(pattern, '', normalized)
    
    # 第二步：移除规格细节，只保留核心参数
    # 对于内存：移除频率 (ecc-2133, noecc-2400 等)
    normalized = re.sub(r'-(no)?ecc-\d+', '', normalized)
    
    # 对于存储：移除后缀修饰符
    normalized = re.sub(r'-(sas|sa|ssd|nvme)$', '', normalized)
    
    # 移除其他规格细节数字 (如频率)
    normalized = re.sub(r'-\d{4,5}$', '', normalized)  # -4800, -5600
    
    return normalized

def find_matching_api2_plans(config_fingerprint, target_plancode_base=None, exclude_known=False):
    """在 API2 catalog 中查找匹配的 planCode
    
    Args:
        config_fingerprint: 配置指纹 (memory, storage)
        target_plancode_base: 目标型号（用于日志）
        exclude_known: 是否排除已知型号（用于增量匹配）
    
    Returns:
        list: 匹配的 planCode 列表
        
    逻辑：
        配置匹配模式：查找所有相同配置的型号
    """
    client = get_ovh_client()
    if not client:
        return []
    
    try:
        catalog = client.get(f'/order/catalog/public/eco?ovhSubsidiary={config["zone"]}')
        matched_plancodes = []
        
        # 配置匹配模式：查找所有相同配置的型号
        add_log("INFO", f"🔍 配置匹配模式：查找所有相同配置的型号", "config_sniper")
        for plan in catalog.get("plans", []):
            plan_code = plan.get("planCode")
            addon_families = plan.get("addonFamilies", [])
            
            # 提取所有可能的配置组合（包括 default 和 addons）
            memory_options = []
            storage_options = []
            
            for family in addon_families:
                family_name = family.get("name", "").lower()
                addons = family.get("addons", [])
                
                if family_name == "memory":
                    # 找到匹配的 memory 配置
                    target_memory_std = standardize_config(config_fingerprint[0])
                    for addon in addons:
                        if standardize_config(addon) == target_memory_std:
                            memory_options.append(addon)
                elif family_name == "storage":
                    # 找到匹配的 storage 配置
                    target_storage_std = standardize_config(config_fingerprint[1])
                    for addon in addons:
                        if standardize_config(addon) == target_storage_std:
                            storage_options.append(addon)
            
            # 遍历所有内存和存储的组合
            if memory_options and storage_options:
                for memory_config in memory_options:
                    for storage_config in storage_options:
                        # 标准化并比较（配置匹配）
                        plan_fingerprint = (
                            standardize_config(memory_config),
                            standardize_config(storage_config)
                        )
                        
                        # 记录所有扫描到的 API2 配置（用于调试）
                        add_log("DEBUG", f"API2 扫描: {plan_code}, memory={standardize_config(memory_config)}, storage={standardize_config(storage_config)}", "config_sniper")
                        
                        # 特别记录 64GB 内存的配置（用于调试）
                        if "64g" in standardize_config(memory_config):
                            add_log("INFO", f"🔍 发现 64GB 配置: {plan_code} | {memory_config} → {standardize_config(memory_config)} | {storage_config} → {standardize_config(storage_config)}", "config_sniper")
                        
                        if plan_fingerprint == config_fingerprint:
                            # 避免重复添加同一个 planCode
                            if plan_code not in matched_plancodes:
                                matched_plancodes.append(plan_code)
                                add_log("INFO", f"✓ API2 配置匹配: {plan_code}", "config_sniper")
                            break  # 找到一个匹配就跳出内层循环
                    else:
                        continue
                    break  # 找到匹配后跳出外层循环
        
        add_log("INFO", f"配置匹配完成，找到 {len(matched_plancodes)} 个 API2 planCode", "config_sniper")
        return matched_plancodes
        
    except Exception as e:
        add_log("ERROR", f"查找匹配 API2 planCode 时出错: {str(e)}")
        return []

def format_memory_display(memory_code):
    """格式化内存显示"""
    match = re.search(r'(\d+)g', memory_code, re.I)
    if match:
        return f"{match.group(1)}GB RAM"
    return memory_code

def format_storage_display(storage_code):
    """格式化存储显示"""
    match = re.search(r'(\d+)x(\d+)(ssd|nvme|hdd)', storage_code, re.I)
    if match:
        count = match.group(1)
        size = match.group(2)
        type_str = match.group(3).upper()
        return f"{count}x {size}GB {type_str}"
    return storage_code

def format_config_display(memory_code, storage_code):
    """格式化配置组合显示"""
    mem_display = format_memory_display(memory_code) if memory_code else "默认内存"
    stor_display = format_storage_display(storage_code) if storage_code else "默认存储"
    return f"{mem_display} + {stor_display}"

def match_config(user_memory, user_storage, ovh_memory, ovh_storage):
    """匹配配置 - 统一使用 standardize_config 进行匹配
    
    Args:
        user_memory: 用户选择的内存配置（如 64g-ecc-2400-24ska01）
        user_storage: 用户选择的存储配置（如 2x450nvme-24ska01）
        ovh_memory: OVH返回的内存配置（如 64g-noecc-2133）
        ovh_storage: OVH返回的存储配置（如 2x450nvme）
    
    Returns:
        bool: 是否匹配
    
    注意：使用与 find_matching_api2_plans 和 addon 查找相同的标准化逻辑，
          确保验证阶段、匹配阶段、下单阶段使用统一的匹配规则。
          只比对核心容量参数，忽略规格细节（如频率、ECC类型等）。
    """
    memory_match = True
    if user_memory and ovh_memory:
        # 使用 standardize_config 标准化后比对
        user_memory_std = standardize_config(user_memory)
        ovh_memory_std = standardize_config(ovh_memory)
        memory_match = (user_memory_std == ovh_memory_std)
        
        # 调试日志
        if user_memory_std != ovh_memory_std:
            add_log("DEBUG", f"内存不匹配: user={user_memory}→{user_memory_std}, ovh={ovh_memory}→{ovh_memory_std}", "config_sniper")
    
    storage_match = True
    if user_storage and ovh_storage:
        # 使用 standardize_config 标准化后比对
        user_storage_std = standardize_config(user_storage)
        ovh_storage_std = standardize_config(ovh_storage)
        storage_match = (user_storage_std == ovh_storage_std)
        
        # 调试日志
        if user_storage_std != ovh_storage_std:
            add_log("DEBUG", f"存储不匹配: user={user_storage}→{user_storage_std}, ovh={ovh_storage}→{ovh_storage_std}", "config_sniper")
    
    result = memory_match and storage_match
    if result:
        add_log("DEBUG", f"✅ 配置匹配成功: memory={standardize_config(user_memory)}, storage={standardize_config(user_storage)}", "config_sniper")
    
    return result

# 配置绑定狙击监控线程
def config_sniper_monitor_loop():
    """配置绑定狙击监控主循环（60秒轮询）"""
    global config_sniper_running
    config_sniper_running = True
    
    add_log("INFO", "配置绑定狙击监控已启动（60秒轮询）", "config_sniper")
    
    while config_sniper_running:
        try:
            # 复制列表副本，避免迭代时被修改
            tasks_snapshot = list(config_sniper_tasks)
            
            # 调试日志：监控循环开始时的任务数量（添加线程ID）
            import threading
            thread_id = threading.current_thread().ident
            add_log("DEBUG", f"监控循环[线程{thread_id}]: 任务数={len(config_sniper_tasks)}, 列表ID={id(config_sniper_tasks)}", "config_sniper")
            
            if len(tasks_snapshot) == 0 and len(config_sniper_tasks) > 0:
                add_log("WARNING", f"监控循环异常：副本为空但原列表有 {len(config_sniper_tasks)} 个任务", "config_sniper")
            elif len(tasks_snapshot) != len(config_sniper_tasks):
                add_log("WARNING", f"监控循环异常：副本 {len(tasks_snapshot)} 个，原列表 {len(config_sniper_tasks)} 个", "config_sniper")
            
            for task in tasks_snapshot:
                # 检查任务是否还在原列表中（可能已被删除，通过ID验证）
                task_still_exists = any(t["id"] == task["id"] for t in config_sniper_tasks)
                if not task_still_exists:
                    continue
                
                if not task.get('enabled'):
                    continue
                
                # 待匹配任务：先尝试匹配 API2
                if task['match_status'] == 'pending_match':
                    handle_pending_match_task(task)
                
                # 已匹配任务：检查可用性并下单
                elif task['match_status'] == 'matched':
                    handle_matched_task(task)
                
                # 已完成任务：跳过
                elif task['match_status'] == 'completed':
                    continue
                
                # 更新最后检查时间
                task['last_check'] = datetime.now().isoformat()
            
            # 只有列表不为空时才保存（避免误保存空列表覆盖文件）
            if len(config_sniper_tasks) > 0:
                save_config_sniper_tasks()
            else:
                add_log("WARNING", "监控循环跳过保存：任务列表为空", "config_sniper")
            time.sleep(60)  # 60秒轮询
            
        except Exception as e:
            add_log("ERROR", f"配置狙击监控循环错误: {str(e)}", "config_sniper")
            time.sleep(60)

def handle_pending_match_task(task):
    """处理待匹配任务 - 增量匹配新增的 planCode，排除已知型号"""
    config = task['bound_config']
    memory_std = standardize_config(config['memory'])
    storage_std = standardize_config(config['storage'])
    config_fingerprint = (memory_std, storage_std)
    
    # 查询当前所有配置匹配的 planCode
    current_matched = find_matching_api2_plans(config_fingerprint, task['api1_planCode'])
    
    # 获取已知型号排除列表（避免重复下单已知型号）
    known_plancodes = task.get('known_plancodes', [])
    existing_matched = task.get('matched_api2', [])
    all_known = set(known_plancodes + existing_matched)
    
    # 找出新增的 planCode（排除所有已知型号）
    new_plancodes = [pc for pc in current_matched if pc not in all_known]
    
    if new_plancodes:
        # 发现新增的 planCode！
        task['matched_api2'] = existing_matched + new_plancodes  # 累加
        
        add_log("INFO", 
            f"✅ 发现新增 planCode！{task['api1_planCode']} 新增 {len(new_plancodes)} 个：{', '.join(new_plancodes)}", 
            "config_sniper")
        
        # 发送 Telegram 通知
        send_telegram_msg(
            f"🆕 发现新增配置！\n"
            f"源型号: {task['api1_planCode']}\n"
            f"绑定配置: {format_config_display(config['memory'], config['storage'])}\n"
            f"新增型号: {', '.join(new_plancodes)}\n"
            f"总计匹配: {len(task['matched_api2'])} 个"
        )
        
        save_config_sniper_tasks()
        
        # 立即检查新增 planCode 的可用性并加入队列（所有机房）
        client = get_ovh_client()
        has_queued = False
        if client:
            for new_plancode in new_plancodes:
                try:
                    if check_and_queue_plancode(new_plancode, task, config, client):
                        has_queued = True
                except Exception as e:
                    add_log("WARNING", f"检查新增 {new_plancode} 可用性失败: {str(e)}", "config_sniper")
        
        # 立即标记任务为已完成（一次性下单，不再继续监控）
        if has_queued:
            task['match_status'] = 'completed'
            save_config_sniper_tasks()
            add_log("INFO", f"✅ 未匹配任务完成！{task['api1_planCode']} 发现新增并已下单，任务结束", "config_sniper")
            send_telegram_msg(
                f"🎉 待匹配任务完成！\n"
                f"源型号: {task['api1_planCode']}\n"
                f"绑定配置: {format_config_display(config['memory'], config['storage'])}\n"
                f"新增型号: {', '.join(new_plancodes)}\n"
                f"✅ 已下单所有机房，任务完成"
            )
    else:
        add_log("DEBUG", f"待匹配任务 {task['api1_planCode']} 暂无新增", "config_sniper")
def check_and_queue_plancode(api2_plancode, task, bound_config, client):
    """检查单个 planCode 的可用性并加入队列
    使用新的配置匹配逻辑：内存提取前两段，存储前缀匹配
    
    Returns:
        bool: 是否有新订单加入队列
    """
    queued_count = 0
    
    try:
        availabilities = client.get(
            '/dedicated/server/datacenter/availabilities',
            planCode=api2_plancode
        )
        
        # 遍历所有配置组合，使用新的匹配逻辑
        for item in availabilities:
            item_memory = item.get("memory")
            item_storage = item.get("storage")
            item_fqn = item.get("fqn")
            
            # 匹配用户绑定的配置
            config_matched = match_config(bound_config['memory'], bound_config['storage'], 
                                         item_memory, item_storage)
            
            if not config_matched:
                continue  # 配置不匹配，跳过
            
            # 配置匹配，检查所有机房
            for dc in item.get("datacenters", []):
                availability = dc.get("availability")
                datacenter = dc.get("datacenter")
                
                # 接受所有非 unavailable 状态
                if availability in ["unavailable", "unknown"]:
                    continue
                
                add_log("INFO", 
                    f"🎯 发现可用！API2={api2_plancode} 配置={item_fqn} 机房={datacenter} 状态={availability}", 
                    "config_sniper")
                
                # 发送配置有货TG通知
                send_telegram_msg(
                    f"📦 配置有货通知！\n"
                    f"源型号: {task['api1_planCode']}\n"
                    f"绑定配置: {format_config_display(bound_config['memory'], bound_config['storage'])}\n"
                    f"匹配型号: {api2_plancode}\n"
                    f"实际配置: {format_config_display(item_memory, item_storage)}\n"
                    f"机房: {datacenter}\n"
                    f"库存状态: {availability}"
                )
                
                # 检查是否已在队列中（同一个 planCode + datacenter 组合）
                existing_queue_item = next((q for q in queue 
                    if q['planCode'] == api2_plancode 
                    and q['datacenter'] == datacenter
                    and q.get('configSniperTaskId') == task['id']), None)
                
                if existing_queue_item:
                    add_log("DEBUG", f"{api2_plancode} ({datacenter}) 已在队列中，跳过", "config_sniper")
                    continue
                
                # 添加到购买队列（用 API2 planCode 下单，带上用户选择的配置）
                current_time = datetime.now().isoformat()
                
                # 从 bound_config 中获取用户选择的原始配置（非标准化版本）
                # bound_config 存储的是 API1 的配置代码，需要转换为 API2 的配置代码
                # 我们需要从 API2 中找到对应的 memory 和 storage 选项
                hardware_options = []
                try:
                    # 获取该 planCode 的配置选项
                    catalog = client.get(f'/order/catalog/public/eco?ovhSubsidiary={config["zone"]}')
                    for plan in catalog.get("plans", []):
                        if plan.get("planCode") == api2_plancode:
                            addon_families = plan.get("addonFamilies", [])
                            
                            # 提取 memory 和 storage 的 addons
                            for family in addon_families:
                                family_name = family.get("name", "").lower()
                                addons = family.get("addons", [])
                                
                                if family_name == "memory":
                                    # 找到匹配的 memory 配置
                                    target_memory_std = standardize_config(bound_config['memory'])
                                    for addon in addons:
                                        if standardize_config(addon) == target_memory_std:
                                            hardware_options.append(addon)
                                            add_log("DEBUG", f"添加 memory 选项: {addon}", "config_sniper")
                                            break
                                
                                elif family_name == "storage":
                                    # 找到匹配的 storage 配置
                                    target_storage_std = standardize_config(bound_config['storage'])
                                    for addon in addons:
                                        if standardize_config(addon) == target_storage_std:
                                            hardware_options.append(addon)
                                            add_log("DEBUG", f"添加 storage 选项: {addon}", "config_sniper")
                                            break
                            break
                except Exception as e:
                    add_log("WARNING", f"获取 {api2_plancode} 的配置选项失败: {str(e)}", "config_sniper")
                
                queue_item = {
                    "id": str(uuid.uuid4()),
                    "planCode": api2_plancode,
                    "datacenter": datacenter,
                    "options": hardware_options,  # 用户选择的 memory + storage
                    "status": "running",
                    "retryCount": 0,
                    "maxRetries": 3,
                    "retryInterval": 30,
                    "createdAt": current_time,
                    "updatedAt": current_time,
                    "lastCheckTime": 0,
                    "configSniperTaskId": task['id']
                }
                
                queue.append(queue_item)
                save_data()
                update_stats()
                queued_count += 1
                
                add_log("INFO", 
                    f"🚀 已添加 {api2_plancode} ({datacenter}) 到购买队列", 
                    "config_sniper")
                
                # 发送加入队列TG通知
                send_telegram_msg(
                    f"🎯 自动下单触发！\n"
                    f"源型号: {task['api1_planCode']}\n"
                    f"绑定配置: {format_config_display(bound_config['memory'], bound_config['storage'])}\n"
                    f"下单型号: {api2_plancode}\n"
                    f"实际配置: {format_config_display(item_memory, item_storage)}\n"
                    f"机房: {datacenter}\n"
                    f"库存状态: {availability}\n"
                    f"✅ 已加入购买队列"
                )
    except Exception as e:
        raise e
    
    return queued_count > 0

def handle_matched_task(task):
    """处理已匹配任务 - 只监控已知型号的可用性（一次性狙击）"""
    bound_config = task['bound_config']
    matched_api2_plancodes = task['matched_api2']  # API2 planCode 列表（已知型号）
    
    client = get_ovh_client()
    if not client:
        return
    
    # 遍历所有已知型号，检查可用性并加入队列（一次性）
    has_queued = False
    for api2_plancode in matched_api2_plancodes:
        try:
            if check_and_queue_plancode(api2_plancode, task, bound_config, client):
                has_queued = True
        except Exception as e:
            add_log("WARNING", f"查询 {api2_plancode} 可用性失败: {str(e)}", "config_sniper")
    
    # 如果有订单加入队列，标记任务为已完成
    if has_queued:
        task['match_status'] = 'completed'
        save_config_sniper_tasks()
        add_log("INFO", f"✅ 任务完成！{task['api1_planCode']} 已加入购买队列，停止监控", "config_sniper")
        send_telegram_msg(
            f"🎉 配置狙击任务完成！\n"
            f"源型号: {task['api1_planCode']}\n"
            f"绑定配置: {format_config_display(bound_config['memory'], bound_config['storage'])}\n"
            f"✅ 已加入购买队列，任务自动完成"
        )

def start_config_sniper_monitor():
    """启动配置绑定狙击监控线程"""
    global config_sniper_running
    
    # 防止重复启动（Flask debug模式会导致重载）
    if config_sniper_running:
        add_log("WARNING", "配置绑定狙击监控已在运行，跳过重复启动", "config_sniper")
        return
    
    thread = threading.Thread(target=config_sniper_monitor_loop)
    thread.daemon = True
    thread.start()
    add_log("INFO", "配置绑定狙击监控线程已启动", "config_sniper")

# ==================== API 接口 ====================

@app.route('/api/config-sniper/options/<planCode>', methods=['GET'])
def get_config_options(planCode):
    """获取指定型号的所有配置选项"""
    try:
        client = get_ovh_client()
        if not client:
            return jsonify({"success": False, "error": "OVH客户端未配置"})
        
        # 查询 API1
        availabilities = client.get(
            '/dedicated/server/datacenter/availabilities',
            planCode=planCode
        )
        
        if not availabilities:
            return jsonify({
                "success": False,
                "error": f"型号 {planCode} 不存在或API1中无数据"
            })
        
        # 提取配置选项
        configs = []
        seen_configs = set()
        
        for item in availabilities:
            memory = item.get("memory")
            storage = item.get("storage")
            config_key = (memory, storage)
            
            if not memory or not storage or config_key in seen_configs:
                continue
            seen_configs.add(config_key)
            
            # 查找该配置匹配的 API2 planCode
            memory_std = standardize_config(memory)
            storage_std = standardize_config(storage)
            config_fingerprint = (memory_std, storage_std)
            
            add_log("DEBUG", f"API1 配置: memory={memory}, storage={storage}", "config_sniper")
            add_log("DEBUG", f"标准化后: memory={memory_std}, storage={storage_std}", "config_sniper")
            
            matched_plancodes = find_matching_api2_plans(config_fingerprint, planCode)
            
            # 为每个匹配的 planCode 查询可用机房
            plancodes_with_datacenters = []
            for api2_plancode in matched_plancodes:
                try:
                    api2_availabilities = client.get(
                        '/dedicated/server/datacenter/availabilities',
                        planCode=api2_plancode
                    )
                    datacenters = []
                    for api2_item in api2_availabilities:
                        for dc in api2_item.get("datacenters", []):
                            datacenter = dc.get("datacenter")
                            if datacenter:
                                datacenters.append(datacenter)
                    
                    if datacenters:  # 只返回有机房的 planCode
                        plancodes_with_datacenters.append({
                            "planCode": api2_plancode,
                            "datacenters": list(set(datacenters))  # 去重
                        })
                except:
                    pass  # 查询失败就跳过
            
            configs.append({
                "memory": {
                    "code": memory,
                    "display": format_memory_display(memory)
                },
                "storage": {
                    "code": storage,
                    "display": format_storage_display(storage)
                },
                "matched_api2": plancodes_with_datacenters,  # planCode + 机房列表
                "match_count": len(plancodes_with_datacenters)  # 匹配数量
            })
        
        return jsonify({
            "success": True,
            "planCode": planCode,
            "configs": configs,
            "total": len(configs)
        })
        
    except Exception as e:
        add_log("ERROR", f"获取配置选项错误: {str(e)}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/config-sniper/tasks', methods=['GET'])
def get_config_sniper_tasks():
    """获取所有配置绑定狙击任务"""
    return jsonify({
        "success": True,
        "tasks": config_sniper_tasks,
        "total": len(config_sniper_tasks)
    })

@app.route('/api/config-sniper/tasks', methods=['POST'])
def create_config_sniper_task():
    """创建配置绑定狙击任务"""
    try:
        data = request.json
        api1_planCode = data.get('api1_planCode')
        bound_config = data.get('bound_config')
        mode = data.get('mode', 'matched')  # 'matched' 或 'pending_match'
        
        if not api1_planCode or not bound_config:
            return jsonify({"success": False, "error": "缺少必要参数"})
        
        # 标准化配置
        memory_std = standardize_config(bound_config['memory'])
        storage_std = standardize_config(bound_config['storage'])
        config_fingerprint = (memory_std, storage_std)
        
        # 查询当前所有配置匹配的 planCode
        current_matched = find_matching_api2_plans(config_fingerprint, api1_planCode)
        
        # 根据用户选择的模式创建任务
        if mode == 'pending_match':
            # 未匹配模式：记录当前所有已知型号作为排除列表，等待新增
            task = {
                "id": str(uuid.uuid4()),
                "api1_planCode": api1_planCode,
                "bound_config": bound_config,
                "match_status": "pending_match",
                "matched_api2": [],  # 空列表，等待新增
                "known_plancodes": current_matched,  # 已知型号排除列表
                "enabled": True,
                "last_check": None,
                "created_at": datetime.now().isoformat()
            }
            message = f"⏳ 已创建待匹配任务（已排除 {len(current_matched)} 个已知型号，等待新增型号）"
        else:
            # 已匹配模式：正常监控这些型号
            task = {
                "id": str(uuid.uuid4()),
                "api1_planCode": api1_planCode,
                "bound_config": bound_config,
                "match_status": "matched" if len(current_matched) > 0 else "pending_match",
                "matched_api2": current_matched if current_matched else [],
                "known_plancodes": [],  # 不需要排除列表
                "enabled": True,
                "last_check": None,
                "created_at": datetime.now().isoformat()
            }
            if len(current_matched) > 0:
                message = f"✅ 已创建监控任务（监控 {len(current_matched)} 个型号）"
            else:
                message = "⏳ 未找到匹配，已创建待匹配任务"
        
        config_sniper_tasks.append(task)
        add_log("DEBUG", f"任务已添加到列表: 当前数量={len(config_sniper_tasks)}, 列表ID={id(config_sniper_tasks)}", "config_sniper")
        save_config_sniper_tasks()
        
        add_log("INFO", f"创建配置绑定任务: {api1_planCode} - {message}", "config_sniper")
        
        return jsonify({
            "success": True,
            "task": task,
            "message": message
        })
        
    except Exception as e:
        add_log("ERROR", f"创建配置绑定任务错误: {str(e)}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/config-sniper/tasks/<task_id>', methods=['DELETE'])
def delete_config_sniper_task(task_id):
    """删除配置绑定狙击任务"""
    task = next((t for t in config_sniper_tasks if t['id'] == task_id), None)
    
    if not task:
        return jsonify({"success": False, "error": "任务不存在"})
    
    config_sniper_tasks.remove(task)  # 直接删除，不重新赋值
    save_config_sniper_tasks()
    
    add_log("INFO", f"删除配置绑定任务: {task['api1_planCode']}", "config_sniper")
    
    return jsonify({"success": True, "message": "任务已删除"})

@app.route('/api/config-sniper/tasks/<task_id>/toggle', methods=['PUT'])
def toggle_config_sniper_task(task_id):
    """启用/禁用配置绑定狙击任务"""
    task = next((t for t in config_sniper_tasks if t['id'] == task_id), None)
    
    if not task:
        return jsonify({"success": False, "error": "任务不存在"})
    
    task['enabled'] = not task.get('enabled', True)
    save_config_sniper_tasks()
    
    status = "启用" if task['enabled'] else "禁用"
    add_log("INFO", f"{status}配置绑定任务: {task['api1_planCode']}", "config_sniper")
    
    return jsonify({
        "success": True,
        "enabled": task['enabled'],
        "message": f"任务已{status}"
    })

@app.route('/api/config-sniper/quick-order', methods=['POST'])
def quick_order():
    """快速下单 - 直接将 planCode + 机房加入购买队列"""
    try:
        data = request.json
        plancode = data.get('planCode')
        datacenter = data.get('datacenter')
        options = data.get('options') or []
        
        if not plancode or not datacenter:
            return jsonify({"success": False, "error": "缺少 planCode 或 datacenter"})

        # 若未显式传入options，则尝试基于可用性推断一个支持价格的配置（含内存+硬盘）
        if not options:
            try:
                # 使用“配置级别”的可用性，包含 memory/storage 以及匹配到的 API2 addons（options）
                availability_by_config = check_server_availability_with_configs(plancode) or {}
                # 严格挑选：指定机房在该配置下为可售（非 unavailable/unknown），且能解析出 addons 选项
                selected_cfg = None
                for _, cfg in availability_by_config.items():
                    if not isinstance(cfg, dict):
                        continue
                    dc_map = (cfg.get("datacenters") or {})
                    dc_status = dc_map.get(datacenter)
                    if dc_status and dc_status not in ["unavailable", "unknown"]:
                        cand_opts = cfg.get("options") or []
                        if cand_opts:
                            selected_cfg = cfg
                            options = cand_opts
                            break
                if not options:
                    # 没有找到在该机房“可售且可定价（有addons）”的配置，直接返回 400，避免错误下单
                    err_msg = f"指定机房无可定价配置（{plancode}@{datacenter}）"
                    add_log("WARNING", f"[config_sniper] {err_msg}", "config_sniper")
                    return jsonify({"success": False, "error": err_msg}), 400
            except Exception as e:
                add_log("WARNING", f"快速下单推断配置失败: {plancode}@{datacenter} - {str(e)}", "config_sniper")
                # 不中断流程，继续按空options尝试价格

        # 先通过临时购物车获取价格，确保该组合可下单（无价格则不支持下单）
        price_result = _get_server_price_internal(plancode, datacenter, options)
        if not price_result.get("success"):
            err = price_result.get("error") or "价格查询失败"
            add_log("WARNING", f"快速下单前价格校验失败: {plancode}@{datacenter} - {err}", "config_sniper")
            return jsonify({"success": False, "error": f"价格校验失败：{err}"}), 400

        # ✅ 显式校验price字段存在且格式正确（即使success=True）
        if "price" not in price_result:
            add_log("WARNING", f"快速下单前价格校验失败: {plancode}@{datacenter} - price字段缺失", "config_sniper")
            return jsonify({"success": False, "error": "价格查询返回数据格式异常：缺少price字段"}), 400
        
        price_payload = price_result.get("price")
        if not isinstance(price_payload, dict):
            add_log("WARNING", f"快速下单前价格校验失败: {plancode}@{datacenter} - price字段类型错误: {type(price_payload)}", "config_sniper")
            return jsonify({"success": False, "error": "价格查询返回数据格式异常：price字段类型错误"}), 400
        
        price_values = price_payload.get("prices")
        if not isinstance(price_values, dict):
            add_log("WARNING", f"快速下单前价格校验失败: {plancode}@{datacenter} - prices字段缺失或类型错误", "config_sniper")
            return jsonify({"success": False, "error": "价格查询返回数据格式异常：prices字段缺失或类型错误"}), 400
        
        with_tax = price_values.get("withTax")
        if with_tax in [None, 0, 0.0]:
            add_log("WARNING", f"快速下单前价格缺失或无效: {plancode}@{datacenter} - withTax={with_tax}", "config_sniper")
            return jsonify({"success": False, "error": "该组合暂无有效价格，暂不支持下单"}), 400

        # 防重复（仅限 quick-order）：若同一 planCode+datacenter+options（配置指纹）
        # 已在队列运行/等待，或刚刚成功下过单，则拒绝再次入队
        # 但如果来自监控且设置了 skipDuplicateCheck，则跳过重复检查
        from_monitor = data.get("fromMonitor", False)
        skip_duplicate_check = data.get("skipDuplicateCheck", False)
        
        if not (from_monitor and skip_duplicate_check):
            now_ts = time.time()
            duplicate_window_seconds = 120  # 2分钟窗口

            def _fingerprint(opts):
                if not opts:
                    return ""
                try:
                    # 规范化：字符串化、去重、排序，生成稳定指纹
                    norm = sorted({str(x).strip() for x in opts if x is not None and str(x).strip() != ""})
                    return "|".join(norm)
                except Exception:
                    return "|".join(sorted(map(str, opts)))

            target_fp = _fingerprint(options)

            # 1) 检查队列中的运行中/等待中任务
            for item in queue:
                if (
                    item.get("planCode") == plancode and
                    item.get("datacenter") == datacenter and
                    item.get("status") in ["running", "pending", "paused"] and
                    _fingerprint(item.get("options")) == target_fp
                ):
                    add_log("INFO", f"检测到重复的队列任务（含配置），拒绝再次入队: {plancode}@{datacenter} options={options} (任务ID: {item.get('id')})", "config_sniper")
                    return jsonify({"success": False, "error": "已存在相同配置的购买任务，稍后再试"}), 429
            # 2) 检查近期成功的历史（避免短时间内多次下单）
            for hist in reversed(purchase_history):
                if (
                    hist.get("planCode") == plancode and
                    hist.get("datacenter") == datacenter and
                    hist.get("status") == "success" and
                    _fingerprint(hist.get("options")) == target_fp
                ):
                    try:
                        ts = hist.get("purchaseTime")
                        # ISO 字符串 -> epoch
                        recent = datetime.fromisoformat(ts).timestamp() if isinstance(ts, str) else None
                        if recent and (now_ts - recent) < duplicate_window_seconds:
                            add_log("INFO", f"检测到近期成功订单（含配置，{int(now_ts - recent)}秒内），拒绝再次入队: {plancode}@{datacenter} options={options}", "config_sniper")
                            return jsonify({"success": False, "error": "刚刚已成功下过同配置订单，稍后再试"}), 429
                    except Exception:
                        pass
        else:
            add_log("INFO", f"来自监控的批量下单，跳过重复检查: {plancode}@{datacenter} options={options}", "config_sniper")

        # 价格校验通过后再创建队列项（不再重复检查可用性）
        current_time = datetime.now().isoformat()
        queue_item = {
            "id": str(uuid.uuid4()),
            "planCode": plancode,
            "datacenter": datacenter,
            "options": options,
            "status": "running",
            "retryCount": 0,
            "maxRetries": 3,
            # 快速下单使用更短的重试间隔，加快抢购节奏
            "retryInterval": 2,
            "createdAt": current_time,
            "updatedAt": current_time,
            "lastCheckTime": 0,
            "quickOrder": True,  # 标记为快速下单
            "priority": 100
        }
        
        # 将快速下单任务插入队列头部，提高优先级
        queue.insert(0, queue_item)
        save_data()
        update_stats()
        
        add_log("INFO", f"快速下单: {plancode} ({datacenter}) 已加入队列（含税价格: {with_tax}，options: {options}）", "config_sniper")
        
        return jsonify({
            "success": True,
            "message": f"✅ {plancode} ({datacenter}) 已加入购买队列",
            "price": price_payload,
            "options": options
        })
        
    except Exception as e:
        add_log("ERROR", f"快速下单错误: {str(e)}", "config_sniper")
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/config-sniper/tasks/<task_id>/check', methods=['POST'])
def check_config_sniper_task(task_id):
    """手动检查单个配置绑定狙击任务"""
    task = next((t for t in config_sniper_tasks if t['id'] == task_id), None)
    
    if not task:
        return jsonify({"success": False, "error": "任务不存在"})
    
    try:
        if task['match_status'] == 'pending_match':
            handle_pending_match_task(task)
        elif task['match_status'] == 'matched':
            handle_matched_task(task)
        elif task['match_status'] == 'completed':
            return jsonify({"success": True, "message": "任务已完成，无需检查"})
        
        task['last_check'] = datetime.now().isoformat()
        save_config_sniper_tasks()
        
        return jsonify({
            "success": True,
            "message": "检查完成",
            "task": task
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ==================== 服务器管理（已购服务器控制）====================

@app.route('/api/server-control/list', methods=['OPTIONS', 'GET'])
def get_my_servers():
    """获取当前账户的服务器列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取服务器列表
        server_names = client.get('/dedicated/server')
        add_log("INFO", f"获取服务器列表成功，共 {len(server_names)} 台", "server_control")
        
        servers = []
        for server_name in server_names:
            try:
                # 获取每台服务器的详细信息
                server_info = client.get(f'/dedicated/server/{server_name}')
                service_info = client.get(f'/dedicated/server/{server_name}/serviceInfos')
                
                servers.append({
                    'serviceName': server_name,
                    'name': server_info.get('name', server_name),
                    'commercialRange': server_info.get('commercialRange', 'N/A'),
                    'datacenter': server_info.get('datacenter', 'N/A'),
                    'state': server_info.get('state', 'unknown'),
                    'monitoring': server_info.get('monitoring', False),
                    'reverse': server_info.get('reverse', ''),
                    'ip': server_info.get('ip', 'N/A'),
                    'os': server_info.get('os', 'N/A'),
                    'bootId': server_info.get('bootId', None),
                    'professionalUse': server_info.get('professionalUse', False),
                    'status': service_info.get('status', 'unknown'),
                    'renewalType': service_info.get('renew', {}).get('automatic', False)
                })
                
            except Exception as e:
                add_log("ERROR", f"获取服务器 {server_name} 详情失败: {str(e)}", "server_control")
                # 即使获取详情失败，也返回基本信息
                servers.append({
                    'serviceName': server_name,
                    'name': server_name,
                    'error': str(e)
                })
        
        return jsonify({
            "success": True,
            "servers": servers,
            "total": len(servers)
        })
        
    except Exception as e:
        add_log("ERROR", f"获取服务器列表失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/reboot', methods=['OPTIONS', 'POST'])
def reboot_server(service_name):
    """重启服务器"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 发送重启请求
        result = client.post(f'/dedicated/server/{service_name}/reboot')
        add_log("INFO", f"服务器 {service_name} 重启请求已发送", "server_control")
        
        return jsonify({
            "success": True,
            "message": f"服务器 {service_name} 重启请求已发送",
            "taskId": result.get('taskId') if isinstance(result, dict) else None
        })
        
    except Exception as e:
        add_log("ERROR", f"重启服务器 {service_name} 失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/templates', methods=['OPTIONS', 'GET'])
def get_os_templates(service_name):
    """获取服务器可用的操作系统模板"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取兼容的操作系统模板
        templates = client.get(f'/dedicated/server/{service_name}/install/compatibleTemplates')
        all_template_names = templates.get('ovh', [])
        add_log("INFO", f"获取服务器 {service_name} 可用系统模板成功，共 {len(all_template_names)} 个", "server_control")
        
        # 获取每个模板的详细信息（不限制数量）
        template_details = []
        for template_name in all_template_names:
            try:
                detail = client.get(f'/dedicated/installationTemplate/{template_name}')
                template_details.append({
                    'templateName': template_name,
                    'distribution': detail.get('distribution', 'N/A'),
                    'family': detail.get('family', 'N/A'),
                    'description': detail.get('description', ''),
                    'bitFormat': detail.get('bitFormat', 64)
                })
            except:
                # 如果无法获取详细信息，使用模板名称
                template_details.append({
                    'templateName': template_name,
                    'distribution': template_name,
                    'family': 'unknown',
                    'bitFormat': 64
                })
        
        # 按distribution排序，常用系统放前面
        priority_order = ['debian', 'ubuntu', 'centos', 'rocky', 'almalinux', 'windows']
        
        def get_priority(template):
            dist = template.get('distribution', '').lower()
            for i, priority_dist in enumerate(priority_order):
                if priority_dist in dist:
                    return i
            return len(priority_order)
        
        template_details.sort(key=lambda t: (get_priority(t), t.get('templateName', '')))
        
        # 统计Ubuntu模板
        ubuntu_count = len([t for t in template_details if 'ubuntu' in t.get('distribution', '').lower()])
        add_log("INFO", f"返回 {len(template_details)} 个模板 (包括 {ubuntu_count} 个Ubuntu)", "server_control")
        
        # 输出前10个模板用于调试
        if len(template_details) > 0:
            top_10 = [t.get('templateName', 'unknown') for t in template_details[:10]]
            add_log("DEBUG", f"前10个模板: {', '.join(top_10)}", "server_control")
        
        return jsonify({
            "success": True,
            "templates": template_details,
            "total": len(template_details)
        })
        
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 系统模板失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/install', methods=['OPTIONS', 'POST'])
def install_os(service_name):
    """重装服务器操作系统"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.json
    template_name = data.get('templateName')
    
    if not template_name:
        return jsonify({"success": False, "error": "未指定系统模板"}), 400
    
    try:
        # 构建安装参数 - OVH API格式
        install_params = {
            'operatingSystem': template_name  # OVH API正确的参数名
        }
        
        # 自定义主机名 - 只在有值时才添加
        if data.get('customHostname'):
            install_params['customHostname'] = data['customHostname']
            add_log("INFO", f"设置自定义主机名: {data['customHostname']}", "server_control")
        
        # Proxmox 9 + ZFS 根文件系统预设
        if data.get('useProxmox9Zfs'):
            raid_level = data.get('zfsRaidLevel', 1)  # 默认 RAID1
            vz_size_mb = data.get('zfsVzSize', 102400)  # 默认 100GB
            
            add_log("INFO", f"🎯 使用 Proxmox 9 + ZFS 根文件系统预设 (RAID{raid_level})", "server_control")
            
            # 获取服务器硬件信息以计算实际容量
            try:
                hardware_info = client.get(f'/dedicated/server/{service_name}/specifications/hardware')
                disk_groups = hardware_info.get('diskGroups', [])
                
                if disk_groups and len(disk_groups) > 0:
                    first_group = disk_groups[0]
                    disk_count = first_group.get('numberOfDisks', 2)
                    single_disk_gb = first_group.get('diskSize', {}).get('value', 480)
                    
                    # 根据 RAID 级别计算总容量
                    if raid_level == 0:
                        total_capacity_gb = single_disk_gb * disk_count
                    else:
                        total_capacity_gb = single_disk_gb
                    
                    add_log("INFO", f"📊 检测到磁盘: {disk_count}x{single_disk_gb}GB, RAID{raid_level} 总容量: {total_capacity_gb}GB", "server_control")
                else:
                    # 默认值
                    total_capacity_gb = 960 if raid_level == 0 else 480
                    add_log("WARNING", f"未检测到磁盘信息，使用默认容量: {total_capacity_gb}GB", "server_control")
            except Exception as e:
                # 获取硬件信息失败，使用默认值
                total_capacity_gb = 960 if raid_level == 0 else 480
                add_log("WARNING", f"获取硬件信息失败，使用默认容量: {total_capacity_gb}GB - {str(e)}", "server_control")
            
            # 计算根目录大小
            # 减去: /boot(1GB) + swap(8GB) + /var/lib/vz
            # 注意: 磁盘厂商的GB和实际GiB有差异，需要预留10%安全余量
            # 480GB 磁盘实际约 438GB 可用
            usable_capacity_mb = int(total_capacity_gb * 1024 * 0.92)  # 预留8%空间
            boot_swap_mb = 1024 + 8192  # 9GB
            root_size_mb = usable_capacity_mb - boot_swap_mb - vz_size_mb
            
            add_log("INFO", f"💾 容量计算: 理论{total_capacity_gb}GB, 实际可用~{usable_capacity_mb//1024}GB, 根目录{root_size_mb//1024}GB", "server_control")
            
            # Proxmox 强制要求独立的 /var/lib/vz 分区
            install_params['storage'] = [
                {
                    'diskGroupId': 0,
                    'partitioning': {
                        'layout': [
                            {
                                'fileSystem': 'ext4',
                                'mountPoint': '/boot',
                                'raidLevel': raid_level,
                                'size': 1024
                            },
                            {
                                'fileSystem': 'swap',
                                'mountPoint': 'swap',
                                'raidLevel': 1,  # swap 不支持 RAID0，必须用 RAID1
                                'size': 8192
                            },
                            {
                                'fileSystem': 'zfs',
                                'mountPoint': '/',
                                'raidLevel': raid_level,
                                'size': root_size_mb,
                                'extras': {
                                    'zp': {
                                        'name': 'rpool'
                                    }
                                }
                            },
                            {
                                'fileSystem': 'zfs',
                                'mountPoint': '/var/lib/vz',
                                'raidLevel': raid_level,
                                'size': 0,  # 剩余空间
                                'extras': {
                                    'zp': {
                                        'name': 'rpool'
                                    }
                                }
                            }
                        ]
                    }
                }
            ]
            
            root_gb = root_size_mb // 1024
            vz_gb = vz_size_mb // 1024
            raid_type = f"RAID{raid_level}"
            add_log("INFO", f"✅ ZFS 配置: /boot (1GB {raid_type}) + swap (8GB RAID1) + / ({root_gb}GB {raid_type}) + /var/lib/vz ({vz_gb}GB {raid_type})", "server_control")
        
        # 自定义存储配置 - OVH API格式的storage数组
        elif data.get('storageConfig'):
            storage_array = data['storageConfig']
            add_log("INFO", f"使用自定义存储配置: {json.dumps(storage_array, indent=2)}", "server_control")
            install_params['storage'] = storage_array
        else:
            # 使用默认分区配置（不传storage参数）
            add_log("INFO", "使用默认分区配置", "server_control")
        
        # 发送安装请求
        add_log("INFO", f"准备发送安装请求到OVH API", "server_control")
        add_log("INFO", f"  - 服务器: {service_name}", "server_control")
        add_log("INFO", f"  - 模板: {template_name}", "server_control")
        add_log("INFO", f"  - 参数: {install_params}", "server_control")
        
        # 使用requests直接调用OVH API（绕过SDK问题）
        add_log("INFO", f"使用requests直接调用OVH API", "server_control")
        
        import requests as req
        import time
        import hashlib
        
        # 根据endpoint配置动态构建API URL
        base_url = get_api_base_url()
        api_url = f"{base_url}/1.0/dedicated/server/{service_name}/reinstall"
        
        # 获取认证信息
        app_key = config.get('appKey', '')
        app_secret = config.get('appSecret', '')
        consumer_key = config.get('consumerKey', '')
        
        # 生成签名
        timestamp = str(int(time.time()))
        method = "POST"
        body = json.dumps(install_params)
        
        # OVH签名格式: $1$+SHA1($AS+$CK+$METHOD+$QUERY+$BODY+$TSTAMP)
        pre_hash = f"{app_secret}+{consumer_key}+{method}+{api_url}+{body}+{timestamp}"
        signature = "$1$" + hashlib.sha1(pre_hash.encode()).hexdigest()
        
        headers = {
            'X-Ovh-Application': app_key,
            'X-Ovh-Consumer': consumer_key,
            'X-Ovh-Timestamp': timestamp,
            'X-Ovh-Signature': signature,
            'Content-Type': 'application/json'
        }
        
        add_log("INFO", f"POST {api_url}", "server_control")
        add_log("INFO", f"Body: {body}", "server_control")
        
        response = req.post(api_url, headers=headers, data=body, timeout=30)
        
        if response.status_code in [200, 201]:
            result = response.json()
            add_log("INFO", f"安装请求成功: {result}", "server_control")
        else:
            add_log("ERROR", f"API返回错误: {response.status_code} - {response.text}", "server_control")
            return jsonify({
                "success": False,
                "error": f"OVH API错误: {response.text}"
            }), response.status_code
        
        add_log("INFO", f"服务器 {service_name} 系统重装请求已发送，模板: {template_name}", "server_control")
        
        return jsonify({
            "success": True,
            "message": f"服务器 {service_name} 系统重装请求已发送",
            "taskId": result.get('taskId') if isinstance(result, dict) else None
        })
        
    except Exception as e:
        add_log("ERROR", f"重装服务器 {service_name} 系统失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500
# 安装步骤中文翻译
def translate_install_step(comment):
    """将OVH API返回的英文步骤翻译成中文"""
    translations = {
        # OVH官方安装步骤（完整21步）
        'Pre-configuring Post-installation': '预配置安装后脚本',
        'Downloading OS image': '下载系统镜像',
        'Deploying OS on disks': '部署系统到磁盘',
        'Configuring Boot': '配置启动项',
        'Checking Partitioning': '检查分区',
        'Switching boot': '切换启动模式',
        'Running Last Reboot': '执行最后重启',
        'Waiting for services to be up': '等待服务启动',
        'Publishing Admin password on API': '发布管理员密码到API',
        
        # BIOS和硬件相关
        'Checking BIOS version': '检查BIOS版本',
        'Running Hardware Reboot': '执行硬件重启',
        'Setting up hardware raid': '配置硬件RAID',
        'Preparing disks for new Partitioning': '准备磁盘分区',
        'Checking hardware': '检查硬件',
        'Initializing hardware': '初始化硬件',
        
        # 安装过程步骤
        'Preparing installation': '准备安装',
        'Partitioning disk': '分区磁盘',
        'Partitioning disks': '分区磁盘',
        'Cleaning Partitioning': '清理分区',
        'Processing Partitioning': '处理分区',
        'Applying Partitioning': '应用分区配置',
        'Formatting partitions': '格式化分区',
        'Installing system': '安装系统',
        'Installing system files': '安装系统文件',
        'Installing packages': '安装软件包',
        'Installing bootloader': '安装引导程序',
        'Installing grub': '安装GRUB引导',
        'Configuring system': '配置系统',
        'Configuring network': '配置网络',
        'Setting up network': '设置网络',
        'Setting up system': '设置系统',
        'Applying configuration': '应用配置',
        'Processing Post-installation configuration': '处理安装后配置',
        'Finalizing installation': '完成安装',
        
        # 重启相关
        'Rebooting': '重启中',
        'Rebooting server': '重启服务器',
        'Reboot': '重启',
        'First boot': '首次启动',
        'Booting': '启动中',
        
        # 服务相关
        'Starting services': '启动服务',
        'Starting system services': '启动系统服务',
        'Enabling services': '启用服务',
        
        # 完成状态
        'Installation completed': '安装完成',
        'Installation finished': '安装完成',
        'Done': '完成',
        'Completed': '已完成',
        
        # 磁盘和分区
        'Wiping disks': '擦除磁盘',
        'Cleaning disks': '清理磁盘',
        'Creating partitions': '创建分区',
        'Creating filesystems': '创建文件系统',
        'Mounting filesystems': '挂载文件系统',
        
        # 下载相关
        'Fetching image': '获取镜像',
        'Extracting image': '解压镜像',
        'Copying files': '复制文件',
        
        # 配置相关
        'Generating configuration': '生成配置',
        'Writing configuration': '写入配置',
        'Setting hostname': '设置主机名',
        'Configuring timezone': '配置时区',
        'Configuring locale': '配置语言',
        
        # 密钥和密码
        'Generating SSH keys': '生成SSH密钥',
        'Setting root password': '设置root密码',
        'Managing Admin password': '管理管理员密码',
        'Publishing password': '发布密码',
        
        # 邮件和通知
        'Sending end of installation mail': '发送安装完成邮件',
        'Sending notification': '发送通知',
        'Notifying completion': '通知完成',
        
        # 常见错误信息
        'Failed': '失败',
        'Failed to download': '下载失败',
        'Failed to install': '安装失败',
        'Error': '错误',
        'Partition error': '分区错误',
        'Boot configuration failed': '启动配置失败',
        'Network configuration failed': '网络配置失败',
        'Timeout': '超时',
    }
    
    # 如果为空，直接返回
    if not comment or comment.strip() == '':
        return comment
    
    # 尝试完全匹配（忽略大小写）
    for key, value in translations.items():
        if comment.lower() == key.lower():
            return value
    
    # 尝试部分匹配（包含关键词）
    comment_lower = comment.lower()
    for eng, chn in translations.items():
        if eng.lower() in comment_lower:
            return chn
    
    # 如果没有匹配，记录日志并返回原文
    add_log("WARNING", f"[翻译] 未找到翻译: '{comment}'", "server_control")
    return comment

@app.route('/api/server-control/<service_name>/install/status', methods=['GET', 'OPTIONS'])
def get_install_status(service_name):
    """获取系统安装进度"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取安装进度
        status = client.get(f'/dedicated/server/{service_name}/install/status')
        
        elapsed_time = status.get('elapsedTime', 0)
        progress_steps = status.get('progress', [])
        
        # 计算总体进度百分比
        total_steps = len(progress_steps)
        completed_steps = sum(1 for step in progress_steps if step.get('status') == 'done')
        progress_percentage = int((completed_steps / total_steps * 100)) if total_steps > 0 else 0
        
        # 检查是否有错误
        has_error = any(step.get('status') == 'error' for step in progress_steps)
        
        # 检查是否全部完成
        all_done = total_steps > 0 and completed_steps == total_steps
        
        # 格式化步骤信息（翻译成中文）
        formatted_steps = []
        for step in progress_steps:
            original_comment = step.get('comment', '')
            translated_comment = translate_install_step(original_comment)
            
            formatted_steps.append({
                'comment': translated_comment,
                'commentOriginal': original_comment,  # 保留原文以便调试
                'status': step.get('status', 'unknown'),
                'error': step.get('error', '')
            })
            
            # 如果有错误，输出详细信息
            if step.get('status') == 'error':
                add_log("ERROR", f"❌ 安装步骤错误: {original_comment}", "server_control")
                add_log("ERROR", f"   错误信息: {step.get('error', 'No error message')}", "server_control")
                add_log("ERROR", f"   完整步骤数据: {json.dumps(step, indent=2)}", "server_control")
        
        add_log("INFO", f"获取服务器 {service_name} 安装进度: {progress_percentage}%", "server_control")
        
        # 如果有错误，输出所有步骤的状态
        if has_error:
            add_log("ERROR", f"⚠️ 安装过程中检测到错误！", "server_control")
            add_log("ERROR", f"   总步骤数: {total_steps}", "server_control")
            add_log("ERROR", f"   已完成: {completed_steps}", "server_control")
            add_log("ERROR", f"   所有步骤状态:", "server_control")
            for i, step in enumerate(progress_steps, 1):
                add_log("ERROR", f"   步骤 {i}: [{step.get('status')}] {step.get('comment')}", "server_control")
        
        return jsonify({
            "success": True,
            "status": {
                'elapsedTime': elapsed_time,
                'progressPercentage': progress_percentage,
                'totalSteps': total_steps,
                'completedSteps': completed_steps,
                'hasError': has_error,
                'allDone': all_done,
                'steps': formatted_steps
            }
        })
        
    except Exception as e:
        error_message = str(e)
        error_type = type(e).__name__
        
        # 详细记录错误信息用于调试
        add_log("DEBUG", f"[Install Status] 异常类型: {error_type}, 错误信息: {error_message}", "server_control")
        
        # 检查是否是"没有安装进度"的错误
        # OVH API在没有进行中的安装时可能返回多种错误
        error_lower = error_message.lower()
        
        # 常见的"无安装进度"错误特征
        no_install_indicators = [
            '404',
            'not found',
            'no installation',
            'no task',
            'does not exist',
            'resource not found',
            'this service is not', 
            'no os installation',
            'not installing',
            'installation not found',
            'not being installed',      # OVH: Server is not being installed
            'not being reinstalled',    # OVH: Server is not being reinstalled
            'being installed or reinstalled at the moment'  # 完整匹配
        ]
        
        is_no_install = any(indicator in error_lower for indicator in no_install_indicators)
        
        if is_no_install:
            add_log("INFO", f"服务器 {service_name} 当前没有正在进行的安装 (原因: {error_message[:100]})", "server_control")
            # 返回200状态码，但标记没有安装进度（避免浏览器显示404错误）
            return jsonify({
                "success": True,
                "hasInstallation": False,  # 标记：没有正在进行的安装
                "message": "当前没有正在进行的安装"
            }), 200
        
        # 其他错误返回500
        add_log("ERROR", f"获取服务器 {service_name} 安装进度失败: [{error_type}] {error_message}", "server_control")
        return jsonify({"success": False, "error": error_message, "type": error_type}), 500

@app.route('/api/server-control/<service_name>/tasks', methods=['OPTIONS', 'GET'])
def get_server_tasks(service_name):
    """获取服务器任务列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取任务列表
        task_ids = client.get(f'/dedicated/server/{service_name}/task')
        
        tasks = []
        # 只获取最近10个任务的详情
        for task_id in task_ids[-10:]:
            try:
                task_detail = client.get(f'/dedicated/server/{service_name}/task/{task_id}')
                tasks.append({
                    'taskId': task_id,
                    'function': task_detail.get('function', 'N/A'),
                    'status': task_detail.get('status', 'unknown'),
                    'comment': task_detail.get('comment', ''),
                    'startDate': task_detail.get('startDate', ''),
                    'doneDate': task_detail.get('doneDate', '')
                })
            except:
                pass
        
        return jsonify({
            "success": True,
            "tasks": tasks,
            "total": len(tasks)
        })
        
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 任务列表失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== 任务可用时间段（计划干预） ====================
@app.route('/api/server-control/<path:service_name>/tasks/<int:task_id>/available-timeslots', methods=['GET', 'OPTIONS'])
def get_task_available_timeslots(service_name, task_id):
    """查询指定任务在时间范围内的可用时间段"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401

    try:
        period_start = request.args.get('periodStart')
        period_end = request.args.get('periodEnd')

        if not period_start or not period_end:
            return jsonify({"success": False, "error": "缺少 periodStart 或 periodEnd 参数 (ISO8601)"}), 400

        add_log("INFO", f"[Task] 查询任务 {task_id} 的可用时间段 {period_start} -> {period_end}", "server_control")

        slots = client.get(
            f'/dedicated/server/{service_name}/task/{task_id}/availableTimeslots',
            periodStart=period_start,
            periodEnd=period_end
        )

        return jsonify({
            "success": True,
            "timeslots": slots or []
        })
    except OvhAPIError as e:
        message = str(e)
        # 无需预约：返回200并标注
        if 'no schedule needed' in message.lower():
            add_log("INFO", f"[Task] 任务无需预约: {message}", "server_control")
            return jsonify({
                "success": True,
                "timeslots": [],
                "scheduleNotRequired": True,
                "message": "该任务无需预约"
            }), 200
        # 任务或服务器不存在 → 404
        if 'not found' in message.lower() or 'does not exist' in message.lower():
            return jsonify({"success": False, "error": "任务或服务器不存在"}), 404
        add_log("ERROR", f"[Task] 可用时间段API错误: {message}", "server_control")
        return jsonify({"success": False, "error": message}), 502
    except Exception as e:
        add_log("ERROR", f"[Task] 查询可用时间段失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<path:service_name>/tasks/<int:task_id>/schedule', methods=['POST', 'OPTIONS'])
def schedule_task_timeslot(service_name, task_id):
    """为任务预约执行时间段"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401

    try:
        data = request.get_json() or {}
        start_date = data.get('startDate')
        end_date = data.get('endDate')

        if not start_date or not end_date:
            return jsonify({"success": False, "error": "缺少 startDate 或 endDate (ISO8601)"}), 400

        add_log("INFO", f"[Task] 预约任务 {task_id} 时间段 {start_date} -> {end_date}", "server_control")

        result = client.post(
            f'/dedicated/server/{service_name}/task/{task_id}/schedule',
            startDate=start_date,
            endDate=end_date
        )

        return jsonify({
            "success": True,
            "result": result
        })
    except OvhAPIError as e:
        message = str(e)
        # 无需预约：直接提示不支持预约
        if 'no schedule needed' in message.lower():
            return jsonify({"success": False, "error": "该任务无需或不支持预约"}), 400
        if 'not found' in message.lower() or 'does not exist' in message.lower():
            return jsonify({"success": False, "error": "任务或服务器不存在"}), 404
        add_log("ERROR", f"[Task] 预约任务API错误: {message}", "server_control")
        return jsonify({"success": False, "error": message}), 502
    except Exception as e:
        add_log("ERROR", f"[Task] 预约任务失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== 服务器高级管理功能 ====================

@app.route('/api/server-control/<service_name>/boot', methods=['OPTIONS', 'GET'])
def get_boot_config(service_name):
    """获取服务器启动配置"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        server_info = client.get(f'/dedicated/server/{service_name}')
        boot_id = server_info.get('bootId')
        boot_list = client.get(f'/dedicated/server/{service_name}/boot')
        boots = []
        
        for bid in boot_list:
            try:
                boot_detail = client.get(f'/dedicated/server/{service_name}/boot/{bid}')
                boots.append({
                    'id': bid,
                    'bootType': boot_detail.get('bootType', 'N/A'),
                    'description': boot_detail.get('description', ''),
                    'kernel': boot_detail.get('kernel', ''),
                    'isCurrent': bid == boot_id
                })
            except:
                pass
        
        return jsonify({"success": True, "currentBootId": boot_id, "boots": boots})
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 启动配置失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/boot/<int:boot_id>', methods=['OPTIONS', 'PUT'])
def set_boot_config(service_name, boot_id):
    """设置服务器启动模式"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        client.put(f'/dedicated/server/{service_name}', bootId=boot_id)
        add_log("INFO", f"服务器 {service_name} 启动模式已设置为 {boot_id}", "server_control")
        return jsonify({"success": True, "message": "启动模式已更新，重启后生效"})
    except Exception as e:
        add_log("ERROR", f"设置服务器 {service_name} 启动模式失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/monitoring', methods=['OPTIONS', 'GET'])
def get_monitoring_status(service_name):
    """获取监控状态"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        server_info = client.get(f'/dedicated/server/{service_name}')
        return jsonify({"success": True, "monitoring": server_info.get('monitoring', False)})
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 监控状态失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/monitoring', methods=['OPTIONS', 'PUT'])
def set_monitoring_status(service_name):
    """设置监控状态"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.json
    enabled = data.get('enabled', False)
    
    try:
        client.put(f'/dedicated/server/{service_name}', monitoring=enabled)
        add_log("INFO", f"服务器 {service_name} 监控已{'开启' if enabled else '关闭'}", "server_control")
        return jsonify({"success": True, "message": f"监控已{'开启' if enabled else '关闭'}"})
    except Exception as e:
        add_log("ERROR", f"设置服务器 {service_name} 监控状态失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/hardware', methods=['OPTIONS', 'GET'])
def get_hardware_info(service_name):
    """获取硬件详细信息"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        hardware = client.get(f'/dedicated/server/{service_name}/specifications/hardware')
        return jsonify({
            "success": True,
            "hardware": {
                'bootMode': hardware.get('bootMode', 'N/A'),
                'coresPerProcessor': hardware.get('coresPerProcessor', 0),
                'threadsPerProcessor': hardware.get('threadsPerProcessor', 0),
                'numberOfProcessors': hardware.get('numberOfProcessors', 0),
                'processorName': hardware.get('processorName', 'N/A'),
                'processorArchitecture': hardware.get('processorArchitecture', 'N/A'),
                'memorySize': hardware.get('memorySize', {}),
                'motherboard': hardware.get('motherboard', 'N/A'),
                'formFactor': hardware.get('formFactor', 'N/A'),
                'description': hardware.get('description', ''),
                'diskGroups': hardware.get('diskGroups', []),
                'expansionCards': hardware.get('expansionCards', []),
                'usbKeys': hardware.get('usbKeys', []),
                'defaultHardwareRaidSize': hardware.get('defaultHardwareRaidSize', {}),
                'defaultHardwareRaidType': hardware.get('defaultHardwareRaidType', 'N/A')
            }
        })
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 硬件信息失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/network-specs', methods=['OPTIONS', 'GET'])
def get_network_specs(service_name):
    """获取网络规格详细信息"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        network = client.get(f'/dedicated/server/{service_name}/specifications/network')
        return jsonify({
            "success": True,
            "network": {
                'bandwidth': network.get('bandwidth', {}),
                'connection': network.get('connection', {}),
                'ola': network.get('ola', {}),
                'routing': network.get('routing', {}),
                'traffic': network.get('traffic', {}),
                'switching': network.get('switching', {}),
                'vmac': network.get('vmac', {}),
                'vrack': network.get('vrack', {})
            }
        })
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 网络规格失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/ips', methods=['OPTIONS', 'GET'])
def get_server_ips(service_name):
    """获取服务器IP列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        ip_list = client.get(f'/dedicated/server/{service_name}/ips')
        ips = []
        for ip in ip_list:
            try:
                ip_detail = client.get(f'/ip/{ip.replace("/", "%2F")}')
                ips.append({
                    'ip': ip,
                    'type': ip_detail.get('type', 'N/A'),
                    'description': ip_detail.get('description', ''),
                    'routedTo': ip_detail.get('routedTo', {}).get('serviceName', '')
                })
            except:
                ips.append({'ip': ip, 'type': 'unknown'})
        
        return jsonify({"success": True, "ips": ips, "total": len(ips)})
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} IP列表失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/reverse', methods=['OPTIONS', 'GET'])
def get_reverse_dns(service_name):
    """获取反向DNS"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        server_info = client.get(f'/dedicated/server/{service_name}')
        main_ip = server_info.get('ip')
        reverse_list = []
        if main_ip:
            try:
                reverses = client.get(f'/dedicated/server/{service_name}/reverse')
                for rev_ip in reverses:
                    rev_detail = client.get(f'/dedicated/server/{service_name}/reverse/{rev_ip}')
                    reverse_list.append({'ipReverse': rev_ip, 'reverse': rev_detail.get('reverse', '')})
            except:
                pass
        
        return jsonify({"success": True, "reverses": reverse_list})
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 反向DNS失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/reverse', methods=['OPTIONS', 'POST'])
def set_reverse_dns(service_name):
    """设置反向DNS"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.json
    ip_address = data.get('ip')
    reverse = data.get('reverse')
    
    if not ip_address or not reverse:
        return jsonify({"success": False, "error": "IP地址和反向DNS不能为空"}), 400
    
    try:
        client.post(f'/dedicated/server/{service_name}/reverse', ipReverse=ip_address, reverse=reverse)
        add_log("INFO", f"服务器 {service_name} IP {ip_address} 反向DNS已设置为 {reverse}", "server_control")
        return jsonify({"success": True, "message": "反向DNS已设置"})
    except Exception as e:
        add_log("ERROR", f"设置服务器 {service_name} 反向DNS失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/serviceinfo', methods=['OPTIONS', 'GET'])
def get_service_info(service_name):
    """获取服务信息（到期时间等）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        service_info = client.get(f'/dedicated/server/{service_name}/serviceInfos')
        return jsonify({
            "success": True,
            "serviceInfo": {
                'status': service_info.get('status', 'unknown'),
                'expiration': service_info.get('expiration', ''),
                'creation': service_info.get('creation', ''),
                'renewalType': service_info.get('renew', {}).get('automatic', False),
                'renewalPeriod': service_info.get('renew', {}).get('period', 0)
            }
        })
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 服务信息失败: {str(e)}", "server_control")

# ==============================================
# 变更联系人 API（Change Contact）
# ==============================================

@app.route('/api/server-control/<path:service_name>/change-contact', methods=['OPTIONS', 'POST'])
def change_contact(service_name):
    """变更服务器联系人（账户、技术、计费联系人）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.get_json()
    contact_admin = data.get('contactAdmin')  # 管理员联系人
    contact_tech = data.get('contactTech')    # 技术联系人
    contact_billing = data.get('contactBilling')  # 计费联系人
    
    # 至少需要指定一个联系人
    if not any([contact_admin, contact_tech, contact_billing]):
        return jsonify({
            "success": False, 
            "error": "至少需要指定一个联系人（管理员、技术或计费）"
        }), 400
    
    try:
        # 构建请求参数
        params = {}
        if contact_admin:
            params['contactAdmin'] = contact_admin
        if contact_tech:
            params['contactTech'] = contact_tech
        if contact_billing:
            params['contactBilling'] = contact_billing
        
        # 调用OVH API变更联系人
        result = client.post(f'/dedicated/server/{service_name}/changeContact', **params)
        
        add_log("INFO", f"服务器 {service_name} 联系人变更请求已提交: {params}", "server_control")
        
        return jsonify({
            "success": True,
            "message": "联系人变更请求已提交",
            "taskId": result.get('id') if isinstance(result, dict) else None,
            "details": result
        })
        
    except Exception as e:
        error_msg = str(e)
        add_log("ERROR", f"变更服务器 {service_name} 联系人失败: {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

# ==============================================
# 维护记录 API（Intervention）
# ==============================================

@app.route('/api/server-control/<service_name>/interventions', methods=['OPTIONS', 'GET'])
def get_interventions(service_name):
    """获取维护记录列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
        
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取维护记录ID列表
        intervention_ids = client.get(f'/dedicated/server/{service_name}/intervention')
        
        # 获取每个维护记录的详细信息
        interventions = []
        for intervention_id in intervention_ids:
            try:
                detail = client.get(f'/dedicated/server/{service_name}/intervention/{intervention_id}')
                interventions.append(detail)
            except Exception as e:
                add_log("WARNING", f"获取维护记录 {intervention_id} 详情失败: {str(e)}", "server_control")
                continue
        
        return jsonify({
            "success": True,
            "interventions": interventions
        })
        
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 维护记录失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/interventions/<intervention_id>', methods=['OPTIONS', 'GET'])
def get_intervention_detail(service_name, intervention_id):
    """获取维护记录详情"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
        
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        detail = client.get(f'/dedicated/server/{service_name}/intervention/{intervention_id}')
        
        return jsonify({
            "success": True,
            "intervention": detail
        })
        
    except Exception as e:
        add_log("ERROR", f"获取维护记录详情失败: {service_name} - {intervention_id} - {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==============================================
# 计划维护 API（Planned Intervention）
# ==============================================

@app.route('/api/server-control/<service_name>/planned-interventions', methods=['OPTIONS', 'GET'])
def get_planned_interventions(service_name):
    """获取计划维护列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
        
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取计划维护ID列表
        intervention_ids = client.get(f'/dedicated/server/{service_name}/plannedIntervention')
        
        # 获取每个计划维护的详细信息
        interventions = []
        for intervention_id in intervention_ids:
            try:
                detail = client.get(f'/dedicated/server/{service_name}/plannedIntervention/{intervention_id}')
                interventions.append(detail)
            except Exception as e:
                add_log("WARNING", f"获取计划维护 {intervention_id} 详情失败: {str(e)}", "server_control")
                continue
        
        return jsonify({
            "success": True,
            "plannedInterventions": interventions
        })
        
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 计划维护失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/planned-interventions/<int:intervention_id>', methods=['OPTIONS', 'GET'])
def get_planned_intervention_detail(service_name, intervention_id):
    """获取计划维护详情"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
        
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        detail = client.get(f'/dedicated/server/{service_name}/plannedIntervention/{intervention_id}')
        
        return jsonify({
            "success": True,
            "plannedIntervention": detail
        })
        
    except Exception as e:
        add_log("ERROR", f"获取计划维护详情失败: {service_name} - {intervention_id} - {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==============================================
# 硬件更换 API（Hardware Replacement）
# ==============================================

@app.route('/api/server-control/<service_name>/hardware/replace', methods=['OPTIONS', 'POST'])
def hardware_replace(service_name):
    """硬件更换支持（硬盘、内存、散热器）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
        
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        data = request.json
        component_type = data.get('componentType')
        comment = data.get('comment', '')
        
        if not component_type:
            return jsonify({"success": False, "error": "缺少 componentType 参数"}), 400
        
        # 根据不同的组件类型调用不同的 OVH API
        result = None
        
        if component_type == 'hardDiskDrive':
            # 硬盘更换：需要 disks 和 inverse 参数
            result = client.post(
                f'/dedicated/server/{service_name}/support/replace/hardDiskDrive',
                comment=comment or "Request hard disk drive replacement - faulty disk detected",
                disks=[],  # 空数组表示自动检测所有故障硬盘
                inverse=True  # 替换所有故障硬盘
            )
        elif component_type == 'memory':
            # 内存更换：需要 details 参数
            details = data.get('details', 'Memory module failure')
            result = client.post(
                f'/dedicated/server/{service_name}/support/replace/memory',
                comment=comment or "Request memory module replacement - hardware failure detected",
                details=details,
                slotsDescription=""
            )
        elif component_type == 'cooling':
            # 散热器更换：需要 details 参数
            details = data.get('details', 'Cooling system failure')
            result = client.post(
                f'/dedicated/server/{service_name}/support/replace/cooling',
                comment=comment or "Request cooling system replacement - fan failure or overheating",
                details=details
            )
        else:
            return jsonify({
                "success": False,
                "error": f"不支持的组件类型: {component_type}"
            }), 400
        
        add_log("INFO", f"硬件更换请求已发送: {service_name} - {component_type}", "server_control")
        
        return jsonify({
            "success": True,
            "message": "硬件更换请求已发送",
            "task": result
        })
        
    except Exception as e:
        error_msg = str(e)
        add_log("ERROR", f"硬件更换失败: {service_name} - {component_type} - {error_msg}", "server_control")
        
        # 检查是否是"Action pending"错误（已有待处理的硬件更换请求）
        if "Action pending" in error_msg:
            # 提取 ticketId（如果有）
            import re
            ticket_match = re.search(r'ticketId[:\s]+(\d+)', error_msg)
            ticket_id = ticket_match.group(1) if ticket_match else "未知"
            
            return jsonify({
                "success": False,
                "error": f"已有待处理的硬件更换工单 (Ticket #{ticket_id})，请等待完成后再提交新请求",
                "ticketId": ticket_id,
                "isPending": True
            }), 400
        
        # 其他错误
        return jsonify({
            "success": False,
            "error": error_msg
        }), 500
@app.route('/api/server-control/<service_name>/network-interfaces', methods=['OPTIONS', 'GET'])
def get_network_interfaces(service_name):
    """获取物理网卡列表（NetworkInterfaceController）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        add_log("INFO", f"[网卡] 获取物理网卡列表: {service_name}", "server_control")
        
        # 获取物理网卡MAC地址列表
        mac_addresses = client.get(f'/dedicated/server/{service_name}/networkInterfaceController')
        
        interfaces = []
        for mac in mac_addresses:
            try:
                # 获取每个网卡的详细信息
                interface_detail = client.get(f'/dedicated/server/{service_name}/networkInterfaceController/{mac}')
                interfaces.append({
                    'mac': mac,
                    'linkType': interface_detail.get('linkType'),  # public, private, public_lag等
                    'virtualNetworkInterface': interface_detail.get('virtualNetworkInterface'),  # 关联的虚拟接口UUID（如果有）
                })
            except Exception as e:
                add_log("WARN", f"[网卡] 获取网卡详情失败 {mac}: {str(e)}", "server_control")
                # 即使单个网卡获取失败，也继续处理其他网卡
                interfaces.append({
                    'mac': mac,
                    'linkType': 'unknown',
                    'error': str(e)
                })
        
        add_log("INFO", f"[网卡] 找到 {len(interfaces)} 个物理网卡", "server_control")
        
        return jsonify({
            "success": True,
            "interfaces": interfaces,
            "count": len(interfaces)
        })
        
    except Exception as e:
        error_msg = str(e)
        add_log("ERROR", f"[网卡] 获取物理网卡列表失败: {service_name} - {error_msg}", "server_control")
        
        # 如果API调用失败，返回空列表
        if "does not exist" in error_msg.lower() or "not found" in error_msg.lower():
            return jsonify({
                "success": True,
                "interfaces": [],
                "count": 0,
                "message": "该服务器暂无网卡信息"
            })
        
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<service_name>/mrtg', methods=['OPTIONS', 'GET'])
def get_mrtg_data(service_name):
    """获取MRTG流量监控数据（支持多网卡）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取查询参数
        period = request.args.get('period', 'daily')  # hourly, daily, weekly, monthly, yearly
        traffic_type = request.args.get('type', 'traffic:download')  # traffic:download, traffic:upload, etc.
        
        add_log("INFO", f"[MRTG] 获取流量数据: {service_name} - {period} - {traffic_type}", "server_control")
        
        # 先获取服务器的所有网卡
        try:
            mac_addresses = client.get(f'/dedicated/server/{service_name}/networkInterfaceController')
        except Exception as e:
            # 如果获取网卡失败，尝试使用旧的MRTG API（已弃用但仍可用）
            add_log("WARN", f"[MRTG] 无法获取网卡列表，使用旧版API: {str(e)}", "server_control")
            try:
                data = client.get(f'/dedicated/server/{service_name}/mrtg', period=period, type=traffic_type)
                return jsonify({
                    "success": True,
                    "data": data,
                    "period": period,
                    "type": traffic_type,
                    "interfaces": []
                })
            except Exception as legacy_error:
                raise Exception(f"新旧API均失败: {str(legacy_error)}")
        
        # 获取每个网卡的MRTG数据
        all_data = []
        for mac in mac_addresses:
            try:
                # 使用新版API（按网卡）
                mrtg_data = client.get(
                    f'/dedicated/server/{service_name}/networkInterfaceController/{mac}/mrtg',
                    period=period,
                    type=traffic_type
                )
                
                all_data.append({
                    'mac': mac,
                    'data': mrtg_data
                })
                add_log("INFO", f"[MRTG] 获取网卡 {mac} 数据成功: {len(mrtg_data)} 个数据点", "server_control")
            except Exception as e:
                add_log("WARN", f"[MRTG] 获取网卡 {mac} 数据失败: {str(e)}", "server_control")
                all_data.append({
                    'mac': mac,
                    'data': [],
                    'error': str(e)
                })
        
        add_log("INFO", f"[MRTG] 成功获取 {len(all_data)} 个网卡的流量数据", "server_control")
        
        return jsonify({
            "success": True,
            "interfaces": all_data,
            "period": period,
            "type": traffic_type,
            "server": service_name
        })
        
    except Exception as e:
        error_msg = str(e)
        add_log("ERROR", f"[MRTG] 获取流量数据失败: {service_name} - {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<service_name>/ola/aggregation', methods=['OPTIONS', 'POST'])
def configure_ola_aggregation(service_name):
    """OLA网络聚合: 将多个网络接口聚合以提升带宽（链路聚合/Link Aggregation）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        data = request.get_json()
        name = data.get('name')
        virtual_network_interfaces = data.get('virtualNetworkInterfaces', [])
        
        if not name:
            return jsonify({"success": False, "error": "缺少聚合名称(name)参数"}), 400
        
        if not virtual_network_interfaces or len(virtual_network_interfaces) < 2:
            return jsonify({"success": False, "error": "至少需要2个网络接口进行聚合"}), 400
        
        add_log("INFO", f"[OLA] 配置网络聚合: {service_name} - {name} - {len(virtual_network_interfaces)}个接口", "server_control")
        
        # 调用OVH API配置网络聚合
        result = client.post(
            f'/dedicated/server/{service_name}/ola/aggregation',
            name=name,
            virtualNetworkInterfaces=virtual_network_interfaces
        )
        
        add_log("INFO", f"[OLA] 网络聚合配置任务已创建: Task#{result.get('taskId')}", "server_control")
        
        return jsonify({
            "success": True,
            "message": "网络聚合配置任务已创建",
            "task": result
        })
        
    except Exception as e:
        error_msg = str(e)
        add_log("ERROR", f"[OLA] 配置网络聚合失败: {service_name} - {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<service_name>/ola/reset', methods=['OPTIONS', 'POST'])
def reset_ola_configuration(service_name):
    """OLA网络聚合: 重置网络接口到默认配置"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        data = request.get_json()
        virtual_network_interface = data.get('virtualNetworkInterface')
        
        if not virtual_network_interface:
            return jsonify({"success": False, "error": "缺少虚拟网络接口UUID(virtualNetworkInterface)参数"}), 400
        
        add_log("INFO", f"[OLA] 重置网络接口: {service_name} - {virtual_network_interface}", "server_control")
        
        # 调用OVH API重置网络配置
        result = client.post(
            f'/dedicated/server/{service_name}/ola/reset',
            virtualNetworkInterface=virtual_network_interface
        )
        
        add_log("INFO", f"[OLA] 网络接口重置任务已创建: Task#{result.get('taskId')}", "server_control")
        
        return jsonify({
            "success": True,
            "message": "网络接口重置任务已创建",
            "task": result
        })
        
    except Exception as e:
        error_msg = str(e)
        add_log("ERROR", f"[OLA] 重置网络接口失败: {service_name} - {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<path:service_name>/hardware-raid-profiles', methods=['GET', 'OPTIONS'])
def get_hardware_raid_profiles(service_name):
    """获取硬件RAID配置信息"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取硬件RAID配置文件
        profiles = client.get(f'/dedicated/server/{service_name}/install/hardwareRaidProfile')
        
        add_log("INFO", f"获取服务器 {service_name} 硬件RAID配置成功", "server_control")
        return jsonify({
            "success": True,
            "profiles": profiles,
            "supported": True
        })
    except Exception as e:
        error_msg = str(e)
        # 如果是不支持硬件RAID，返回成功但profiles为空
        if "not supported" in error_msg.lower():
            add_log("INFO", f"服务器 {service_name} 不支持硬件RAID", "server_control")
            return jsonify({
                "success": True,
                "profiles": [],
                "supported": False,
                "message": "此服务器不支持硬件RAID"
            })
        else:
            add_log("ERROR", f"获取服务器 {service_name} 硬件RAID配置失败: {error_msg}", "server_control")
            return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<path:service_name>/hardware-disk-info', methods=['GET', 'OPTIONS'])
def get_hardware_disk_info(service_name):
    """获取服务器硬件磁盘信息"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取硬件规格（包含磁盘信息）
        hardware = client.get(f'/dedicated/server/{service_name}/specifications/hardware')
        
        # 调试：输出原始硬件信息
        add_log("DEBUG", f"原始硬件信息: {json.dumps(hardware, indent=2)}", "server_control")
        
        # 提取磁盘信息
        disk_groups = {}
        
        if 'diskGroups' in hardware:
            add_log("INFO", f"找到 {len(hardware['diskGroups'])} 个磁盘组", "server_control")
            for group_info in hardware['diskGroups']:
                # 使用实际的 diskGroupId，而不是枚举索引
                disk_group_id = group_info.get('diskGroupId', 0)
                add_log("DEBUG", f"磁盘组 {disk_group_id} 原始信息: {json.dumps(group_info, indent=2)}", "server_control")
                
                # 获取磁盘数量和大小信息
                number_of_disks = group_info.get('numberOfDisks', 0)
                disk_size = group_info.get('diskSize', {})
                disk_size_value = disk_size.get('value', 0) if disk_size else 0
                disk_size_unit = disk_size.get('unit', 'GB') if disk_size else 'GB'
                
                add_log("DEBUG", f"磁盘组 {disk_group_id}: {number_of_disks}块 x {disk_size_value}{disk_size_unit}", "server_control")
                
                disk_group = {
                    'id': disk_group_id,  # 使用实际的 diskGroupId
                    'diskType': group_info.get('diskType'),
                    'description': group_info.get('description'),
                    'raidController': group_info.get('raidController'),
                    'disks': []
                }
                
                # 根据 numberOfDisks 和 diskSize 生成磁盘对象数组
                for disk_number in range(number_of_disks):
                    disk_info = {
                        'capacity': disk_size_value,
                        'unit': disk_size_unit,
                        'number': disk_number + 1,  # 磁盘编号从1开始
                        'diskType': group_info.get('diskType')
                    }
                    disk_group['disks'].append(disk_info)
                
                add_log("DEBUG", f"磁盘组 {disk_group_id} 生成 {len(disk_group['disks'])} 个磁盘对象", "server_control")
                disk_groups[str(disk_group_id)] = disk_group
        
        add_log("INFO", f"获取服务器 {service_name} 磁盘信息成功: {len(disk_groups)} 个磁盘组", "server_control")
        return jsonify({
            "success": True,
            "diskGroups": disk_groups,
            "hardware": hardware
        })
    except Exception as e:
        error_msg = str(e)
        add_log("ERROR", f"获取服务器 {service_name} 硬件磁盘信息失败: {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<path:service_name>/partition-schemes', methods=['GET', 'OPTIONS'])
def get_partition_schemes(service_name):
    """获取可用的分区方案"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取模板的分区方案
        data = request.args
        template_name = data.get('templateName')
        
        add_log("INFO", f"[Partition] 请求获取分区方案: server={service_name}, template={template_name}", "server_control")
        
        if not template_name:
            add_log("ERROR", f"[Partition] 缺少templateName参数", "server_control")
            return jsonify({"success": False, "error": "缺少templateName参数"}), 400
        
        from urllib.parse import quote
        
        # URL编码模板名称，避免特殊字符问题
        encoded_template = quote(template_name, safe='')
        
        schemes = client.get(f'/dedicated/installationTemplate/{encoded_template}/partitionScheme')
        add_log("INFO", f"[Partition] OVH返回方案列表: {schemes}", "server_control")
        scheme_details = []
        
        for scheme_name in schemes:
            try:
                add_log("INFO", f"[Partition] 处理方案: {scheme_name}", "server_control")
                
                # URL编码方案名称
                encoded_scheme = quote(scheme_name, safe='')
                
                # 获取方案信息
                scheme_url = f'/dedicated/installationTemplate/{encoded_template}/partitionScheme/{encoded_scheme}'
                add_log("INFO", f"[Partition] 获取方案信息URL: {scheme_url}", "server_control")
                scheme_info = client.get(scheme_url)
                
                # 获取分区列表
                partition_url = f'/dedicated/installationTemplate/{encoded_template}/partitionScheme/{encoded_scheme}/partition'
                add_log("INFO", f"[Partition] 获取分区列表URL: {partition_url}", "server_control")
                partitions = client.get(partition_url)
                
                partition_details = []
                for partition_name in partitions:
                    encoded_partition = quote(partition_name, safe='')
                    partition_info = client.get(f'/dedicated/installationTemplate/{encoded_template}/partitionScheme/{encoded_scheme}/partition/{encoded_partition}')
                    partition_details.append({
                        'mountpoint': partition_name,
                        'filesystem': partition_info.get('filesystem', ''),
                        'size': partition_info.get('size', 0),
                        'order': partition_info.get('order', 0),
                        'raid': partition_info.get('raid', None),
                        'type': partition_info.get('type', 'primary')
                    })
                
                scheme_details.append({
                    'name': scheme_name,
                    'priority': scheme_info.get('priority', 0),
                    'partitions': sorted(partition_details, key=lambda x: x['order'])
                })
            except Exception as e:
                # 如果获取详情失败，至少返回方案名称
                add_log("WARNING", f"[Partition] 获取方案 {scheme_name} 详情失败: {str(e)}", "server_control")
                scheme_details.append({
                    'name': scheme_name,
                    'priority': 0,
                    'partitions': []
                })
        
        add_log("INFO", f"[Partition] 成功获取 {len(scheme_details)} 个分区方案", "server_control")
        return jsonify({"success": True, "schemes": scheme_details})
    except Exception as e:
        add_log("ERROR", f"[Partition] 获取分区方案失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/console', methods=['GET', 'OPTIONS'])
def get_ipmi_console(service_name):
    """获取IPMI/KVM控制台访问"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        add_log("INFO", f"[IPMI] 获取服务器 {service_name} IPMI信息", "server_control")
        
        # 获取IPMI功能信息
        ipmi_info = client.get(f'/dedicated/server/{service_name}/features/ipmi')
        add_log("INFO", f"[IPMI] IPMI信息: {ipmi_info}", "server_control")
        
        # 根据服务器支持的特性选择访问类型
        supported_features = ipmi_info.get('supportedFeatures', {})
        access_type = None
        
        if supported_features.get('kvmipHtml5URL'):
            access_type = 'kvmipHtml5URL'
        elif supported_features.get('kvmipJnlp'):
            access_type = 'kvmipJnlp'
        elif supported_features.get('serialOverLanURL'):
            access_type = 'serialOverLanURL'
        else:
            add_log("ERROR", f"[IPMI] 服务器不支持任何KVM访问类型", "server_control")
            return jsonify({
                "success": False, 
                "error": "服务器不支持KVM控制台访问"
            }), 400
        
        # 创建KVM控制台访问 - 使用POST方法，包含ttl参数
        add_log("INFO", f"[IPMI] 请求KVM控制台访问，类型: {access_type}", "server_control")
        
        # 获取客户端真实IP（从请求头中获取）
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()
        
        add_log("INFO", f"[IPMI] 客户端IP: {client_ip}", "server_control")
        add_log("INFO", f"[IPMI] X-Forwarded-For: {request.headers.get('X-Forwarded-For')}", "server_control")
        add_log("INFO", f"[IPMI] remote_addr: {request.remote_addr}", "server_control")
        
        # 创建访问任务（返回taskId）
        # ttl有效值: 15, 60, 120, 240, 480, 1440 (分钟)
        # 只有公网IP才添加白名单，避免传入127.0.0.1导致403
        task_params = {
            'type': access_type,
            'ttl': 15  # 15分钟有效期
        }
        
        # 检查是否为有效的公网IP
        if client_ip and not client_ip.startswith('127.') and not client_ip.startswith('192.168.') and not client_ip.startswith('10.'):
            task_params['ipToAllow'] = client_ip
            add_log("INFO", f"[IPMI] 添加IP白名单: {client_ip}", "server_control")
        else:
            add_log("WARNING", f"[IPMI] 跳过IP白名单（本地或内网IP）: {client_ip}", "server_control")
        
        task = client.post(
            f'/dedicated/server/{service_name}/features/ipmi/access',
            **task_params
        )
        
        task_id = task.get('taskId')
        add_log("INFO", f"[IPMI] 创建访问任务: taskId={task_id}, status={task.get('status')}", "server_control")
        
        # 轮询任务状态直到完成
        import time
        max_retries = 10
        retry_count = 0
        task_completed = False
        
        while retry_count < max_retries:
            time.sleep(2)  # 等待2秒
            retry_count += 1
            
            # 检查任务状态
            task_status = client.get(f'/dedicated/server/{service_name}/task/{task_id}')
            status = task_status.get('status')
            add_log("INFO", f"[IPMI] 任务状态检查 ({retry_count}/{max_retries}): {status}", "server_control")
            
            if status == 'done':
                add_log("INFO", f"[IPMI] 任务完成！", "server_control")
                task_completed = True
                break
            elif status in ['cancelled', 'customerError', 'ovhError']:
                add_log("ERROR", f"[IPMI] 任务失败: {status}", "server_control")
                return jsonify({
                    "success": False,
                    "error": f"IPMI访问任务失败: {status}"
                }), 500
        
        # ✅ 检查任务是否真的完成，而不是检查计数器
        if not task_completed:
            add_log("ERROR", f"[IPMI] 任务超时（{max_retries * 2}秒内未完成）", "server_control")
            return jsonify({
                "success": False,
                "error": "IPMI访问任务超时"
            }), 500
        
        # 获取访问URL
        console_access = client.get(
            f'/dedicated/server/{service_name}/features/ipmi/access?type={access_type}'
        )
        
        add_log("INFO", f"[IPMI] 控制台访问信息: {console_access}", "server_control")
        
        return jsonify({
            "success": True,
            "ipmi": ipmi_info,
            "console": console_access,
            "accessType": access_type
        })
        
    except Exception as e:
        add_log("ERROR", f"[IPMI] 获取IPMI控制台失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/boot-mode', methods=['GET', 'OPTIONS'])
def get_boot_modes(service_name):
    """获取可用的启动模式列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        add_log("INFO", f"[Boot] 获取服务器 {service_name} 启动模式列表", "server_control")
        
        # 获取服务器当前配置
        server_info = client.get(f'/dedicated/server/{service_name}')
        current_boot_id = server_info.get('bootId')
        
        # 获取所有可用的启动模式
        boot_ids = client.get(f'/dedicated/server/{service_name}/boot')
        
        boot_modes = []
        for boot_id in boot_ids:
            boot_info = client.get(f'/dedicated/server/{service_name}/boot/{boot_id}')
            boot_modes.append({
                'id': boot_id,
                'bootType': boot_info.get('bootType'),
                'description': boot_info.get('description'),
                'kernel': boot_info.get('kernel'),
                'active': boot_id == current_boot_id
            })
        
        add_log("INFO", f"[Boot] 找到 {len(boot_modes)} 个启动模式", "server_control")
        
        return jsonify({
            "success": True,
            "currentBootId": current_boot_id,
            "bootModes": boot_modes
        })
        
    except Exception as e:
        add_log("ERROR", f"[Boot] 获取启动模式失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/boot-mode', methods=['PUT', 'OPTIONS'])
def change_boot_mode(service_name):
    """切换启动模式（如切换到Rescue模式）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        data = request.get_json()
        boot_id = data.get('bootId')
        
        if not boot_id:
            return jsonify({"success": False, "error": "缺少bootId参数"}), 400
        
        add_log("INFO", f"[Boot] 切换服务器 {service_name} 启动模式到 {boot_id}", "server_control")
        
        # 修改服务器启动配置
        result = client.put(
            f'/dedicated/server/{service_name}',
            bootId=boot_id
        )
        
        add_log("INFO", f"[Boot] 启动模式切换成功，需要重启服务器生效", "server_control")
        
        return jsonify({
            "success": True,
            "message": "启动模式已切换，需要重启服务器生效",
            "bootId": boot_id
        })
        
    except Exception as e:
        add_log("ERROR", f"[Boot] 切换启动模式失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/statistics', methods=['GET', 'OPTIONS'])
def get_traffic_statistics(service_name):
    """获取服务器流量统计"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        add_log("INFO", f"[Stats] 获取服务器 {service_name} 流量统计", "server_control")
        
        # 获取时间范围参数（默认最近24小时）
        period = request.args.get('period', 'lastday')  # lastday, lastweek, lastmonth, lastyear
        type_param = request.args.get('type', 'traffic:download')  # traffic:download, traffic:upload
        
        # 先检查是否支持statistics API
        try:
            # 尝试使用requests库直接调用（因为OVH SDK对这个API支持有问题）
            import requests as req
            
            # 根据endpoint配置动态构建API URL
            base_url = get_api_base_url()
            api_url = f"{base_url}/1.0/dedicated/server/{service_name}/statistics?period={period}&type={type_param}"
            
            # 获取OVH认证信息
            app_key = config.get('appKey', '')
            app_secret = config.get('appSecret', '')
            consumer_key = config.get('consumerKey', '')
            
            headers = {
                'X-Ovh-Application': app_key,
                'X-Ovh-Consumer': consumer_key
            }
            
            add_log("INFO", f"[Stats] 请求API: {api_url}", "server_control")
            response = req.get(api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                stats = response.json()
                add_log("INFO", f"[Stats] 流量统计获取成功，共 {len(stats)} 个数据点", "server_control")
                
                return jsonify({
                    "success": True,
                    "statistics": stats,
                    "period": period,
                    "type": type_param
                })
            else:
                add_log("ERROR", f"[Stats] API返回错误: {response.status_code} - {response.text}", "server_control")
                return jsonify({
                    "success": False,
                    "error": f"流量统计API不可用 (HTTP {response.status_code})"
                }), 500
                
        except Exception as stats_error:
            add_log("ERROR", f"[Stats] 流量统计API调用失败: {str(stats_error)}", "server_control")
            
            # 返回友好的错误提示
            return jsonify({
                "success": False,
                "error": "该服务器可能不支持流量统计功能",
                "details": str(stats_error)
            }), 500
        
    except Exception as e:
        add_log("ERROR", f"[Stats] 获取流量统计失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/network-stats', methods=['GET', 'OPTIONS'])
def get_network_interface_stats(service_name):
    """获取网络接口详细信息"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        add_log("INFO", f"[Network] 获取服务器 {service_name} 网络接口信息", "server_control")
        
        # 获取网络接口控制器信息
        network_info = client.get(f'/dedicated/server/{service_name}/networkInterfaceController')
        
        interfaces = []
        for mac in network_info:
            interface_detail = client.get(
                f'/dedicated/server/{service_name}/networkInterfaceController/{mac}'
            )
            interfaces.append(interface_detail)
        
        add_log("INFO", f"[Network] 找到 {len(interfaces)} 个网络接口", "server_control")
        
        return jsonify({
            "success": True,
            "interfaces": interfaces
        })
        
    except Exception as e:
        add_log("ERROR", f"[Network] 获取网络接口信息失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Burst 突发带宽 ====================
@app.route('/api/server-control/<service_name>/burst', methods=['OPTIONS', 'GET'])
def get_burst(service_name):
    """获取突发带宽状态"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        burst = client.get(f'/dedicated/server/{service_name}/burst')
        add_log("INFO", f"获取服务器 {service_name} 突发带宽状态成功", "server_control")
        return jsonify({
            "success": True,
            "burst": burst
        })
    except Exception as e:
        error_msg = str(e)
        # 如果对象不存在，说明该服务器不支持突发带宽
        if 'does not exist' in error_msg.lower() or 'not exist' in error_msg.lower():
            add_log("INFO", f"服务器 {service_name} 不支持突发带宽功能", "server_control")
            return jsonify({
                "success": False,
                "error": "该服务器不支持突发带宽功能",
                "notAvailable": True
            }), 404
        add_log("ERROR", f"获取服务器 {service_name} 突发带宽失败: {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<service_name>/burst', methods=['OPTIONS', 'PUT'])
def update_burst(service_name):
    """更新突发带宽状态"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.get_json()
    status = data.get('status')  # active, inactive, inactivePending
    
    if not status:
        return jsonify({"success": False, "error": "缺少status参数"}), 400
    
    try:
        result = client.put(f'/dedicated/server/{service_name}/burst', status=status)
        add_log("INFO", f"更新服务器 {service_name} 突发带宽状态为: {status}", "server_control")
        return jsonify({
            "success": True,
            "message": "突发带宽状态已更新",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"更新服务器 {service_name} 突发带宽失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Firewall 防火墙 ====================
@app.route('/api/server-control/<service_name>/firewall', methods=['OPTIONS', 'GET'])
def get_firewall(service_name):
    """获取防火墙状态"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        firewall = client.get(f'/dedicated/server/{service_name}/features/firewall')
        add_log("INFO", f"获取服务器 {service_name} 防火墙状态成功", "server_control")
        return jsonify({
            "success": True,
            "firewall": firewall
        })
    except Exception as e:
        error_msg = str(e)
        # 如果对象不存在，说明该服务器不支持防火墙
        if 'does not exist' in error_msg.lower() or 'not exist' in error_msg.lower():
            add_log("INFO", f"服务器 {service_name} 不支持防火墙功能", "server_control")
            return jsonify({
                "success": False,
                "error": "该服务器不支持防火墙功能",
                "notAvailable": True
            }), 404
        add_log("ERROR", f"获取服务器 {service_name} 防火墙失败: {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<service_name>/firewall', methods=['OPTIONS', 'PUT'])
def update_firewall(service_name):
    """更新防火墙状态"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.get_json()
    enabled = data.get('enabled')  # True/False
    
    if enabled is None:
        return jsonify({"success": False, "error": "缺少enabled参数"}), 400
    
    try:
        result = client.put(f'/dedicated/server/{service_name}/features/firewall', enabled=enabled)
        status_text = "启用" if enabled else "禁用"
        add_log("INFO", f"{status_text}服务器 {service_name} 防火墙", "server_control")
        return jsonify({
            "success": True,
            "message": f"防火墙已{status_text}",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"更新服务器 {service_name} 防火墙失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Backup FTP 备份FTP ====================
@app.route('/api/server-control/<service_name>/backup-ftp', methods=['OPTIONS', 'GET'])
def get_backup_ftp(service_name):
    """获取备份FTP信息"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        backup_ftp = client.get(f'/dedicated/server/{service_name}/features/backupFTP')
        add_log("INFO", f"获取服务器 {service_name} 备份FTP信息成功", "server_control")
        return jsonify({
            "success": True,
            "backupFtp": backup_ftp
        })
    except Exception as e:
        error_msg = str(e)
        if 'does not exist' in error_msg.lower():
            return jsonify({"success": False, "error": "备份FTP未激活", "notActivated": True}), 404
        add_log("ERROR", f"获取服务器 {service_name} 备份FTP失败: {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<service_name>/backup-ftp', methods=['OPTIONS', 'POST'])
def activate_backup_ftp(service_name):
    """激活备份FTP"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/features/backupFTP')
        add_log("INFO", f"激活服务器 {service_name} 备份FTP成功", "server_control")
        return jsonify({
            "success": True,
            "message": "备份FTP已激活",
            "result": result
        })
    except Exception as e:
        error_msg = str(e)
        # 如果无法使用该服务
        if 'cannot benefit' in error_msg.lower() or 'not available' in error_msg.lower():
            add_log("INFO", f"服务器 {service_name} 无法使用备份FTP服务: {error_msg}", "server_control")
            return jsonify({
                "success": False,
                "error": "该服务器无法使用备份FTP服务",
                "notAvailable": True,
                "reason": error_msg
            }), 400
        add_log("ERROR", f"激活服务器 {service_name} 备份FTP失败: {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<service_name>/backup-ftp', methods=['OPTIONS', 'DELETE'])
def delete_backup_ftp(service_name):
    """删除备份FTP"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.delete(f'/dedicated/server/{service_name}/features/backupFTP')
        add_log("INFO", f"删除服务器 {service_name} 备份FTP成功", "server_control")
        return jsonify({
            "success": True,
            "message": "备份FTP已删除",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"删除服务器 {service_name} 备份FTP失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/backup-ftp/access', methods=['OPTIONS', 'GET'])
def get_backup_ftp_access(service_name):
    """获取备份FTP访问控制列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        # 获取访问控制IP块列表
        ip_blocks = client.get(f'/dedicated/server/{service_name}/features/backupFTP/access')
        
        # 获取每个IP块的详细信息
        access_list = []
        for ip_block in ip_blocks:
            try:
                detail = client.get(f'/dedicated/server/{service_name}/features/backupFTP/access/{ip_block}')
                access_list.append(detail)
            except Exception as e:
                add_log("WARN", f"获取备份FTP访问详情失败 {ip_block}: {str(e)}", "server_control")
                access_list.append({'ipBlock': ip_block, 'error': str(e)})
        
        return jsonify({
            "success": True,
            "accessList": access_list
        })
    except Exception as e:
        add_log("ERROR", f"获取服务器 {service_name} 备份FTP访问列表失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/backup-ftp/access', methods=['OPTIONS', 'POST'])
def add_backup_ftp_access(service_name):
    """添加备份FTP访问IP"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.get_json()
    ip_block = data.get('ipBlock')
    ftp = data.get('ftp', True)
    nfs = data.get('nfs', False)
    cifs = data.get('cifs', False)
    
    if not ip_block:
        return jsonify({"success": False, "error": "缺少ipBlock参数"}), 400
    
    try:
        result = client.post(
            f'/dedicated/server/{service_name}/features/backupFTP/access',
            cifs=cifs,
            ftp=ftp,
            ipBlock=ip_block,
            nfs=nfs
        )
        add_log("INFO", f"添加备份FTP访问IP {ip_block} 成功", "server_control")
        return jsonify({
            "success": True,
            "message": "访问IP已添加",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"添加备份FTP访问IP失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/backup-ftp/access/<path:ip_block>', methods=['OPTIONS', 'DELETE'])
def delete_backup_ftp_access(service_name, ip_block):
    """删除备份FTP访问IP"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.delete(f'/dedicated/server/{service_name}/features/backupFTP/access/{ip_block}')
        add_log("INFO", f"删除备份FTP访问IP {ip_block} 成功", "server_control")
        return jsonify({
            "success": True,
            "message": "访问IP已删除"
        })
    except Exception as e:
        add_log("ERROR", f"删除备份FTP访问IP失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/backup-ftp/password', methods=['OPTIONS', 'POST'])
def change_backup_ftp_password(service_name):
    """修改备份FTP密码"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/features/backupFTP/password')
        add_log("INFO", f"修改服务器 {service_name} 备份FTP密码成功", "server_control")
        return jsonify({
            "success": True,
            "message": "密码已重置，新密码已发送至邮箱",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"修改备份FTP密码失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/backup-ftp/authorizable-blocks', methods=['OPTIONS', 'GET'])
def get_backup_ftp_authorizable_blocks(service_name):
    """获取可授权的IP块列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        blocks = client.get(f'/dedicated/server/{service_name}/features/backupFTP/authorizableBlocks')
        return jsonify({
            "success": True,
            "blocks": blocks
        })
    except Exception as e:
        add_log("ERROR", f"获取可授权IP块失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Backup Cloud 云备份 ====================
@app.route('/api/server-control/<service_name>/backup-cloud', methods=['OPTIONS', 'GET'])
def get_backup_cloud(service_name):
    """获取云备份信息"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        backup_cloud = client.get(f'/dedicated/server/{service_name}/features/backupCloud')
        add_log("INFO", f"获取服务器 {service_name} 云备份信息成功", "server_control")
        return jsonify({
            "success": True,
            "backupCloud": backup_cloud
        })
    except Exception as e:
        error_msg = str(e)
        if 'does not exist' in error_msg.lower():
            return jsonify({"success": False, "error": "云备份未激活", "notActivated": True}), 404
        add_log("ERROR", f"获取服务器 {service_name} 云备份失败: {error_msg}", "server_control")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/api/server-control/<service_name>/backup-cloud/offer-details', methods=['OPTIONS', 'GET'])
def get_backup_cloud_offer_details(service_name):
    """获取云备份套餐详情"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        offer_details = client.get(f'/dedicated/server/{service_name}/backupCloudOfferDetails')
        return jsonify({
            "success": True,
            "offerDetails": offer_details
        })
    except Exception as e:
        add_log("ERROR", f"获取云备份套餐详情失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Secondary DNS 从DNS ====================
@app.route('/api/server-control/<service_name>/secondary-dns', methods=['OPTIONS', 'GET'])
def get_secondary_dns_domains(service_name):
    """获取从DNS域名列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        domains = client.get(f'/dedicated/server/{service_name}/secondaryDnsDomains')
        dns_list = []
        for domain in domains:
            try:
                detail = client.get(f'/dedicated/server/{service_name}/secondaryDnsDomains/{domain}')
                detail['domain'] = domain
                dns_list.append(detail)
            except:
                dns_list.append({'domain': domain})
        
        return jsonify({
            "success": True,
            "domains": dns_list
        })
    except Exception as e:
        add_log("ERROR", f"获取从DNS域名失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/secondary-dns', methods=['OPTIONS', 'POST'])
def add_secondary_dns_domain(service_name):
    """添加从DNS域名"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.get_json()
    domain = data.get('domain')
    
    if not domain:
        return jsonify({"success": False, "error": "缺少domain参数"}), 400
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/secondaryDnsDomains', domain=domain)
        add_log("INFO", f"添加从DNS域名 {domain} 成功", "server_control")
        return jsonify({
            "success": True,
            "message": "从DNS域名已添加"
        })
    except Exception as e:
        add_log("ERROR", f"添加从DNS域名失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/secondary-dns/<path:domain>', methods=['OPTIONS', 'DELETE'])
def delete_secondary_dns_domain(service_name, domain):
    """删除从DNS域名"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        client.delete(f'/dedicated/server/{service_name}/secondaryDnsDomains/{domain}')
        add_log("INFO", f"删除从DNS域名 {domain} 成功", "server_control")
        return jsonify({
            "success": True,
            "message": "从DNS域名已删除"
        })
    except Exception as e:
        add_log("ERROR", f"删除从DNS域名失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Virtual MAC Address 虚拟MAC ====================
@app.route('/api/server-control/<service_name>/virtual-mac', methods=['OPTIONS', 'GET'])
def get_virtual_mac_list(service_name):
    """获取虚拟MAC地址列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        mac_addresses = client.get(f'/dedicated/server/{service_name}/virtualMac')
        mac_list = []
        for mac in mac_addresses:
            try:
                detail = client.get(f'/dedicated/server/{service_name}/virtualMac/{mac}')
                detail['macAddress'] = mac
                mac_list.append(detail)
            except:
                mac_list.append({'macAddress': mac})
        
        return jsonify({
            "success": True,
            "virtualMacs": mac_list
        })
    except Exception as e:
        add_log("ERROR", f"获取虚拟MAC列表失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/virtual-mac', methods=['OPTIONS', 'POST'])
def create_virtual_mac(service_name):
    """创建虚拟MAC地址"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.get_json()
    ip_address = data.get('ipAddress')
    mac_type = data.get('type')  # ovh, vmware
    virtual_machine_name = data.get('virtualMachineName')
    
    if not ip_address or not mac_type:
        return jsonify({"success": False, "error": "缺少必需参数"}), 400
    
    try:
        result = client.post(
            f'/dedicated/server/{service_name}/virtualMac',
            ipAddress=ip_address,
            type=mac_type,
            virtualMachineName=virtual_machine_name
        )
        add_log("INFO", f"创建虚拟MAC成功: {ip_address}", "server_control")
        return jsonify({
            "success": True,
            "message": "虚拟MAC已创建",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"创建虚拟MAC失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Virtual Network Interface 虚拟网络接口 ====================
@app.route('/api/server-control/<service_name>/virtual-network-interface', methods=['OPTIONS', 'GET'])
def get_virtual_network_interfaces(service_name):
    """获取虚拟网络接口列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        uuids = client.get(f'/dedicated/server/{service_name}/virtualNetworkInterface')
        interfaces = []
        for uuid in uuids:
            try:
                detail = client.get(f'/dedicated/server/{service_name}/virtualNetworkInterface/{uuid}')
                detail['uuid'] = uuid
                interfaces.append(detail)
            except:
                interfaces.append({'uuid': uuid})
        
        return jsonify({
            "success": True,
            "interfaces": interfaces
        })
    except Exception as e:
        add_log("ERROR", f"获取虚拟网络接口失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/virtual-network-interface/<uuid>/enable', methods=['OPTIONS', 'POST'])
def enable_virtual_network_interface(service_name, uuid):
    """启用虚拟网络接口"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/virtualNetworkInterface/{uuid}/enable')
        add_log("INFO", f"启用虚拟网络接口 {uuid} 成功", "server_control")
        return jsonify({
            "success": True,
            "message": "虚拟网络接口已启用"
        })
    except Exception as e:
        add_log("ERROR", f"启用虚拟网络接口失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/virtual-network-interface/<uuid>/disable', methods=['OPTIONS', 'POST'])
def disable_virtual_network_interface(service_name, uuid):
    """禁用虚拟网络接口"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/virtualNetworkInterface/{uuid}/disable')
        add_log("INFO", f"禁用虚拟网络接口 {uuid} 成功", "server_control")
        return jsonify({
            "success": True,
            "message": "虚拟网络接口已禁用"
        })
    except Exception as e:
        add_log("ERROR", f"禁用虚拟网络接口失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== OLA 完整管理 ====================
@app.route('/api/server-control/<service_name>/ola/group', methods=['OPTIONS', 'POST'])
def ola_group(service_name):
    """创建OLA组"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/ola/group')
        add_log("INFO", f"创建OLA组成功: {service_name}", "server_control")
        return jsonify({
            "success": True,
            "message": "OLA组已创建",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"创建OLA组失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/ola/ungroup', methods=['OPTIONS', 'POST'])
def ola_ungroup(service_name):
    """解散OLA组"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/ola/ungroup')
        add_log("INFO", f"解散OLA组成功: {service_name}", "server_control")
        return jsonify({
            "success": True,
            "message": "OLA组已解散",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"解散OLA组失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== vRack 管理 ====================
@app.route('/api/server-control/<service_name>/vrack', methods=['OPTIONS', 'GET'])
def get_vrack_list(service_name):
    """获取vRack列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        vracks = client.get(f'/dedicated/server/{service_name}/vrack')
        vrack_list = []
        for vrack in vracks:
            try:
                detail = client.get(f'/dedicated/server/{service_name}/vrack/{vrack}')
                detail['vrackName'] = vrack
                vrack_list.append(detail)
            except:
                vrack_list.append({'vrackName': vrack})
        
        return jsonify({
            "success": True,
            "vracks": vrack_list
        })
    except Exception as e:
        add_log("ERROR", f"获取vRack列表失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/vrack/<path:vrack>', methods=['OPTIONS', 'DELETE'])
def remove_from_vrack(service_name, vrack):
    """从vRack中移除服务器"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.delete(f'/dedicated/server/{service_name}/vrack/{vrack}')
        add_log("INFO", f"从vRack {vrack} 移除服务器成功", "server_control")
        return jsonify({
            "success": True,
            "message": "服务器已从vRack移除"
        })
    except Exception as e:
        add_log("ERROR", f"从vRack移除服务器失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Orderable Services 可订购服务 ====================
@app.route('/api/server-control/<service_name>/orderable/bandwidth', methods=['OPTIONS', 'GET'])
def get_orderable_bandwidth(service_name):
    """获取可订购带宽"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        orderable = client.get(f'/dedicated/server/{service_name}/orderable/bandwidth')
        return jsonify({
            "success": True,
            "orderable": orderable
        })
    except Exception as e:
        add_log("ERROR", f"获取可订购带宽失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/orderable/traffic', methods=['OPTIONS', 'GET'])
def get_orderable_traffic(service_name):
    """获取可订购流量"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        orderable = client.get(f'/dedicated/server/{service_name}/orderable/traffic')
        return jsonify({
            "success": True,
            "orderable": orderable
        })
    except Exception as e:
        add_log("ERROR", f"获取可订购流量失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/orderable/ip', methods=['OPTIONS', 'GET'])
def get_orderable_ip(service_name):
    """获取可订购IP"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        orderable = client.get(f'/dedicated/server/{service_name}/orderable/ip')
        return jsonify({
            "success": True,
            "orderable": orderable
        })
    except Exception as e:
        add_log("ERROR", f"获取可订购IP失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Options 选项管理 ====================
@app.route('/api/server-control/<service_name>/options', methods=['OPTIONS', 'GET'])
def get_server_options(service_name):
    """获取服务器选项列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        options = client.get(f'/dedicated/server/{service_name}/option')
        option_list = []
        for option in options:
            try:
                detail = client.get(f'/dedicated/server/{service_name}/option/{option}')
                detail['option'] = option
                option_list.append(detail)
            except:
                option_list.append({'option': option})
        
        return jsonify({
            "success": True,
            "options": option_list
        })
    except Exception as e:
        add_log("ERROR", f"获取服务器选项失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== IP Specifications IP规格 ====================
@app.route('/api/server-control/<service_name>/ip-specs', methods=['OPTIONS', 'GET'])
def get_ip_specs(service_name):
    """获取IP规格信息"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        ip_specs = client.get(f'/dedicated/server/{service_name}/specifications/ip')
        return jsonify({
            "success": True,
            "ipSpecs": ip_specs
        })
    except Exception as e:
        add_log("ERROR", f"获取IP规格失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== IP 高级管理 ====================
@app.route('/api/server-control/<service_name>/ip/can-be-moved-to', methods=['OPTIONS', 'GET'])
def get_ip_can_be_moved_to(service_name):
    """检查IP可迁移目标"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        targets = client.get(f'/dedicated/server/{service_name}/ipCanBeMovedTo')
        return jsonify({
            "success": True,
            "targets": targets
        })
    except Exception as e:
        add_log("ERROR", f"获取IP迁移目标失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/ip/country-available', methods=['OPTIONS', 'GET'])
def get_ip_country_available(service_name):
    """获取可用IP国家列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        countries = client.get(f'/dedicated/server/{service_name}/ipCountryAvailable')
        return jsonify({
            "success": True,
            "countries": countries
        })
    except Exception as e:
        add_log("ERROR", f"获取可用IP国家失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/ip/move', methods=['OPTIONS', 'POST'])
def move_ip(service_name):
    """迁移IP到其他服务器"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.get_json()
    ip = data.get('ip')
    to = data.get('to')
    
    if not ip or not to:
        return jsonify({"success": False, "error": "缺少必需参数"}), 400
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/ipMove', ip=ip, to=to)
        add_log("INFO", f"IP迁移任务已创建: {ip} -> {to}", "server_control")
        return jsonify({
            "success": True,
            "message": "IP迁移任务已创建",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"IP迁移失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Ongoing Tasks 进行中任务 ====================
@app.route('/api/server-control/<service_name>/ongoing', methods=['OPTIONS', 'GET'])
def get_ongoing_tasks(service_name):
    """获取进行中的任务"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        ongoing = client.get(f'/dedicated/server/{service_name}/ongoing')
        return jsonify({
            "success": True,
            "ongoing": ongoing
        })
    except Exception as e:
        add_log("ERROR", f"获取进行中任务失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Windows License 许可证 ====================
@app.route('/api/server-control/<service_name>/license/windows/compliant', methods=['OPTIONS', 'GET'])
def get_compliant_windows_versions(service_name):
    """获取兼容的Windows版本"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        versions = client.get(f'/dedicated/server/{service_name}/license/compliantWindows')
        return jsonify({
            "success": True,
            "versions": versions
        })
    except Exception as e:
        add_log("ERROR", f"获取兼容Windows版本失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/license/windows-sql/compliant', methods=['OPTIONS', 'GET'])
def get_compliant_windows_sql_versions(service_name):
    """获取兼容的Windows SQL Server版本"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        versions = client.get(f'/dedicated/server/{service_name}/license/compliantWindowsSqlServer')
        return jsonify({
            "success": True,
            "versions": versions
        })
    except Exception as e:
        add_log("ERROR", f"获取兼容SQL Server版本失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== Service Termination 终止服务 ====================
@app.route('/api/server-control/<service_name>/terminate', methods=['OPTIONS', 'POST'])
def terminate_service(service_name):
    """终止服务器服务"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/terminate')
        add_log("WARNING", f"服务器 {service_name} 终止请求已提交", "server_control")
        return jsonify({
            "success": True,
            "message": "终止请求已提交",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"终止服务失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/confirm-termination', methods=['OPTIONS', 'POST'])
def confirm_termination(service_name):
    """确认终止服务"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.get_json()
    token = data.get('token')
    
    if not token:
        return jsonify({"success": False, "error": "缺少token参数"}), 400
    
    try:
        result = client.post(f'/dedicated/server/{service_name}/confirmTermination', token=token)
        add_log("WARNING", f"服务器 {service_name} 终止已确认", "server_control")
        return jsonify({
            "success": True,
            "message": "终止已确认"
        })
    except Exception as e:
        add_log("ERROR", f"确认终止失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== SPLA 软件许可证 ====================
@app.route('/api/server-control/<service_name>/spla', methods=['OPTIONS', 'GET'])
def get_spla_list(service_name):
    """获取SPLA许可证列表"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    try:
        spla_ids = client.get(f'/dedicated/server/{service_name}/spla')
        spla_list = []
        for spla_id in spla_ids:
            try:
                detail = client.get(f'/dedicated/server/{service_name}/spla/{spla_id}')
                detail['id'] = spla_id
                spla_list.append(detail)
            except:
                spla_list.append({'id': spla_id})
        
        return jsonify({
            "success": True,
            "splaList": spla_list
        })
    except Exception as e:
        add_log("ERROR", f"获取SPLA列表失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<service_name>/spla', methods=['OPTIONS', 'POST'])
def create_spla(service_name):
    """创建SPLA许可证"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401
    
    data = request.get_json()
    license_type = data.get('type')
    serial_number = data.get('serialNumber')
    
    if not license_type:
        return jsonify({"success": False, "error": "缺少type参数"}), 400
    
    try:
        result = client.post(
            f'/dedicated/server/{service_name}/spla',
            type=license_type,
            serialNumber=serial_number
        )
        add_log("INFO", f"创建SPLA许可证成功: {license_type}", "server_control")
        return jsonify({
            "success": True,
            "message": "SPLA许可证已创建",
            "result": result
        })
    except Exception as e:
        add_log("ERROR", f"创建SPLA许可证失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

# ==================== VPS 监控相关功能 ====================

# ==================== BIOS 设置 ====================
@app.route('/api/server-control/<path:service_name>/bios-settings', methods=['GET', 'OPTIONS'])
def get_server_bios_settings(service_name):
    """获取服务器 BIOS 设置"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401

    try:
        add_log("INFO", f"[BIOS] 获取服务器 {service_name} BIOS 设置", "server_control")
        bios = client.get(f'/dedicated/server/{service_name}/biosSettings')
        return jsonify({
            "success": True,
            "bios": bios
        })
    except OvhAPIError as e:
        message = str(e)
        # OVH 返回对象不存在 -> 此服务器不支持 BIOS 设置 API
        if 'does not exist' in message or 'object' in message.lower():
            add_log("WARNING", f"[BIOS] 服务器 {service_name} 不支持 BIOS 设置: {message}", "server_control")
            return jsonify({"success": False, "error": "BIOS 设置不可用"}), 404
        add_log("ERROR", f"[BIOS] API错误: {message}", "server_control")
        return jsonify({"success": False, "error": message}), 502
    except Exception as e:
        add_log("ERROR", f"[BIOS] 获取BIOS设置失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/server-control/<path:service_name>/bios-settings/sgx', methods=['GET', 'OPTIONS'])
def get_server_bios_settings_sgx(service_name):
    """获取服务器 SGX BIOS 设置（如果支持）"""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    client = get_ovh_client()
    if not client:
        return jsonify({"success": False, "error": "未配置OVH API密钥"}), 401

    try:
        add_log("INFO", f"[BIOS] 获取服务器 {service_name} SGX BIOS 设置", "server_control")
        sgx = client.get(f'/dedicated/server/{service_name}/biosSettings/sgx')
        return jsonify({
            "success": True,
            "sgx": sgx
        })
    except OvhAPIError as e:
        message = str(e)
        if 'does not exist' in message or 'object' in message.lower():
            add_log("WARNING", f"[BIOS] 服务器 {service_name} 不支持 SGX: {message}", "server_control")
            return jsonify({"success": False, "error": "SGX 不可用"}), 404
        add_log("ERROR", f"[BIOS] SGX API错误: {message}", "server_control")
        return jsonify({"success": False, "error": message}), 502
    except Exception as e:
        add_log("ERROR", f"[BIOS] 获取SGX失败: {str(e)}", "server_control")
        return jsonify({"success": False, "error": str(e)}), 500

def check_vps_datacenter_availability(plan_code, ovh_subsidiary="IE"):
    """
    检查VPS套餐的数据中心可用性
    
    Args:
        plan_code: VPS套餐代码，如 vps-2025-model1
        ovh_subsidiary: OVH子公司代码，默认IE
    
    Returns:
        dict: 包含数据中心可用性信息的字典
    """
    try:
        # 根据endpoint配置动态构建API URL
        base_url = get_api_base_url()
        url = f"{base_url}/v1/vps/order/rule/datacenter"
        params = {
            'ovhSubsidiary': ovh_subsidiary,
            'planCode': plan_code
        }
        headers = {'accept': 'application/json'}
        
        add_log("INFO", f"检查VPS可用性: {plan_code} (subsidiary: {ovh_subsidiary})", "vps_monitor")
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            add_log("INFO", f"VPS {plan_code} 数据中心信息获取成功", "vps_monitor")
            return data
        else:
            add_log("ERROR", f"获取VPS数据中心信息失败: HTTP {response.status_code}", "vps_monitor")
            return None
            
    except Exception as e:
        add_log("ERROR", f"检查VPS可用性时出错: {str(e)}", "vps_monitor")
        return None
def send_vps_summary_notification(plan_code, datacenters_list, change_type):
    """
    发送VPS库存变化汇总通知（多个数据中心）
    
    Args:
        plan_code: VPS套餐代码
        datacenters_list: 数据中心列表 [{'name': '', 'code': '', 'status': '', 'days': 0}, ...]
        change_type: 变化类型 (available/unavailable/initial)
    """
    try:
        tg_token = config.get('tgToken')
        tg_chat_id = config.get('tgChatId')
        
        if not tg_token or not tg_chat_id or not datacenters_list:
            return False
        
        # 状态翻译
        status_map = {
            'available': '现货',
            'out-of-stock': '无货',
            'out-of-stock-preorder-allowed': '缺货（可预订）',
            'unavailable': '不可用',
            'unknown': '未知'
        }
        
        # VPS型号翻译
        vps_model_map = {
            'vps-2025-model1': 'VPS-1',
            'vps-2025-model2': 'VPS-2',
            'vps-2025-model3': 'VPS-3',
            'vps-2025-model4': 'VPS-4',
            'vps-2025-model5': 'VPS-5',
            'vps-2025-model6': 'VPS-6',
        }
        plan_code_display = vps_model_map.get(plan_code, plan_code)
        
        # 标题和emoji
        if change_type == "initial":
            emoji = "📊"
            title = "VPS初始状态"
        elif change_type == "available":
            emoji = "🎉"
            title = "VPS补货通知"
        else:
            emoji = "📦"
            title = "VPS下架通知"
        
        # 构建消息
        message = f"{emoji} {title}\n\n套餐: {plan_code_display}\n"
        message += f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # 添加数据中心列表
        for idx, dc in enumerate(datacenters_list, 1):
            status_cn = status_map.get(dc['status'], dc['status'])
            message += f"{idx}. {dc['name']} ({dc['code']})\n"
            message += f"   状态: {status_cn}"
            if dc.get('days', 0) > 0:
                message += f" | 预计交付: {dc['days']}天"
            message += "\n"
        
        # 添加footer
        if change_type == "available":
            message += "\n💡 快去抢购吧！"
        
        result = send_telegram_msg(message)
        
        if result:
            add_log("INFO", f"✅ VPS汇总通知发送成功: {plan_code} ({len(datacenters_list)}个机房)", "vps_monitor")
        else:
            add_log("WARNING", f"⚠️ VPS汇总通知发送失败: {plan_code}", "vps_monitor")
        
        return result
        
    except Exception as e:
        add_log("ERROR", f"发送VPS汇总通知时出错: {str(e)}", "vps_monitor")
        return False

def send_vps_notification(plan_code, datacenter_info, change_type):
    """
    发送VPS库存变化通知
    
    Args:
        plan_code: VPS套餐代码
        datacenter_info: 数据中心信息
        change_type: 变化类型 (available/unavailable)
    """
    try:
        tg_token = config.get('tgToken')
        tg_chat_id = config.get('tgChatId')
        
        if not tg_token or not tg_chat_id:
            add_log("WARNING", "Telegram配置不完整，无法发送通知", "vps_monitor")
            return False
        
        dc_name = datacenter_info.get('datacenter', 'Unknown')
        dc_code = datacenter_info.get('code', 'Unknown')
        status = datacenter_info.get('status', 'unknown')
        days_before_delivery = datacenter_info.get('daysBeforeDelivery', 0)
        
        # 状态翻译成中文
        status_map = {
            'available': '现货',
            'out-of-stock': '无货',
            'out-of-stock-preorder-allowed': '缺货（可预订）',
            'unavailable': '不可用',
            'unknown': '未知'
        }
        status_cn = status_map.get(status, status)
        
        # VPS型号翻译成友好名称
        vps_model_map = {
            'vps-2025-model1': 'VPS-1',
            'vps-2025-model2': 'VPS-2',
            'vps-2025-model3': 'VPS-3',
            'vps-2025-model4': 'VPS-4',
            'vps-2025-model5': 'VPS-5',
            'vps-2025-model6': 'VPS-6',
        }
        plan_code_display = vps_model_map.get(plan_code, plan_code)
        
        if change_type == "available":
            emoji = "🎉"
            title = "VPS补货通知"
            status_text = f"状态: {status_cn}"
            if days_before_delivery > 0:
                status_text += f"\n预计交付: {days_before_delivery}天"
            footer = "💡 快去抢购吧！"
        else:
            emoji = "📦"
            title = "VPS下架通知"
            status_text = f"状态: {status_cn}"
            footer = ""
        
        message = (
            f"{emoji} {title}\n\n"
            f"套餐: {plan_code_display}\n"
            f"数据中心: {dc_name} ({dc_code})\n"
            f"{status_text}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        if footer:
            message += f"\n\n{footer}"
        
        result = send_telegram_msg(message)
        
        if result:
            add_log("INFO", f"✅ VPS通知发送成功: {plan_code}@{dc_name}", "vps_monitor")
        else:
            add_log("WARNING", f"⚠️ VPS通知发送失败: {plan_code}@{dc_name}", "vps_monitor")
        
        return result
        
    except Exception as e:
        add_log("ERROR", f"发送VPS通知时出错: {str(e)}", "vps_monitor")
        return False

def vps_monitor_loop():
    """VPS监控主循环"""
    global vps_monitor_running
    
    add_log("INFO", "VPS监控循环已启动", "vps_monitor")
    
    while vps_monitor_running:
        try:
            if vps_subscriptions:
                add_log("INFO", f"开始检查 {len(vps_subscriptions)} 个VPS订阅...", "vps_monitor")
                
                for subscription in vps_subscriptions:
                    if not vps_monitor_running:
                        break
                    
                    plan_code = subscription.get('planCode')
                    ovh_subsidiary = subscription.get('ovhSubsidiary', 'IE')
                    notify_available = subscription.get('notifyAvailable', True)
                    notify_unavailable = subscription.get('notifyUnavailable', False)
                    monitored_datacenters = subscription.get('datacenters', [])
                    
                    # 获取当前可用性
                    current_data = check_vps_datacenter_availability(plan_code, ovh_subsidiary)
                    
                    if not current_data or 'datacenters' not in current_data:
                        add_log("WARNING", f"无法获取VPS {plan_code} 的数据中心信息", "vps_monitor")
                        continue
                    
                    last_status = subscription.get('lastStatus', {})
                    current_datacenters = current_data['datacenters']
                    
                    # 收集变化的数据中心
                    initial_available = []  # 首次检查有货
                    new_available = []  # 从无货变有货
                    new_unavailable = []  # 从有货变无货
                    is_first_check_overall = len(last_status) == 0
                    
                    # 检查每个数据中心的变化
                    for dc in current_datacenters:
                        dc_code = dc.get('code')
                        dc_name = dc.get('datacenter')
                        current_status = dc.get('status')
                        days = dc.get('daysBeforeDelivery', 0)
                        
                        # 如果指定了数据中心列表，只监控列表中的
                        if monitored_datacenters and dc_code not in monitored_datacenters:
                            continue
                        
                        # 获取上次状态
                        old_status = last_status.get(dc_code)
                        is_first_check = old_status is None
                        
                        # 首次检查：收集所有数据中心状态
                        if is_first_check:
                            initial_available.append({
                                'name': dc_name,
                                'code': dc_code,
                                'status': current_status,
                                'days': days
                            })
                            # 添加到历史记录
                            if current_status not in ['out-of-stock', 'out-of-stock-preorder-allowed']:
                                if 'history' not in subscription:
                                    subscription['history'] = []
                                subscription['history'].append({
                                    'timestamp': datetime.now().isoformat(),
                                    'datacenter': dc_name,
                                    'datacenterCode': dc_code,
                                    'status': current_status,
                                    'changeType': 'available',
                                    'oldStatus': None
                                })
                        
                        # 非首次检查：监控状态变化
                        else:
                            # 从无货变有货
                            if old_status in ['out-of-stock', 'out-of-stock-preorder-allowed'] and \
                               current_status not in ['out-of-stock', 'out-of-stock-preorder-allowed']:
                                new_available.append({
                                    'name': dc_name,
                                    'code': dc_code,
                                    'status': current_status,
                                    'days': days
                                })
                                # 添加到历史记录
                                if 'history' not in subscription:
                                    subscription['history'] = []
                                subscription['history'].append({
                                    'timestamp': datetime.now().isoformat(),
                                    'datacenter': dc_name,
                                    'datacenterCode': dc_code,
                                    'status': current_status,
                                    'changeType': 'available',
                                    'oldStatus': old_status
                                })
                            
                            # 从有货变无货
                            elif old_status not in ['out-of-stock', 'out-of-stock-preorder-allowed'] and \
                                 current_status in ['out-of-stock', 'out-of-stock-preorder-allowed']:
                                new_unavailable.append({
                                    'name': dc_name,
                                    'code': dc_code,
                                    'status': current_status,
                                    'days': days
                                })
                                # 添加到历史记录
                                if 'history' not in subscription:
                                    subscription['history'] = []
                                subscription['history'].append({
                                    'timestamp': datetime.now().isoformat(),
                                    'datacenter': dc_name,
                                    'datacenterCode': dc_code,
                                    'status': current_status,
                                    'changeType': 'unavailable',
                                    'oldStatus': old_status
                                })
                        
                        # 更新最后状态
                        last_status[dc_code] = current_status
                    
                    # 发送汇总通知
                    if is_first_check_overall and initial_available:
                        # 首次检查：发送初始状态汇总
                        if notify_available:
                            add_log("INFO", f"VPS {plan_code} 初始状态检查完成，{len(initial_available)}个数据中心", "vps_monitor")
                            send_vps_summary_notification(plan_code, initial_available, 'initial')
                    else:
                        # 后续检查：发送补货汇总
                        if new_available and notify_available:
                            add_log("INFO", f"VPS {plan_code} 补货：{len(new_available)}个数据中心", "vps_monitor")
                            send_vps_summary_notification(plan_code, new_available, 'available')
                        
                        # 发送下架汇总
                        if new_unavailable and notify_unavailable:
                            add_log("INFO", f"VPS {plan_code} 下架：{len(new_unavailable)}个数据中心", "vps_monitor")
                            send_vps_summary_notification(plan_code, new_unavailable, 'unavailable')
                    
                    # 更新订阅的最后状态
                    subscription['lastStatus'] = last_status
                    
                    # 限制历史记录数量
                    if 'history' in subscription and len(subscription['history']) > 100:
                        subscription['history'] = subscription['history'][-100:]
                    
                    time.sleep(1)  # 避免请求过快
                
                # 保存更新后的订阅数据
                save_vps_subscriptions()
            else:
                add_log("INFO", "当前无VPS订阅，跳过检查", "vps_monitor")
            
        except Exception as e:
            add_log("ERROR", f"VPS监控循环出错: {str(e)}", "vps_monitor")
            add_log("ERROR", f"错误详情: {traceback.format_exc()}", "vps_monitor")
        
        # 等待下次检查
        if vps_monitor_running:
            add_log("INFO", f"等待 {vps_check_interval} 秒后进行下次VPS检查...", "vps_monitor")
            for _ in range(vps_check_interval):
                if not vps_monitor_running:
                    break
                time.sleep(1)
    
    add_log("INFO", "VPS监控循环已停止", "vps_monitor")

# ==================== VPS 监控 API 接口 ====================

@app.route('/api/vps-monitor/subscriptions', methods=['GET'])
def get_vps_subscriptions():
    """获取VPS订阅列表"""
    return jsonify(vps_subscriptions)

@app.route('/api/vps-monitor/subscriptions', methods=['POST'])
def add_vps_subscription():
    """添加VPS订阅"""
    global vps_subscriptions
    
    data = request.json
    plan_code = data.get('planCode')
    ovh_subsidiary = data.get('ovhSubsidiary', 'IE')
    datacenters = data.get('datacenters', [])
    monitor_linux = data.get('monitorLinux', True)
    monitor_windows = data.get('monitorWindows', False)
    notify_available = data.get('notifyAvailable', True)
    notify_unavailable = data.get('notifyUnavailable', False)
    
    if not plan_code:
        return jsonify({"status": "error", "message": "缺少planCode参数"}), 400
    
    # 检查是否已存在
    existing = next((s for s in vps_subscriptions if s['planCode'] == plan_code and s['ovhSubsidiary'] == ovh_subsidiary), None)
    if existing:
        return jsonify({"status": "error", "message": "该VPS套餐已订阅"}), 400
    
    subscription = {
        'id': str(uuid.uuid4()),
        'planCode': plan_code,
        'ovhSubsidiary': ovh_subsidiary,
        'datacenters': datacenters,
        'monitorLinux': monitor_linux,
        'monitorWindows': monitor_windows,
        'notifyAvailable': notify_available,
        'notifyUnavailable': notify_unavailable,
        'lastStatus': {},
        'history': [],
        'createdAt': datetime.now().isoformat()
    }
    
    vps_subscriptions.append(subscription)
    save_vps_subscriptions()
    
    add_log("INFO", f"添加VPS订阅: {plan_code} (subsidiary: {ovh_subsidiary})", "vps_monitor")
    
    # 自动启动监控（如果还未启动）
    global vps_monitor_running, vps_monitor_thread
    if not vps_monitor_running:
        vps_monitor_running = True
        vps_monitor_thread = threading.Thread(target=vps_monitor_loop, daemon=True)
        vps_monitor_thread.start()
        add_log("INFO", f"自动启动VPS监控 (检查间隔: {vps_check_interval}秒)", "vps_monitor")
    
    return jsonify({"status": "success", "message": f"已订阅 {plan_code}", "subscription": subscription})

@app.route('/api/vps-monitor/subscriptions/<subscription_id>', methods=['DELETE'])
def remove_vps_subscription(subscription_id):
    """删除VPS订阅"""
    global vps_subscriptions, vps_monitor_running
    
    original_count = len(vps_subscriptions)
    vps_subscriptions = [s for s in vps_subscriptions if s['id'] != subscription_id]
    
    if len(vps_subscriptions) < original_count:
        save_vps_subscriptions()
        add_log("INFO", f"删除VPS订阅: {subscription_id}", "vps_monitor")
        
        # 如果删除后没有订阅了，自动停止监控
        if len(vps_subscriptions) == 0 and vps_monitor_running:
            vps_monitor_running = False
            add_log("INFO", "所有订阅已删除，自动停止VPS监控", "vps_monitor")
        
        return jsonify({"status": "success", "message": "订阅已删除"})
    else:
        return jsonify({"status": "error", "message": "订阅不存在"}), 404

@app.route('/api/vps-monitor/subscriptions/clear', methods=['DELETE'])
def clear_vps_subscriptions():
    """清空所有VPS订阅"""
    global vps_subscriptions, vps_monitor_running
    
    count = len(vps_subscriptions)
    vps_subscriptions.clear()
    save_vps_subscriptions()
    
    add_log("INFO", f"清空所有VPS订阅 ({count} 项)", "vps_monitor")
    
    # 清空订阅后自动停止监控
    if vps_monitor_running:
        vps_monitor_running = False
        add_log("INFO", "所有订阅已清空，自动停止VPS监控", "vps_monitor")
    
    return jsonify({"status": "success", "count": count, "message": f"已清空 {count} 个订阅"})

@app.route('/api/vps-monitor/subscriptions/<subscription_id>/history', methods=['GET'])
def get_vps_subscription_history(subscription_id):
    """获取VPS订阅的历史记录"""
    subscription = next((s for s in vps_subscriptions if s['id'] == subscription_id), None)
    
    if not subscription:
        return jsonify({"status": "error", "message": "订阅不存在"}), 404
    
    history = subscription.get('history', [])
    # 返回倒序历史记录（最新的在前）
    reversed_history = list(reversed(history))
    
    return jsonify({
        "planCode": subscription['planCode'],
        "history": reversed_history
    })

@app.route('/api/vps-monitor/start', methods=['POST'])
def start_vps_monitor():
    """启动VPS监控"""
    global vps_monitor_running, vps_monitor_thread
    
    if vps_monitor_running:
        return jsonify({"status": "info", "message": "VPS监控已在运行中"})
    
    vps_monitor_running = True
    vps_monitor_thread = threading.Thread(target=vps_monitor_loop, daemon=True)
    vps_monitor_thread.start()
    
    add_log("INFO", f"VPS监控已启动 (检查间隔: {vps_check_interval}秒)", "vps_monitor")
    return jsonify({"status": "success", "message": "VPS监控已启动"})

@app.route('/api/vps-monitor/stop', methods=['POST'])
def stop_vps_monitor():
    """停止VPS监控"""
    global vps_monitor_running
    
    if not vps_monitor_running:
        return jsonify({"status": "info", "message": "VPS监控未运行"})
    
    vps_monitor_running = False
    add_log("INFO", "正在停止VPS监控...", "vps_monitor")
    
    return jsonify({"status": "success", "message": "VPS监控已停止"})

@app.route('/api/vps-monitor/status', methods=['GET'])
def get_vps_monitor_status():
    """获取VPS监控状态"""
    status = {
        'running': vps_monitor_running,
        'subscriptions_count': len(vps_subscriptions),
        'check_interval': vps_check_interval
    }
    return jsonify(status)

@app.route('/api/vps-monitor/interval', methods=['PUT'])
def set_vps_monitor_interval():
    """设置VPS监控间隔"""
    global vps_check_interval
    
    data = request.json
    interval = data.get('interval')
    
    if not interval or interval < 60:
        return jsonify({"status": "error", "message": "间隔不能小于60秒"}), 400
    
    vps_check_interval = interval
    save_vps_subscriptions()
    
    add_log("INFO", f"VPS检查间隔已设置为 {interval} 秒", "vps_monitor")
    return jsonify({"status": "success", "message": f"检查间隔已设置为 {interval} 秒"})

@app.route('/api/vps-monitor/check/<plan_code>', methods=['POST'])
def manual_check_vps(plan_code):
    """手动检查VPS可用性"""
    data = request.json or {}
    ovh_subsidiary = data.get('ovhSubsidiary', 'IE')
    
    result = check_vps_datacenter_availability(plan_code, ovh_subsidiary)
    
    if result:
        return jsonify({
            "status": "success",
            "data": result
        })
    else:
        return jsonify({
            "status": "error",
            "message": "获取VPS数据中心信息失败"
        }), 500

# ==================== OVH 账户管理 API ====================

@app.route('/api/ovh/account/info', methods=['GET'])
def get_account_info():
    """获取OVH账户信息 - GET /me"""
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        account_info = client.get('/me')
        add_log("INFO", "成功获取账户信息", "account_management")
        return jsonify({
            "status": "success",
            "data": account_info
        })
    except Exception as e:
        add_log("ERROR", f"获取账户信息失败: {str(e)}", "account_management")
        return jsonify({
            "status": "error",
            "message": f"获取账户信息失败: {str(e)}"
        }), 500

@app.route('/api/ovh/account/refunds', methods=['GET'])
def get_account_refunds():
    """获取退款列表 - GET /me/refund"""
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        # 获取退款ID列表
        refund_ids = client.get('/me/refund')
        
        # 获取每个退款的详细信息
        refunds = []
        for refund_id in refund_ids[:20]:  # 限制最多20个
            try:
                refund_detail = client.get(f'/me/refund/{refund_id}')
                refunds.append(refund_detail)
            except Exception as e:
                add_log("WARNING", f"获取退款 {refund_id} 详情失败: {str(e)}", "account_management")
        
        add_log("INFO", f"成功获取 {len(refunds)} 条退款记录", "account_management")
        return jsonify({
            "status": "success",
            "data": refunds
        })
    except Exception as e:
        add_log("ERROR", f"获取退款列表失败: {str(e)}", "account_management")
        return jsonify({
            "status": "error",
            "message": f"获取退款列表失败: {str(e)}"
        }), 500

@app.route('/api/ovh/account/credit-balance', methods=['GET'])
def get_credit_balance():
    """获取信用余额列表 - GET /me/credit/balance"""
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        # 获取余额名称列表
        balance_names = client.get('/me/credit/balance')
        
        # 获取每个余额的详细信息
        balances = []
        for balance_name in balance_names:
            try:
                balance_detail = client.get(f'/me/credit/balance/{balance_name}')
                balances.append(balance_detail)
            except Exception as e:
                add_log("WARNING", f"获取余额 {balance_name} 详情失败: {str(e)}", "account_management")
        
        add_log("INFO", f"成功获取 {len(balances)} 个信用余额", "account_management")
        return jsonify({
            "status": "success",
            "data": balances
        })
    except Exception as e:
        add_log("ERROR", f"获取信用余额失败: {str(e)}", "account_management")
        return jsonify({
            "status": "error",
            "message": f"获取信用余额失败: {str(e)}"
        }), 500

@app.route('/api/ovh/account/email-history', methods=['GET', 'OPTIONS'])
def get_email_history():
    """获取邮件历史 - GET /me/notification/email/history"""
    if request.method == 'OPTIONS':
        return '', 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        # 获取邮件ID列表
        email_ids = client.get('/me/notification/email/history')
        
        # 反转列表，获取最新的邮件（ID通常是递增的，所以反转后最新的在前）
        email_ids = list(reversed(email_ids))
        
        # 获取每封邮件的详细信息
        emails = []
        # 限制最多获取50封邮件，避免请求过多
        for email_id in email_ids[:50]:
            try:
                email_detail = client.get(f'/me/notification/email/history/{email_id}')
                emails.append(email_detail)
            except Exception as e:
                add_log("WARNING", f"获取邮件 {email_id} 详情失败: {str(e)}", "account_management")
        
        add_log("INFO", f"成功获取 {len(emails)} 封邮件（总共 {len(email_ids)} 封）", "account_management")
        return jsonify({
            "status": "success",
            "data": emails
        })
    except Exception as e:
        add_log("ERROR", f"获取邮件历史失败: {str(e)}", "account_management")
        return jsonify({
            "status": "error",
            "message": f"获取邮件历史失败: {str(e)}"
        }), 500

@app.route('/api/ovh/contact-change-requests', methods=['GET', 'OPTIONS'])
def get_contact_change_requests():
    """获取联系人变更请求列表 - GET /me/task/contactChange"""
    if request.method == 'OPTIONS':
        return '', 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        # 获取联系人变更请求ID列表
        task_ids = client.get('/me/task/contactChange')
        
        # 获取每个请求的详细信息
        tasks = []
        for task_id in task_ids:
            try:
                task_detail = client.get(f'/me/task/contactChange/{task_id}')
                # 记录任务详情以调试（检查是否有token字段）
                add_log("DEBUG", f"任务 {task_id} 详情字段: {list(task_detail.keys())}", "server_control")
                tasks.append(task_detail)
            except Exception as e:
                add_log("WARNING", f"获取联系人变更请求 {task_id} 详情失败: {str(e)}", "server_control")
        
        # 按请求日期倒序排列（最新的在前）
        tasks.sort(key=lambda x: x.get('dateRequest', ''), reverse=True)
        
        add_log("INFO", f"成功获取 {len(tasks)} 个联系人变更请求", "server_control")
        return jsonify({
            "status": "success",
            "data": tasks
        })
    except Exception as e:
        add_log("ERROR", f"获取联系人变更请求列表失败: {str(e)}", "server_control")
        return jsonify({
            "status": "error",
            "message": f"获取联系人变更请求列表失败: {str(e)}"
        }), 500

@app.route('/api/ovh/contact-change-requests/<int:task_id>', methods=['GET', 'OPTIONS'])
def get_contact_change_request_detail(task_id):
    """获取联系人变更请求详情 - GET /me/task/contactChange/{id}"""
    if request.method == 'OPTIONS':
        return '', 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        task_detail = client.get(f'/me/task/contactChange/{task_id}')
        add_log("INFO", f"成功获取联系人变更请求 {task_id} 详情", "server_control")
        return jsonify({
            "status": "success",
            "data": task_detail
        })
    except Exception as e:
        add_log("ERROR", f"获取联系人变更请求 {task_id} 详情失败: {str(e)}", "server_control")
        return jsonify({
            "status": "error",
            "message": f"获取联系人变更请求详情失败: {str(e)}"
        }), 500

@app.route('/api/ovh/contact-change-requests/<int:task_id>/accept', methods=['POST', 'OPTIONS'])
def accept_contact_change_request(task_id):
    """接受联系人变更请求 - POST /me/task/contactChange/{id}/accept"""
    if request.method == 'OPTIONS':
        return '', 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        data = request.get_json() or {}
        token = data.get('token')
        
        # 检查是否提供了 token
        if not token:
            return jsonify({
                "status": "error",
                "message": "缺少必需的 token 参数。请从邮件中获取 token 并输入。"
            }), 400
        
        # 使用提供的 token 调用
        client.post(f'/me/task/contactChange/{task_id}/accept', token=token)
        add_log("INFO", f"成功接受联系人变更请求 {task_id}", "server_control")
        return jsonify({
            "status": "success",
            "message": "联系人变更请求已接受"
        })
    except Exception as e:
        add_log("ERROR", f"接受联系人变更请求 {task_id} 失败: {str(e)}", "server_control")
        return jsonify({
            "status": "error",
            "message": f"接受联系人变更请求失败: {str(e)}"
        }), 500

@app.route('/api/ovh/contact-change-requests/<int:task_id>/refuse', methods=['POST', 'OPTIONS'])
def refuse_contact_change_request(task_id):
    """拒绝联系人变更请求 - POST /me/task/contactChange/{id}/refuse"""
    if request.method == 'OPTIONS':
        return '', 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        data = request.get_json() or {}
        token = data.get('token')
        
        # 检查是否提供了 token
        if not token:
            return jsonify({
                "status": "error",
                "message": "缺少必需的 token 参数。请从邮件中获取 token 并输入。"
            }), 400
        
        # 使用提供的 token 调用
        client.post(f'/me/task/contactChange/{task_id}/refuse', token=token)
        add_log("INFO", f"成功拒绝联系人变更请求 {task_id}", "server_control")
        return jsonify({
            "status": "success",
            "message": "联系人变更请求已拒绝"
        })
    except Exception as e:
        add_log("ERROR", f"拒绝联系人变更请求 {task_id} 失败: {str(e)}", "server_control")
        return jsonify({
            "status": "error",
            "message": f"拒绝联系人变更请求失败: {str(e)}"
        }), 500

@app.route('/api/ovh/contact-change-requests/<int:task_id>/resend-email', methods=['POST', 'OPTIONS'])
def resend_contact_change_email(task_id):
    """重发联系人变更邮件 - POST /me/task/contactChange/{id}/resendEmail"""
    if request.method == 'OPTIONS':
        return '', 200
    
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        client.post(f'/me/task/contactChange/{task_id}/resendEmail')
        add_log("INFO", f"成功重发联系人变更请求 {task_id} 的邮件", "server_control")
        return jsonify({
            "status": "success",
            "message": "确认邮件已重新发送"
        })
    except Exception as e:
        add_log("ERROR", f"重发联系人变更请求 {task_id} 邮件失败: {str(e)}", "server_control")
        return jsonify({
            "status": "error",
            "message": f"重发邮件失败: {str(e)}"
        }), 500

@app.route('/api/ovh/account/sub-accounts', methods=['GET'])
def get_sub_accounts():
    """获取子账户列表 - GET /me/subAccount"""
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        # 获取子账户ID列表
        sub_account_ids = client.get('/me/subAccount')
        
        # 获取每个子账户的详细信息
        sub_accounts = []
        for sub_id in sub_account_ids:
            try:
                sub_detail = client.get(f'/me/subAccount/{sub_id}')
                sub_accounts.append(sub_detail)
            except Exception as e:
                add_log("WARNING", f"获取子账户 {sub_id} 详情失败: {str(e)}", "account_management")
        
        add_log("INFO", f"成功获取 {len(sub_accounts)} 个子账户", "account_management")
        return jsonify({
            "status": "success",
            "data": sub_accounts
        })
    except Exception as e:
        add_log("ERROR", f"获取子账户列表失败: {str(e)}", "account_management")
        return jsonify({
            "status": "error",
            "message": f"获取子账户列表失败: {str(e)}"
        }), 500

@app.route('/api/ovh/account/bills', methods=['GET'])
def get_account_bills():
    """获取账单列表 - GET /me/bill"""
    client = get_ovh_client()
    if not client:
        return jsonify({"status": "error", "message": "未配置OVH API"}), 400
    
    try:
        # 获取账单ID列表
        bill_ids = client.get('/me/bill')
        
        # 获取最近20个账单的详细信息
        bills = []
        for bill_id in bill_ids[:20]:
            try:
                bill_detail = client.get(f'/me/bill/{bill_id}')
                bills.append(bill_detail)
            except Exception as e:
                add_log("WARNING", f"获取账单 {bill_id} 详情失败: {str(e)}", "account_management")
        
        add_log("INFO", f"成功获取 {len(bills)} 条账单记录", "account_management")
        return jsonify({
            "status": "success",
            "data": bills
        })
    except Exception as e:
        add_log("ERROR", f"获取账单列表失败: {str(e)}", "account_management")
        return jsonify({
            "status": "error",
            "message": f"获取账单列表失败: {str(e)}"
        }), 500

if __name__ == '__main__':
    # 确保所有文件都存在
    ensure_files_exist()
    
    # 初始化监控器
    init_monitor()
    
    # Load data first (会加载订阅数据)
    load_data()
    
    # 检查间隔全局强制为5秒（无任何条件判断）
    monitor.check_interval = 5
    save_subscriptions()
    print(f"监控检查间隔已强制设置为: 5秒（全局固定值）")
    
    # 只在主进程启动后台线程（避免Flask reloader重复启动）
    # 使用环境变量判断是否为主进程
    import os
    is_main_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    
    print(f"进程检查: WERKZEUG_RUN_MAIN={os.environ.get('WERKZEUG_RUN_MAIN')}, 是否启动后台线程={is_main_process}")
    
    if is_main_process or not app.debug:
        # 在主进程或非debug模式下启动后台线程
        print("启动后台线程...")
        # Start queue processor
        start_queue_processor()
        
        # 启动配置绑定狙击监控
        start_config_sniper_monitor()
        
        # 启动自动刷新缓存
        start_auto_refresh_cache()
    else:
        print("跳过后台线程启动（等待主进程）")
    
    # 自动启动服务器监控（如果有订阅）
    # 检查间隔全局强制为5秒
    monitor.check_interval = 5
    
    if len(monitor.subscriptions) > 0:
        monitor.start()
        add_log("INFO", f"自动启动服务器监控（{len(monitor.subscriptions)} 个订阅，检查间隔: 5秒）")
    
    # Add initial log
    add_log("INFO", "Server started")
    
    # 从 .env 读取配置
    PORT = int(os.getenv('PORT', 5000))
    DEBUG = os.getenv('DEBUG', 'false').lower() == 'true'
    
    # 打印配置信息
    print("=" * 60)
    print(f"🚀 后端服务启动配置")
    print(f"   端口: {PORT}")
    print(f"   调试模式: {'开启' if DEBUG else '关闭'}")
    print(f"   API密钥验证: {'开启' if os.getenv('ENABLE_API_KEY_AUTH', 'true').lower() == 'true' else '关闭'}")
    print("=" * 60)
    
    # Run the Flask app
    # 从 .env 文件读取端口和调试模式配置
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG)