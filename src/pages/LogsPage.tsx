
import { useState, useEffect, useRef } from "react";
import { api } from "@/utils/apiClient";
import { toast } from "sonner";
import { useIsMobile } from "@/hooks/use-mobile";

interface LogEntry {
  id: string;
  timestamp: string;
  level: "INFO" | "WARNING" | "ERROR" | "DEBUG";
  message: string;
  source: string;
}

const LogsPage = () => {
  const isMobile = useIsMobile();
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [filterLevel, setFilterLevel] = useState<string>("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [filteredLogs, setFilteredLogs] = useState<LogEntry[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Fetch logs
  const fetchLogs = async () => {
    try {
      const response = await api.get(`/logs`);
      setLogs(response.data);
    } catch (error) {
      console.error("Error fetching logs:", error);
      if (!isLoading) {
        // Only show error toast if not initial loading
        toast.error("获取日志失败");
      }
    } finally {
      setIsLoading(false);
    }
  };

  // Clear logs
  const clearLogs = async () => {
    if (!confirm("确定要清空所有日志吗？此操作不可撤销。")) {
      return;
    }
    
    try {
      await api.delete(`/logs`);
      toast.success("已清空日志");
      fetchLogs();
    } catch (error) {
      console.error("Error clearing logs:", error);
      toast.error("清空日志失败");
    }
  };

  // Scroll to bottom of logs
  const scrollToBottom = () => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  // Initial fetch and auto-refresh setup
  useEffect(() => {
    fetchLogs();
    
    let interval: NodeJS.Timeout;
    if (autoRefresh) {
      interval = setInterval(fetchLogs, 5000);
    }
    
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [autoRefresh]);

  // Apply filters
  useEffect(() => {
    if (logs.length === 0) return;
    
    let filtered = [...logs];
    
    // Apply level filter
    if (filterLevel !== "all") {
      filtered = filtered.filter(log => log.level === filterLevel);
    }
    
    // Apply search filter
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      filtered = filtered.filter(
        log => 
          log.message.toLowerCase().includes(term) ||
          log.source.toLowerCase().includes(term)
      );
    }
    
    setFilteredLogs(filtered);
  }, [logs, filterLevel, searchTerm]);

  // Scroll to bottom when logs update
  useEffect(() => {
    if (autoRefresh && !searchTerm && filterLevel === "all") {
      scrollToBottom();
    }
  }, [filteredLogs, autoRefresh, searchTerm, filterLevel]);

  // Get background color based on log level
  const getLogLevelStyle = (level: string) => {
    switch (level) {
      case "ERROR":
        return "bg-red-500/20 text-red-400";
      case "WARNING":
        return "bg-yellow-500/20 text-yellow-400";
      case "INFO":
        return "bg-blue-500/20 text-blue-400";
      case "DEBUG":
        return "bg-purple-500/20 text-purple-400";
      default:
        return "bg-cyber-grid/20 text-cyber-muted";
    }
  };

  return (
    <div className="space-y-4 sm:space-y-6 min-w-0 max-w-full overflow-x-hidden">
      <div>
        <h1 className={`${isMobile ? 'text-2xl' : 'text-3xl'} font-bold mb-1 cyber-glow-text`}>详细日志</h1>
        <p className="text-cyber-muted text-sm mb-4 sm:mb-6">查看系统运行日志记录</p>
      </div>

      {/* Filters and controls */}
      <div className="cyber-panel p-3 sm:p-4 mb-4 sm:mb-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-4">
          <div className="relative sm:col-span-2 lg:col-span-1">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyber-muted">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              </svg>
            </div>
            <input
              type="text"
              placeholder={isMobile ? "搜索..." : "搜索日志内容..."}
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="cyber-input pl-9 w-full text-sm"
            />
          </div>
          
          <div>
            <select
              value={filterLevel}
              onChange={(e) => setFilterLevel(e.target.value)}
              className="cyber-input w-full text-sm"
            >
              <option value="all">所有级别</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
              <option value="DEBUG">DEBUG</option>
            </select>
          </div>
          
          <div className="flex items-center justify-between sm:col-span-2 lg:col-span-1 gap-1.5 sm:gap-2">
            <div className="flex items-center flex-shrink-0">
              <label className="cursor-pointer flex items-center space-x-1 sm:space-x-2 text-cyber-muted hover:text-cyber-text transition-colors text-xs sm:text-sm whitespace-nowrap">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={() => setAutoRefresh(!autoRefresh)}
                  className="form-checkbox cyber-input h-3.5 w-3.5 sm:h-4 sm:w-4"
                />
                <span className="hidden sm:inline">自动刷新</span>
                <span className="sm:hidden">自动</span>
              </label>
            </div>
            
            <div className="flex items-center gap-1.5 sm:gap-2">
              <button
                onClick={() => fetchLogs()}
                className="cyber-button text-xs flex items-center justify-center gap-1 px-2 sm:px-3 min-w-[60px] sm:min-w-0"
                title="刷新日志"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="1 4 1 10 7 10"></polyline>
                  <polyline points="23 20 23 14 17 14"></polyline>
                  <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"></path>
                </svg>
                <span className="whitespace-nowrap">刷新</span>
              </button>
              
              <button
                onClick={clearLogs}
                className="cyber-button text-xs flex items-center justify-center gap-1 bg-red-500/10 border-red-500/30 hover:border-red-500/50 px-2 sm:px-3 min-w-[60px] sm:min-w-0"
                disabled={isLoading || logs.length === 0}
                title="清空日志"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 6h18"></path>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
                <span className="whitespace-nowrap">清空</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Logs display */}
      <div className="cyber-panel overflow-hidden max-w-full">
        <div className="flex justify-between items-center px-2 sm:px-3 py-2 border-b border-cyber-grid/30 bg-cyber-grid/5">
          <h2 className="font-semibold text-xs sm:text-sm">系统日志</h2>
          <div className="flex items-center gap-2 sm:gap-3">
            <div className="text-cyber-muted text-[10px] sm:text-xs">
              {filteredLogs.length} 条
            </div>
            {logs.length > 0 && !isMobile && (
              <div className="text-[10px] text-cyber-accent/60">
                最新: {new Date(logs[logs.length - 1]?.timestamp).toLocaleTimeString()}
              </div>
            )}
          </div>
        </div>
        
        {isLoading ? (
          <div className="p-4 animate-pulse space-y-3">
            {[...Array(10)].map((_, i) => (
              <div key={i} className="h-6 bg-cyber-grid/30 rounded"></div>
            ))}
          </div>
        ) : filteredLogs.length === 0 ? (
          <div className="p-8 text-center">
            <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyber-muted mx-auto mb-4">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="16" y1="13" x2="8" y2="13"></line>
              <line x1="16" y1="17" x2="8" y2="17"></line>
              <polyline points="10 9 9 9 8 9"></polyline>
            </svg>
            <p className="text-cyber-muted">没有日志记录</p>
          </div>
        ) : (
          <div className="max-h-[calc(100vh-280px)] sm:max-h-[calc(100vh-250px)] overflow-y-auto overflow-x-hidden overscroll-contain">
            {/* 桌面端表格头部 */}
            {!isMobile && filteredLogs.length > 0 && (
              <div className="sticky top-0 z-10 bg-slate-900/95 backdrop-blur-sm border-b border-cyber-grid/30 px-3 sm:px-4 py-2.5">
                <div className="flex items-center gap-2 text-xs font-semibold text-cyber-muted">
                  <div className="flex-shrink-0 w-10 text-right">#</div>
                  <div className="flex-shrink-0 w-32">时间</div>
                  <div className="flex-shrink-0 w-16">级别</div>
                  <div className="flex-shrink-0 w-20">来源</div>
                  <div className="flex-1 min-w-0">消息</div>
                </div>
              </div>
            )}
            <div className="divide-y divide-cyber-grid/10">
              {filteredLogs.map((log, index) => (
                <div
                  key={log.id}
                  className="px-3 sm:px-4 py-2.5 hover:bg-cyber-grid/5 transition-colors overflow-hidden"
                >
                  {isMobile ? (
                    /* 移动端：卡片布局 */
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-cyber-muted/60 font-mono text-[10px]">
                            #{filteredLogs.length - index}
                          </span>
                          <span className={`inline-flex items-center justify-center px-1.5 py-0.5 rounded text-[9px] font-semibold ${getLogLevelStyle(log.level)}`}>
                            {log.level}
                          </span>
                        </div>
                        <div className="text-cyber-muted font-mono text-[10px]">
                          {new Date(log.timestamp).toLocaleString('zh-CN', {
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit',
                            hour12: false
                          }).replace(/\//g, '-')}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-cyber-accent/80 font-mono text-[10px]" title={log.source}>
                          {log.source}
                        </span>
                      </div>
                      <div className="text-slate-200 text-xs leading-relaxed break-words">
                        {log.message}
                      </div>
                    </div>
                  ) : (
                    /* 桌面端：列表布局 */
                    <div className="flex items-start gap-2 text-sm min-w-0">
                      {/* 序号 */}
                      <div className="flex-shrink-0 w-10 text-cyber-muted/60 text-right font-mono text-xs">
                        #{filteredLogs.length - index}
                      </div>
                      
                      {/* 时间戳 */}
                      <div className="flex-shrink-0 w-32 text-cyber-muted font-mono text-xs">
                        {new Date(log.timestamp).toLocaleString('zh-CN', {
                          month: '2-digit',
                          day: '2-digit',
                          hour: '2-digit',
                          minute: '2-digit',
                          second: '2-digit',
                          hour12: false
                        }).replace(/\//g, '-')}
                      </div>
                      
                      {/* 级别 */}
                      <div className="flex-shrink-0 w-16">
                        <span className={`inline-flex items-center justify-center w-full px-1.5 py-0.5 rounded text-[10px] font-semibold ${getLogLevelStyle(log.level)}`}>
                          {log.level}
                        </span>
                      </div>
                      
                      {/* 来源 */}
                      <div className="flex-shrink-0 w-20 text-cyber-accent/80 font-mono text-[10px] truncate" title={log.source}>
                        [{log.source}]
                      </div>
                      
                      {/* 消息 */}
                      <div className="flex-1 text-slate-200 text-xs leading-relaxed break-words overflow-wrap-anywhere min-w-0">
                        {log.message}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <div ref={logEndRef} />
          </div>
        )}
      </div>
    </div>
  );
};

export default LogsPage;
