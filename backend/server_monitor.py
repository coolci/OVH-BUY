"""
服务器监控模块
定时检查服务器可用性变化并发送通知
"""

import threading
import time
from datetime import datetime, timedelta
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed


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
        self._external_add_log = add_log_func
        
        self.subscriptions = []  # 订阅列表
        self.known_servers = set()  # 已知服务器集合
        self.running = False  # 运行状态
        self.check_interval = 5  # 检查间隔（秒），默认5秒
        self.thread = None
        self.max_workers = 4  # 并发检查的最大线程数
        
        # Options 缓存：key = f"{plan_code}|{datacenter}"，value = {"options": list, "timestamp": float}
        # 用于在 Telegram callback_data 中 options 丢失时恢复（旧机制，保留兼容性）
        self.options_cache = {}
        self.options_cache_ttl = 24 * 3600  # 缓存有效期：24小时（秒）
        
        # UUID 消息缓存：key = UUID字符串，value = {"planCode": str, "datacenter": str, "options": list, "timestamp": float}
        # 用于通过UUID恢复完整的下单配置信息
        self.message_uuid_cache = {}
        self.message_uuid_cache_ttl = 24 * 3600  # 缓存有效期：24小时（秒）
        
        # ✅ 添加线程锁保护缓存操作的并发安全
        self._cache_lock = threading.Lock()
        self._trace_ctx = threading.local()

        def _log_wrapper(level, message, category="monitor"):
            trace_id = getattr(self._trace_ctx, "trace_id", None)
            prefix = f"[trace:{trace_id}] " if trace_id else ""
            self._external_add_log(level, f"{prefix}{message}", category)

        self.add_log = _log_wrapper
        
        self.add_log("INFO", "服务器监控器初始化完成", "monitor")
    
    def _set_trace_id(self, trace_id):
        if trace_id:
            self._trace_ctx.trace_id = trace_id
    
    def _clear_trace_id(self):
        if hasattr(self._trace_ctx, "trace_id"):
            del self._trace_ctx.trace_id
    
    def _get_trace_id(self):
        return getattr(self._trace_ctx, "trace_id", None)
    
    def _limit_history_size(self, subscription, max_size=100):
        """
        限制订阅历史记录数量，保留最近N条
        
        Args:
            subscription: 订阅对象
            max_size: 最大历史记录数量，默认100
        """
        if "history" not in subscription:
            subscription["history"] = []
        
        if len(subscription["history"]) > max_size:
            subscription["history"] = subscription["history"][-max_size:]
    
    def _now_beijing(self) -> datetime:
        """返回北京时间（Asia/Shanghai）的当前时间。"""
        try:
            from zoneinfo import ZoneInfo  # Python 3.9+
            return datetime.now(ZoneInfo("Asia/Shanghai"))
        except Exception:
            # 兼容无zoneinfo环境：使用UTC+8近似
            return datetime.utcnow() + timedelta(hours=8)
    
    def add_subscription(self, plan_code, datacenters=None, notify_available=True, notify_unavailable=False, server_name=None, last_status=None, history=None, auto_order=False, quantity=1):
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
            auto_order: 是否自动下单
            quantity: 自动下单数量（每个配置×每个数据中心的数量）
        """
        # 检查是否已存在
        existing = next((s for s in self.subscriptions if s["planCode"] == plan_code), None)
        if existing:
            self.add_log("WARNING", f"订阅已存在: {plan_code}，将更新配置（不会重置状态，避免重复通知）", "monitor")
            existing["datacenters"] = datacenters or []
            existing["notifyAvailable"] = notify_available
            existing["notifyUnavailable"] = notify_unavailable
            # 更新自动下单标记和数量
            existing["autoOrder"] = bool(auto_order)
            if auto_order:
                existing["quantity"] = max(1, int(quantity)) if quantity else 1
            elif "quantity" in existing:
                # 如果关闭自动下单，移除数量字段
                existing.pop("quantity", None)
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
        # 自动下单标记和数量
        if auto_order:
            subscription["autoOrder"] = True
            subscription["quantity"] = max(1, int(quantity)) if quantity else 1
        
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
    
    def check_availability_change(self, subscription, trace_id=None):
        """
        检查单个订阅的可用性变化（配置级别监控）
        
        Args:
            subscription: 订阅配置
        """
        plan_code = subscription["planCode"]
        trace_id = trace_id or self._get_trace_id()
        
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
                    self._check_and_notify_change(subscription, plan_code, dc, status, old_status, None, dc, trace_id=trace_id)
                
                # 如果是配置级别的数据（新版配置监控）
                elif isinstance(config_data, dict) and "datacenters" in config_data:
                    memory = config_data.get("memory", "N/A")
                    storage = config_data.get("storage", "N/A")
                    config_display = f"{memory} + {storage}"
                    
                    # ✅ 为每个配置生成子 UUID，确保配置和价格匹配
                    config_trace_id = str(uuid.uuid4())
                    self.add_log("INFO", f"检查配置: {config_display} [config-trace:{config_trace_id}]", "monitor")
                    
                    # 准备配置信息
                    config_info = {
                        "memory": memory,
                        "storage": storage,
                        "display": config_display,
                        "options": config_data.get("options", [])  # 包含API2格式的选项代码
                    }
                    
                    # ✅ 并发价格校验：先收集所有需要价格校验的数据中心，然后并发执行
                    # 使用配置级别的子 UUID 进行追踪
                    dc_status_map = {}  # dc -> (status, status_key, old_status)
                    price_check_tasks = []  # 需要价格校验的任务列表
                    
                    for dc, status in config_data["datacenters"].items():
                        # 如果指定了数据中心列表，只监控列表中的
                        if monitored_dcs and dc not in monitored_dcs:
                            continue
                        
                        # 使用配置作为key来追踪状态
                        status_key = f"{dc}|{config_key}"
                        old_status = last_status.get(status_key)
                        dc_status_map[dc] = (status, status_key, old_status)
                        
                        # 如果可用性显示有货，需要价格校验
                        if status != "unavailable":
                            price_check_tasks.append(dc)
                    
                    # ✅ 并发执行价格校验（使用配置级别的子 UUID）
                    price_check_results = {}  # dc -> (price_available, price_check_error)
                    if price_check_tasks:
                        # 使用配置级别的子 UUID 进行价格校验追踪
                        self._set_trace_id(config_trace_id)
                        
                        try:
                            with ThreadPoolExecutor(max_workers=min(10, len(price_check_tasks))) as executor:
                                future_to_dc = {
                                    executor.submit(self._verify_price_available, plan_code, dc, config_info): dc
                                    for dc in price_check_tasks
                                }
                                
                                for future in as_completed(future_to_dc):
                                    dc = future_to_dc[future]
                                    try:
                                        price_available, price_check_error = future.result()
                                        price_check_results[dc] = (price_available, price_check_error)
                                        config_desc = f" [{config_display}]" if config_display else ""
                                        if price_available:
                                            self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 价格校验通过 [config-trace:{config_trace_id}]", "monitor")
                                        else:
                                            error_msg = f"，原因: {price_check_error}" if price_check_error else ""
                                            self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 价格校验失败{error_msg} [config-trace:{config_trace_id}]", "monitor")
                                    except Exception as e:
                                        # 价格校验异常，视为失败
                                        price_check_results[dc] = (False, f"价格校验异常: {str(e)}")
                                        self.add_log("WARNING", f"{plan_code}@{dc} 价格校验异常: {str(e)} [config-trace:{config_trace_id}]", "monitor")
                        finally:
                            self._clear_trace_id()
                    
                    # ✅ 根据价格校验结果确定实际状态
                    notifications_to_send = []
                    for dc, (status, status_key, old_status) in dc_status_map.items():
                        # 确定实际状态
                        actual_status = status
                        price_check_failed = False
                        price_check_error = None
                        
                        if status != "unavailable":
                            # 可用性显示有货，需要根据价格校验结果确定实际状态
                            if dc in price_check_results:
                                price_available, price_check_error = price_check_results[dc]
                                if not price_available:
                                    actual_status = "price_check_failed"
                                    price_check_failed = True
                                    config_desc = f" [{config_display}]" if config_display else ""
                                    error_msg = f"，原因: {price_check_error}" if price_check_error else ""
                                    self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 可用性显示有货但价格校验失败{error_msg}，标记为price_check_failed（将触发通知但不自动下单）", "monitor")
                                else:
                                    actual_status = "available"
                                    config_desc = f" [{config_display}]" if config_display else ""
                                    self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 可用性有货且价格校验通过，确认有货", "monitor")
                            else:
                                # 理论上不应该到这里，但为了安全起见
                                actual_status = "price_check_failed"
                                price_check_failed = True
                                price_check_error = "价格校验未执行"
                        
                        # 检查是否需要发送通知（包括首次检查）
                        status_changed = False
                        change_type = None
                        
                        # 首次检查时也发送通知（如果配置允许）
                        if old_status is None:
                            config_desc = f" [{config_display}]" if config_display else ""
                            if actual_status == "price_check_failed":
                                # 价格校验失败，触发通知（说明可用性有货但价格校验失败）
                                self.add_log("INFO", f"首次检查: {plan_code}@{dc}{config_desc} 可用性有货但价格校验失败，发送通知", "monitor")
                                if subscription.get("notifyAvailable", True):
                                    status_changed = True
                                    change_type = "price_check_failed"  # 特殊类型：价格校验失败
                            elif actual_status == "unavailable":
                                # 普通无货情况
                                self.add_log("INFO", f"首次检查: {plan_code}@{dc}{config_desc} 无货", "monitor")
                                # 首次检查无货时不通知（除非用户明确要求）
                                if subscription.get("notifyUnavailable", False):
                                    status_changed = True
                                    change_type = "unavailable"
                            else:
                                # 首次检查有货时发送通知
                                self.add_log("INFO", f"首次检查: {plan_code}@{dc}{config_desc} 有货（价格校验通过），发送通知", "monitor")
                                if subscription.get("notifyAvailable", True):
                                    status_changed = True
                                    change_type = "available"
                        # 从无货变有货（价格校验通过）
                        elif old_status == "unavailable" and actual_status == "available":
                            if subscription.get("notifyAvailable", True):
                                status_changed = True
                                change_type = "available"
                                config_desc = f" [{config_display}]" if config_display else ""
                                self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从无货变有货（价格校验通过）", "monitor")
                                # ✅ 补货历时时间将在后续统一计算并添加到通知中
                        # 从无货变价格校验失败（可用性有货但价格校验失败）
                        elif old_status == "unavailable" and actual_status == "price_check_failed":
                            self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从无货变可用性有货但价格校验失败，发送通知", "monitor")
                            if subscription.get("notifyAvailable", True):
                                status_changed = True
                                change_type = "price_check_failed"  # 特殊类型：价格校验失败
                        # 从价格校验失败变有货（价格校验通过）
                        elif old_status == "price_check_failed" and actual_status == "available":
                            if subscription.get("notifyAvailable", True):
                                status_changed = True
                                change_type = "available"
                                config_desc = f" [{config_display}]" if config_display else ""
                                self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从价格校验失败变有货（价格校验通过）", "monitor")
                        # 从价格校验失败变无货（真正的无货）
                        elif old_status == "price_check_failed" and actual_status == "unavailable":
                            if subscription.get("notifyUnavailable", False):
                                status_changed = True
                                change_type = "unavailable"
                                config_desc = f" [{config_display}]" if config_display else ""
                                self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从价格校验失败变无货", "monitor")
                        # 从有货变无货或价格校验失败
                        elif old_status == "available" and actual_status in ["unavailable", "price_check_failed"]:
                            if actual_status == "unavailable":
                                if subscription.get("notifyUnavailable", False):
                                    status_changed = True
                                    change_type = "unavailable"
                                    config_desc = f" [{config_display}]" if config_display else ""
                                    self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从有货变无货", "monitor")
                            else:  # price_check_failed
                                # 从有货变价格校验失败（可用性有货但价格校验失败）
                                self.add_log("INFO", f"{plan_code}@{dc}{config_desc} 从有货变可用性有货但价格校验失败，发送通知", "monitor")
                                if subscription.get("notifyAvailable", True):
                                    status_changed = True
                                    change_type = "price_check_failed"
                        
                        if status_changed:
                            # ✅ 记录检测到有货的时间（用于计算延迟）
                            detected_time = self._now_beijing()
                            notification_item = {
                                "dc": dc,
                                "status": actual_status,  # 使用实际状态（经过价格校验）
                                "old_status": old_status,
                                "status_key": status_key,
                                "change_type": change_type,
                                "price_check_failed": price_check_failed,  # 标记价格校验失败
                                "price_check_error": price_check_error,  # 价格校验失败原因
                                "config_trace_id": config_trace_id,  # 配置级别的子 UUID
                                "trace_id": trace_id,  # 订阅级别的主 UUID
                                "detected_time": detected_time.isoformat()  # 检测到有货的时间
                            }
                            # ✅ 如果是"从无货变有货"，添加补货历时时间
                            if change_type == "available" and old_status == "unavailable":
                                # 计算补货历时时间（从上次无货到本次有货的时间）
                                duration_text = None
                                try:
                                    # 查找最近一次无货的时间
                                    last_unavailable_ts = None
                                    same_config_display = config_info.get("display") if config_info else None
                                    for entry in reversed(subscription.get("history", [])):
                                        if entry.get("datacenter") != dc:
                                            continue
                                        if entry.get("changeType") not in ["unavailable", "price_check_failed"]:
                                            continue
                                        if same_config_display:
                                            cfg = entry.get("config", {})
                                            if cfg.get("display") != same_config_display:
                                                continue
                                        last_unavailable_ts = entry.get("timestamp")
                                        if last_unavailable_ts:
                                            break
                                    
                                    if last_unavailable_ts:
                                        # 解析ISO时间，按北京时间计算时长
                                        from datetime import datetime as _dt
                                        try:
                                            start_dt = _dt.fromisoformat(last_unavailable_ts.replace("Z", "+00:00"))
                                        except Exception:
                                            start_dt = _dt.fromisoformat(last_unavailable_ts)
                                        if start_dt.tzinfo is None:
                                            try:
                                                from zoneinfo import ZoneInfo
                                                start_dt = start_dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                                            except Exception:
                                                pass
                                        delta = self._now_beijing() - start_dt
                                        total_sec = int(delta.total_seconds())
                                        if total_sec < 0:
                                            total_sec = 0
                                        days = total_sec // 86400
                                        rem = total_sec % 86400
                                        hours = rem // 3600
                                        minutes = (rem % 3600) // 60
                                        seconds = rem % 60
                                        if days > 0:
                                            duration_text = f"历时 {days}天{hours}小时{minutes}分{seconds}秒"
                                        elif hours > 0:
                                            duration_text = f"历时 {hours}小时{minutes}分{seconds}秒"
                                        elif minutes > 0:
                                            duration_text = f"历时 {minutes}分{seconds}秒"
                                        else:
                                            duration_text = f"历时 {seconds}秒"
                                except Exception as e:
                                    self.add_log("DEBUG", f"计算补货历时异常: {str(e)}", "monitor")
                                    duration_text = None
                                
                                if duration_text:
                                    notification_item["duration_text"] = duration_text
                            
                            notifications_to_send.append(notification_item)
                        
                        # 更新状态记录（使用实际状态，包括特殊状态值 "price_check_failed"）
                        last_status[status_key] = actual_status
                    
                    # 对于同一个配置，只查询一次价格（使用第一个有货的数据中心）
                    price_text = None
                    price_fetch_error = None
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
                                    local_trace_id = trace_id or self._get_trace_id()
                                    if local_trace_id:
                                        self._set_trace_id(local_trace_id)
                                    try:
                                        price_result = self._get_price_info(plan_code, first_available_dc, config_info)
                                        price_queue.put(price_result)
                                    except Exception as e:
                                        self.add_log("WARNING", f"价格获取线程异常: {str(e)}", "monitor")
                                        price_queue.put(None)
                                    finally:
                                        if local_trace_id:
                                            self._clear_trace_id()
                                
                                # 启动价格获取线程
                                price_thread = threading.Thread(
                                    target=fetch_price, 
                                    daemon=True,
                                    name=f"PriceFetch-{plan_code}-{first_available_dc}"
                                )
                                price_thread.start()
                                start_time = time.time()
                                price_thread.join(timeout=30.0)  # 最多等待30秒
                                elapsed_time = time.time() - start_time
                                
                                if price_thread.is_alive():
                                    # ✅ 线程超时，记录详细信息（daemon线程会在主程序退出时自动结束）
                                    price_fetch_error = f"价格接口超时（等待{elapsed_time:.1f}秒）"
                                    self.add_log("WARNING", 
                                        f"价格获取超时（已等待{elapsed_time:.1f}秒，线程ID: {price_thread.ident}），"
                                        f"发送不带价格的通知。daemon线程将在后台继续运行直到完成。", 
                                        "monitor")
                                    price_text = None
                                else:
                                    # 线程已完成，尝试获取结果
                                    try:
                                        price_text = price_queue.get_nowait()
                                    except queue.Empty:
                                        price_fetch_error = f"价格接口结束但无返回数据（耗时{elapsed_time:.1f}秒）"
                                        price_text = None
                                        self.add_log("WARNING", 
                                            f"价格获取线程已完成但队列为空（耗时{elapsed_time:.1f}秒）", 
                                            "monitor")
                                
                                if price_text:
                                    self.add_log("DEBUG", 
                                        f"配置 {config_display} 价格获取成功（耗时{elapsed_time:.1f}秒）: {price_text}，将在所有通知中复用", 
                                        "monitor")
                                else:
                                    self.add_log("WARNING", 
                                        f"配置 {config_display} 价格获取失败（耗时{elapsed_time:.1f}秒），通知中不包含价格信息", 
                                        "monitor")
                                    if not price_fetch_error:
                                        price_fetch_error = "价格接口未返回结果"
                            except Exception as e:
                                # ✅ 统一错误处理：记录详细异常信息
                                price_fetch_error = f"价格接口异常: {str(e)}"
                                self.add_log("WARNING", f"价格获取过程异常: {str(e)}", "monitor")
                                self.add_log("DEBUG", f"价格获取异常详情: {traceback.format_exc()}", "monitor")
                    
                    # 按change_type分组发送通知（汇总同一配置的所有有货机房）
                    available_notifications = [n for n in notifications_to_send if n["change_type"] == "available"]
                    unavailable_notifications = [n for n in notifications_to_send if n["change_type"] == "unavailable"]
                    price_check_failed_notifications = [n for n in notifications_to_send if n["change_type"] == "price_check_failed"]
                    
                    # 过滤出需要下单的通知：只有从无货变有货或首次检查有货的情况才下单
                    # ✅ 关键：排除价格校验失败的情况，只对价格校验通过的情况下单
                    # 持续有货的情况不重复下单
                    order_notifications = [
                        n for n in available_notifications 
                        if n.get("old_status") in [None, "unavailable"]  # 首次检查有货 或 从无货变有货
                        and not n.get("price_check_failed", False)  # 排除价格校验失败的情况
                    ]
                    
                    # 在发送有货通知之前，优先尝试下单（仅当订阅开启 autoOrder）
                    # 只对从无货变有货的情况下单，持续有货不重复下单
                    if order_notifications and subscription.get("autoOrder"):
                        try:
                            import requests
                            from api_key_config import API_SECRET_KEY
                            quantity = subscription.get("quantity", 1)  # 获取下单数量，默认为1
                            
                            # 计算总订单数：配置数量（1个配置）× 数据中心数量 × 数量
                            total_orders = len(order_notifications) * quantity
                            self.add_log("INFO", f"[monitor->order] 开始批量下单: {plan_code}, 配置数=1, 数据中心数={len(order_notifications)}, 数量={quantity}, 总订单数={total_orders}", "monitor")
                            self.add_log("INFO", f"[monitor->order] 下单条件：仅对从无货变有货的情况下单（过滤掉持续有货的情况）", "monitor")
                            
                            success_count = 0
                            fail_count = 0
                            
                            for notif in order_notifications:
                                dc_to_order = notif["dc"]
                                # 使用配置级 options（若存在），否则留空让后端自动匹配
                                order_options = (config_info.get("options") if config_info else []) or []
                                
                                # 为每个数据中心创建 quantity 个订单
                                for i in range(quantity):
                                    payload = {
                                        "planCode": plan_code,
                                        "datacenter": dc_to_order,
                                        "options": order_options,
                                        "fromMonitor": True,  # 标记来自监控，绕过2分钟限制
                                        "skipDuplicateCheck": True  # 跳过重复检查，允许批量下单
                                    }
                                    headers = {"X-API-Key": API_SECRET_KEY}
                                    api_url = "http://127.0.0.1:19998/api/config-sniper/quick-order"
                                    
                                    order_num = i + 1
                                    self.add_log("INFO", f"[monitor->order] 尝试快速下单 ({order_num}/{quantity}): {plan_code}@{dc_to_order}, options={order_options}", "monitor")
                                    try:
                                        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
                                        if resp.status_code == 200:
                                            success_count += 1
                                            self.add_log("INFO", f"[monitor->order] 快速下单成功 ({order_num}/{quantity}): {plan_code}@{dc_to_order}", "monitor")
                                        else:
                                            fail_count += 1
                                            self.add_log("WARNING", f"[monitor->order] 快速下单失败 ({order_num}/{quantity}, {resp.status_code}): {resp.text}", "monitor")
                                    except requests.exceptions.RequestException as e:
                                        fail_count += 1
                                        self.add_log("WARNING", f"[monitor->order] 快速下单请求异常 ({order_num}/{quantity}): {str(e)}", "monitor")
                            
                            self.add_log("INFO", f"[monitor->order] 批量下单完成: 成功={success_count}, 失败={fail_count}, 总计={total_orders}", "monitor")
                        except Exception as e:
                            # ✅ 统一错误处理：记录详细异常信息
                            self.add_log("WARNING", f"[monitor->order] 下单前置流程异常: {str(e)}", "monitor")
                            self.add_log("DEBUG", f"[monitor->order] 下单异常详情: {traceback.format_exc()}", "monitor")
                    
                    # 发送有货通知（汇总所有有货的机房到一个通知，带按钮）
                    if available_notifications:
                        config_desc = f" [{config_info['display']}]" if config_info else ""
                        self.add_log("INFO", f"准备发送汇总提醒: {plan_code}{config_desc} - {len(available_notifications)}个机房有货", "monitor")
                        server_name = subscription.get("serverName")
                        
                        # 创建包含价格的配置信息副本
                        config_info_with_price = config_info.copy() if config_info else None
                        if config_info_with_price:
                            config_info_with_price["cached_price"] = price_text  # 传递查询到的价格
                        
                        # 汇总所有有货的机房数据（包含补货历时时间和检测时间）
                        available_dcs = []
                        for n in available_notifications:
                            dc_info = {"dc": n["dc"], "status": n["status"]}
                            if "duration_text" in n:
                                dc_info["duration_text"] = n["duration_text"]
                            if "detected_time" in n:
                                dc_info["detected_time"] = n["detected_time"]
                            available_dcs.append(dc_info)
                        # 从第一个通知项中获取 config_trace_id（所有通知项共享同一个配置）
                        config_trace_id_for_notif = available_notifications[0].get("config_trace_id") if available_notifications else None
                        self.send_availability_alert_grouped(
                            plan_code, available_dcs, config_info_with_price, server_name,
                            price_error_message=price_fetch_error if not price_text else None,
                            trace_id=trace_id,
                            config_trace_id=config_trace_id_for_notif
                        )
                        
                        # 添加到历史记录
                        if "history" not in subscription:
                            subscription["history"] = []
                        
                        for notif in available_notifications:
                            history_entry = {
                                "timestamp": self._now_beijing().isoformat(),
                                "datacenter": notif["dc"],
                                "status": notif["status"],
                                "changeType": notif["change_type"],
                                "oldStatus": notif["old_status"]
                            }
                            
                            if config_info:
                                history_entry["config"] = config_info
                            
                            subscription["history"].append(history_entry)
                    
                    # 发送价格校验失败通知（可用性有货但价格校验失败，触发通知但不自动下单）
                    for notif in price_check_failed_notifications:
                        config_desc = f" [{config_info['display']}]" if config_info else ""
                        self.add_log("INFO", f"准备发送价格校验失败提醒: {plan_code}@{notif['dc']}{config_desc} - 可用性有货但价格校验失败", "monitor")
                        server_name = subscription.get("serverName")
                        
                        # 尝试获取价格信息（即使价格校验失败，也可能有价格数据）
                        price_text_failed = None
                        try:
                            price_text_failed = self._get_price_info(plan_code, notif["dc"], config_info)
                        except Exception as e:
                            self.add_log("DEBUG", f"价格校验失败通知中尝试获取价格信息失败: {str(e)}", "monitor")
                        
                        # 创建包含价格信息的配置信息副本
                        config_info_with_price_failed = config_info.copy() if config_info else None
                        if config_info_with_price_failed and price_text_failed:
                            config_info_with_price_failed["cached_price"] = price_text_failed
                            config_info_with_price_failed["price_check_error"] = notif.get("price_check_error")
                        
                        # 发送特殊通知，说明可用性有货但价格校验失败
                        self.send_availability_alert(
                            plan_code, 
                            notif["dc"], 
                            "unavailable",  # 状态标记为无货（因为实际不可下单）
                            "price_check_failed",  # 特殊类型：价格校验失败
                            config_info_with_price_failed if config_info_with_price_failed else config_info, 
                            server_name,
                            duration_text=None,
                            price_check_error=notif.get("price_check_error"),
                            trace_id=trace_id,
                            config_trace_id=notif.get("config_trace_id"),
                            detected_time=notif.get("detected_time")
                        )
                        
                        # 添加到历史记录
                        if "history" not in subscription:
                            subscription["history"] = []
                        
                        history_entry = {
                            "timestamp": self._now_beijing().isoformat(),
                            "datacenter": notif["dc"],
                            "status": "price_check_failed",  # 使用特殊状态值
                            "changeType": "price_check_failed",
                            "oldStatus": notif["old_status"]
                        }
                        
                        if config_info:
                            history_entry["config"] = config_info
                        
                        subscription["history"].append(history_entry)
                    
                    # ✅ 发送无货通知（聚合发送，所有机房汇总到一个通知）
                    if unavailable_notifications:
                        config_desc = f" [{config_info['display']}]" if config_info else ""
                        self.add_log("INFO", f"准备发送聚合下架提醒: {plan_code}{config_desc} - {len(unavailable_notifications)}个机房", "monitor")
                        server_name = subscription.get("serverName")
                        
                        # 先计算所有下架机房的持续时长
                        unavailable_dcs = []
                        for notif in unavailable_notifications:
                            # 计算从有货到无货的持续时长（仅在确实是从有货变无货时计算）
                            duration_text = None
                            # 只有当前状态是无货，且旧状态不是无货或None时，才是"从有货变无货"
                            is_became_unavailable = (notif["change_type"] == "unavailable" and 
                                                      notif.get("old_status") not in ["unavailable", None])
                            if is_became_unavailable:
                                try:
                                    last_available_ts = None
                                    same_config_display = config_info.get("display") if config_info else None
                                    history_list = subscription.get("history", [])
                                    self.add_log("INFO", f"[历时计算] {plan_code}@{notif['dc']} 从有货变无货，old_status={notif.get('old_status')}, 历史记录数: {len(history_list)}, 配置: {same_config_display}", "monitor")
                                    # 如果历史记录为空，尝试从同一轮检查的有货通知中获取时间戳
                                    # 注意：有货通知的历史记录已经在上面添加到 subscription["history"] 中
                                    # 从后向前查找最近一次相同机房（且相同配置显示文本时更精确）的 available 记录
                                    for entry in reversed(history_list):
                                        if entry.get("datacenter") != notif["dc"]:
                                            continue
                                        if entry.get("changeType") != "available":
                                            continue
                                        if same_config_display:
                                            cfg = entry.get("config", {})
                                            if cfg and cfg.get("display") != same_config_display:
                                                continue
                                        last_available_ts = entry.get("timestamp")
                                        if last_available_ts:
                                            self.add_log("INFO", f"[历时计算] 找到有货记录: {plan_code}@{notif['dc']}, 时间: {last_available_ts}", "monitor")
                                            break
                                    if last_available_ts:
                                        try:
                                            # 解析ISO时间，按北京时间计算时长（兼容无时区与带时区）
                                            from datetime import datetime as _dt
                                            try:
                                                # 优先解析为带时区
                                                start_dt = _dt.fromisoformat(last_available_ts.replace("Z", "+00:00"))
                                            except Exception:
                                                start_dt = _dt.fromisoformat(last_available_ts)
                                            # 若解析为naive时间，视为北京时间
                                            if start_dt.tzinfo is None:
                                                try:
                                                    from zoneinfo import ZoneInfo
                                                    start_dt = start_dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                                                except Exception:
                                                    # 退化：将其视为UTC+8
                                                    start_dt = start_dt
                                            delta = self._now_beijing() - start_dt
                                            total_sec = int(delta.total_seconds())
                                            if total_sec < 0:
                                                total_sec = 0
                                            days = total_sec // 86400
                                            rem = total_sec % 86400
                                            hours = rem // 3600
                                            minutes = (rem % 3600) // 60
                                            seconds = rem % 60
                                            if days > 0:
                                                duration_text = f"历时 {days}天{hours}小时{minutes}分{seconds}秒"
                                            elif hours > 0:
                                                duration_text = f"历时 {hours}小时{minutes}分{seconds}秒"
                                            elif minutes > 0:
                                                duration_text = f"历时 {minutes}分{seconds}秒"
                                            else:
                                                duration_text = f"历时 {seconds}秒"
                                            self.add_log("INFO", f"[历时计算] 计算成功: {plan_code}@{notif['dc']}, {duration_text}", "monitor")
                                        except Exception as e:
                                            self.add_log("WARNING", f"[历时计算] 计算异常: {plan_code}@{notif['dc']}, 错误: {str(e)}", "monitor")
                                            duration_text = None
                                    else:
                                        self.add_log("INFO", f"[历时计算] 未找到有货记录: {plan_code}@{notif['dc']}, 无法计算历时", "monitor")
                                except Exception as e:
                                    self.add_log("WARNING", f"[历时计算] 查找异常: {plan_code}@{notif['dc']}, 错误: {str(e)}", "monitor")
                                    duration_text = None
                            
                            # 添加到下架机房列表
                            dc_info = {"dc": notif["dc"], "status": notif["status"]}
                            if duration_text:
                                dc_info["duration_text"] = duration_text
                            unavailable_dcs.append(dc_info)
                        
                        # 从第一个通知项中获取 config_trace_id（所有通知项共享同一个配置）
                        config_trace_id_for_notif = unavailable_notifications[0].get("config_trace_id") if unavailable_notifications else None
                        # 聚合发送下架通知
                        self.send_unavailable_alert_grouped(
                            plan_code, unavailable_dcs, config_info, server_name,
                            trace_id=trace_id,
                            config_trace_id=config_trace_id_for_notif
                        )
                        
                        # 添加到历史记录
                        if "history" not in subscription:
                            subscription["history"] = []
                        
                        for notif in unavailable_notifications:
                            history_entry = {
                                "timestamp": self._now_beijing().isoformat(),
                                "datacenter": notif["dc"],
                                "status": notif["status"],
                                "changeType": notif["change_type"],
                                "oldStatus": notif["old_status"]
                            }
                            
                            if config_info:
                                history_entry["config"] = config_info
                            
                            subscription["history"].append(history_entry)
                    
                    # ✅ 使用统一方法限制历史记录数量（在循环外统一限制，避免重复检查）
                    self._limit_history_size(subscription)

            # ✅ 更新状态：保留循环中更新的actual_status，只为新配置初始化状态
            # 注意：last_status 在循环中已经正确更新为经过价格校验的 actual_status
            # 这里只需要为新出现的配置（循环中没有遇到的）初始化状态
            for config_key, config_data in current_availability.items():
                if isinstance(config_data, str):
                    # 简单的数据中心状态（旧版兼容）
                    if config_key not in last_status:
                        last_status[config_key] = config_data
                elif isinstance(config_data, dict) and "datacenters" in config_data:
                    # 配置级别的状态
                    for dc, status in config_data["datacenters"].items():
                        status_key = f"{dc}|{config_key}"
                        # 只有当last_status中没有这个key时才初始化（新配置）
                        # 如果已经有了（在循环中更新过），就不要覆盖，保留price_check_failed等状态
                        if status_key not in last_status:
                            last_status[status_key] = status

            # ✅ 使用循环中更新的last_status，保留了经过价格校验的actual_status
            subscription["lastStatus"] = last_status
            
        except Exception as e:
            self.add_log("ERROR", f"检查 {plan_code} 可用性时出错: {str(e)}", "monitor")
            self.add_log("ERROR", f"错误详情: {traceback.format_exc()}", "monitor")
    
    def _check_and_notify_change(self, subscription, plan_code, dc, status, old_status, config_info=None, status_key=None, trace_id=None):
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

            # 如果是“有货 -> 无货”，计算本次有货持续时长
            duration_text = None
            if change_type == "unavailable":
                try:
                    last_available_ts = None
                    same_config_display = config_info.get("display") if config_info else None
                    # 从后向前查找最近一次相同机房（且相同配置显示文本时更精确）的 available 记录
                    for entry in reversed(subscription.get("history", [])):
                        if entry.get("datacenter") != dc:
                            continue
                        if entry.get("changeType") != "available":
                            continue
                        if same_config_display:
                            cfg = entry.get("config", {})
                            if cfg.get("display") != same_config_display:
                                continue
                        last_available_ts = entry.get("timestamp")
                        if last_available_ts:
                            break
                    if last_available_ts:
                        try:
                            # 解析ISO时间，按北京时间计算时长（兼容无时区与带时区）
                            from datetime import datetime as _dt
                            try:
                                # 优先解析为带时区
                                start_dt = _dt.fromisoformat(last_available_ts.replace("Z", "+00:00"))
                            except Exception:
                                start_dt = _dt.fromisoformat(last_available_ts)
                            # 若解析为naive时间，视为北京时间
                            if start_dt.tzinfo is None:
                                try:
                                    from zoneinfo import ZoneInfo
                                    start_dt = start_dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                                except Exception:
                                    # 退化：将其视为UTC+8
                                    start_dt = start_dt
                            delta = self._now_beijing() - start_dt
                            total_sec = int(delta.total_seconds())
                            if total_sec < 0:
                                total_sec = 0
                            days = total_sec // 86400
                            rem = total_sec % 86400
                            hours = rem // 3600
                            minutes = (rem % 3600) // 60
                            seconds = rem % 60
                            if days > 0:
                                duration_text = f"历时 {days}天{hours}小时{minutes}分{seconds}秒"
                            elif hours > 0:
                                duration_text = f"历时 {hours}小时{minutes}分{seconds}秒"
                            elif minutes > 0:
                                duration_text = f"历时 {minutes}分{seconds}秒"
                            else:
                                duration_text = f"历时 {seconds}秒"
                        except Exception as e:
                            # ✅ 统一错误处理：记录异常但不中断流程
                            self.add_log("DEBUG", f"计算历时异常: {str(e)}", "monitor")
                            duration_text = None
                except Exception as e:
                    # ✅ 统一错误处理：记录异常但不中断流程
                    self.add_log("DEBUG", f"查找有货记录异常: {str(e)}", "monitor")
                    duration_text = None

            self.send_availability_alert(
                plan_code,
                dc,
                status,
                change_type,
                config_info,
                server_name,
                duration_text=duration_text,
                trace_id=trace_id,
                detected_time=None  # 旧版兼容代码，不记录检测时间
            )
            
            # 添加到历史记录
            if "history" not in subscription:
                subscription["history"] = []
            
            history_entry = {
                "timestamp": self._now_beijing().isoformat(),
                "datacenter": dc,
                "status": status,
                "changeType": change_type,
                "oldStatus": old_status
            }
            
            # 添加配置信息到历史记录
            if config_info:
                history_entry["config"] = config_info
            
            subscription["history"].append(history_entry)
            
            # ✅ 使用统一方法限制历史记录数量，保留最近100条
            self._limit_history_size(subscription)
    
    def send_availability_alert_grouped(self, plan_code, available_dcs, config_info=None, server_name=None, price_error_message=None, trace_id=None, config_trace_id=None):
        """
        发送汇总的可用性提醒（一个通知包含多个有货的机房，带内联键盘按钮）
        
        Args:
            plan_code: 服务器型号
            available_dcs: 有货的数据中心列表 [{"dc": "gra", "status": "available"}, ...]
            config_info: 配置信息 {"memory": "xxx", "storage": "xxx", "display": "xxx", "options": [...]}
            server_name: 服务器友好名称
            trace_id: 订阅级别的主 UUID
            config_trace_id: 配置级别的子 UUID
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
            elif price_error_message:
                message += f"\n⚠️ 价格提示：{price_error_message}\n"
            
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
                    "lim": "🇵🇱 波兰·利马诺瓦",
                    "vin": "🇺🇸 美国·弗吉尼亚",
                    "hil": "🇺🇸 美国·俄勒冈"
                }
                dc_display = dc_display_map.get(dc.lower(), dc.upper())
                message += f"  • {dc_display} ({dc.upper()})"
                # ✅ 如果有补货历时时间，添加到机房信息中
                if "duration_text" in dc_info and dc_info["duration_text"]:
                    duration_value = dc_info["duration_text"].replace("历时 ", "")
                    message += f" - ⏱️ 上次无货→本次有货: {duration_value}"
                message += "\n"
            
            # ✅ 计算检测时间和推送延迟
            push_time = self._now_beijing()
            detected_times = []
            for dc_info in available_dcs:
                if "detected_time" in dc_info:
                    try:
                        from datetime import datetime as _dt
                        detected_dt = _dt.fromisoformat(dc_info["detected_time"].replace("Z", "+00:00"))
                        if detected_dt.tzinfo is None:
                            try:
                                from zoneinfo import ZoneInfo
                                detected_dt = detected_dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                            except Exception:
                                pass
                        detected_times.append(detected_dt)
                    except Exception:
                        pass
            
            if trace_id or config_trace_id:
                if trace_id and config_trace_id:
                    # 两个 UUID 时，使用换行显示，更清晰
                    message += f"\n🆔 Trace ID:\n  订阅: {trace_id}\n  配置: {config_trace_id}"
                elif trace_id:
                    message += f"\n🆔 Trace ID: {trace_id}"
                elif config_trace_id:
                    message += f"\n🆔 Trace ID: {config_trace_id}"
            
            # ✅ 显示检测时间、推送时间和延迟
            if detected_times:
                earliest_detected = min(detected_times)
                delay = push_time - earliest_detected
                delay_seconds = int(delay.total_seconds())
                delay_minutes = delay_seconds // 60
                delay_secs = delay_seconds % 60
                
                message += f"\n⏰ 检测时间: {earliest_detected.strftime('%Y-%m-%d %H:%M:%S')}"
                message += f"\n📤 推送时间: {push_time.strftime('%Y-%m-%d %H:%M:%S')}"
                if delay_seconds > 0:
                    if delay_minutes > 0:
                        message += f"\n⏱️ 推送延迟: {delay_minutes}分{delay_secs}秒"
                    else:
                        message += f"\n⏱️ 推送延迟: {delay_secs}秒"
                else:
                    message += f"\n⏱️ 推送延迟: <1秒"
            else:
                message += f"\n⏰ 推送时间: {push_time.strftime('%Y-%m-%d %H:%M:%S')}"
            
            message += f"\n\n💡 点击下方按钮可直接下单对应机房！"
            
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
                    "lim": "🇵🇱 Lim",
                    "vin": "🇺🇸 Vin",
                    "hil": "🇺🇸 Hil"
                }
                # 生成按钮文本，包含机房信息和"一键下单"提示
                dc_display_short = dc_display_map.get(dc.lower(), dc.upper())
                button_text = f"{dc_display_short} 一键下单"
                
                # 提取配置信息
                options = config_info.get("options", []) if config_info else []
                
                # 为每个按钮生成UUID并存储完整配置信息（UUID机制）
                message_uuid = str(uuid.uuid4())
                # ✅ 使用锁保护缓存写入操作
                with self._cache_lock:
                    self.message_uuid_cache[message_uuid] = {
                        "planCode": plan_code,
                        "datacenter": dc,
                        "options": options,
                        "configInfo": config_info,  # 保存完整的config_info以便将来扩展
                        "timestamp": time.time()
                    }
                self.add_log("DEBUG", f"生成消息UUID: {message_uuid}, 配置: {plan_code}@{dc}, options={options}", "monitor")
                
                # callback_data 只包含UUID（使用短格式：u=uuid）
                # 格式：{"a":"add_to_queue","u":"uuid"}，JSON后约45-50字节，远小于64字节限制
                callback_data = {
                    "a": "add_to_queue",
                    "u": message_uuid  # u = uuid
                }
                callback_data_str = json.dumps(callback_data, ensure_ascii=False, separators=(',', ':'))
                
                # UUID机制下，callback_data通常只有40-50字节，远小于64字节限制
                if len(callback_data_str) > 64:
                    self.add_log("WARNING", f"UUID callback_data异常长: {len(callback_data_str)}字节, UUID={message_uuid}", "monitor")
                
                callback_data_final = callback_data_str[:64]  # 安全限制，但通常不会截断
                
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
    
    def send_unavailable_alert_grouped(self, plan_code, unavailable_dcs, config_info=None, server_name=None, trace_id=None, config_trace_id=None):
        """
        发送聚合的下架通知（一个通知包含多个无货的机房）
        
        Args:
            plan_code: 服务器型号
            unavailable_dcs: 无货的数据中心列表 [{"dc": "gra", "duration_text": "历时 xxx"}, ...]
            config_info: 配置信息 {"memory": "xxx", "storage": "xxx", "display": "xxx"}
            server_name: 服务器友好名称
            trace_id: 订阅级别的主 UUID
            config_trace_id: 配置级别的子 UUID
        """
        try:
            message = f"📦 服务器下架通知\n\n"
            
            if server_name:
                message += f"服务器: {server_name}\n"
            
            message += f"型号: {plan_code}\n"
            
            if config_info:
                message += (
                    f"配置: {config_info['display']}\n"
                    f"├─ 内存: {config_info['memory']}\n"
                    f"└─ 存储: {config_info['storage']}\n"
                )
            
            message += f"\n已下架机房 ({len(unavailable_dcs)} 个):\n"
            
            # 数据中心显示名称映射
            dc_display_map = {
                "gra": "🇫🇷 法国·格拉沃利讷",
                "rbx": "🇫🇷 法国·鲁贝",
                "sbg": "🇫🇷 法国·斯特拉斯堡",
                "bhs": "🇨🇦 加拿大·博瓦桑",
                "syd": "🇦🇺 澳大利亚·悉尼",
                "sgp": "🇸🇬 新加坡",
                "ynm": "🇮🇳 印度·孟买",
                "waw": "🇵🇱 波兰·华沙",
                "fra": "🇩🇪 德国·法兰克福",
                "lon": "🇬🇧 英国·伦敦",
                "par": "🇫🇷 法国·巴黎",
                "eri": "🇮🇹 意大利·埃里切",
                "lim": "🇵🇱 波兰·利马诺瓦",
                "vin": "🇺🇸 美国·弗吉尼亚",
                "hil": "🇺🇸 美国·俄勒冈"
            }
            
            for dc_info in unavailable_dcs:
                dc = dc_info.get("dc", "")
                dc_display = dc_display_map.get(dc.lower(), dc.upper())
                message += f"  • {dc_display} ({dc.upper()})"
                # ✅ 如果有上架持续时长，添加到机房信息中
                if "duration_text" in dc_info and dc_info["duration_text"]:
                    duration_value = dc_info["duration_text"].replace("历时 ", "")
                    message += f" - ⏱️ 本次上架持续: {duration_value}"
                message += "\n"
            
            if trace_id or config_trace_id:
                if trace_id and config_trace_id:
                    # 两个 UUID 时，使用换行显示，更清晰
                    message += f"\n🆔 Trace ID:\n  订阅: {trace_id}\n  配置: {config_trace_id}"
                elif trace_id:
                    message += f"\n🆔 Trace ID: {trace_id}"
                elif config_trace_id:
                    message += f"\n🆔 Trace ID: {config_trace_id}"
            message += f"\n⏰ 时间: {self._now_beijing().strftime('%Y-%m-%d %H:%M:%S')}"
            
            config_desc = f" [{config_info['display']}]" if config_info else ""
            self.add_log("INFO", f"正在发送聚合下架Telegram通知: {plan_code}{config_desc} - {len(unavailable_dcs)}个机房", "monitor")
            result = self.send_notification(message)
            
            if result:
                self.add_log("INFO", f"✅ Telegram聚合下架通知发送成功: {plan_code}{config_desc}", "monitor")
            else:
                self.add_log("WARNING", f"⚠️ Telegram聚合下架通知发送失败: {plan_code}{config_desc}", "monitor")
                
        except Exception as e:
            self.add_log("ERROR", f"发送聚合下架提醒时发生异常: {str(e)}", "monitor")
            import traceback
            self.add_log("ERROR", f"错误详情: {traceback.format_exc()}", "monitor")
    
    def send_availability_alert(self, plan_code, datacenter, status, change_type, config_info=None, server_name=None, duration_text=None, price_check_error=None, trace_id=None, config_trace_id=None, detected_time=None):
        """
        发送可用性变化提醒
        
        Args:
            plan_code: 服务器型号
            datacenter: 数据中心
            status: 状态
            change_type: 变化类型
            config_info: 配置信息 {"memory": "xxx", "storage": "xxx", "display": "xxx"}
            server_name: 服务器友好名称（如"KS-2 | Intel Xeon-D 1540"）
            trace_id: 订阅级别的主 UUID
            config_trace_id: 配置级别的子 UUID
            detected_time: 检测到有货的时间（ISO格式字符串）
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
                price_fetch_error = None
                
                # 如果config_info中包含已查询的价格，直接使用
                if config_info and "cached_price" in config_info:
                    price_text = config_info.get("cached_price")
                    if price_text:
                        self.add_log("DEBUG", f"使用已查询的价格: {price_text}", "monitor")
                
                # 如果没有缓存的价格，才去查询
                if not price_text:
                    try:
                        import threading
                        import queue
                        price_queue = queue.Queue()
                        
                        def fetch_price():
                            local_trace_id = trace_id or self._get_trace_id()
                            if local_trace_id:
                                self._set_trace_id(local_trace_id)
                            try:
                                price_result = self._get_price_info(plan_code, datacenter, config_info)
                                price_queue.put(price_result)
                            except Exception as e:
                                self.add_log("WARNING", f"价格获取线程异常: {str(e)}", "monitor")
                                price_queue.put(None)
                            finally:
                                if local_trace_id:
                                    self._clear_trace_id()
                        
                        # 启动价格获取线程
                        price_thread = threading.Thread(
                            target=fetch_price, 
                            daemon=True,
                            name=f"PriceFetch-{plan_code}-{datacenter}"
                        )
                        price_thread.start()
                        start_time = time.time()
                        price_thread.join(timeout=30.0)  # 最多等待30秒
                        elapsed_time = time.time() - start_time
                        
                        if price_thread.is_alive():
                            # ✅ 线程超时，记录详细信息（daemon线程会在主程序退出时自动结束）
                            price_fetch_error = f"价格接口超时（等待{elapsed_time:.1f}秒）"
                            self.add_log("WARNING", 
                                f"价格获取超时（已等待{elapsed_time:.1f}秒，线程ID: {price_thread.ident}），"
                                f"发送不带价格的通知。daemon线程将在后台继续运行直到完成。", 
                                "monitor")
                            price_text = None
                        else:
                            # 线程已完成，尝试获取结果
                            try:
                                price_text = price_queue.get_nowait()
                            except queue.Empty:
                                price_fetch_error = f"价格接口结束但无返回数据（耗时{elapsed_time:.1f}秒）"
                                price_text = None
                                self.add_log("WARNING", 
                                    f"价格获取线程已完成但队列为空（耗时{elapsed_time:.1f}秒）", 
                                    "monitor")
                        
                        if not price_text:
                            # 如果价格获取失败，记录警告但继续发送通知
                            if not price_fetch_error:
                                price_fetch_error = f"价格接口未返回结果（耗时{elapsed_time:.1f}秒）"
                            self.add_log("WARNING", 
                                f"价格获取失败或超时（耗时{elapsed_time:.1f}秒），通知中不包含价格信息", 
                                "monitor")
                    except Exception as e:
                        price_fetch_error = f"价格接口异常: {str(e)}"
                        self.add_log("WARNING", f"价格获取过程异常: {str(e)}，发送不带价格的通知", "monitor")
                        import traceback
                        self.add_log("WARNING", f"价格获取异常详情: {traceback.format_exc()}", "monitor")
                
                # 如果有价格信息，添加到消息中
                if price_text:
                    message += f"\n💰 价格: {price_text}\n"
                elif price_fetch_error:
                    message += f"\n⚠️ 价格提示：{price_fetch_error}\n"
                
                message += f"状态: {status}\n"
                
                # ✅ 如果有补货历时时间，添加到消息中
                if duration_text:
                    duration_value = duration_text.replace("历时 ", "")
                    message += f"⏱️ 上次无货→本次有货: {duration_value}\n"
                
                # ✅ 显示检测时间、推送时间和延迟
                push_time = self._now_beijing()
                if detected_time:
                    try:
                        from datetime import datetime as _dt
                        detected_dt = _dt.fromisoformat(detected_time.replace("Z", "+00:00"))
                        if detected_dt.tzinfo is None:
                            try:
                                from zoneinfo import ZoneInfo
                                detected_dt = detected_dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                            except Exception:
                                pass
                        delay = push_time - detected_dt
                        delay_seconds = int(delay.total_seconds())
                        delay_minutes = delay_seconds // 60
                        delay_secs = delay_seconds % 60
                        
                        message += f"⏰ 检测时间: {detected_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        message += f"📤 推送时间: {push_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        if delay_seconds > 0:
                            if delay_minutes > 0:
                                message += f"⏱️ 推送延迟: {delay_minutes}分{delay_secs}秒\n"
                            else:
                                message += f"⏱️ 推送延迟: {delay_secs}秒\n"
                        else:
                            message += f"⏱️ 推送延迟: <1秒\n"
                    except Exception:
                        message += f"⏰ 推送时间: {push_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                else:
                    message += f"⏰ 推送时间: {push_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                
                if trace_id or config_trace_id:
                    if trace_id and config_trace_id:
                        # 两个 UUID 时，使用换行显示，更清晰
                        message += f"\n🆔 Trace ID:\n  订阅: {trace_id}\n  配置: {config_trace_id}"
                    elif trace_id:
                        message += f"\n🆔 Trace ID: {trace_id}"
                    elif config_trace_id:
                        message += f"\n🆔 Trace ID: {config_trace_id}"
                
                message += f"\n\n💡 快去抢购吧！"
            elif change_type == "price_check_failed":
                # 价格校验失败通知：可用性有货但价格校验失败
                message = f"📦 服务器可用性通知\n\n"
                
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
                
                # 如果有价格信息，显示价格（即使价格校验失败）
                price_text = None
                if config_info and "cached_price" in config_info:
                    price_text = config_info.get("cached_price")
                    if price_text:
                        message += f"\n💰 价格: {price_text}\n"
                
                message += f"\n状态: 可用性显示有货\n"
                message += f"时间: {self._now_beijing().strftime('%Y-%m-%d %H:%M:%S')}\n"
                if trace_id or config_trace_id:
                    if trace_id and config_trace_id:
                        # 两个 UUID 时，使用换行显示，更清晰
                        message += f"🆔 Trace ID:\n  订阅: {trace_id}\n  配置: {config_trace_id}\n"
                    elif trace_id:
                        message += f"🆔 Trace ID: {trace_id}\n"
                    elif config_trace_id:
                        message += f"🆔 Trace ID: {config_trace_id}\n"
                message += "\n"
                message += f"⚠️ 特别说明：\n"
                if price_check_error:
                    message += f"（价格校验未通过: {price_check_error}，已跳过自动下单）"
                else:
                    message += f"（价格校验未通过，已跳过自动下单）"
            else:
                # 基础消息
                message = f"📦 服务器下架通知\n\n"
                
                # 添加服务器名称（如果有）
                if server_name:
                    message += f"服务器: {server_name}\n"
                
                message += f"型号: {plan_code}\n"
                
                # 添加配置信息（如果有），使用与上架通知相同的树状格式
                if config_info:
                    message += (
                        f"配置: {config_info['display']}\n"
                        f"├─ 内存: {config_info['memory']}\n"
                        f"└─ 存储: {config_info['storage']}\n"
                    )
                
                message += f"\n数据中心: {datacenter}\n"
                message += f"状态: 已无货\n"
                message += f"⏰ 时间: {self._now_beijing().strftime('%Y-%m-%d %H:%M:%S')}"
                if trace_id or config_trace_id:
                    if trace_id and config_trace_id:
                        # 两个 UUID 时，使用换行显示，更清晰
                        message += f"\n🆔 Trace ID:\n  订阅: {trace_id}\n  配置: {config_trace_id}"
                    elif trace_id:
                        message += f"\n🆔 Trace ID: {trace_id}"
                    elif config_trace_id:
                        message += f"\n🆔 Trace ID: {config_trace_id}"
                # 若可用，追加"从有货到无货历时多久"，格式与时间保持一致
                if duration_text:
                    duration_value = duration_text.replace("历时 ", "")
                    message += f"\n⏱️ 本次上架持续: {duration_value}"
            
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
    
    def _verify_price_available(self, plan_code, datacenter, config_info=None):
        """
        验证价格是否可用（可下单）
        只有价格校验通过才算真正有货
        
        Args:
            plan_code: 服务器型号
            datacenter: 数据中心
            config_info: 配置信息 {"memory": "xxx", "storage": "xxx", "display": "xxx", "options": [...]}
        
        Returns:
            tuple: (bool, str) - (True表示价格可用（可下单），False表示价格不可用, 失败原因)
        """
        try:
            # 提取配置选项
            options = []
            
            if config_info:
                # 如果config_info中已经有options字段（API2格式），直接使用
                if 'options' in config_info and config_info['options']:
                    options = config_info['options']
            
            # 使用HTTP请求调用内部价格API进行价格校验
            import requests
            
            # 调用内部API端点
            api_url = "http://127.0.0.1:19998/api/internal/monitor/price"
            payload = {
                "plan_code": plan_code,
                "datacenter": datacenter,
                "options": options
            }
            
            try:
                response = requests.post(api_url, json=payload, timeout=30)  # 价格校验超时设置为30秒
                response.raise_for_status()
                result = response.json()
            except requests.exceptions.Timeout:
                error_msg = "价格校验API请求超时（30秒）"
                self.add_log("DEBUG", f"价格校验API请求超时: {plan_code}@{datacenter}", "monitor")
                return False, error_msg
            except requests.exceptions.RequestException as e:
                error_msg = f"价格校验API请求失败: {str(e)}"
                self.add_log("DEBUG", f"价格校验API请求失败: {plan_code}@{datacenter} - {str(e)}", "monitor")
                return False, error_msg
            
            # 检查价格是否有效（与quick-order中的逻辑一致）
            if not result.get("success"):
                error_msg = result.get("error", "未知错误")
                self.add_log("DEBUG", f"价格校验失败: {plan_code}@{datacenter} - {error_msg}", "monitor")
                return False, error_msg
            
            if "price" not in result:
                error_msg = "price字段缺失"
                self.add_log("DEBUG", f"价格校验失败: {plan_code}@{datacenter} - price字段缺失", "monitor")
                return False, error_msg
            
            price_info = result.get("price")
            if not isinstance(price_info, dict):
                error_msg = "price字段类型错误"
                self.add_log("DEBUG", f"价格校验失败: {plan_code}@{datacenter} - price字段类型错误", "monitor")
                return False, error_msg
            
            prices = price_info.get("prices", {})
            if not isinstance(prices, dict):
                error_msg = "prices字段缺失或类型错误"
                self.add_log("DEBUG", f"价格校验失败: {plan_code}@{datacenter} - prices字段缺失或类型错误", "monitor")
                return False, error_msg
            
            with_tax = prices.get("withTax")
            if with_tax in [None, 0, 0.0]:
                error_msg = f"withTax无效({with_tax})"
                self.add_log("DEBUG", f"价格校验失败: {plan_code}@{datacenter} - withTax无效({with_tax})", "monitor")
                return False, error_msg
            
            # 价格校验通过
            self.add_log("DEBUG", f"价格校验通过: {plan_code}@{datacenter} - 含税价格: {with_tax}", "monitor")
            return True, None
                
        except Exception as e:
            error_msg = f"价格校验过程异常: {str(e)}"
            self.add_log("WARNING", f"价格校验过程异常: {plan_code}@{datacenter} - {str(e)}", "monitor")
            return False, error_msg
    
    def _get_price_info(self, plan_code, datacenter, config_info=None):
        """
        获取配置后的价格信息（实时查询）
        
        Args:
            plan_code: 服务器型号
            datacenter: 数据中心（用于查询）
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
            
            # 实时查询价格（不使用缓存）
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
                f"时间: {self._now_beijing().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"💡 快去查看详情！"
            )
            
            self.send_notification(message)
            self.add_log("INFO", f"发送新服务器提醒: {server.get('planCode')}", "monitor")
            
        except Exception as e:
            self.add_log("ERROR", f"发送新服务器提醒失败: {str(e)}", "monitor")
    
    def _cleanup_expired_caches(self):
        """清理过期的缓存项（UUID和options缓存）- 线程安全"""
        current_time = time.time()
        expired_uuids = []
        expired_options_keys = []
        
        # ✅ 使用锁保护缓存操作
        with self._cache_lock:
            # 清理过期的UUID缓存
            for uuid_key, cache_data in list(self.message_uuid_cache.items()):
                cache_timestamp = cache_data.get("timestamp", 0)
                if current_time - cache_timestamp >= self.message_uuid_cache_ttl:
                    expired_uuids.append(uuid_key)
            
            for uuid_key in expired_uuids:
                del self.message_uuid_cache[uuid_key]
            
            # 清理过期的options缓存
            for options_key, cache_data in list(self.options_cache.items()):
                cache_timestamp = cache_data.get("timestamp", 0)
                if current_time - cache_timestamp >= self.options_cache_ttl:
                    expired_options_keys.append(options_key)
            
            for options_key in expired_options_keys:
                del self.options_cache[options_key]
        
        if expired_uuids or expired_options_keys:
            self.add_log("DEBUG", f"清理过期缓存: UUID={len(expired_uuids)}个, Options={len(expired_options_keys)}个", "monitor")
    
    def _run_subscription_check(self, subscription, trace_id):
        """执行单个订阅的检查任务（带Trace ID上下文）"""
        self._set_trace_id(trace_id)
        plan_code = subscription.get("planCode")
        try:
            self.add_log("INFO", f"开始处理订阅: {plan_code}", "monitor")
            self.check_availability_change(subscription, trace_id=trace_id)
            self.add_log("INFO", f"完成处理订阅: {plan_code}", "monitor")
        finally:
            self._clear_trace_id()
    
    def monitor_loop(self):
        """监控主循环"""
        self.add_log("INFO", "监控循环已启动", "monitor")
        
        while self.running:
            try:
                # 定期清理过期缓存（每次循环清理一次）
                self._cleanup_expired_caches()
                
                # 检查订阅的服务器
                if self.subscriptions:
                    subscription_count = len(self.subscriptions)
                    self.add_log("INFO", f"开始检查 {subscription_count} 个订阅...", "monitor")
                    
                    # ✅ 创建副本避免在遍历时修改列表导致的竞态条件
                    subscriptions_copy = list(self.subscriptions)
                    max_workers = min(self.max_workers, subscription_count) or 1
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_map = {}
                        for subscription in subscriptions_copy:
                            if not self.running:
                                break
                            if subscription not in self.subscriptions:
                                self.add_log("DEBUG", f"订阅 {subscription.get('planCode')} 在检查期间被删除，跳过", "monitor")
                                continue
                            trace_id = str(uuid.uuid4())
                            future = executor.submit(self._run_subscription_check, subscription, trace_id)
                            future_map[future] = (subscription.get("planCode"), trace_id)
                        
                        for future, (plan_code, trace_id) in future_map.items():
                            try:
                                future.result()
                            except Exception as e:
                                self.add_log(
                                    "ERROR",
                                    f"[trace:{trace_id}] 并发检查订阅 {plan_code} 时异常: {str(e)}",
                                    "monitor"
                                )
                else:
                    self.add_log("INFO", "当前无订阅，跳过检查", "monitor")
                
                # 注意：新服务器检查需要在外部调用时传入服务器列表
                
            except Exception as e:
                # ✅ 统一错误处理：记录详细异常信息
                self.add_log("ERROR", f"监控循环出错: {str(e)}", "monitor")
                self.add_log("ERROR", f"错误详情: {traceback.format_exc()}", "monitor")
            
            # 等待下次检查（使用可中断的sleep）
            # 注意：每次循环都重新读取 check_interval，确保使用最新值
            if self.running:
                current_interval = self.check_interval  # 在循环开始前读取当前值
                self.add_log("INFO", f"等待 {current_interval} 秒后进行下次检查...", "monitor")
                # 分段sleep，每秒检查一次running状态，实现快速停止
                for _ in range(current_interval):
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
        """设置检查间隔（秒）- 已禁用，全局固定为5秒"""
        # 检查间隔全局固定为5秒，不允许修改
        self.check_interval = 5
        self.add_log("INFO", "检查间隔已全局固定为5秒，无法修改", "monitor")
        return True
