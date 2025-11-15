import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { Database, Trash2, RefreshCw, HardDrive, Clock, Server, CheckCircle2, XCircle, AlertCircle } from "lucide-react";
import { api, getApiSecretKey } from "@/utils/apiClient";

interface CacheInfo {
  backend: {
    hasCachedData: boolean;
    timestamp: number | null;
    cacheAge: number | null;
    cacheDuration: number;
    serverCount: number;
    cacheValid: boolean;
  };
  storage: {
    dataDir: string;
    cacheDir: string;
    logsDir: string;
    files: {
      config: boolean;
      servers: boolean;
      logs: boolean;
      queue: boolean;
      history: boolean;
    };
  };
}

export const CacheManager = () => {
  const [cacheInfo, setCacheInfo] = useState<CacheInfo | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isClearingCache, setIsClearingCache] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // 格式化缓存时间
  const formatCacheAge = (ageInSeconds: number | null): string => {
    if (ageInSeconds === null) return "无缓存";
    
    const hours = Math.floor(ageInSeconds / 3600);
    const minutes = Math.floor((ageInSeconds % 3600) / 60);
    
    if (hours > 0) {
      return `${hours}小时${minutes}分钟前`;
    } else if (minutes > 0) {
      return `${minutes}分钟前`;
    } else {
      return "刚刚";
    }
  };

  // 获取缓存信息
  const fetchCacheInfo = async () => {
    // 检查是否有 API 密钥
    const apiKey = getApiSecretKey();
    if (!apiKey) {
      console.log('未设置 API 密钥，跳过缓存信息加载');
      setIsLoading(false);
      return;
    }
    
    setIsLoading(true);
    try {
      const response = await api.get(`/cache/info`);
      setCacheInfo(response.data);
    } catch (error) {
      console.error("获取缓存信息失败:", error);
      // 如果是 401 错误，说明密钥不正确
      if ((error as any)?.response?.status === 401) {
        console.log('API 密钥验证失败，请在设置页面配置正确的密钥');
      }
    } finally {
      setIsLoading(false);
    }
  };

  // 清除后端缓存
  const clearBackendCache = async (type: 'all' | 'memory' | 'files' = 'all') => {
    setIsClearingCache(true);
    try {
      const response = await api.post(`/cache/clear`, { type });
      toast.success(response.data.message || "已清除后端缓存");
      fetchCacheInfo();
    } catch (error) {
      console.error("清除后端缓存失败:", error);
      toast.error("清除后端缓存失败");
    } finally {
      setIsClearingCache(false);
    }
  };

  // 清除所有缓存（现在只有后端缓存）
  const clearAllCache = async () => {
    await clearBackendCache('all');
  };

  // 组件挂载时自动获取缓存信息
  useEffect(() => {
    fetchCacheInfo();
  }, []);

  return (
    <div className="space-y-3">
      <Card className="border-cyber-accent/20">
        <CardHeader className="px-4 py-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2 text-cyber-accent text-base">
                <Database className="w-4 h-4" />
                缓存管理
              </CardTitle>
              <CardDescription className="mt-0.5 text-xs">
                管理后端服务器列表缓存（2小时有效期）
              </CardDescription>
            </div>
            {isLoading && (
              <RefreshCw className="w-4 h-4 animate-spin text-cyber-accent" />
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3 px-4 pb-4">
          {/* 操作按钮 */}
          <div className="flex flex-wrap gap-2.5">
            <button
              onClick={fetchCacheInfo}
              disabled={isLoading}
              className="cyber-button text-xs flex items-center justify-center gap-1.5 px-3.5 py-2 bg-cyber-surface-dark/50 border-cyber-border/50 text-cyber-muted hover:bg-cyber-surface-dark hover:border-cyber-accent/30 hover:text-cyber-accent disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <RefreshCw className={`w-3 h-3 flex-shrink-0 ${isLoading ? 'animate-spin' : ''}`} />
              <span>刷新信息</span>
            </button>
            <button
              onClick={clearAllCache}
              disabled={isClearingCache}
              className="cyber-button text-xs flex items-center justify-center gap-1.5 px-3.5 py-2 bg-cyber-surface-dark/50 border-cyber-border/50 text-cyber-muted hover:bg-red-900/20 hover:border-red-700/30 hover:text-red-300 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Trash2 className={`w-3 h-3 flex-shrink-0 ${isClearingCache ? 'animate-pulse' : ''}`} />
              <span>{isClearingCache ? '清除中...' : '清除后端缓存'}</span>
            </button>
          </div>

          {/* 后端缓存信息 */}
          {cacheInfo ? (
            <Card className="border-blue-500/30 bg-gradient-to-br from-blue-500/10 to-transparent">
              <CardHeader className="px-3 py-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm flex items-center gap-1.5">
                    <Database className="w-3.5 h-3.5 text-blue-400" />
                    后端缓存
                  </CardTitle>
                  <Badge 
                    variant="outline" 
                    className={cacheInfo.backend.cacheValid 
                      ? 'bg-green-500/10 text-green-300 border-green-500/30 text-[10px] px-1.5 py-0' 
                      : 'bg-red-500/10 text-red-300 border-red-500/30 text-[10px] px-1.5 py-0'
                    }
                  >
                    {cacheInfo.backend.cacheValid ? (
                      <>
                        <CheckCircle2 className="w-2.5 h-2.5 mr-0.5" />
                        有效
                      </>
                    ) : (
                      <>
                        <AlertCircle className="w-2.5 h-2.5 mr-0.5" />
                        已过期
                      </>
                    )}
                  </Badge>
                </div>
                <CardDescription className="text-[10px]">内存缓存 + 文件存储</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 px-3 pb-3">
                {/* 缓存统计 */}
                <div className="space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-0.5 bg-blue-500/5 p-2 rounded">
                      <p className="text-[10px] text-muted-foreground">服务器数量</p>
                      <p className="text-lg font-bold font-mono text-blue-400">
                        {cacheInfo.backend.hasCachedData ? cacheInfo.backend.serverCount : 0}
                      </p>
                    </div>
                    <div className="space-y-0.5 bg-blue-500/5 p-2 rounded">
                      <p className="text-[10px] text-muted-foreground">缓存时间</p>
                      <p className="text-xs font-mono text-blue-300">
                        {formatCacheAge(cacheInfo.backend.cacheAge)}
                      </p>
                    </div>
                  </div>
                  <div className="space-y-0.5 bg-blue-500/5 p-2 rounded">
                    <p className="text-[10px] text-muted-foreground">缓存有效期</p>
                    <p className="text-xs font-mono text-blue-300">
                      {Math.floor(cacheInfo.backend.cacheDuration / 3600)} 小时
                      <span className="text-[10px] text-muted-foreground ml-2">
                        ({cacheInfo.backend.cacheDuration / 60} 分钟)
                      </span>
                    </p>
                  </div>
                </div>

                {/* 存储位置 */}
                <div className="border-t border-blue-500/20 pt-2">
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <HardDrive className="w-3 h-3 text-blue-400" />
                    <span className="text-xs font-medium">存储位置</span>
                  </div>
                  <div className="space-y-1 bg-black/20 rounded p-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground w-10">数据:</span>
                      <code className="text-[10px] font-mono text-blue-300">{cacheInfo.storage.dataDir}/</code>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground w-10">缓存:</span>
                      <code className="text-[10px] font-mono text-blue-300">{cacheInfo.storage.cacheDir}/</code>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-muted-foreground w-10">日志:</span>
                      <code className="text-[10px] font-mono text-blue-300">{cacheInfo.storage.logsDir}/</code>
                    </div>
                  </div>
                </div>

                {/* 操作按钮 */}
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={() => clearBackendCache('memory')}
                    disabled={isClearingCache}
                    className="cyber-button text-xs flex items-center justify-center gap-1.5 h-7 bg-blue-900/20 border-blue-700/40 text-blue-300 hover:bg-blue-800/30 hover:border-blue-600/50 hover:text-blue-200 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-200"
                  >
                    <Trash2 className={`w-3 h-3 flex-shrink-0 ${isClearingCache ? 'animate-pulse' : ''}`} />
                    <span>清除内存</span>
                  </button>
                  <button
                    onClick={() => clearBackendCache('files')}
                    disabled={isClearingCache}
                    className="cyber-button text-xs flex items-center justify-center gap-1.5 h-7 bg-blue-900/20 border-blue-700/40 text-blue-300 hover:bg-blue-800/30 hover:border-blue-600/50 hover:text-blue-200 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-200"
                  >
                    <Trash2 className={`w-3 h-3 flex-shrink-0 ${isClearingCache ? 'animate-pulse' : ''}`} />
                    <span>清除文件</span>
                  </button>
                </div>
              </CardContent>
            </Card>
          ) : (
            !isLoading && (
              <Card className="border-dashed border-muted-foreground/20">
                <CardContent className="flex flex-col items-center justify-center py-6 text-center px-3">
                  <RefreshCw className="w-10 h-10 text-muted-foreground mb-2 opacity-30" />
                  <p className="text-xs text-muted-foreground mb-1">未加载后端缓存信息</p>
                  <p className="text-[10px] text-muted-foreground mb-3">点击上方“刷新信息”按钮获取缓存状态</p>
                  <button
                    onClick={fetchCacheInfo}
                    className="cyber-button text-xs flex items-center justify-center gap-1.5 h-7"
                  >
                    <RefreshCw className="w-3 h-3 flex-shrink-0" />
                    <span>立即获取</span>
                  </button>
                </CardContent>
              </Card>
            )
          )}
        </CardContent>
      </Card>
    </div>
  );
};
