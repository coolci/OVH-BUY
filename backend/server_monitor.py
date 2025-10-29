"""
服务器监控模块
定时检查服务器可用性变化并发送通知
"""

import threading
import time
from datetime import datetime
import traceback


class ServerMonitor:
    """服务器监控类"""
    
    def __init__(self, check_availability_func, send_notification_func, add_log_func):
        """
        初始化监控器
        
        Args:
            check_availability_func: 检查服务器可用性的函数
            send_notification_func: 发送通知的函数
            add_log_func: 添加日志的函数
        """
        self.check_availability = check_availability_func
        self.send_notification = send_notification_func
        self.add_log = add_log_func
        
        self.subscriptions = []  # 订阅列表
        self.known_servers = set()  # 已知服务器集合
        self.running = False  # 运行状态
        self.check_interval = 60  # 检查间隔（秒），默认60秒
        self.thread = None
        
        # 价格缓存：key = f"{plan_code}|{sorted_options}"，value = {"price": str, "timestamp": float}
        self.price_cache = {}
        self.price_cache_ttl = 3 * 24 * 3600  # 缓存有效期：3天（秒）
        
        self.add_log("INFO", "服务器监控器初始化完成", "monitor")
    
    def add_subscription(self, plan_code, datacenters=None, notify_available=True, notify_unavailable=False, server_name=None, last_status=None, history=None):
        """
        添加服务器订阅
        
        Args:
            plan_code: 服务器型号代码
            datacenters: 要监控的数据中心列表，None或空列表表示监控所有
            notify_available: 是否在有货时提醒
            notify_unavailable: 是否在无货时提醒
            server_name: 服务器友好名称（如"KS-2 | Intel Xeon-D 1540"）
            last_status: 上次检查的状态字典（用于恢复，避免重复通知）
            history: 历史记录列表（用于恢复）
        """
        # 检查是否已存在
        existing = next((s for s in self.subscriptions if s["planCode"] == plan_code), None)
        if existing:
            self.add_log("WARNING", f"订阅已存在: {plan_code}，将更新配置（不会重置状态，避免重复通知）", "monitor")
            existing["datacenters"] = datacenters or []
            existing["notifyAvailable"] = notify_available
            existing["notifyUnavailable"] = notify_unavailable
            # 更新服务器名称（总是更新，即使为None也要更新）
            existing["serverName"] = server_name
            # 确保历史记录字段存在
            if "history" not in existing:
                existing["history"] = []
            # ✅ 不重置 lastStatus，保留已知状态，避免重复通知
            return
        
        subscription = {
            "planCode": plan_code,
            "datacenters": datacenters or [],
            "notifyAvailable": notify_available,
            "notifyUnavailable": notify_unavailable,
            "lastStatus": last_status if last_status is not None else {},  # 恢复上次状态或初始化为空
            "createdAt": datetime.now().isoformat(),
            "history": history if history is not None else []  # 恢复历史记录或初始化为空
        }
        
        # 添加服务器名称（如果提供）
        if server_name:
            subscription["serverName"] = server_name
        
        self.subscriptions.append(subscription)
        
        display_name = f"{plan_code} ({server_name})" if server_name else plan_code
        self.add_log("INFO", f"添加订阅: {display_name}, 数据中心: {datacenters or '全部'}", "monitor")
    
    def remove_subscription(self, plan_code):
        """删除订阅"""
        original_count = len(self.subscriptions)
        self.subscriptions = [s for s in self.subscriptions if s["planCode"] != plan_code]
        
        if len(self.subscriptions) < original_count:
            self.add_log("INFO", f"删除订阅: {plan_code}", "monitor")
            return True
        return False
    
    def clear_subscriptions(self):
        """清空所有订阅"""
        count = len(self.subscriptions)
        self.subscriptions = []
        self.add_log("INFO", f"清空所有订阅 ({count} 项)", "monitor")
        return count
    
    def check_availability_change(self, subscription):
        """
        检查单个订阅的可用性变化（配置级别监控）
        
        Args:
            subscription: 订阅配置
        """
        plan_code = subscription["planCode"]
        
        try:
            # 获取当前可用性（支持配置级别）
            current_availability = self.check_availability(plan_code)
            if not current_availability:
                self.add_log("WARNING", f"无法获取 {plan_code} 的可用性信息", "monitor")
                return
            
            last_status = subscription.get("lastStatus", {})
            monitored_dcs = subscription.get("datacenters", [])
            
            # 调试日志
            self.add_log("INFO", f"订阅 {plan_code} - 监控数据中心: {monitored_dcs if monitored_dcs else '全部'}", "monitor")
            self.add_log("INFO", f"订阅 {plan_code} - 当前发现 {len(current_availability)} 个配置组合", "monitor")
            
            # 遍历当前所有配置组合
            for config_key, config_data in current_availability.items():
                # config_key 格式: "plancode.memory.storage" 或 "datacenter"
                # config_data 格式: {"datacenters": {"dc1": "status1", ...}, "memory": "xxx", "storage": "xxx"}
                
                # 如果是简单的数据中心状态（旧版兼容）
                if isinstance(config_data, str):
                    dc = config_key
                    status = config_data
                    
                    # 如果指定了数据中心列表，只监控列表中的
                    if monitored_dcs and dc not in monitored_dcs:
                        continue
                    
                    old_status = last_status.get(dc)
                    self._check_and_notify_change(subscription, plan_code, dc, status, old_status, None, dc)
                
                # 如果是配置级别的数据（新版配置监控）
                elif isinstance(config_data, dict) and "datacenters" in config_data:
                    memory = config_data.get("memory", "N/A")
                    storage = config_data.get("storage", "N/A")
                    config_display = f"{memory} + {storage}"
                    
                    self.add_log("INFO", f"检查配置: {config_display}", "monitor")
                    
                    # 准备配置信息
                    config_info = {
                        "memory": memory,
                        "storage": storage,
                        "display": config_display,
                        "options": config_data.get("options", [])  # 包含API2格式的选项代码
                    }
                    
                    # 先收集所有需要发送通知的数据中心
                    notifications_to_send = []
                    for dc, status in config_data["datacenters"].items():
                        # 如果指定了数据中心列表，只监控列表中的
                        if monitored_dcs and dc not in monitored_dcs:
                            continue
                        
                        # 使用配置作为key来追踪状态
                        status_key = f"{dc}|{config_key}"
                        old_status = last_status.get(status_key)
                        
                        # 检查是否需要发送通知（包括首次检查）
                        status_changed = False
                        change_type = None
                        
                        # 首次检查时也发送通知（如果配置允许）
                        if old_status is None:
                            config_desc = f" [{config_display}]" if config_display else ""
                            if status == "unavailable":
                                self.add_log("INFO", f"首次检查: {plan_code}@{dc}{config_desc} 无货", "monitor")
                                # 首次检查无货时不通知（除非用户明确要求）
                                if subscription.get("notifyUnavailable", False):
                                    status_changed = True
                                    change_type = "unavailable"
                            else:
                                # 首次检查有货时发送通知
                                self.add_log("INFO", f"首次检查: {plan_code}@{dc}{config_desc} 有货（状态: {status}），发送通知", "monitor")
                                if subscription.get("notifyAvailable", True):
                                    status_changed = True
                                    change_type = "available"
                        # 从无货变有货
                        elif old_status == "unavailable" and status != "unavailable":
                            if subscription.get("notifyAvailable", True):
                                status_changed = True
                                change_type = "available"
                                config_desc = f" [{config_display}]" if config_display else ""
                                self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从无货变有货（状态: {status}）", "monitor")
                        # 从有货变无货
                        elif old_status not in ["unavailable", None] and status == "unavailable":
                            if subscription.get("notifyUnavailable", False):
                                status_changed = True
                                change_type = "unavailable"
                                config_desc = f" [{config_display}]" if config_display else ""
                                self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从有货变无货", "monitor")
                        
                        if status_changed:
                            notifications_to_send.append({
                                "dc": dc,
                                "status": status,
                                "old_status": old_status,
                                "status_key": status_key,
                                "change_type": change_type
                            })
                    
                    # 对于同一个配置，只查询一次价格（使用第一个有货的数据中心）
                    price_text = None
                    if notifications_to_send:
                        # 找出第一个有货的数据中心用于价格查询
                        first_available_dc = None
                        for notif in notifications_to_send:
                            if notif["change_type"] == "available" and notif["status"] != "unavailable":
                                first_available_dc = notif["dc"]
                                break
                        
                        # 如果有有货的数据中心，查询价格
                        if first_available_dc:
                            try:
                                import threading
                                import queue
                                price_queue = queue.Queue()
                                
                                def fetch_price():
                                    try:
                                        price_result = self._get_price_info(plan_code, first_available_dc, config_info)
                                        price_queue.put(price_result)
                                    except Exception as e:
                                        self.add_log("WARNING", f"价格获取线程异常: {str(e)}", "monitor")
                                        price_queue.put(None)
                                
                                # 启动价格获取线程
                                price_thread = threading.Thread(target=fetch_price, daemon=True)
                                price_thread.start()
                                price_thread.join(timeout=30.0)  # 最多等待30秒
                                
                                if price_thread.is_alive():
                                    self.add_log("WARNING", f"价格获取超时（30秒），发送不带价格的通知", "monitor")
                                else:
                                    try:
                                        price_text = price_queue.get_nowait()
                                    except queue.Empty:
                                        pass
                                
                                if price_text:
                                    self.add_log("DEBUG", f"配置 {config_display} 价格获取成功: {price_text}，将在所有通知中复用", "monitor")
                                else:
                                    self.add_log("WARNING", f"配置 {config_display} 价格获取失败，通知中不包含价格信息", "monitor")
                            except Exception as e:
                                self.add_log("WARNING", f"价格获取过程异常: {str(e)}", "monitor")
                    
                    # 按change_type分组发送通知（汇总同一配置的所有有货机房）
                    available_notifications = [n for n in notifications_to_send if n["change_type"] == "available"]
                    unavailable_notifications = [n for n in notifications_to_send if n["change_type"] == "unavailable"]
                    
                    # 发送有货通知（汇总所有有货的机房到一个通知，带按钮）
                    if available_notifications:
                        config_desc = f" [{config_info['display']}]" if config_info else ""
                        self.add_log("INFO", f"准备发送汇总提醒: {plan_code}{config_desc} - {len(available_notifications)}个机房有货", "monitor")
                        server_name = subscription.get("serverName")
                        
                        # 创建包含价格的配置信息副本
                        config_info_with_price = config_info.copy() if config_info else None
                        if config_info_with_price:
                            config_info_with_price["cached_price"] = price_text  # 传递缓存的价格
                        
                        # 汇总所有有货的机房数据
                        available_dcs = [{"dc": n["dc"], "status": n["status"]} for n in available_notifications]
                        self.send_availability_alert_grouped(
                            plan_code, available_dcs, config_info_with_price, server_name
                        )
                        
                        # 添加到历史记录
                        if "history" not in subscription:
                            subscription["history"] = []
                        
                        for notif in available_notifications:
                            history_entry = {
                                "timestamp": datetime.now().isoformat(),
                                "datacenter": notif["dc"],
                                "status": notif["status"],
                                "changeType": notif["change_type"],
                                "oldStatus": notif["old_status"]
                            }
                            
                            if config_info:
                                history_entry["config"] = config_info
                            
                            subscription["history"].append(history_entry)
                    
                    # 发送无货通知（每个机房单独发送）
                    for notif in unavailable_notifications:
                        config_desc = f" [{config_info['display']}]" if config_info else ""
                        self.add_log("INFO", f"准备发送提醒: {plan_code}@{notif['dc']}{config_desc} - {notif['change_type']}", "monitor")
                        server_name = subscription.get("serverName")
                        
                        self.send_availability_alert(plan_code, notif["dc"], notif["status"], notif["change_type"], 
                                                    config_info, server_name)
                        
                        # 添加到历史记录
                        if "history" not in subscription:
                            subscription["history"] = []
                        
                        history_entry = {
                            "timestamp": datetime.now().isoformat(),
                            "datacenter": notif["dc"],
                            "status": notif["status"],
                            "changeType": notif["change_type"],
                            "oldStatus": notif["old_status"]
                        }
                        
                        if config_info:
                            history_entry["config"] = config_info
                        
                        subscription["history"].append(history_entry)
                    
                    # 限制历史记录数量
                    if len(subscription["history"]) > 100:
                        subscription["history"] = subscription["history"][-100:]
            
            # 更新状态（需要转换为状态字典）
            new_last_status = {}
            for config_key, config_data in current_availability.items():
                if isinstance(config_data, str):
                    # 简单的数据中心状态
                    new_last_status[config_key] = config_data
                elif isinstance(config_data, dict) and "datacenters" in config_data:
                    # 配置级别的状态
                    for dc, status in config_data["datacenters"].items():
                        status_key = f"{dc}|{config_key}"
                        new_last_status[status_key] = status
            
            subscription["lastStatus"] = new_last_status
            
        except Exception as e:
            self.add_log("ERROR", f"检查 {plan_code} 可用性时出错: {str(e)}", "monitor")
            self.add_log("ERROR", f"错误详情: {traceback.format_exc()}", "monitor")
    
    def _check_and_notify_change(self, subscription, plan_code, dc, status, old_status, config_info=None, status_key=None):
        """
        检查状态变化并发送通知
        
        Args:
            subscription: 订阅对象
            plan_code: 服务器型号
            dc: 数据中心
            status: 当前状态
            old_status: 旧状态
            config_info: 配置信息 {"memory": "xxx", "storage": "xxx", "display": "xxx"}
            status_key: 状态键（用于lastStatus）
        """
        # 状态变化检测（包括首次检查）
        status_changed = False
        change_type = None
        
        # 首次检查时也发送通知（如果配置允许）
        if old_status is None:
            config_desc = f" [{config_info['display']}]" if config_info else ""
            if status == "unavailable":
                self.add_log("INFO", f"首次检查: {plan_code}@{dc}{config_desc} 无货", "monitor")
                # 首次检查无货时不通知（除非用户明确要求）
                if subscription.get("notifyUnavailable", False):
                    status_changed = True
                    change_type = "unavailable"
            else:
                # 首次检查有货时发送通知
                self.add_log("INFO", f"首次检查: {plan_code}@{dc}{config_desc} 有货（状态: {status}），发送通知", "monitor")
                if subscription.get("notifyAvailable", True):
                    status_changed = True
                    change_type = "available"
        # 从无货变有货
        elif old_status == "unavailable" and status != "unavailable":
            if subscription.get("notifyAvailable", True):
                status_changed = True
                change_type = "available"
                config_desc = f" [{config_info['display']}]" if config_info else ""
                self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从无货变有货", "monitor")
        
        # 从有货变无货
        elif old_status not in ["unavailable", None] and status == "unavailable":
            if subscription.get("notifyUnavailable", False):
                status_changed = True
                change_type = "unavailable"
                config_desc = f" [{config_info['display']}]" if config_info else ""
                self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从有货变无货", "monitor")
        
        # 发送通知并记录历史
        if status_changed:
            config_desc = f" [{config_info['display']}]" if config_info else ""
            self.add_log("INFO", f"准备发送提醒: {plan_code}@{dc}{config_desc} - {change_type}", "monitor")
            # 获取服务器名称
            server_name = subscription.get("serverName")
            self.send_availability_alert(plan_code, dc, status, change_type, config_info, server_name)
            
            # 添加到历史记录
            if "history" not in subscription:
                subscription["history"] = []
            
            history_entry = {
                "timestamp": datetime.now().isoformat(),
                "datacenter": dc,
                "status": status,
                "changeType": change_type,
                "oldStatus": old_status
            }
            
            # 添加配置信息到历史记录
            if config_info:
                history_entry["config"] = config_info
            
            subscription["history"].append(history_entry)
            
            # 限制历史记录数量，保留最近100条
            if len(subscription["history"]) > 100:
                subscription["history"] = subscription["history"][-100:]
    
    def send_availability_alert_grouped(self, plan_code, available_dcs, config_info=None, server_name=None):
        """
        发送汇总的可用性提醒（一个通知包含多个有货的机房，带内联键盘按钮）
        
        Args:
            plan_code: 服务器型号
            available_dcs: 有货的数据中心列表 [{"dc": "gra", "status": "available"}, ...]
            config_info: 配置信息 {"memory": "xxx", "storage": "xxx", "display": "xxx", "options": [...]}
            server_name: 服务器友好名称
        """
        try:
            import json
            import base64
            
            message = f"🎉 服务器上架通知！\n\n"
            
            if server_name:
                message += f"服务器: {server_name}\n"
            
            message += f"型号: {plan_code}\n"
            
            if config_info:
                message += (
                    f"配置: {config_info['display']}\n"
                    f"├─ 内存: {config_info['memory']}\n"
                    f"└─ 存储: {config_info['storage']}\n"
                )
            
            # 添加价格信息
            price_text = None
            if config_info and "cached_price" in config_info:
                price_text = config_info.get("cached_price")
            
            if price_text:
                message += f"\n💰 价格: {price_text}\n"
            
            message += f"\n✅ 有货的机房 ({len(available_dcs)}个):\n"
            for dc_info in available_dcs:
                dc = dc_info.get("dc", "")
                status = dc_info.get("status", "")
                # 数据中心名称映射
                dc_display_map = {
                    "gra": "🇫🇷 法国·格拉沃利讷",
                    "rbx": "🇫🇷 法国·鲁贝",
                    "sbg": "🇫🇷 法国·斯特拉斯堡",
                    "bhs": "🇨🇦 加拿大·博舍维尔",
                    "syd": "🇦🇺 澳大利亚·悉尼",
                    "sgp": "🇸🇬 新加坡",
                    "ynm": "🇮🇳 印度·孟买",
                    "waw": "🇵🇱 波兰·华沙",
                    "fra": "🇩🇪 德国·法兰克福",
                    "lon": "🇬🇧 英国·伦敦",
                    "par": "🇫🇷 法国·巴黎",
                    "eri": "🇮🇹 意大利·埃里切",
                    "lim": "🇵🇱 波兰·利马诺瓦"
                }
                dc_display = dc_display_map.get(dc.lower(), dc.upper())
                message += f"  • {dc_display} ({dc.upper()})\n"
            
            message += f"\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            # 构建内联键盘按钮（每个机房一个按钮，最多每行2个按钮）
            inline_keyboard = []
            row = []
            for idx, dc_info in enumerate(available_dcs):
                dc = dc_info.get("dc", "")
                dc_display_map = {
                    "gra": "🇫🇷 Gra",
                    "rbx": "🇫🇷 Rbx",
                    "sbg": "🇫🇷 Sbg",
                    "bhs": "🇨🇦 Bhs",
                    "syd": "🇦🇺 Syd",
                    "sgp": "🇸🇬 Sgp",
                    "ynm": "🇮🇳 Mum",
                    "waw": "🇵🇱 Waw",
                    "fra": "🇩🇪 Fra",
                    "lon": "🇬🇧 Lon",
                    "par": "🇫🇷 Par",
                    "eri": "🇮🇹 Eri",
                    "lim": "🇵🇱 Lim"
                }
                button_text = dc_display_map.get(dc.lower(), dc.upper())
                
                # 构建回调数据：planCode|datacenter|options(JSON编码)
                options = config_info.get("options", []) if config_info else []
                callback_data = {
                    "action": "add_to_queue",
                    "planCode": plan_code,
                    "datacenter": dc,
                    "options": options
                }
                # Telegram callback_data 最大64字节，使用base64编码压缩
                callback_data_str = json.dumps(callback_data, ensure_ascii=False, separators=(',', ':'))
                if len(callback_data_str.encode('utf-8')) > 60:  # 留4字节给base64前缀
                    # 如果数据太大，使用base64编码
                    callback_data_encoded = base64.b64encode(callback_data_str.encode('utf-8')).decode('utf-8')
                    callback_data_final = "b64:" + callback_data_encoded[:60]  # 确保不超过64字节
                else:
                    callback_data_final = callback_data_str[:64]
                
                row.append({
                    "text": button_text,
                    "callback_data": callback_data_final
                })
                
                # 每行最多2个按钮
                if len(row) >= 2 or idx == len(available_dcs) - 1:
                    inline_keyboard.append(row)
                    row = []
            
            reply_markup = {"inline_keyboard": inline_keyboard}
            
            config_desc = f" [{config_info['display']}]" if config_info else ""
            self.add_log("INFO", f"正在发送汇总Telegram通知: {plan_code}{config_desc} - {len(available_dcs)}个机房", "monitor")
            
            # 调用发送函数，传入reply_markup
            # 检查send_notification是否支持reply_markup参数
            import inspect
            sig = inspect.signature(self.send_notification)
            if 'reply_markup' in sig.parameters:
                result = self.send_notification(message, reply_markup=reply_markup)
            else:
                # 如果不支持，先尝试用**kwargs方式调用
                try:
                    result = self.send_notification(message, **{"reply_markup": reply_markup})
                except:
                    # 如果还是不支持，先记录警告然后只发送消息
                    self.add_log("WARNING", "send_notification函数不支持reply_markup参数，仅发送文字消息", "monitor")
                    result = self.send_notification(message)
            
            if result:
                self.add_log("INFO", f"✅ Telegram汇总通知发送成功: {plan_code}{config_desc}", "monitor")
            else:
                self.add_log("WARNING", f"⚠️ Telegram汇总通知发送失败: {plan_code}{config_desc}", "monitor")
                
        except Exception as e:
            self.add_log("ERROR", f"发送汇总提醒时发生异常: {str(e)}", "monitor")
            import traceback
            self.add_log("ERROR", f"错误详情: {traceback.format_exc()}", "monitor")
    
    def send_availability_alert(self, plan_code, datacenter, status, change_type, config_info=None, server_name=None):
        """
        发送可用性变化提醒
        
        Args:
            plan_code: 服务器型号
            datacenter: 数据中心
            status: 状态
            change_type: 变化类型
            config_info: 配置信息 {"memory": "xxx", "storage": "xxx", "display": "xxx"}
            server_name: 服务器友好名称（如"KS-2 | Intel Xeon-D 1540"）
        """
        try:
            if change_type == "available":
                # 基础消息
                message = f"🎉 服务器上架通知！\n\n"
                
                # 添加服务器名称（如果有）
                if server_name:
                    message += f"服务器: {server_name}\n"
                
                message += f"型号: {plan_code}\n"
                message += f"数据中心: {datacenter}\n"
                
                # 添加配置信息（如果有）
                if config_info:
                    message += (
                        f"配置: {config_info['display']}\n"
                        f"├─ 内存: {config_info['memory']}\n"
                        f"└─ 存储: {config_info['storage']}\n"
                    )
                
                # 获取价格信息（优先使用缓存的价格）
                price_text = None
                
                # 如果config_info中包含缓存的价格，直接使用
                if config_info and "cached_price" in config_info:
                    price_text = config_info.get("cached_price")
                    if price_text:
                        self.add_log("DEBUG", f"使用缓存的价格: {price_text}", "monitor")
                
                # 如果没有缓存的价格，才去查询
                if not price_text:
                    try:
                        import threading
                        import queue
                        price_queue = queue.Queue()
                        
                        def fetch_price():
                            try:
                                price_result = self._get_price_info(plan_code, datacenter, config_info)
                                price_queue.put(price_result)
                            except Exception as e:
                                self.add_log("WARNING", f"价格获取线程异常: {str(e)}", "monitor")
                                price_queue.put(None)
                        
                        # 启动价格获取线程
                        price_thread = threading.Thread(target=fetch_price, daemon=True)
                        price_thread.start()
                        price_thread.join(timeout=30.0)  # 最多等待30秒
                        
                        if price_thread.is_alive():
                            # 如果线程还在运行，说明超时了
                            self.add_log("WARNING", f"价格获取超时（30秒），发送不带价格的通知", "monitor")
                            price_text = None
                        else:
                            # 尝试获取结果（如果线程完成）
                            try:
                                price_text = price_queue.get_nowait()
                            except queue.Empty:
                                price_text = None
                        
                        if not price_text:
                            # 如果价格获取失败，记录警告但继续发送通知
                            self.add_log("WARNING", f"价格获取失败或超时，通知中不包含价格信息", "monitor")
                    except Exception as e:
                        self.add_log("WARNING", f"价格获取过程异常: {str(e)}，发送不带价格的通知", "monitor")
                        import traceback
                        self.add_log("WARNING", f"价格获取异常详情: {traceback.format_exc()}", "monitor")
                
                # 如果有价格信息，添加到消息中
                if price_text:
                    message += f"\n💰 价格: {price_text}\n"
                
                message += (
                    f"状态: {status}\n"
                    f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"💡 快去抢购吧！"
                )
            else:
                # 基础消息
                message = f"📦 服务器下架通知\n\n"
                
                # 添加服务器名称（如果有）
                if server_name:
                    message += f"服务器: {server_name}\n"
                
                message += f"型号: {plan_code}\n"
                message += f"数据中心: {datacenter}\n"
                
                # 添加配置信息（如果有）
                if config_info:
                    message += (
                        f"配置: {config_info['display']}\n"
                    )
                
                message += (
                    f"状态: 已无货\n"
                    f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            
            config_desc = f" [{config_info['display']}]" if config_info else ""
            self.add_log("INFO", f"正在发送Telegram通知: {plan_code}@{datacenter}{config_desc}", "monitor")
            result = self.send_notification(message)
            
            if result:
                self.add_log("INFO", f"✅ Telegram通知发送成功: {plan_code}@{datacenter}{config_desc} - {change_type}", "monitor")
            else:
                self.add_log("WARNING", f"⚠️ Telegram通知发送失败: {plan_code}@{datacenter}{config_desc}", "monitor")
            
        except Exception as e:
            self.add_log("ERROR", f"发送提醒时发生异常: {str(e)}", "monitor")
            self.add_log("ERROR", f"错误详情: {traceback.format_exc()}", "monitor")
    
    def _get_price_cache_key(self, plan_code, options):
        """
        生成价格缓存键
        
        Args:
            plan_code: 服务器型号
            options: 配置选项列表
        
        Returns:
            str: 缓存键
        """
        # 对options进行排序以确保相同配置生成相同键
        sorted_options = sorted(options) if options else []
        return f"{plan_code}|{','.join(sorted_options)}"
    
    def _get_cached_price(self, plan_code, options):
        """
        从缓存中获取价格
        
        Args:
            plan_code: 服务器型号
            options: 配置选项列表
        
        Returns:
            str or None: 缓存的价格文本，如果缓存不存在或过期返回None
        """
        cache_key = self._get_price_cache_key(plan_code, options)
        
        if cache_key in self.price_cache:
            cached_data = self.price_cache[cache_key]
            timestamp = cached_data.get("timestamp", 0)
            current_time = time.time()
            
            # 检查缓存是否过期
            if current_time - timestamp < self.price_cache_ttl:
                price_text = cached_data.get("price")
                age_hours = (current_time - timestamp) / 3600
                self.add_log("DEBUG", f"使用缓存价格（已缓存 {age_hours:.1f} 小时）: {price_text}", "monitor")
                return price_text
            else:
                # 缓存过期，删除
                del self.price_cache[cache_key]
                self.add_log("DEBUG", f"缓存已过期，删除: {cache_key}", "monitor")
        
        return None
    
    def _set_cached_price(self, plan_code, options, price_text):
        """
        将价格保存到缓存
        
        Args:
            plan_code: 服务器型号
            options: 配置选项列表
            price_text: 价格文本
        """
        cache_key = self._get_price_cache_key(plan_code, options)
        self.price_cache[cache_key] = {
            "price": price_text,
            "timestamp": time.time()
        }
        self.add_log("DEBUG", f"价格已缓存: {cache_key} = {price_text}", "monitor")
    
    def _get_price_info(self, plan_code, datacenter, config_info=None):
        """
        获取配置后的价格信息（带缓存支持）
        
        Args:
            plan_code: 服务器型号
            datacenter: 数据中心（用于查询，但不影响缓存键）
            config_info: 配置信息 {"memory": "xxx", "storage": "xxx", "display": "xxx", "options": [...]}
        
        Returns:
            str: 价格信息文本，如果获取失败返回None
        """
        try:
            # 提取配置选项
            options = []
            
            if config_info:
                # 如果config_info中已经有options字段（API2格式），直接使用
                if 'options' in config_info and config_info['options']:
                    options = config_info['options']
            
            # 先检查缓存
            cached_price = self._get_cached_price(plan_code, options)
            if cached_price:
                return cached_price
            
            # 缓存不存在或过期，查询新价格
            # 使用HTTP请求调用内部价格API（确保在正确的上下文访问配置）
            import requests
            
            self.add_log("DEBUG", f"开始获取价格: plan_code={plan_code}, datacenter={datacenter}, options={options}", "monitor")
            
            # 调用内部API端点
            api_url = "http://127.0.0.1:19998/api/internal/monitor/price"
            payload = {
                "plan_code": plan_code,
                "datacenter": datacenter,
                "options": options
            }
            
            try:
                response = requests.post(api_url, json=payload, timeout=30)
                response.raise_for_status()
                result = response.json()
            except requests.exceptions.RequestException as e:
                self.add_log("WARNING", f"价格API请求失败: {str(e)}", "monitor")
                return None
            
            if result.get("success") and result.get("price"):
                price_info = result["price"]
                prices = price_info.get("prices", {})
                with_tax = prices.get("withTax")
                currency = prices.get("currencyCode", "EUR")
                
                if with_tax is not None:
                    # 格式化价格
                    currency_symbol = "€" if currency == "EUR" else "$" if currency == "USD" else currency
                    price_text = f"{currency_symbol}{with_tax:.2f}/月"
                    self.add_log("DEBUG", f"价格获取成功: {price_text}", "monitor")
                    
                    # 保存到缓存
                    self._set_cached_price(plan_code, options, price_text)
                    
                    return price_text
                else:
                    self.add_log("WARNING", f"价格获取成功但withTax为None: result={result}", "monitor")
            else:
                error_msg = result.get("error", "未知错误")
                self.add_log("WARNING", f"价格获取失败: {error_msg}", "monitor")
            
            return None
                
        except Exception as e:
            self.add_log("WARNING", f"获取价格信息时出错: {str(e)}", "monitor")
            import traceback
            self.add_log("WARNING", f"价格获取异常堆栈: {traceback.format_exc()}", "monitor")
            return None
    
    def check_new_servers(self, current_server_list):
        """
        检查新服务器上架
        
        Args:
            current_server_list: 当前服务器列表
        """
        try:
            current_codes = {s.get("planCode") for s in current_server_list if s.get("planCode")}
            
            # 首次运行，初始化已知服务器
            if not self.known_servers:
                self.known_servers = current_codes
                self.add_log("INFO", f"初始化已知服务器列表: {len(current_codes)} 台", "monitor")
                return
            
            # 找出新服务器
            new_servers = current_codes - self.known_servers
            
            if new_servers:
                for server_code in new_servers:
                    server = next((s for s in current_server_list if s.get("planCode") == server_code), None)
                    if server:
                        self.send_new_server_alert(server)
                
                # 更新已知服务器列表
                self.known_servers = current_codes
                self.add_log("INFO", f"检测到 {len(new_servers)} 台新服务器上架", "monitor")
        
        except Exception as e:
            self.add_log("ERROR", f"检查新服务器时出错: {str(e)}", "monitor")
    
    def send_new_server_alert(self, server):
        """发送新服务器上架提醒"""
        try:
            message = (
                f"🆕 新服务器上架通知！\n\n"
                f"型号: {server.get('planCode', 'N/A')}\n"
                f"名称: {server.get('name', 'N/A')}\n"
                f"CPU: {server.get('cpu', 'N/A')}\n"
                f"内存: {server.get('memory', 'N/A')}\n"
                f"存储: {server.get('storage', 'N/A')}\n"
                f"带宽: {server.get('bandwidth', 'N/A')}\n"
                f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"💡 快去查看详情！"
            )
            
            self.send_notification(message)
            self.add_log("INFO", f"发送新服务器提醒: {server.get('planCode')}", "monitor")
            
        except Exception as e:
            self.add_log("ERROR", f"发送新服务器提醒失败: {str(e)}", "monitor")
    
    def monitor_loop(self):
        """监控主循环"""
        self.add_log("INFO", "监控循环已启动", "monitor")
        
        while self.running:
            try:
                # 检查订阅的服务器
                if self.subscriptions:
                    self.add_log("INFO", f"开始检查 {len(self.subscriptions)} 个订阅...", "monitor")
                    
                    for subscription in self.subscriptions:
                        if not self.running:  # 检查是否被停止
                            break
                        self.check_availability_change(subscription)
                        time.sleep(1)  # 避免请求过快
                else:
                    self.add_log("INFO", "当前无订阅，跳过检查", "monitor")
                
                # 注意：新服务器检查需要在外部调用时传入服务器列表
                
            except Exception as e:
                self.add_log("ERROR", f"监控循环出错: {str(e)}", "monitor")
                self.add_log("ERROR", f"错误详情: {traceback.format_exc()}", "monitor")
            
            # 等待下次检查（使用可中断的sleep）
            if self.running:
                self.add_log("INFO", f"等待 {self.check_interval} 秒后进行下次检查...", "monitor")
                # 分段sleep，每秒检查一次running状态，实现快速停止
                for _ in range(self.check_interval):
                    if not self.running:
                        break
                    time.sleep(1)
        
        self.add_log("INFO", "监控循环已停止", "monitor")
    
    def start(self):
        """启动监控"""
        if self.running:
            self.add_log("WARNING", "监控已在运行中", "monitor")
            return False
        
        self.running = True
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread.start()
        
        self.add_log("INFO", f"服务器监控已启动 (检查间隔: {self.check_interval}秒)", "monitor")
        return True
    
    def stop(self):
        """停止监控"""
        if not self.running:
            self.add_log("WARNING", "监控未运行", "monitor")
            return False
        
        self.running = False
        self.add_log("INFO", "正在停止服务器监控...", "monitor")
        
        # 等待线程结束（最多等待3秒，因为已优化为1秒检查一次）
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        
        self.add_log("INFO", "服务器监控已停止", "monitor")
        return True
    
    def get_status(self):
        """获取监控状态"""
        return {
            "running": self.running,
            "subscriptions_count": len(self.subscriptions),
            "known_servers_count": len(self.known_servers),
            "check_interval": self.check_interval,
            "subscriptions": self.subscriptions
        }
    
    def set_check_interval(self, interval):
        """设置检查间隔（秒）"""
        if interval < 60:
            self.add_log("WARNING", "检查间隔不能小于60秒", "monitor")
            return False
        
        self.check_interval = interval
        self.add_log("INFO", f"检查间隔已设置为 {interval} 秒", "monitor")
        return True
