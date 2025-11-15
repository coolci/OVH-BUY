
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { useAPI } from "@/context/APIContext";
import { api } from "@/utils/apiClient";

interface StatsType {
  activeQueues: number;
  totalServers: number;
  availableServers: number;
  purchaseSuccess: number;
  purchaseFailed: number;
  queueProcessorRunning?: boolean;
  monitorRunning?: boolean;
}

interface QueueItem {
  id: string;
  planCode: string;
  datacenter: string;
  status: string;
  retryCount: number;
  retryInterval: number;
  createdAt: string;
}

const Dashboard = () => {
  const { isAuthenticated } = useAPI();
  const [stats, setStats] = useState<StatsType>({
    activeQueues: 0,
    totalServers: 0,
    availableServers: 0,
    purchaseSuccess: 0,
    purchaseFailed: 0,
    queueProcessorRunning: true,
    monitorRunning: false,
  });
  const [queueItems, setQueueItems] = useState<QueueItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [statsResponse, queueResponse] = await Promise.all([
          api.get(`/stats`),
          api.get(`/queue`)
        ]);
        setStats(statsResponse.data);
        // 只显示活跃的队列项（running, pending, paused），最多3个
        const activeItems = queueResponse.data
          .filter((item: QueueItem) => ['running', 'pending', 'paused'].includes(item.status))
          .slice(0, 3);
        setQueueItems(activeItems);
      } catch (error) {
        console.error("Error fetching data:", error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
    
    // Set up polling interval
    const interval = setInterval(fetchData, 30000);
    
    return () => clearInterval(interval);
  }, []);

  const containerVariants = {
    hidden: { opacity: 0 },
    visible: {
      opacity: 1,
      transition: {
        staggerChildren: 0.1
      }
    }
  };

  const itemVariants = {
    hidden: { y: 20, opacity: 0 },
    visible: { y: 0, opacity: 1 }
  };

  return (
    <div className="space-y-6">
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <h1 className="text-3xl font-bold mb-1 cyber-glow-text">仪表盘</h1>
        <p className="text-cyber-muted mb-6">OVH 服务器抢购平台状态概览</p>
      </motion.div>

      <motion.div
        variants={containerVariants}
        initial="hidden"
        animate="visible"
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6"
      >
        {/* 活跃队列 */}
        <motion.div variants={itemVariants} className="cyber-card relative overflow-hidden flex flex-col">
          <div className="absolute top-0 right-0 w-16 h-16 -mr-6 -mt-6 bg-cyber-accent/10 rounded-full blur-xl"></div>
          <div className="flex justify-between items-start flex-1 min-h-[60px]">
            <div>
              <h3 className="text-cyber-muted text-sm mb-1">活跃队列</h3>
              {isLoading ? (
                <div className="h-8 w-16 bg-cyber-grid animate-pulse rounded"></div>
              ) : (
                <p className="text-3xl font-cyber font-bold text-cyber-accent">{stats.activeQueues}</p>
              )}
            </div>
            <div className="p-2 bg-cyber-accent/10 rounded-full">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyber-accent">
                <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path>
                <rect x="8" y="2" width="8" height="4" rx="1" ry="1"></rect>
                <path d="M9 12h6"></path>
                <path d="M9 16h6"></path>
                <path d="M9 8h6"></path>
              </svg>
            </div>
          </div>
          <div className="mt-4 text-cyber-muted text-xs h-5 flex items-center">
            <Link to="/queue" className="inline-flex items-center gap-1.5 hover:text-cyber-accent transition-colors font-medium">
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyber-accent flex-shrink-0">
                <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path>
                <rect x="8" y="2" width="8" height="4" rx="1" ry="1"></rect>
                <path d="M9 12h6"></path>
                <path d="M9 16h6"></path>
                <path d="M9 8h6"></path>
              </svg>
              <span className="leading-none">查看队列</span>
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="flex-shrink-0">
                <path d="m9 18 6-6-6-6"/>
              </svg>
            </Link>
          </div>
        </motion.div>

        {/* 服务器总数 */}
        <motion.div variants={itemVariants} className="cyber-card relative overflow-hidden flex flex-col">
          <div className="absolute top-0 right-0 w-16 h-16 -mr-6 -mt-6 bg-cyber-neon/10 rounded-full blur-xl"></div>
          <div className="flex justify-between items-start flex-1 min-h-[60px]">
            <div className="flex-1">
              <h3 className="text-cyber-muted text-sm mb-2">服务器总数</h3>
              <div className="flex items-baseline gap-4">
                <div>
                  {isLoading ? (
                    <div className="h-8 w-16 bg-cyber-grid animate-pulse rounded"></div>
                  ) : (
                    <p className="text-3xl font-cyber font-bold text-cyber-neon">{stats.totalServers}</p>
                  )}
                </div>
                <div className="flex items-center gap-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${
                    stats.availableServers > 0 ? 'bg-green-400 animate-pulse' : 'bg-cyber-muted'
                  }`}></span>
                  <span className="text-xs text-cyber-muted">可用:</span>
                  {isLoading ? (
                    <span className="text-xs text-cyber-muted">-</span>
                  ) : (
                    <span className={`text-lg font-bold ${
                      stats.availableServers > 0 ? 'text-green-400' : 'text-cyber-muted'
                    }`}>{stats.availableServers}</span>
                  )}
                </div>
              </div>
            </div>
            <div className="p-2 bg-cyber-neon/10 rounded-full flex-shrink-0">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyber-neon">
                <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
                <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
                <line x1="6" y1="6" x2="6.01" y2="6"></line>
                <line x1="6" y1="18" x2="6.01" y2="18"></line>
              </svg>
            </div>
          </div>
          <div className="mt-4 text-cyber-muted text-xs h-5 flex items-center">
            <Link to="/servers" className="inline-flex items-center gap-1.5 hover:text-cyber-neon transition-colors font-medium">
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyber-neon flex-shrink-0">
                <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
                <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
                <line x1="6" y1="6" x2="6.01" y2="6"></line>
                <line x1="6" y1="18" x2="6.01" y2="18"></line>
              </svg>
              <span className="leading-none">查看服务器</span>
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="flex-shrink-0">
                <path d="m9 18 6-6-6-6"/>
              </svg>
            </Link>
          </div>
        </motion.div>

        {/* 抢购成功 */}
        <motion.div variants={itemVariants} className="cyber-card relative overflow-hidden flex flex-col">
          <div className="absolute top-0 right-0 w-16 h-16 -mr-6 -mt-6 bg-green-500/10 rounded-full blur-xl"></div>
          <div className="flex justify-between items-start flex-1 min-h-[60px]">
            <div>
              <h3 className="text-cyber-muted text-sm mb-1">抢购成功</h3>
              {isLoading ? (
                <div className="h-8 w-16 bg-cyber-grid animate-pulse rounded"></div>
              ) : (
                <p className="text-3xl font-cyber font-bold text-green-400">{stats.purchaseSuccess}</p>
              )}
            </div>
            <div className="p-2 bg-green-500/10 rounded-full">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-green-400">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                <polyline points="22 4 12 14.01 9 11.01"></polyline>
              </svg>
            </div>
          </div>
          <div className="mt-4 text-cyber-muted text-xs h-5 flex items-center">
            <Link to="/history" className="inline-flex items-center gap-1.5 hover:text-green-400 transition-colors font-medium">
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-green-400 flex-shrink-0">
                <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                <line x1="16" y1="2" x2="16" y2="6"></line>
                <line x1="8" y1="2" x2="8" y2="6"></line>
                <line x1="3" y1="10" x2="21" y2="10"></line>
              </svg>
              <span className="leading-none">查看历史</span>
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="flex-shrink-0">
                <path d="m9 18 6-6-6-6"/>
              </svg>
            </Link>
          </div>
        </motion.div>
      </motion.div>

      {/* 最近活动和队列状态 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-6 mt-4 sm:mt-6">
        {/* 活跃队列详情 */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5, duration: 0.3 }}
          className="cyber-card lg:col-span-2"
        >
          <div className="flex justify-between items-center mb-4 sm:mb-5">
            <h2 className="text-base sm:text-lg font-bold text-cyber-text">活跃队列</h2>
            <Link 
              to="/queue" 
              className="text-cyber-muted text-xs hover:text-cyber-accent transition-colors"
            >
              查看全部
            </Link>
          </div>

          {isLoading ? (
            <div className="space-y-4">
              {[...Array(3)].map((_, i) => (
                <div key={i} className="h-20 bg-cyber-grid/50 animate-pulse rounded-lg"></div>
              ))}
            </div>
          ) : stats.activeQueues === 0 ? (
            <div className="bg-cyber-grid/10 p-8 rounded-lg text-center border border-cyber-grid/30">
              <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyber-muted/50 mx-auto mb-4">
                <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                <line x1="16" y1="2" x2="16" y2="6"></line>
                <line x1="8" y1="2" x2="8" y2="6"></line>
                <line x1="3" y1="10" x2="21" y2="10"></line>
              </svg>
              <p className="text-cyber-muted text-base mb-4">暂无活跃任务</p>
              <Link 
                to="/queue" 
                className="cyber-button text-sm inline-flex items-center px-5 py-2.5 gap-2"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="5" x2="12" y2="19"></line>
                  <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
                创建抢购任务
              </Link>
            </div>
          ) : (
            <div className="space-y-3">
              {queueItems.map((item) => (
                <div key={item.id} className="p-3 sm:p-4 bg-cyber-grid/10 rounded-lg border border-cyber-accent/20 hover:border-cyber-accent/40 hover:bg-cyber-grid/15 transition-all duration-200 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 group">
                  <div className="flex-1 min-w-0 w-full sm:w-auto">
                    <p className="font-semibold text-sm sm:text-base text-cyber-accent truncate group-hover:text-cyber-neon transition-colors">{item.planCode}</p>
                    <div className="flex items-center gap-2 sm:gap-3 text-xs sm:text-sm text-cyber-muted mt-1.5 flex-wrap">
                      <span className="flex items-center gap-1.5">
                        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyber-accent/60 sm:w-3.5 sm:h-3.5">
                          <circle cx="12" cy="12" r="10"></circle>
                          <polyline points="12 6 12 12 16 14"></polyline>
                        </svg>
                        {item.datacenter.toUpperCase()}
                      </span>
                      <span className="text-cyber-grid">•</span>
                      <span>第 {item.retryCount + 1} 次尝试</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0 w-full sm:w-auto">
                    <span className={`text-xs sm:text-sm px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-lg flex items-center font-medium ${
                      item.status === 'running' ? 'bg-green-500/20 text-green-400 border border-green-500/30' :
                      item.status === 'pending' ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30' :
                      'bg-gray-500/20 text-gray-400 border border-gray-500/30'
                    }`}>
                      <span className={`w-1.5 h-1.5 sm:w-2 sm:h-2 mr-1.5 sm:mr-2 rounded-full ${
                        item.status === 'running' ? 'bg-green-400 animate-pulse' :
                        item.status === 'pending' ? 'bg-yellow-400' :
                        'bg-gray-400'
                      }`}></span>
                      {item.status === 'running' ? '运行中' :
                       item.status === 'pending' ? '等待中' : '已暂停'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </motion.div>

        {/* 系统状态 */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6, duration: 0.3 }}
          className="cyber-card"
        >
          <h2 className="text-base sm:text-lg font-bold mb-4 sm:mb-5">系统状态</h2>
          <div className="space-y-3 sm:space-y-4">
            <div className="flex justify-between items-center p-2.5 sm:p-3 rounded-lg bg-cyber-grid/5 hover:bg-cyber-grid/10 transition-colors">
              <span className="text-cyber-text text-xs sm:text-sm font-medium">API 连接</span>
              <span className={`flex items-center text-xs sm:text-sm font-semibold ${isAuthenticated ? 'text-green-400' : 'text-red-400'}`}>
                <span className={`w-2 h-2 sm:w-2.5 sm:h-2.5 rounded-full mr-1.5 sm:mr-2 ${isAuthenticated ? 'bg-green-400 animate-pulse shadow-lg shadow-green-400/50' : 'bg-red-400'}`}></span>
                {isAuthenticated ? '已连接' : '未连接'}
              </span>
            </div>
            <div className="flex justify-between items-center p-2.5 sm:p-3 rounded-lg bg-cyber-grid/5 hover:bg-cyber-grid/10 transition-colors">
              <span className="text-cyber-text text-xs sm:text-sm font-medium">自动抢购</span>
              <span className={`flex items-center text-xs sm:text-sm font-semibold ${stats.activeQueues > 0 ? 'text-green-400' : 'text-cyber-muted'}`}>
                <span className={`w-2 h-2 sm:w-2.5 sm:h-2.5 rounded-full mr-1.5 sm:mr-2 ${stats.activeQueues > 0 ? 'bg-green-400 animate-pulse shadow-lg shadow-green-400/50' : 'bg-cyber-muted'}`}></span>
                {stats.activeQueues > 0 ? '运行中' : '暂无任务'}
              </span>
            </div>
            <div className="flex justify-between items-center p-2.5 sm:p-3 rounded-lg bg-cyber-grid/5 hover:bg-cyber-grid/10 transition-colors">
              <span className="text-cyber-text text-xs sm:text-sm font-medium">服务器监控</span>
              <span className={`flex items-center text-xs sm:text-sm font-semibold ${stats.monitorRunning ? 'text-green-400' : 'text-cyber-muted'}`}>
                <span className={`w-2 h-2 sm:w-2.5 sm:h-2.5 rounded-full mr-1.5 sm:mr-2 ${stats.monitorRunning ? 'bg-green-400 animate-pulse shadow-lg shadow-green-400/50' : 'bg-cyber-muted'}`}></span>
                {stats.monitorRunning ? '运行中' : '待启用'}
              </span>
            </div>
            <div className="flex justify-between items-center p-2.5 sm:p-3 rounded-lg bg-cyber-grid/5 mt-3 sm:mt-4 border-t border-cyber-grid/30 pt-3 sm:pt-4">
              <span className="text-cyber-muted text-xs sm:text-sm">系统版本</span>
              <span className="text-cyber-text text-xs sm:text-sm font-mono font-semibold">v3.0.0</span>
            </div>
          </div>
        </motion.div>
      </div>
    </div>
  );
};

export default Dashboard;
