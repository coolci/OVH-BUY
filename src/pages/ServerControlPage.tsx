import React, { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/utils/apiClient";
import { useToast } from "../components/ToastContainer";
import { Server, RefreshCw, Power, HardDrive, X, AlertCircle, Activity, Cpu, Wifi, Calendar, Monitor, Mail, BarChart3, Check, Cog, Zap, Shield, Database, Globe, Network, Settings } from "lucide-react";
import { useIsMobile } from "@/hooks/use-mobile";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface ServerInfo {
  serviceName: string;
  name: string;
  commercialRange: string;
  datacenter: string;
  state: string;
  ip: string;
  os: string;
}

interface OSTemplate {
  templateName: string;
  distribution: string;
  family: string;
  bitFormat: number;
}

interface ServerTask {
  taskId: number;
  function: string;
  status: string;
  startDate: string;
  doneDate: string;
}

interface PartitionScheme {
  name: string;
  priority: number;
  partitions: {
    mountpoint: string;
    filesystem: string;
    size: number;
    order: number;
    raid: string | null;
    type: string;
  }[];
}

interface ServiceInfo {
  status: string;
  creation: string;
  expiration: string;
  renewalType: boolean;
}

interface BootMode {
  id: number;
  bootType: string;
  description: string;
  kernel: string;
  active: boolean;
}

interface InstallStep {
  comment: string;
  commentOriginal?: string;  // 原文（用于调试）
  status: 'doing' | 'done' | 'error' | 'todo' | 'init';
  error: string;
}

interface InstallProgress {
  elapsedTime: number;
  progressPercentage: number;
  totalSteps: number;
  completedSteps: number;
  hasError: boolean;
  allDone: boolean;
  steps: InstallStep[];
}

interface DiskInfo {
  capacity: number;
  unit: string;
  interface: string;
  technology: string;
  number: number;
}

interface DiskGroup {
  id: number;
  raidController: string;
  disks: DiskInfo[];
}

interface HardwareRaidProfile {
  diskGroupId: number;
  mode: string;  // raid0, raid1, raid5, raid6, raid10
  name: string;
  description: string;
}

interface NetworkSpecs {
  bandwidth: {
    InternetToOvh?: { unit: string; value: number };
    OvhToInternet?: { unit: string; value: number };
    OvhToOvh?: { unit: string; value: number };
    type?: string;
  };
  connection?: {
    unit: string;
    value: number;
  };
  ola?: {
    available: boolean;
    availableModes?: Array<{
      default: boolean;
      interfaces: Array<{
        aggregation: boolean;
        count: number;
        type: string;
      }>;
      name: string;
    }>;
    supportedModes?: string[];
  };
  routing?: {
    ipv4?: {
      gateway: string;
      ip: string;
      network: string;
    };
    ipv6?: {
      gateway: string;
      ip: string;
      network: string;
    };
  };
  switching?: {
    name: string;
  };
  traffic?: {
    inputQuotaSize?: { unit: string; value: number };
    inputQuotaUsed?: { unit: string; value: number };
    isThrottled?: boolean;
    outputQuotaSize?: { unit: string; value: number };
    outputQuotaUsed?: { unit: string; value: number };
    resetQuotaDate?: string;
  };
  vmac?: {
    quota: number;
    supported: boolean;
  };
  vrack?: {
    bandwidth?: { unit: string; value: number };
    type?: string;
  };
}

interface CustomPartition {
  mountpoint: string;
  filesystem: string;
  size: number;
  order: number;
  raid?: string;
  type: string;
  diskGroupId?: number;  // 指定使用哪个磁盘组
}

const ServerControlPage: React.FC = () => {
  const isMobile = useIsMobile();
  const { showToast, showConfirm } = useToast();
  const [servers, setServers] = useState<ServerInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  
  // Task 3: 重装系统状态
  const [selectedServer, setSelectedServer] = useState<ServerInfo | null>(null);
  const [showReinstallDialog, setShowReinstallDialog] = useState(false);
  const [osTemplates, setOsTemplates] = useState<OSTemplate[]>([]);
  const [templateSearchQuery, setTemplateSearchQuery] = useState(""); // 模板搜索
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [customHostname, setCustomHostname] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);
  const [partitionSchemes, setPartitionSchemes] = useState<PartitionScheme[]>([]);
  const [selectedScheme, setSelectedScheme] = useState("");
  const [showPartitionDetails, setShowPartitionDetails] = useState(false);
  const [loadingPartitions, setLoadingPartitions] = useState(false);
  
  // 磁盘配置状态
  const [diskGroups, setDiskGroups] = useState<{ [key: string]: DiskGroup }>({});
  const [raidProfiles, setRaidProfiles] = useState<HardwareRaidProfile[]>([]);
  const [raidSupported, setRaidSupported] = useState(true);
  const [useCustomStorage, setUseCustomStorage] = useState(false);
  const [selectedRaidConfigs, setSelectedRaidConfigs] = useState<{ [diskGroupId: number]: string }>({});
  const [customPartitions, setCustomPartitions] = useState<CustomPartition[]>([]);
  const [loadingDiskInfo, setLoadingDiskInfo] = useState(false);
  
  // 软RAID配置状态
  const [useSoftwareRaid, setUseSoftwareRaid] = useState(false);
  const [softwareRaidLevel, setSoftwareRaidLevel] = useState<string>('raid1');
  
  // 分区编辑状态
  const [showPartitionEditor, setShowPartitionEditor] = useState(false);
  const [editingPartition, setEditingPartition] = useState<CustomPartition | null>(null);
  const [editingPartitionIndex, setEditingPartitionIndex] = useState<number>(-1);
  
  // 智能配置确认对话框
  const [showSmartConfigDialog, setShowSmartConfigDialog] = useState(false);
  const [smartConfigInfo, setSmartConfigInfo] = useState({ 
    groupCount: 0, 
    diskCount: 0, 
    scenario: '',
    diskDetails: '' // 新增：磁盘详细信息
  });
  
  // Task 4: 任务查看状态
  const [showTasksDialog, setShowTasksDialog] = useState(false);
  const [serverTasks, setServerTasks] = useState<ServerTask[]>([]);
  // 任务可用时间段
  const [showTimeslotsDialog, setShowTimeslotsDialog] = useState(false);
  const [selectedTaskForTimeslots, setSelectedTaskForTimeslots] = useState<ServerTask | null>(null);
  const [timeslots, setTimeslots] = useState<{ startDate: string; endDate: string; }[]>([]);
  const [loadingTimeslots, setLoadingTimeslots] = useState(false);
  const [periodStart, setPeriodStart] = useState<string>('');
  const [periodEnd, setPeriodEnd] = useState<string>('');
  
  // Task 5: 监控功能
  const [monitoring, setMonitoring] = useState(false);
  const [loadingMonitoring, setLoadingMonitoring] = useState(false);
  
  // Task 6: 硬件信息
  const [hardware, setHardware] = useState<any>(null);
  const [loadingHardware, setLoadingHardware] = useState(false);
  
  // BIOS 设置
  const [biosSettings, setBiosSettings] = useState<any | null>(null);
  const [biosSgx, setBiosSgx] = useState<any | null>(null);
  const [loadingBios, setLoadingBios] = useState(false);
  
  // Task 7: IP管理
  const [ips, setIps] = useState<any[]>([]);
  const [loadingIPs, setLoadingIPs] = useState(false);
  
  // Task 8: 服务信息
  const [serviceInfo, setServiceInfo] = useState<ServiceInfo | null>(null);
  const [loadingService, setLoadingService] = useState(false);
  
  // Task 10: 启动模式
  const [showBootModeDialog, setShowBootModeDialog] = useState(false);
  const [bootModes, setBootModes] = useState<BootMode[]>([]);
  const [loadingBootModes, setLoadingBootModes] = useState(false);

  // IPMI链接模态框
  const [showIpmiLinkDialog, setShowIpmiLinkDialog] = useState(false);
  const [ipmiLink, setIpmiLink] = useState<string>('');
  const [ipmiLoading, setIpmiLoading] = useState(false);
  const [ipmiCountdown, setIpmiCountdown] = useState(20);

  // 安装进度监控
  const [showInstallProgress, setShowInstallProgress] = useState(false);
  const [installProgress, setInstallProgress] = useState<InstallProgress | null>(null);
  const [installCompleted, setInstallCompleted] = useState(false); // 标记安装是否已完成
  const [autoCloseCountdown, setAutoCloseCountdown] = useState(8); // 自动关闭倒计时
  const [installPollingInterval, setInstallPollingInterval] = useState<NodeJS.Timeout | null>(null);
  const installProgressRef = useRef<InstallProgress | null>(null); // 用于在定时器回调中访问最新状态
  const completionToastShownRef = useRef<boolean>(false); // 防止重复显示完成提示

  // 硬件更换功能
  const [showHardwareReplaceDialog, setShowHardwareReplaceDialog] = useState(false);
  const [hardwareReplaceType, setHardwareReplaceType] = useState<'hardDiskDrive' | 'memory' | 'cooling' | ''>('');
  const [hardwareReplaceComment, setHardwareReplaceComment] = useState('');
  const [hardwareReplaceDetails, setHardwareReplaceDetails] = useState('');

  // 维护记录功能
  const [interventions, setInterventions] = useState<any[]>([]);
  const [loadingInterventions, setLoadingInterventions] = useState(false);
  
  // 计划维护功能
  const [plannedInterventions, setPlannedInterventions] = useState<any[]>([]);
  const [loadingPlannedInterventions, setLoadingPlannedInterventions] = useState(false);

  // 网络接口功能（物理网卡）
  const [networkInterfaces, setNetworkInterfaces] = useState<any[]>([]);
  const [loadingNetworkInterfaces, setLoadingNetworkInterfaces] = useState(false);

  // MRTG流量监控功能
  const [mrtgData, setMrtgData] = useState<any>(null);
  const [loadingMrtg, setLoadingMrtg] = useState(false);
  const [mrtgPeriod, setMrtgPeriod] = useState('daily');  // hourly, daily, weekly, monthly, yearly

  // 变更联系人功能
  const [showChangeContactDialog, setShowChangeContactDialog] = useState(false);
  const [contactAdmin, setContactAdmin] = useState('');
  const [contactTech, setContactTech] = useState('');
  const [contactBilling, setContactBilling] = useState('');
  const [loadingChangeContact, setLoadingChangeContact] = useState(false);
  
  // 联系人变更请求管理
  interface ContactChangeRequest {
    id: number;
    askingAccount?: string;
    contactTypes: string[];
    dateDone?: string;
    dateRequest: string;
    fromAccount?: string;
    serviceDomain?: string;
    state: string;
    toAccount?: string;
  }
  
  const [contactChangeRequests, setContactChangeRequests] = useState<ContactChangeRequest[]>([]);
  const [loadingContactRequests, setLoadingContactRequests] = useState(false);
  const [loadingTokenAction, setLoadingTokenAction] = useState(false);
  const [contactDialogTab, setContactDialogTab] = useState<'submit' | 'requests'>('submit');
  const [showTokenDialog, setShowTokenDialog] = useState(false);
  const [selectedRequest, setSelectedRequest] = useState<ContactChangeRequest | null>(null);
  const [tokenAction, setTokenAction] = useState<'accept' | 'refuse' | null>(null);
  const [token, setToken] = useState('');

  // 网络规格功能
  const [networkSpecs, setNetworkSpecs] = useState<NetworkSpecs | null>(null);
  const [loadingNetworkSpecs, setLoadingNetworkSpecs] = useState(false);
  const [showNetworkSpecsDialog, setShowNetworkSpecsDialog] = useState(false);

  // 高级功能管理
  const [showAdvancedDialog, setShowAdvancedDialog] = useState(false);
  const [advancedTab, setAdvancedTab] = useState<'burst' | 'firewall' | 'backup' | 'dns' | 'vmac' | 'vrack' | 'orderable' | 'options' | 'ip'>('burst');
  
  // Burst突发带宽
  const [burst, setBurst] = useState<any>(null);
  const [loadingBurst, setLoadingBurst] = useState(false);
  
  // Firewall防火墙
  const [firewall, setFirewall] = useState<any>(null);
  const [loadingFirewall, setLoadingFirewall] = useState(false);
  
  // Backup FTP
  const [backupFtp, setBackupFtp] = useState<any>(null);
  const [backupFtpAccess, setBackupFtpAccess] = useState<any[]>([]);
  const [loadingBackupFtp, setLoadingBackupFtp] = useState(false);
  
  // Secondary DNS
  const [secondaryDns, setSecondaryDns] = useState<any[]>([]);
  const [loadingSecondaryDns, setLoadingSecondaryDns] = useState(false);
  
  // Virtual MAC
  const [virtualMacs, setVirtualMacs] = useState<any[]>([]);
  const [loadingVirtualMacs, setLoadingVirtualMacs] = useState(false);
  
  // vRack
  const [vracks, setVracks] = useState<any[]>([]);
  const [loadingVracks, setLoadingVracks] = useState(false);
  
  // Orderable Services
  const [orderableBandwidth, setOrderableBandwidth] = useState<any>(null);
  const [orderableTraffic, setOrderableTraffic] = useState<any>(null);
  const [orderableIp, setOrderableIp] = useState<any>(null);
  const [loadingOrderable, setLoadingOrderable] = useState(false);
  
  // Options
  const [serverOptions, setServerOptions] = useState<any[]>([]);
  const [loadingOptions, setLoadingOptions] = useState(false);
  
  // IP Specs
  const [ipSpecs, setIpSpecs] = useState<any>(null);
  const [loadingIpSpecs, setLoadingIpSpecs] = useState(false);

  // 加载 BIOS 设置
  const fetchBiosSettings = async () => {
    if (!selectedServer) return;
    setLoadingBios(true);
    setBiosSettings(null);
    setBiosSgx(null);
    try {
      const resp = await api.get(`/server-control/${selectedServer.serviceName}/bios-settings`);
      const data = resp.data?.bios ?? resp.data?.data ?? resp.data;
      setBiosSettings(data);
      // 尝试获取 SGX（如果不支持则忽略错误）
      try {
        const sgxResp = await api.get(`/server-control/${selectedServer.serviceName}/bios-settings/sgx`);
        setBiosSgx(sgxResp.data?.sgx ?? sgxResp.data?.data ?? sgxResp.data);
      } catch (_) {
        setBiosSgx(null);
      }
    } catch (error: any) {
      console.error('获取BIOS设置失败:', error);
      if (error?.response?.status === 404) {
        // 该服务器不支持 BIOS 设置
        setBiosSettings({});
        setBiosSgx(null);
        showToast({ title: '该服务器不支持 BIOS 设置', type: 'info' });
      } else {
        showToast({ title: '获取BIOS设置失败', type: 'error' });
      }
    } finally {
      setLoadingBios(false);
    }
  };

  // Task 1: 获取服务器列表（只显示活跃服务器）
  const fetchServers = async () => {
    setIsLoading(true);
    try {
      const response = await api.get('/server-control/list');
      if (response.data.success) {
        // 过滤：只显示未过期、未暂停的服务器
        const activeServers = response.data.servers.filter((s: any) => {
          const state = s.state?.toLowerCase();
          const status = s.status?.toLowerCase();
          
          // 排除已过期、已暂停的服务器
          if (status === 'expired' || status === 'suspended') return false;
          if (state === 'error' || state === 'suspended') return false;
          
          // 只显示正常状态
          return state === 'ok' || state === 'active';
        });
        
        setServers(activeServers);
        
        // 自动选择第一台服务器
        if (activeServers.length > 0 && !selectedServer) {
          setSelectedServer(activeServers[0]);
        }
        
        const filteredCount = response.data.total - activeServers.length;
        showToast({ 
          type: 'success', 
          title: `已加载 ${activeServers.length} 台活跃服务器` + 
                 (filteredCount > 0 ? ` (已过滤 ${filteredCount} 台)` : '')
        });
      }
    } catch (error: any) {
      console.error('获取服务器列表失败:', error);
      showToast({ type: 'error', title: '获取服务器列表失败' });
    } finally {
      setIsLoading(false);
    }
  };

  // Task 2: 重启服务器
  const handleReboot = async (server: ServerInfo) => {
    const confirmed = await showConfirm({
      title: '确定要重启服务器吗？',
      message: `${server.name} (${server.serviceName})`,
      confirmText: '重启',
      cancelText: '取消'
    });

    if (!confirmed) return;

    try {
      const response = await api.post(`/server-control/${server.serviceName}/reboot`);
      if (response.data.success) {
        showToast({ type: 'success', title: '重启请求已发送' });
      }
    } catch (error: any) {
      console.error('重启失败:', error);
      showToast({ type: 'error', title: '重启失败' });
    }
  };

  // Task 3: 获取系统模板
  const fetchOSTemplates = async (serviceName: string) => {
    try {
      const response = await api.get(`/server-control/${serviceName}/templates`);
      if (response.data.success) {
        const templates = response.data.templates;
        setOsTemplates(templates);
        
        // 调试日志
        console.log(`[Templates] 总共收到 ${templates.length} 个模板`);
        const ubuntuTemplates = templates.filter((t: OSTemplate) => 
          t.distribution.toLowerCase().includes('ubuntu') ||
          t.templateName.toLowerCase().includes('ubuntu')
        );
        console.log(`[Templates] Ubuntu模板数量: ${ubuntuTemplates.length}`);
        if (ubuntuTemplates.length > 0) {
          console.log('[Templates] Ubuntu模板列表:', ubuntuTemplates.map((t: OSTemplate) => t.templateName));
        }
        console.log('[Templates] 前10个模板:', templates.slice(0, 10).map((t: OSTemplate) => t.templateName));
      }
    } catch (error: any) {
      console.error('获取模板失败:', error);
      showToast({ type: 'error', title: '获取系统模板失败' });
    }
  };

  // Task 3.1: 获取分区方案
  const fetchPartitionSchemes = async (serviceName: string, templateName: string) => {
    console.log('[Partition] 开始加载分区方案:', { serviceName, templateName });
    setLoadingPartitions(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/partition-schemes?templateName=${templateName}`);
      console.log('[Partition] API响应:', response.data);
      
      if (response.data.success) {
        setPartitionSchemes(response.data.schemes);
        // 不自动选择，让用户决定是否使用自定义分区
        setSelectedScheme('');
        
        if (response.data.schemes.length > 0) {
          console.log('[Partition] 加载到方案:', response.data.schemes);
          showToast({ 
            type: 'info', 
            title: `已加载 ${response.data.schemes.length} 个分区方案（可选）` 
          });
        } else {
          console.log('[Partition] 模板无分区方案');
          showToast({ 
            type: 'warning', 
            title: '该模板无可用分区方案' 
          });
        }
      }
    } catch (error: any) {
      console.error('[Partition] 获取失败:', error);
      console.error('[Partition] 错误详情:', error.response?.data);
      setPartitionSchemes([]);
      setSelectedScheme('');
      showToast({ 
        type: 'error', 
        title: '获取分区方案失败，请重启后端服务器' 
      });
    } finally {
      setLoadingPartitions(false);
    }
  };

  // 获取服务器磁盘信息
  const fetchDiskInfo = async (serviceName: string) => {
    setLoadingDiskInfo(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/hardware-disk-info`);
      
      if (response.data.success) {
        setDiskGroups(response.data.diskGroups);
        console.log('[DiskInfo] 磁盘组信息:', response.data.diskGroups);
        
        // 初始化默认的RAID配置选择（默认不选择，保持空）
        const defaultConfigs: { [diskGroupId: number]: string } = {};
        Object.keys(response.data.diskGroups).forEach(groupId => {
          defaultConfigs[parseInt(groupId)] = '';
        });
        setSelectedRaidConfigs(defaultConfigs);
      }
    } catch (error: any) {
      console.error('[DiskInfo] 获取磁盘信息失败:', error);
      showToast({ 
        type: 'error', 
        title: '获取磁盘信息失败',
        message: error.response?.data?.error || error.message 
      });
    } finally {
      setLoadingDiskInfo(false);
    }
  };

  // 获取硬件RAID配置文件
  const fetchRaidProfiles = async (serviceName: string) => {
    try {
      const response = await api.get(`/server-control/${serviceName}/hardware-raid-profiles`);
      
      if (response.data.success) {
        setRaidProfiles(response.data.profiles);
        console.log('[RAID] RAID配置文件:', response.data.profiles);
        
        // 设置RAID支持状态
        if (response.data.supported === false) {
          setRaidSupported(false);
          console.log('[RAID] 此服务器不支持硬件RAID');
        } else {
          setRaidSupported(true);
        }
      }
    } catch (error: any) {
      console.error('[RAID] 获取RAID配置失败:', error);
      // 某些服务器可能不支持硬件RAID，不显示错误提示
      console.log('[RAID] 服务器可能不支持硬件RAID或配置为空');
      setRaidProfiles([]);
      setRaidSupported(false);
    }
  };

  // 智能配置：根据磁盘组自动生成最佳方案
  const applySmartConfig = () => {
    const diskGroupsArray = Object.entries(diskGroups);
    const groupCount = diskGroupsArray.length;
    
    if (groupCount === 0) {
      showToast({ type: 'warning', title: '未检测到磁盘组' });
      return;
    }

    // 清空现有配置
    setCustomPartitions([]);
    setSelectedRaidConfigs({});
    setUseCustomStorage(true);
    setUseSoftwareRaid(false);

    if (groupCount === 1) {
      // 单磁盘组：检查盘数
      const [groupId, group] = diskGroupsArray[0];
      const diskCount = group.disks.length;
      
      if (diskCount === 1) {
        // 单盘：使用默认分区，无RAID
        showToast({ 
          type: 'success', 
          title: '已应用智能配置',
          message: '单磁盘 - 默认系统分区（无RAID）'
        });
        
        // 不设置自定义配置，使用默认
        setUseCustomStorage(false);
        
      } else {
        // 多盘（如4x2TB）：配置RAID0系统盘
        const totalCapacity = diskCount * (group.disks[0]?.capacity || 0);
        const capacityUnit = group.disks[0]?.unit || 'GB';
        
        // 系统盘分区（使用RAID0）
        const systemPartitions: CustomPartition[] = [
          {
            mountpoint: '/boot',
            filesystem: 'ext4',
            size: 1024,
            order: 1,
            type: 'primary',
            diskGroupId: parseInt(groupId),
            raid: 'raid1'  // /boot使用RAID1保证安全
          },
          {
            mountpoint: 'swap',
            filesystem: 'swap',
            size: 8192,
            order: 2,
            type: 'primary',
            diskGroupId: parseInt(groupId)
            // swap分区不设置RAID（OVH不允许swap使用RAID0）
          },
          {
            mountpoint: '/',
            filesystem: 'ext4',
            size: 0,
            order: 3,
            type: 'primary',
            diskGroupId: parseInt(groupId),
            raid: 'raid0'  // 根分区RAID0（最大容量和性能）
          }
        ];
        
        setCustomPartitions(systemPartitions);
        
        // 如果支持硬件RAID，配置硬件RAID0
        if (raidSupported) {
          setSelectedRaidConfigs({
            [parseInt(groupId)]: 'raid0'
          });
        }
        
        showToast({ 
          type: 'success', 
          title: '已应用智能配置',
          message: `${diskCount}x${group.disks[0]?.capacity}${capacityUnit} → RAID0系统盘（约${totalCapacity}${capacityUnit}可用）`
        });
      }
      
      } else if (groupCount >= 2) {
        // 多磁盘组：使用默认分区（OVH会自动智能分配）
        setUseCustomStorage(false);
        setUseSoftwareRaid(false);
        setCustomPartitions([]);
        setSelectedRaidConfigs({});
        
        showToast({ 
          type: 'success', 
          title: '已应用智能配置',
          message: `多磁盘组使用默认分区（OVH自动分配）- ${groupCount}个磁盘组`
        });
      }
    
    setShowSmartConfigDialog(false);
  };

  // Task 3: 打开重装对话框（先检查是否有正在进行的安装）
  const openReinstallDialog = async (server: ServerInfo) => {
    setSelectedServer(server);
    
    // 先检查是否有正在进行的安装
    try {
      const response = await api.get(`/server-control/${server.serviceName}/install/status`);
      
      if (response.data.success) {
        // 检查是否有安装进度
        if (response.data.hasInstallation === false) {
          // 没有正在进行的安装，继续打开重装对话框
          // 静默处理，不显示任何提示
        } else if (response.data.status) {
          const progress = response.data.status;
          
          // 如果有正在进行的安装（未完成且无错误）
          if (!progress.allDone && !progress.hasError && progress.totalSteps > 0) {
            // 设置初始进度数据
            setInstallProgress(progress);
            
            // 启动轮询（会自动显示进度窗口）
            startInstallProgressMonitoring();
            
            showToast({ 
              type: 'info', 
              title: `检测到正在进行的安装 (${progress.progressPercentage}%)` 
            });
            
            return; // 不打开重装对话框
          }
        }
      }
    } catch (error: any) {
      // 真实错误（如网络错误），静默处理
      console.log('检查安装进度时出错，继续打开重装对话框');
    }
    
    // 没有正在进行的安装，正常打开重装对话框
    setSelectedTemplate("");
    setTemplateSearchQuery(""); // 重置搜索框
    setCustomHostname("");
    setPartitionSchemes([]);
    setSelectedScheme("");
    setShowPartitionDetails(false);
    setUseCustomStorage(false);
    setSelectedRaidConfigs({});
    setCustomPartitions([]);
    setRaidSupported(true); // 重置RAID支持状态，将在API调用后更新
    setUseSoftwareRaid(false); // 重置软RAID状态
    setSoftwareRaidLevel('raid1'); // 重置为默认RAID 1
    setShowReinstallDialog(true);
    
    // 并行加载OS模板和磁盘信息
    await Promise.all([
      fetchOSTemplates(server.serviceName),
      fetchDiskInfo(server.serviceName),
      fetchRaidProfiles(server.serviceName)
    ]);
  };

  // Task 3: 重装系统
  const handleReinstall = async () => {
    if (!selectedServer || !selectedTemplate) {
      showToast({ type: 'error', title: '请选择系统模板' });
      return;
    }

    const confirmed = await showConfirm({
      title: '确定要重装系统吗？',
      message: `服务器: ${selectedServer.name}\n此操作将清空所有数据！`,
      confirmText: '确认重装',
      cancelText: '取消'
    });

    if (!confirmed) return;

    setIsProcessing(true);
    try {
      const installData: any = {
        templateName: selectedTemplate,
        customHostname: customHostname || undefined
      };
      
      // 如果用户启用了自定义存储配置或软RAID
      if (useCustomStorage || useSoftwareRaid) {
        // 按磁盘组分组配置
        const diskGroupConfigs: Map<number, any> = new Map();
        
        // 处理自定义分区
        let partitions = customPartitions;
        
        // 如果启用了软RAID但没有自定义分区，生成默认软RAID分区
        if (useSoftwareRaid && customPartitions.length === 0) {
          partitions = [
            {
              mountpoint: '/',
              filesystem: 'ext4',
              size: 0,
              order: 1,
              type: 'primary',
              raid: softwareRaidLevel,
              diskGroupId: 0
            }
          ];
          console.log('[Install] 使用默认软RAID分区:', partitions);
        }
        
        // 按磁盘组分组分区
        partitions.forEach(partition => {
          const groupId = partition.diskGroupId !== undefined ? partition.diskGroupId : 0;
          
          if (!diskGroupConfigs.has(groupId)) {
            diskGroupConfigs.set(groupId, {
              diskGroupId: groupId,
              partitioning: {
                layout: []
              }
            });
          }
          
          const config = diskGroupConfigs.get(groupId);
          
          // 转换分区格式为OVH API格式
          const ovhPartition: any = {
            mountPoint: partition.mountpoint,
            fileSystem: partition.filesystem,
            size: partition.size || 0
          };
          
          // 添加软RAID级别（如果有）
          if (partition.raid) {
            // 将 'raid0' 转换为数字 0
            const raidMatch = partition.raid.match(/raid(\d+)/);
            if (raidMatch) {
              ovhPartition.raidLevel = parseInt(raidMatch[1]);
            }
          }
          
          config.partitioning.layout.push(ovhPartition);
        });
        
        // 添加硬件RAID配置
        Object.entries(selectedRaidConfigs).forEach(([diskGroupId, raidMode]) => {
          if (raidMode) {
            const groupId = parseInt(diskGroupId);
            
            if (!diskGroupConfigs.has(groupId)) {
              diskGroupConfigs.set(groupId, {
                diskGroupId: groupId
              });
            }
            
            const config = diskGroupConfigs.get(groupId);
            
            // OVH硬件RAID格式
            if (!config.hardwareRaid) {
              config.hardwareRaid = [];
            }
            
            // 将 'raid0' 转换为 '0'
            const raidLevel = raidMode.replace('raid', '');
            config.hardwareRaid.push({
              disks: diskGroups[groupId]?.disks?.map((d: any) => d.number) || [],
              mode: raidLevel,
              name: `raid${raidLevel}`,
              step: 1
            });
          }
        });
        
        // 转换为数组
        const storageArray = Array.from(diskGroupConfigs.values());
        
        if (storageArray.length > 0) {
          installData.storageConfig = storageArray;
          console.log('[Install] 使用自定义存储配置:', JSON.stringify(storageArray, null, 2));
        }
      } else if (selectedScheme) {
        // 如果用户选择了分区方案（旧方式），传递给后端
        installData.partitionSchemeName = selectedScheme;
        console.log('[Install] 使用自定义分区方案:', selectedScheme);
      } else {
        console.log('[Install] 使用默认分区');
      }
      
      console.log('[Install] 安装数据:', installData);
      const response = await api.post(`/server-control/${selectedServer.serviceName}/install`, installData);

      if (response.data.success) {
        showToast({ type: 'success', title: '系统重装请求已发送' });
        setShowReinstallDialog(false);
        
        // 启动安装进度监控
        startInstallProgressMonitoring();
      }
    } catch (error: any) {
      console.error('重装失败:', error);
      showToast({ type: 'error', title: '重装系统失败', message: error.response?.data?.error || error.message });
    } finally {
      setIsProcessing(false);
    }
  };

  // 安装进度：获取安装进度
  const fetchInstallProgress = async () => {
    if (!selectedServer) return;
    
    try {
      const response = await api.get(`/server-control/${selectedServer.serviceName}/install/status`);
      
      console.log('[fetchInstallProgress] 响应数据:', response.data);
      
      if (response.data.success) {
        // 检查是否有安装进度
        if (response.data.hasInstallation === false) {
          console.log('[fetchInstallProgress] 检测到安装完成, installProgress:', installProgress);
          
          // 没有安装任务了，说明安装完成
          stopInstallProgressMonitoring();
          
          // 判断：如果之前有进度数据，说明安装刚完成
          // 使用ref获取最新状态，而不是闭包中的旧状态
          const latestProgress = installProgressRef.current;
          console.log('[fetchInstallProgress] 最新进度状态:', latestProgress);
          
          // 检查是否已经显示过Toast（防止重复显示）
          if (!completionToastShownRef.current && latestProgress && latestProgress.progressPercentage > 0) {
            console.log('[fetchInstallProgress] 显示安装完成提示（仅一次）');
            completionToastShownRef.current = true; // 标记已显示
            installProgressRef.current = null; // 立即清空ref，防止其他请求重复触发
            
            showToast({ 
              type: 'success', 
              title: '✅ 系统安装完成！',
              message: '服务器已成功安装系统'
            });
            
            // 设置完成状态，显示完成页面
            setInstallCompleted(true);
            setAutoCloseCountdown(8); // 重置倒计时
            
            // 启动倒计时（每秒减1）
            let countdown = 8;
            const countdownInterval = setInterval(() => {
              countdown--;
              setAutoCloseCountdown(countdown);
              
              if (countdown <= 0) {
                clearInterval(countdownInterval);
                console.log('[fetchInstallProgress] 倒计时结束，自动关闭进度模态框');
                setShowInstallProgress(false);
                setInstallProgress(null);
                setInstallCompleted(false);
              }
            }, 1000);
          } else if (completionToastShownRef.current) {
            console.log('[fetchInstallProgress] Toast已显示过，跳过');
          } else {
            console.log('[fetchInstallProgress] 没有之前的进度数据，不显示提示');
          }
          
          return;
        }
        
        // 有安装进度数据
        if (response.data.status) {
          const progress = response.data.status;
          setInstallProgress(progress);
          installProgressRef.current = progress; // 同步更新ref
          
          // 如果安装完成或出错，停止轮询
          if (progress.allDone || progress.hasError) {
            stopInstallProgressMonitoring();
            
            if (progress.allDone) {
              showToast({ type: 'success', title: '系统安装完成！' });
            } else if (progress.hasError) {
              showToast({ type: 'error', title: '系统安装出错' });
            }
          } else {
            // 根据进度动态调整轮询间隔
            adjustPollingInterval(progress.progressPercentage);
          }
        }
      }
    } catch (error: any) {
      // 网络错误或500错误
      if (error.response?.status === 500) {
        stopInstallProgressMonitoring();
      }
      
      // 记录错误日志
      console.error('获取安装进度失败:', error);
    }
  };

  // 动态调整轮询间隔
  const adjustPollingInterval = (progressPercentage: number) => {
    if (!installPollingInterval) return;
    
    let newInterval = 5000; // 默认5秒
    
    if (progressPercentage >= 90) {
      newInterval = 1000; // 90%以上：1秒（最快）
      console.log('[轮询] 进度>=90%，切换到1秒轮询');
    } else if (progressPercentage >= 80) {
      newInterval = 2000; // 80-89%：2秒（加快）
      console.log('[轮询] 进度>=80%，切换到2秒轮询');
    } else if (progressPercentage >= 70) {
      newInterval = 3000; // 70-79%：3秒
    }
    
    // 如果间隔需要改变，重新设置定时器
    const currentInterval = installPollingInterval as any;
    if (currentInterval._idleTimeout !== newInterval) {
      clearInterval(installPollingInterval!);
      const interval = setInterval(() => {
        fetchInstallProgress();
      }, newInterval);
      setInstallPollingInterval(interval);
    }
  };

  // 安装进度：启动轮询
  const startInstallProgressMonitoring = () => {
    // 先清除之前的轮询
    if (installPollingInterval) {
      clearInterval(installPollingInterval);
    }
    
    // 显示进度模态框
    setShowInstallProgress(true);
    
    // 重置完成提示标志（开始新的安装）
    completionToastShownRef.current = false;
    setInstallCompleted(false);
    setAutoCloseCountdown(8);
    
    // 如果没有现有进度数据，清空（避免闪烁）
    // 如果有现有数据，保留它（用于恢复进度显示）
    if (!installProgress) {
      setInstallProgress(null);
      installProgressRef.current = null;
    } else {
      installProgressRef.current = installProgress;
    }
    
    // 立即获取一次进度
    fetchInstallProgress();
    
    // 初始轮询间隔：5秒（会根据进度动态调整）
    const interval = setInterval(() => {
      fetchInstallProgress();
    }, 5000);
    
    setInstallPollingInterval(interval);
  };

  // 安装进度：停止轮询
  const stopInstallProgressMonitoring = () => {
    if (installPollingInterval) {
      clearInterval(installPollingInterval);
      setInstallPollingInterval(null);
    }
    // 清空ref状态（下次安装时重新开始）
    // 注意：不清空installProgress state，让窗口保持显示最后的进度
  };

  // 安装进度：手动关闭进度模态框
  const closeInstallProgress = () => {
    stopInstallProgressMonitoring();
    setShowInstallProgress(false);
    setInstallProgress(null);
    setInstallCompleted(false);
    setAutoCloseCountdown(8);
    installProgressRef.current = null; // 清空ref
    completionToastShownRef.current = false; // 重置标志
  };

  // 清理：组件卸载时停止轮询
  useEffect(() => {
    return () => {
      stopInstallProgressMonitoring();
    };
  }, [installPollingInterval]);

  // Task 4: 获取服务器任务
  const fetchServerTasks = async (serviceName: string) => {
    try {
      const response = await api.get(`/server-control/${serviceName}/tasks`);
      if (response.data.success) {
        setServerTasks(response.data.tasks);
      }
    } catch (error: any) {
      console.error('获取任务失败:', error);
      showToast({ type: 'error', title: '获取任务列表失败' });
    }
  };

  // Task 4: 打开任务对话框
  const openTasksDialog = async (server: ServerInfo) => {
    setSelectedServer(server);
    setShowTasksDialog(true);
    await fetchServerTasks(server.serviceName);
  };

  const openTimeslots = async (task: ServerTask) => {
    if (!selectedServer) return;
    setSelectedTaskForTimeslots(task);
    // 默认查询未来14天
    const now = new Date();
    const end = new Date(now.getTime() + 14 * 24 * 60 * 60 * 1000);
    const startIso = now.toISOString();
    const endIso = end.toISOString();
    setPeriodStart(startIso);
    setPeriodEnd(endIso);
    setShowTimeslotsDialog(true);
    await fetchAvailableTimeslots(task.taskId, startIso, endIso);
  };

  const fetchAvailableTimeslots = async (taskId: number, start: string, end: string) => {
    if (!selectedServer) return;
    setLoadingTimeslots(true);
    try {
      const response = await api.get(`/server-control/${selectedServer.serviceName}/tasks/${taskId}/available-timeslots`, {
        params: { periodStart: start, periodEnd: end }
      });
      if (response.data.success) {
        setTimeslots(response.data.timeslots || []);
        if (response.data.scheduleNotRequired) {
          showToast({ type: 'info', title: '该任务无需预约' });
        } else if ((response.data.timeslots || []).length === 0) {
          showToast({ type: 'info', title: '无可用时间段' });
        }
      } else {
        setTimeslots([]);
      }
    } catch (error: any) {
      console.error('获取可用时间段失败:', error);
      const msg = error?.response?.data?.error || '获取可用时间段失败';
      showToast({ type: 'error', title: msg });
      setTimeslots([]);
    } finally {
      setLoadingTimeslots(false);
    }
  };

  // Task 5: 获取监控状态
  const fetchMonitoring = async (serviceName: string) => {
    try {
      const response = await api.get(`/server-control/${serviceName}/monitoring`);
      if (response.data.success) {
        setMonitoring(response.data.monitoring);
      }
    } catch (error: any) {
      console.error('获取监控状态失败:', error);
    }
  };

  // Task 5: 切换监控
  const toggleMonitoring = async () => {
    if (!selectedServer) return;
    setLoadingMonitoring(true);
    try {
      await api.put(`/server-control/${selectedServer.serviceName}/monitoring`, { 
        enabled: !monitoring 
      });
      setMonitoring(!monitoring);
      showToast({ 
        type: 'success', 
        title: `监控已${!monitoring ? '开启' : '关闭'}` 
      });
    } catch (error) {
      showToast({ type: 'error', title: '操作失败' });
    } finally {
      setLoadingMonitoring(false);
    }
  };

  // Task 6: 获取硬件信息
  const fetchHardware = async (serviceName: string) => {
    setLoadingHardware(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/hardware`);
      if (response.data.success) {
        setHardware(response.data.hardware);
      }
    } catch (error: any) {
      console.error('获取硬件信息失败:', error);
    } finally {
      setLoadingHardware(false);
    }
  };

  // Task 7: 获取IP列表
  const fetchIPs = async (serviceName: string) => {
    setLoadingIPs(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/ips`);
      if (response.data.success) {
        setIps(response.data.ips || []);
      }
    } catch (error: any) {
      console.error('获取IP列表失败:', error);
    } finally {
      setLoadingIPs(false);
    }
  };

  // Task 8: 获取服务信息
  const fetchServiceInfo = async (serviceName: string) => {
    setLoadingService(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/serviceinfo`);
      if (response.data.success) {
        setServiceInfo(response.data.serviceInfo);
      }
    } catch (error: any) {
      console.error('获取服务信息失败:', error);
    } finally {
      setLoadingService(false);
    }
  };

  // 获取网络规格
  const fetchNetworkSpecs = async (serviceName: string) => {
    setLoadingNetworkSpecs(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/network-specs`);
      if (response.data.success) {
        setNetworkSpecs(response.data.network);
        showToast({ 
          type: 'success', 
          title: '网络规格已加载' 
        });
      } else {
        showToast({ 
          type: 'error', 
          title: '获取网络规格失败',
          message: response.data.error || '未知错误'
        });
      }
    } catch (error: any) {
      console.error('获取网络规格失败:', error);
      showToast({ 
        type: 'error', 
        title: '获取网络规格失败',
        message: error.message || '网络错误'
      });
    } finally {
      setLoadingNetworkSpecs(false);
    }
  };

  // 打开网络规格对话框
  const handleOpenNetworkSpecs = (server: ServerInfo) => {
    setSelectedServer(server);
    setShowNetworkSpecsDialog(true);
    fetchNetworkSpecs(server.serviceName);
  };

  // 打开高级功能对话框
  const handleOpenAdvanced = (server: ServerInfo, tab: string = 'burst') => {
    setSelectedServer(server);
    setAdvancedTab(tab as any);
    setShowAdvancedDialog(true);
    // 根据标签加载相应数据
    if (tab === 'burst') fetchBurst(server.serviceName);
    else if (tab === 'firewall') fetchFirewall(server.serviceName);
    else if (tab === 'backup') fetchBackupFtp(server.serviceName);
    else if (tab === 'dns') fetchSecondaryDns(server.serviceName);
    else if (tab === 'vmac') fetchVirtualMacs(server.serviceName);
    else if (tab === 'vrack') fetchVracks(server.serviceName);
    else if (tab === 'orderable') fetchOrderable(server.serviceName);
    else if (tab === 'options') fetchServerOptions(server.serviceName);
    else if (tab === 'ip') fetchIpSpecs(server.serviceName);
  };

  // 获取Burst突发带宽
  const fetchBurst = async (serviceName: string) => {
    setLoadingBurst(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/burst`);
      if (response.data.success) {
        setBurst(response.data.burst);
      } else if (response.data.notAvailable) {
        setBurst({ notAvailable: true, error: response.data.error });
      }
    } catch (error: any) {
      if (error?.response?.status === 404 && error?.response?.data?.notAvailable) {
        // 服务器不支持此功能，这是正常情况，不记录错误
        setBurst({ notAvailable: true, error: error.response.data.error });
      } else {
        // 真正的错误才记录到控制台
        console.error('获取突发带宽失败:', error);
        setBurst(null);
      }
    } finally {
      setLoadingBurst(false);
    }
  };

  // 更新Burst状态
  const updateBurstStatus = async (status: string) => {
    if (!selectedServer) return;
    try {
      const response = await api.put(`/server-control/${selectedServer.serviceName}/burst`, { status });
      if (response.data.success) {
        showToast({ type: 'success', title: '突发带宽状态已更新' });
        fetchBurst(selectedServer.serviceName);
      }
    } catch (error: any) {
      showToast({ type: 'error', title: '更新失败', message: error.message });
    }
  };

  // 获取Firewall防火墙
  const fetchFirewall = async (serviceName: string) => {
    setLoadingFirewall(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/firewall`);
      if (response.data.success) {
        setFirewall(response.data.firewall);
      } else if (response.data.notAvailable) {
        setFirewall({ notAvailable: true, error: response.data.error });
      }
    } catch (error: any) {
      if (error?.response?.status === 404 && error?.response?.data?.notAvailable) {
        // 服务器不支持此功能，这是正常情况，不记录错误
        setFirewall({ notAvailable: true, error: error.response.data.error });
      } else {
        // 真正的错误才记录到控制台
        console.error('获取防火墙失败:', error);
        setFirewall(null);
      }
    } finally {
      setLoadingFirewall(false);
    }
  };

  // 更新Firewall状态
  const updateFirewallStatus = async (enabled: boolean) => {
    if (!selectedServer) return;
    try {
      const response = await api.put(`/server-control/${selectedServer.serviceName}/firewall`, { enabled });
      if (response.data.success) {
        showToast({ type: 'success', title: `防火墙已${enabled ? '启用' : '禁用'}` });
        fetchFirewall(selectedServer.serviceName);
      }
    } catch (error: any) {
      showToast({ type: 'error', title: '更新失败', message: error.message });
    }
  };

  // 获取Backup FTP
  const fetchBackupFtp = async (serviceName: string) => {
    setLoadingBackupFtp(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/backup-ftp`);
      if (response.data.success) {
        setBackupFtp(response.data.backupFtp);
        // 获取访问列表（如果备份FTP已激活）
        try {
          const accessResp = await api.get(`/server-control/${serviceName}/backup-ftp/access`);
          if (accessResp.data.success) {
            setBackupFtpAccess(accessResp.data.accessList || []);
          }
        } catch (accessError: any) {
          // 访问列表获取失败不影响主功能
          setBackupFtpAccess([]);
        }
      } else if (response.data.notAvailable) {
        setBackupFtp({ notAvailable: true, error: response.data.error });
      }
    } catch (error: any) {
      if (error?.response?.status === 404 && error?.response?.data?.notAvailable) {
        // 服务器不支持此功能，这是正常情况
        setBackupFtp({ notAvailable: true, error: error.response.data.error });
      } else if (error?.response?.status === 400 && error?.response?.data?.notAvailable) {
        // 服务器无法使用此服务，这是正常情况
        setBackupFtp({ notAvailable: true, error: error.response.data.error });
      } else if (error?.response?.status === 404 && error?.response?.data?.notActivated) {
        // 备份FTP未激活，这也是正常情况
        setBackupFtp({ notActivated: true });
      } else {
        // 真正的错误才记录到控制台
        console.error('获取备份FTP失败:', error);
        setBackupFtp(null);
      }
    } finally {
      setLoadingBackupFtp(false);
    }
  };

  // 获取Secondary DNS
  const fetchSecondaryDns = async (serviceName: string) => {
    setLoadingSecondaryDns(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/secondary-dns`);
      if (response.data.success) {
        setSecondaryDns(response.data.domains || []);
      }
    } catch (error: any) {
      console.error('获取从DNS失败:', error);
    } finally {
      setLoadingSecondaryDns(false);
    }
  };

  // 获取Virtual MAC
  const fetchVirtualMacs = async (serviceName: string) => {
    setLoadingVirtualMacs(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/virtual-mac`);
      if (response.data.success) {
        setVirtualMacs(response.data.virtualMacs || []);
      }
    } catch (error: any) {
      console.error('获取虚拟MAC失败:', error);
    } finally {
      setLoadingVirtualMacs(false);
    }
  };

  // 获取vRack列表
  const fetchVracks = async (serviceName: string) => {
    setLoadingVracks(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/vrack`);
      if (response.data.success) {
        setVracks(response.data.vracks || []);
      }
    } catch (error: any) {
      console.error('获取vRack列表失败:', error);
    } finally {
      setLoadingVracks(false);
    }
  };

  // 获取可订购服务
  const fetchOrderable = async (serviceName: string) => {
    setLoadingOrderable(true);
    try {
      const [bandwidthResp, trafficResp, ipResp] = await Promise.all([
        api.get(`/server-control/${serviceName}/orderable/bandwidth`).catch(() => ({ data: { success: false } })),
        api.get(`/server-control/${serviceName}/orderable/traffic`).catch(() => ({ data: { success: false } })),
        api.get(`/server-control/${serviceName}/orderable/ip`).catch(() => ({ data: { success: false } }))
      ]);
      if (bandwidthResp.data.success) setOrderableBandwidth(bandwidthResp.data.orderable);
      if (trafficResp.data.success) setOrderableTraffic(trafficResp.data.orderable);
      if (ipResp.data.success) setOrderableIp(ipResp.data.orderable);
    } catch (error: any) {
      console.error('获取可订购服务失败:', error);
    } finally {
      setLoadingOrderable(false);
    }
  };

  // 获取服务器选项
  const fetchServerOptions = async (serviceName: string) => {
    setLoadingOptions(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/options`);
      if (response.data.success) {
        setServerOptions(response.data.options || []);
      }
    } catch (error: any) {
      console.error('获取服务器选项失败:', error);
    } finally {
      setLoadingOptions(false);
    }
  };

  // 获取IP规格
  const fetchIpSpecs = async (serviceName: string) => {
    setLoadingIpSpecs(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/ip-specs`);
      if (response.data.success) {
        setIpSpecs(response.data.ipSpecs);
      }
    } catch (error: any) {
      console.error('获取IP规格失败:', error);
    } finally {
      setLoadingIpSpecs(false);
    }
  };

  // 变更联系人
  const handleChangeContact = async () => {
    if (!selectedServer) return;
    
    // 验证至少填写一个联系人
    if (!contactAdmin && !contactTech && !contactBilling) {
      showToast({ 
        type: 'error', 
        title: '至少需要指定一个联系人' 
      });
      return;
    }

    const confirmed = await showConfirm({
      title: '确认变更联系人',
      message: `将为服务器 ${selectedServer.name} (${selectedServer.serviceName}) 变更联系人信息。此操作可能需要验证。`,
      confirmText: '确认变更',
      cancelText: '取消'
    });

    if (!confirmed) return;

    setLoadingChangeContact(true);
    try {
      const response = await api.post(`/server-control/${selectedServer.serviceName}/change-contact`, {
        contactAdmin: contactAdmin || undefined,
        contactTech: contactTech || undefined,
        contactBilling: contactBilling || undefined
      });

      if (response.data.success) {
        showToast({ 
          type: 'success', 
          title: '联系人变更请求已提交',
          message: response.data.taskId ? `任务ID: ${response.data.taskId}` : undefined
        });
        
        // 清空表单并关闭对话框
        setContactAdmin('');
        setContactTech('');
        setContactBilling('');
        setShowChangeContactDialog(false);
      }
    } catch (error: any) {
      console.error('变更联系人失败:', error);
      showToast({ 
        type: 'error', 
        title: '变更联系人失败',
        message: error.response?.data?.error || error.message 
      });
    } finally {
      setLoadingChangeContact(false);
    }
  };

  // 打开变更联系人对话框
  const openChangeContactDialog = () => {
    if (!selectedServer) return;
    
    // 从服务信息中预填联系人（如果有）
    // 这里可以根据实际API返回的数据进行调整
    setContactAdmin('');
    setContactTech('');
    setContactBilling('');
    setShowChangeContactDialog(true);
    // 同时加载联系人变更请求列表
    fetchContactChangeRequests();
  };

  // 获取联系人变更请求列表
  const fetchContactChangeRequests = async () => {
    setLoadingContactRequests(true);
    try {
      const response = await api.get('/ovh/contact-change-requests');
      if (response.data.status === 'success') {
        setContactChangeRequests(response.data.data || []);
      }
    } catch (error: any) {
      console.error('获取联系人变更请求列表失败:', error);
      showToast({
        type: 'error',
        title: '获取联系人变更请求列表失败',
        message: error.response?.data?.message || error.message
      });
    } finally {
      setLoadingContactRequests(false);
    }
  };

  // 打开 token 输入对话框
  const openTokenDialog = (request: ContactChangeRequest, action: 'accept' | 'refuse') => {
    setSelectedRequest(request);
    setTokenAction(action);
    setToken('');
    setShowTokenDialog(true);
  };

  // 处理接受/拒绝请求
  const handleTokenAction = async () => {
    if (!selectedRequest || !tokenAction || !token) return;
    
    setLoadingTokenAction(true);
    try {
      const endpoint = tokenAction === 'accept' 
        ? `/ovh/contact-change-requests/${selectedRequest.id}/accept`
        : `/ovh/contact-change-requests/${selectedRequest.id}/refuse`;
      
      const response = await api.post(endpoint, { token });
      
      if (response.data.status === 'success') {
        showToast({
          type: 'success',
          title: tokenAction === 'accept' ? '请求已接受' : '请求已拒绝',
          message: response.data.message
        });
        setShowTokenDialog(false);
        setToken('');
        setTokenAction(null);
        setSelectedRequest(null);
        // 刷新请求列表
        fetchContactChangeRequests();
      }
    } catch (error: any) {
      console.error(`${tokenAction === 'accept' ? '接受' : '拒绝'}请求失败:`, error);
      showToast({
        type: 'error',
        title: `${tokenAction === 'accept' ? '接受' : '拒绝'}请求失败`,
        message: error.response?.data?.message || error.message
      });
    } finally {
      setLoadingTokenAction(false);
    }
  };

  // 重发邮件
  const handleResendEmail = async (request: ContactChangeRequest) => {
    try {
      const response = await api.post(`/ovh/contact-change-requests/${request.id}/resend-email`);
      
      if (response.data.status === 'success') {
        showToast({
          type: 'success',
          title: '邮件已重发',
          message: response.data.message
        });
      }
    } catch (error: any) {
      console.error('重发邮件失败:', error);
      showToast({
        type: 'error',
        title: '重发邮件失败',
        message: error.response?.data?.message || error.message
      });
    }
  };


  // IPMI控制台
  const openIPMIConsole = async () => {
    if (!selectedServer) return;
    try {
      console.log('=== 开始获取IPMI ===');
      console.log('服务器:', selectedServer.serviceName);
      
      // 启动倒计时
      setIpmiLoading(true);
      setIpmiCountdown(20);
      
      // 倒计时计时器
      const countdownInterval = setInterval(() => {
        setIpmiCountdown(prev => {
          if (prev <= 1) {
            clearInterval(countdownInterval);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
      
      const response = await api.get(`/server-control/${selectedServer.serviceName}/console`);
      
      // 清除倒计时
      clearInterval(countdownInterval);
      setIpmiLoading(false);
      console.log('收到响应:', response);
      console.log('响应数据:', response.data);
      
      if (response.data.success && response.data.console) {
        console.log('✅ 响应成功');
        const value = response.data.console.value;
        const accessType = response.data.accessType;
        
        console.log('accessType:', accessType);
        console.log('value length:', value?.length);
        
        if (!value) {
          console.error('❌ value为空');
          showToast({ type: 'error', title: '无法获取控制台访问' });
          return;
        }

        // 判断访问类型
        console.log('IPMI访问类型:', accessType);
        console.log('IPMI访问值前100字符:', value.substring(0, 100));
        
        if (accessType === 'kvmipJnlp') {
          // JNLP文件 - 下载并提示用户
          const blob = new Blob([value], { type: 'application/x-java-jnlp-file' });
          const url = window.URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `ipmi-${selectedServer.serviceName}.jnlp`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          window.URL.revokeObjectURL(url);
          
          showToast({ 
            type: 'success', 
            title: 'JNLP文件已下载，请用Java打开'
          });
        } else if (accessType === 'kvmipHtml5URL' || accessType === 'serialOverLanURL') {
          // HTML5或Serial URL - 显示链接模态框让用户点击
          console.log('显示IPMI链接:', value);
          setIpmiLink(value);
          setShowIpmiLinkDialog(true);
          showToast({ type: 'success', title: 'IPMI访问已就绪' });
        } else {
          console.error('❌ 未知的访问类型:', accessType);
          setIpmiLoading(false);
          showToast({ type: 'error', title: '不支持的访问类型: ' + accessType });
        }
      } else {
        console.error('❌ 响应失败或无console数据');
        console.log('success:', response.data.success);
        console.log('console:', response.data.console);
        setIpmiLoading(false);
        showToast({ type: 'error', title: '无效的响应数据' });
      }
    } catch (error: any) {
      console.error('❌ 打开IPMI控制台失败:', error);
      console.error('错误详情:', error.response?.data);
      setIpmiLoading(false);
      showToast({ type: 'error', title: '打开IPMI控制台失败' });
    }
  };

  // Task 10: 获取启动模式列表
  const fetchBootModes = async () => {
    if (!selectedServer) return;
    setLoadingBootModes(true);
    try {
      const response = await api.get(`/server-control/${selectedServer.serviceName}/boot-mode`);
      if (response.data.success) {
        setBootModes(response.data.bootModes);
        setShowBootModeDialog(true);
      }
    } catch (error: any) {
      console.error('获取启动模式失败:', error);
      showToast({ type: 'error', title: '获取启动模式失败' });
    } finally {
      setLoadingBootModes(false);
    }
  };

  // Task 10: 切换启动模式
  const changeBootMode = async (bootId: number) => {
    if (!selectedServer) return;
    
    const confirmed = await showConfirm({
      title: '确定切换启动模式？',
      message: '切换后将自动重启服务器',
      confirmText: '确认切换并重启',
      cancelText: '取消'
    });

    if (!confirmed) return;

    try {
      // 1. 切换启动模式
      const response = await api.put(`/server-control/${selectedServer.serviceName}/boot-mode`, {
        bootId
      });
      
      if (response.data.success) {
        showToast({ type: 'success', title: '启动模式已切换' });
        setShowBootModeDialog(false);
        
        // 2. 自动重启服务器
        showToast({ type: 'info', title: '正在重启服务器...' });
        const rebootResponse = await api.post(`/server-control/${selectedServer.serviceName}/reboot`);
        
        if (rebootResponse.data.success) {
          showToast({ type: 'success', title: '服务器已重启，启动模式生效' });
        } else {
          showToast({ type: 'warning', title: '启动模式已切换，但重启失败，请手动重启' });
        }
      }
    } catch (error: any) {
      console.error('切换启动模式失败:', error);
      showToast({ type: 'error', title: '切换启动模式失败' });
    }
  };

  // 维护记录：获取列表
  const fetchInterventions = async (serviceName: string) => {
    setLoadingInterventions(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/interventions`);
      if (response.data.success) {
        setInterventions(response.data.interventions || []);
      }
    } catch (error: any) {
      console.error('获取维护记录失败:', error);
      setInterventions([]);
    } finally {
      setLoadingInterventions(false);
    }
  };

  // 计划维护：获取列表
  const fetchPlannedInterventions = async (serviceName: string) => {
    setLoadingPlannedInterventions(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/planned-interventions`);
      if (response.data.success) {
        setPlannedInterventions(response.data.plannedInterventions || []);
      }
    } catch (error: any) {
      console.error('获取计划维护失败:', error);
      setPlannedInterventions([]);
    } finally {
      setLoadingPlannedInterventions(false);
    }
  };

  // 网络接口：获取物理网卡列表
  const fetchNetworkInterfaces = async (serviceName: string) => {
    setLoadingNetworkInterfaces(true);
    try {
      const response = await api.get(`/server-control/${serviceName}/network-interfaces`);
      if (response.data.success) {
        setNetworkInterfaces(response.data.interfaces || []);
      }
    } catch (error: any) {
      console.error('获取物理网卡失败:', error);
      setNetworkInterfaces([]);
    } finally {
      setLoadingNetworkInterfaces(false);
    }
  };

  // MRTG流量监控：获取流量数据（同时获取上传和下载）
  const fetchMrtgData = async (serviceName: string, period?: string) => {
    setLoadingMrtg(true);
    try {
      const currentPeriod = period || mrtgPeriod;
      
      // 同时获取上传和下载数据
      const [downloadResponse, uploadResponse] = await Promise.all([
        api.get(`/server-control/${serviceName}/mrtg?period=${currentPeriod}&type=traffic:download`),
        api.get(`/server-control/${serviceName}/mrtg?period=${currentPeriod}&type=traffic:upload`)
      ]);
      
      if (downloadResponse.data.success && uploadResponse.data.success) {
        // 合并数据
        setMrtgData({
          period: currentPeriod,
          download: downloadResponse.data,
          upload: uploadResponse.data
        });
      }
    } catch (error: any) {
      console.error('获取MRTG数据失败:', error);
      setMrtgData(null);
    } finally {
      setLoadingMrtg(false);
    }
  };

  // 硬件更换：提交请求
  const handleHardwareReplace = async () => {
    if (!selectedServer || !hardwareReplaceType) return;

    const componentNames: Record<string, string> = {
      hardDiskDrive: '硬盘',
      memory: '内存',
      cooling: '散热器'
    };

    const confirmed = await showConfirm({
      title: `申请更换${componentNames[hardwareReplaceType]}？`,
      message: `服务器: ${selectedServer.name}\n此操作将创建硬件更换工单`,
      confirmText: '确认申请',
      cancelText: '取消'
    });

    if (!confirmed) return;

    setIsProcessing(true);
    try {
      const requestData: any = {
        componentType: hardwareReplaceType,
        comment: hardwareReplaceComment || undefined  // 如果用户没填写，让后端使用默认英文comment
      };

      // memory 和 cooling 需要 details 参数
      if (hardwareReplaceType === 'memory' || hardwareReplaceType === 'cooling') {
        requestData.details = hardwareReplaceDetails || undefined;  // 如果用户没填写，让后端使用默认英文details
      }

      const response = await api.post(
        `/server-control/${selectedServer.serviceName}/hardware/replace`,
        requestData
      );

      if (response.data.success) {
        showToast({ 
          type: 'success', 
          title: '硬件更换请求已提交成功' 
        });
        setShowHardwareReplaceDialog(false);
      }
    } catch (error: any) {
      console.error('硬件更换请求失败:', error);
      
      // 检查是否是"待处理"错误
      if (error.response?.data?.isPending) {
        showToast({ 
          type: 'warning', 
          title: error.response.data.error || '已有待处理的硬件更换请求' 
        });
      } else {
        showToast({ 
          type: 'error', 
          title: '硬件更换请求失败',
          message: error.response?.data?.error || '未知错误'
        });
      }
    } finally {
      setIsProcessing(false);
    }
  };

  useEffect(() => {
    fetchServers();
  }, []);

  // Task 5-8: 当选择服务器时加载数据
  useEffect(() => {
    if (selectedServer) {
      fetchMonitoring(selectedServer.serviceName);
      fetchHardware(selectedServer.serviceName);
      fetchIPs(selectedServer.serviceName);
      fetchServiceInfo(selectedServer.serviceName);
      fetchInterventions(selectedServer.serviceName);
      fetchPlannedInterventions(selectedServer.serviceName);
      fetchNetworkInterfaces(selectedServer.serviceName);
      fetchMrtgData(selectedServer.serviceName);  // 初始加载MRTG数据
    }
  }, [selectedServer]);

  // MRTG: 当时间周期变化时重新加载数据
  useEffect(() => {
    if (selectedServer) {
      fetchMrtgData(selectedServer.serviceName, mrtgPeriod);
    }
  }, [mrtgPeriod]);

  return (
    <div className="space-y-6">
      <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }}>
        {/* 页面标题 */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-2 sm:gap-3">
            <div className="p-1.5 sm:p-2 bg-cyber-accent/10 rounded-lg border border-cyber-accent/30">
              <Server className="text-cyber-accent" size={isMobile ? 20 : 24} />
            </div>
            <div>
              <h1 className={`${isMobile ? 'text-2xl' : 'text-3xl'} font-bold cyber-glow-text`}>服务器控制中心</h1>
              <p className="text-cyber-muted text-xs sm:text-sm">管理您的 OVH 独立服务器</p>
            </div>
          </div>
          <button
            onClick={fetchServers}
            disabled={isLoading}
            className="px-3 sm:px-4 py-1.5 sm:py-2 bg-cyber-accent text-white rounded-lg hover:bg-cyber-accent/80 disabled:opacity-50 flex items-center gap-2 transition-all shadow-neon-sm text-xs sm:text-sm">
            <RefreshCw className={`w-3 h-3 sm:w-4 sm:h-4 ${isLoading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>

        {/* 服务器选择器 */}
        {isLoading ? (
          <div className="cyber-card">
            <div className="flex items-center justify-center py-12">
              <RefreshCw className="w-8 h-8 animate-spin text-cyber-accent" />
            </div>
          </div>
        ) : servers.length === 0 ? (
          <div className="cyber-card text-center py-12 text-cyber-muted">
            暂无活跃服务器
          </div>
        ) : (
          <>
            <div className="cyber-card">
              <label className="block text-cyber-text font-medium mb-2">选择服务器</label>
              <select
                value={selectedServer?.serviceName || ''}
                onChange={(e) => {
                  const server = servers.find(s => s.serviceName === e.target.value);
                  setSelectedServer(server || null);
                }}
                className="w-full px-4 py-3 bg-cyber-bg border-2 border-cyber-accent/40 rounded-lg text-cyber-text focus:border-cyber-accent focus:ring-2 focus:ring-cyber-accent/30 hover:border-cyber-accent/60 transition-all cursor-pointer"
                style={{
                  background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)'
                }}>
                <option value="" className="bg-cyber-bg text-cyber-muted">请选择服务器</option>
                {servers.map(s => (
                  <option 
                    key={s.serviceName} 
                    value={s.serviceName}
                    className="bg-cyber-bg text-cyber-text py-2"
                    style={{
                      background: 'rgba(15, 23, 42, 0.98)',
                      padding: '8px 12px'
                    }}>
                    {s.name} ({s.commercialRange}) - {s.datacenter}
                  </option>
                ))}
              </select>
            </div>

            {/* 选中服务器的详细信息 */}
            {selectedServer && (
              <>
              <div className="cyber-card">
                <h3 className={`${isMobile ? 'text-base' : 'text-lg'} font-semibold text-cyber-text mb-3 sm:mb-4`}>服务器信息</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4 text-xs sm:text-sm mb-4 sm:mb-6">
                  <div>
                    <span className="text-cyber-muted">服务名称:</span>
                    <span className="text-cyber-text ml-2">{selectedServer.serviceName}</span>
                  </div>
                  <div>
                    <span className="text-cyber-muted">显示名称:</span>
                    <span className="text-cyber-text ml-2">{selectedServer.name}</span>
                  </div>
                  <div>
                    <span className="text-cyber-muted">型号:</span>
                    <span className="text-cyber-text ml-2 font-mono">{selectedServer.commercialRange}</span>
                  </div>
                  <div>
                    <span className="text-cyber-muted">数据中心:</span>
                    <span className="text-cyber-text ml-2">{selectedServer.datacenter}</span>
                  </div>
                  <div>
                    <span className="text-cyber-muted">IP地址:</span>
                    <span className="text-cyber-text ml-2 font-mono">{selectedServer.ip}</span>
                  </div>
                  <div>
                    <span className="text-cyber-muted">操作系统:</span>
                    <span className="text-cyber-text ml-2">{selectedServer.os}</span>
                  </div>
                  <div>
                    <span className="text-cyber-muted">状态:</span>
                    <span className="text-green-400 ml-2 capitalize">{selectedServer.state}</span>
                  </div>
                </div>

                {/* 操作按钮 */}
                <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
                  <button
                    onClick={() => openTasksDialog(selectedServer)}
                    className="px-4 py-2 bg-cyber-grid/50 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-accent/10 transition-all flex items-center gap-2 justify-center">
                    <Activity className="w-4 h-4" />
                    查看任务
                  </button>
                  <button
                    onClick={() => handleReboot(selectedServer)}
                    className="px-4 py-2 bg-cyber-grid/50 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-accent/10 transition-all flex items-center gap-2 justify-center">
                    <Power className="w-4 h-4" />
                    重启服务器
                  </button>
                  <button
                    onClick={openIPMIConsole}
                    className="px-4 py-2 bg-blue-500/10 border border-blue-500/30 rounded-lg text-blue-400 hover:bg-blue-500/20 transition-all flex items-center gap-2 justify-center">
                    <Monitor className="w-4 h-4" />
                    IPMI控制台
                  </button>
                  <button
                    onClick={() => openReinstallDialog(selectedServer)}
                    className="px-4 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 hover:bg-red-500/20 transition-all flex items-center gap-2 justify-center">
                    <HardDrive className="w-4 h-4" />
                    重装系统
                  </button>
                  <button
                    onClick={fetchBootModes}
                    disabled={loadingBootModes}
                    className="px-4 py-2 bg-orange-500/10 border border-orange-500/30 rounded-lg text-orange-400 hover:bg-orange-500/20 transition-all flex items-center gap-2 justify-center disabled:opacity-50">
                    <HardDrive className="w-4 h-4" />
                    {loadingBootModes ? '加载中...' : '启动模式'}
                  </button>
                  <button
                    onClick={fetchBiosSettings}
                    disabled={loadingBios}
                    className="px-4 py-2 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-yellow-400 hover:bg-yellow-500/20 transition-all flex items-center gap-2 justify-center disabled:opacity-50">
                    <Cog className="w-4 h-4" />
                    {loadingBios ? '加载中...' : 'BIOS 设置'}
                  </button>
                  <button
                    onClick={() => {
                      setHardwareReplaceType('');
                      setShowHardwareReplaceDialog(true);
                    }}
                    className="px-4 py-2 bg-purple-500/10 border border-purple-500/30 rounded-lg text-purple-400 hover:bg-purple-500/20 transition-all flex items-center gap-2 justify-center">
                    <Cpu className="w-4 h-4" />
                    硬件更换
                  </button>
                  <button
                    onClick={openChangeContactDialog}
                    className="px-4 py-2 bg-green-500/10 border border-green-500/30 rounded-lg text-green-400 hover:bg-green-500/20 transition-all flex items-center gap-2 justify-center">
                    <Mail className="w-4 h-4" />
                    变更联系人
                  </button>
                  <button
                    onClick={() => handleOpenNetworkSpecs(selectedServer)}
                    disabled={loadingNetworkSpecs}
                    className="px-4 py-2 bg-cyan-500/10 border border-cyan-500/30 rounded-lg text-cyan-400 hover:bg-cyan-500/20 transition-all flex items-center gap-2 justify-center disabled:opacity-50">
                    <Wifi className="w-4 h-4" />
                    {loadingNetworkSpecs ? '加载中...' : '网络规格'}
                  </button>
                  <button
                    onClick={() => handleOpenAdvanced(selectedServer, 'burst')}
                    className="px-4 py-2 bg-gradient-to-r from-yellow-500/10 to-orange-500/10 border border-yellow-500/30 rounded-lg text-yellow-400 hover:from-yellow-500/20 hover:to-orange-500/20 transition-all flex items-center gap-2 justify-center">
                    <Settings className="w-4 h-4" />
                    高级功能
                  </button>
                </div>
              </div>

              {/* Task 6: 硬件信息 */}
              <div className="cyber-card">
                <h3 className="text-lg font-semibold text-cyber-text mb-4 flex items-center gap-2">
                  <Cpu className="w-5 h-5 text-cyber-accent" />
                  硬件配置
                </h3>
                {loadingHardware ? (
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    加载中...
                  </div>
                ) : hardware ? (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                    {/* 左列：基础信息 */}
                    <div className="space-y-3">
                      {/* 处理器 */}
                      <div className="flex items-baseline">
                        <span className="text-cyber-muted text-sm w-16">处理器:</span>
                        <div className="flex-1">
                          <span className="text-cyber-text font-semibold">{hardware.processorName}</span>
                          {hardware.coresPerProcessor > 0 && hardware.threadsPerProcessor > 0 && (
                            <span className="text-cyber-muted text-sm ml-2">
                              ({hardware.coresPerProcessor}核/{hardware.threadsPerProcessor}线程)
                            </span>
                          )}
                        </div>
                      </div>

                      {/* 架构 */}
                      <div className="flex items-baseline">
                        <span className="text-cyber-muted text-sm w-16">架构:</span>
                        <span className="text-cyber-text">{hardware.processorArchitecture}</span>
                      </div>

                      {/* 内存 */}
                      <div className="flex items-baseline">
                        <span className="text-cyber-muted text-sm w-16">内存:</span>
                        <span className="text-cyber-text font-semibold">{hardware.memorySize?.value} {hardware.memorySize?.unit}</span>
                      </div>
                    </div>

                    {/* 右列：存储配置 */}
                    {hardware.diskGroups && hardware.diskGroups.length > 0 && (
                      <div>
                        <div className="text-cyber-muted text-sm mb-3">存储配置</div>
                        <div className="space-y-2">
                          {hardware.diskGroups.map((group: any, idx: number) => (
                            <div key={idx} className="pl-3 border-l-2 border-cyber-accent/30">
                              <div className="text-sm text-cyber-text font-semibold">
                                {group.numberOfDisks}x {group.diskSize?.value}{group.diskSize?.unit} {group.diskType}
                                {group.defaultHardwareRaidType && group.defaultHardwareRaidType !== 'N/A' && (
                                  <span className="text-cyber-muted font-normal ml-2">
                                    (RAID {group.defaultHardwareRaidType.replace('raid', '')})
                                  </span>
                                )}
                              </div>
                              {group.description && (
                                <div className="text-xs text-cyber-muted mt-1">
                                  {group.description}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* 扩展卡（如果有，横跨两列） */}
                    {hardware.expansionCards && hardware.expansionCards.length > 0 && (
                      <div className="lg:col-span-2">
                        <div className="text-cyber-muted text-sm mb-2 border-t border-cyber-accent/10 pt-3">扩展设备</div>
                        <div className="space-y-1 text-sm pl-3">
                          {hardware.expansionCards.map((card: any, idx: number) => (
                            <div key={idx} className="text-cyber-text">
                              <span className="text-cyber-muted uppercase">{card.type}:</span> {card.description}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-cyber-muted text-sm">暂无硬件信息</p>
                )}
              </div>

              {/* BIOS 设置 */}
              <div className="cyber-card">
                <h3 className="text-lg font-semibold text-cyber-text mb-4 flex items-center gap-2">
                  <Cog className="w-5 h-5 text-cyber-accent" />
                  BIOS 设置
                </h3>
                {loadingBios ? (
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    加载中...
                  </div>
                ) : biosSettings ? (
                  <div className="space-y-4">
                    <div className="text-sm">
                      <div className="text-cyber-muted mb-2">当前 BIOS 配置</div>
                      <div className="bg-cyber-grid/30 border border-cyber-accent/20 rounded-lg overflow-hidden">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="bg-cyber-grid/40">
                              <th className="text-left px-3 py-2 text-cyber-muted font-normal">键</th>
                              <th className="text-left px-3 py-2 text-cyber-muted font-normal">值</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(biosSettings).map(([key, value]) => (
                              <tr key={key} className="border-t border-cyber-accent/10">
                                <td className="px-3 py-2 text-cyber-text font-mono break-all">{key}</td>
                                <td className="px-3 py-2 text-cyber-text break-words">
                                  {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    {biosSgx && (
                      <div className="text-sm">
                        <div className="text-cyber-muted mb-2">SGX</div>
                        <div className="bg-cyber-grid/30 border border-cyber-accent/20 rounded-lg overflow-hidden">
                          <table className="w-full text-sm">
                            <tbody>
                              {Object.entries(biosSgx).map(([key, value]) => (
                                <tr key={key} className="border-t border-cyber-accent/10">
                                  <td className="px-3 py-2 text-cyber-text font-mono break-all w-48">{key}</td>
                                  <td className="px-3 py-2 text-cyber-text break-words">
                                    {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-cyber-muted text-sm">点击上方“BIOS 设置”加载信息</p>
                )}
              </div>

              {/* Task 7: IP管理 */}
              <div className="cyber-card">
                <h3 className="text-lg font-semibold text-cyber-text mb-4 flex items-center gap-2">
                  <Wifi className="w-5 h-5 text-cyber-accent" />
                  IP地址管理
                </h3>
                {loadingIPs ? (
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    加载中...
                  </div>
                ) : ips.length > 0 ? (
                  <div className="space-y-2">
                    {ips.map((ip, idx) => (
                      <div key={idx} className="p-3 bg-cyber-grid/30 border border-cyber-accent/20 rounded-lg">
                        <div className="flex justify-between items-center">
                          <div>
                            <div className="text-cyber-text font-mono font-semibold">{ip.ip}</div>
                            <div className="text-xs text-cyber-muted mt-1">类型: {ip.type}</div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-cyber-muted text-sm">暂无IP信息</p>
                )}
              </div>

              {/* Task 8: 服务信息 */}
              <div className="cyber-card">
                <h3 className="text-lg font-semibold text-cyber-text mb-4 flex items-center gap-2">
                  <Calendar className="w-5 h-5 text-cyber-accent" />
                  服务信息
                </h3>
                {loadingService ? (
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    加载中...
                  </div>
                ) : serviceInfo ? (
                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <span className="text-cyber-muted">状态:</span>
                      <span className="text-cyber-text ml-2 capitalize">{serviceInfo.status}</span>
                    </div>
                    <div>
                      <span className="text-cyber-muted">到期时间:</span>
                      <span className="text-cyber-text ml-2">{new Date(serviceInfo.expiration).toLocaleDateString('zh-CN')}</span>
                    </div>
                    <div>
                      <span className="text-cyber-muted">创建时间:</span>
                      <span className="text-cyber-text ml-2">{new Date(serviceInfo.creation).toLocaleDateString('zh-CN')}</span>
                    </div>
                    <div>
                      <span className="text-cyber-muted">自动续费:</span>
                      <span className={`ml-2 ${serviceInfo.renewalType ? 'text-green-400' : 'text-orange-400'}`}>
                        {serviceInfo.renewalType ? '已开启' : '已关闭'}
                      </span>
                    </div>
                  </div>
                ) : (
                  <p className="text-cyber-muted text-sm">暂无服务信息</p>
                )}
              </div>

              {/* Task 5: 监控控制 */}
              <div className="cyber-card">
                <h3 className="text-lg font-semibold text-cyber-text mb-4">服务器监控</h3>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-cyber-text">OVH监控服务</p>
                    <p className="text-sm text-cyber-muted mt-1">自动监控服务器可用性并发送告警</p>
                  </div>
                  <button
                    onClick={toggleMonitoring}
                    disabled={loadingMonitoring}
                    className={`px-6 py-2 rounded-lg font-medium transition-all disabled:opacity-50 ${
                      monitoring 
                        ? 'bg-green-500 text-white hover:bg-green-600' 
                        : 'bg-gray-600 text-white hover:bg-gray-700'
                    }`}>
                    {loadingMonitoring ? '处理中...' : (monitoring ? '已开启' : '已关闭')}
                  </button>
                </div>
              </div>

              {/* 维护记录 */}
              <div className="cyber-card">
                <h3 className="text-lg font-semibold text-cyber-text mb-4 flex items-center gap-2">
                  <AlertCircle className="w-5 h-5 text-yellow-400" />
                  维护记录
                </h3>
                {loadingInterventions ? (
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    加载中...
                  </div>
                ) : interventions.length > 0 ? (
                  <div className="space-y-2">
                    {interventions.slice(0, 5).map((intervention, idx) => (
                      <div key={intervention.interventionId || idx} className="p-3 bg-cyber-grid/30 border border-cyber-accent/20 rounded-lg">
                        <div className="flex justify-between items-start">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-cyber-text font-semibold">#{intervention.interventionId}</span>
                              <span className={`text-xs px-2 py-0.5 rounded ${
                                intervention.status === 'done' ? 'bg-green-500/20 text-green-400' :
                                intervention.status === 'doing' ? 'bg-blue-500/20 text-blue-400' :
                                'bg-gray-500/20 text-gray-400'
                              }`}>
                                {intervention.status}
                              </span>
                            </div>
                            <div className="text-sm text-cyber-muted">
                              类型: {intervention.type || '未知'}
                            </div>
                            <div className="text-xs text-cyber-muted/70 mt-1">
                              {intervention.date ? new Date(intervention.date).toLocaleString('zh-CN') : '无日期'}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                    {interventions.length > 5 && (
                      <div className="text-center text-cyber-muted text-sm pt-2">
                        还有 {interventions.length - 5} 条历史记录
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-cyber-muted text-sm">暂无维护记录</p>
                )}
              </div>

              {/* 计划维护 */}
              <div className="cyber-card">
                <h3 className="text-lg font-semibold text-cyber-text mb-4 flex items-center gap-2">
                  <Calendar className="w-5 h-5 text-blue-400" />
                  计划维护 <span className="text-xs text-blue-400 bg-blue-500/10 px-2 py-0.5 rounded">BETA</span>
                </h3>
                {loadingPlannedInterventions ? (
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    加载中...
                  </div>
                ) : plannedInterventions.length > 0 ? (
                  <div className="space-y-2">
                    {plannedInterventions.map((intervention, idx) => (
                      <div key={intervention.id || idx} className="p-4 bg-blue-500/10 border border-blue-500/30 rounded-lg">
                        <div className="flex justify-between items-start mb-2">
                          <div className="flex items-center gap-2">
                            <span className="text-blue-400 font-semibold">#{intervention.id}</span>
                            <span className={`text-xs px-2 py-0.5 rounded ${
                              intervention.status === 'confirmed' ? 'bg-green-500/20 text-green-400' :
                              intervention.status === 'scheduled' ? 'bg-orange-500/20 text-orange-400' :
                              'bg-gray-500/20 text-gray-400'
                            }`}>
                              {intervention.status}
                            </span>
                          </div>
                        </div>
                        <div className="space-y-1 text-sm">
                          <div className="text-cyber-text">
                            类型: {intervention.type || '未知'}
                          </div>
                          {intervention.expectedEndDate && (
                            <div className="text-cyber-muted">
                              预计时间: {new Date(intervention.expectedEndDate).toLocaleString('zh-CN')}
                            </div>
                          )}
                          {intervention.description && (
                            <div className="text-cyber-muted/80 text-xs mt-2 p-2 bg-cyber-grid/20 rounded">
                              {intervention.description}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-6">
                    <Calendar className="w-12 h-12 text-cyber-muted/30 mx-auto mb-2" />
                    <p className="text-cyber-muted text-sm">暂无计划维护</p>
                  </div>
                )}
              </div>

              {/* 网络接口（物理网卡） */}
              <div className="cyber-card">
                <h3 className="text-lg font-semibold text-cyber-text mb-2 flex items-center gap-2">
                  <Wifi className="w-5 h-5 text-blue-400" />
                  网络接口
                </h3>
                <p className="text-xs text-cyber-muted mb-4">
                  服务器物理网卡信息
                </p>
                {loadingNetworkInterfaces ? (
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    加载中...
                  </div>
                ) : networkInterfaces.length > 0 ? (
                  <div className="space-y-2">
                    {networkInterfaces.map((iface, idx) => (
                      <div key={iface.mac || idx} className="p-4 bg-blue-500/10 border border-blue-500/30 rounded-lg">
                        <div className="flex justify-between items-start mb-2">
                          <div className="flex items-center gap-2">
                            <span className="text-blue-400 font-semibold font-mono text-sm">{iface.mac}</span>
                            <span className={`text-xs px-2 py-0.5 rounded ${
                              iface.linkType === 'public' ? 'bg-green-500/20 text-green-400' :
                              iface.linkType === 'private' ? 'bg-orange-500/20 text-orange-400' :
                              iface.linkType?.includes('lag') ? 'bg-purple-500/20 text-purple-400' :
                              'bg-gray-500/20 text-gray-400'
                            }`}>
                              {iface.linkType === 'public' ? '公网' :
                               iface.linkType === 'private' ? '私网' :
                               iface.linkType === 'public_lag' ? '公网聚合' :
                               iface.linkType === 'private_lag' ? '私网聚合' :
                               iface.linkType === 'isolated' ? '隔离' :
                               iface.linkType || '未知'}
                            </span>
                          </div>
                        </div>
                        <div className="space-y-1 text-sm">
                          {iface.virtualNetworkInterface && (
                            <div className="text-cyber-muted/80 text-xs p-2 bg-purple-500/10 rounded flex items-center gap-2">
                              <span className="text-purple-400">🔗</span>
                              <span>已关联OLA虚拟接口</span>
                            </div>
                          )}
                          {iface.error && (
                            <div className="text-red-400/80 text-xs p-2 bg-red-500/10 rounded">
                              错误: {iface.error}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-6">
                    <Wifi className="w-12 h-12 text-cyber-muted/30 mx-auto mb-2" />
                    <p className="text-cyber-muted text-sm">该服务器暂无网卡信息</p>
                  </div>
                )}
              </div>

              {/* MRTG流量监控图表 */}
              <div className="cyber-card">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <BarChart3 className="w-5 h-5 text-cyan-400" />
                    <h3 className="text-lg font-semibold text-cyber-text">流量监控</h3>
                  </div>
                  <button
                    onClick={() => selectedServer && fetchMrtgData(selectedServer.serviceName)}
                    className="p-2 rounded-lg bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 transition-colors"
                    disabled={loadingMrtg}
                  >
                    <RefreshCw className={`w-4 h-4 ${loadingMrtg ? 'animate-spin' : ''}`} />
                  </button>
                </div>

                {/* 时间周期选择器 */}
                <div className="mb-4">
                  <label className="block text-sm text-cyber-muted mb-2">时间周期</label>
                  <select
                    value={mrtgPeriod}
                    onChange={(e) => {
                      setMrtgPeriod(e.target.value);
                      if (selectedServer) {
                        fetchMrtgData(selectedServer.serviceName, e.target.value);
                      }
                    }}
                    className="w-full bg-cyber-grid border border-cyber-border rounded-lg px-3 py-2 text-cyber-text focus:outline-none focus:border-cyan-500"
                  >
                    <option value="hourly">每小时</option>
                    <option value="daily">每天（默认）</option>
                    <option value="weekly">每周</option>
                    <option value="monthly">每月</option>
                    <option value="yearly">每年</option>
                  </select>
                </div>

                {/* 图表区域 */}
                {loadingMrtg ? (
                  <div className="flex items-center justify-center py-12">
                    <RefreshCw className="w-8 h-8 animate-spin text-cyan-400" />
                    <span className="ml-3 text-cyber-muted">加载中...</span>
                  </div>
                ) : mrtgData && mrtgData.download && mrtgData.upload ? (
                  <div className="space-y-6">
                    {mrtgData.download.interfaces.map((downloadIface: any, idx: number) => {
                      const uploadIface = mrtgData.upload.interfaces.find((u: any) => u.mac === downloadIface.mac);
                      if (!downloadIface.data || downloadIface.data.length === 0) return null;
                      if (!uploadIface || !uploadIface.data || uploadIface.data.length === 0) return null;
                      
                      // 合并上传和下载数据到同一时间轴
                      const chartData = downloadIface.data.map((downPoint: any, i: number) => {
                        const upPoint = uploadIface.data[i];
                        return {
                          time: new Date(downPoint.timestamp * 1000).toLocaleString('zh-CN', {
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit'
                          }),
                          timestamp: downPoint.timestamp,
                          download: downPoint.value?.value || 0,
                          upload: upPoint?.value?.value || 0,
                          unit: downPoint.value?.unit || 'bps'
                        };
                      });

                      // 计算统计信息
                      const downloadValues = chartData.map(d => d.download);
                      const uploadValues = chartData.map(d => d.upload);
                      const totalValues = chartData.map(d => d.download + d.upload);  // 每个时间点的总带宽
                      
                      const downloadAvg = downloadValues.reduce((a, b) => a + b, 0) / downloadValues.length;
                      const uploadAvg = uploadValues.reduce((a, b) => a + b, 0) / uploadValues.length;
                      const downloadMax = Math.max(...downloadValues);
                      const uploadMax = Math.max(...uploadValues);
                      const totalMax = Math.max(...totalValues);  // ✅ 正确：每个时刻总带宽的最大值
                      const downloadCurrent = downloadValues[downloadValues.length - 1] || 0;
                      const uploadCurrent = uploadValues[uploadValues.length - 1] || 0;
                      
                      // 格式化数值（bits/s -> Mbps/Gbps）
                      const formatBandwidth = (bps: number) => {
                        if (bps >= 1000000000) return `${(bps / 1000000000).toFixed(2)} Gbps`;
                        if (bps >= 1000000) return `${(bps / 1000000).toFixed(2)} Mbps`;
                        if (bps >= 1000) return `${(bps / 1000).toFixed(2)} Kbps`;
                        return `${bps.toFixed(0)} bps`;
                      };
                      
                      // 生成智能总结
                      const generateSummary = () => {
                        const totalAvg = downloadAvg + uploadAvg;
                        const periodText = mrtgPeriod === 'hourly' ? '过去1小时' :
                                         mrtgPeriod === 'daily' ? '过去24小时' :
                                         mrtgPeriod === 'weekly' ? '过去7天' :
                                         mrtgPeriod === 'monthly' ? '过去30天' : '过去1年';
                        
                        // 找到峰值发生的时刻
                        const peakIndex = totalValues.indexOf(totalMax);
                        const peakDownload = downloadValues[peakIndex];
                        const peakUpload = uploadValues[peakIndex];
                        
                        return `${periodText}，平均带宽 ${formatBandwidth(totalAvg)}（↓${formatBandwidth(downloadAvg)} ↑${formatBandwidth(uploadAvg)}），峰值 ${formatBandwidth(totalMax)}（↓${formatBandwidth(peakDownload)} ↑${formatBandwidth(peakUpload)}）`;
                      };

                      return (
                        <div key={idx} className="p-4 bg-cyan-500/5 border border-cyan-500/20 rounded-lg">
                          {/* 网卡标题 */}
                          <h4 className="text-sm font-semibold text-cyan-400 flex items-center gap-2 mb-3">
                            <Wifi className="w-4 h-4" />
                            网卡: <span className="font-mono">{downloadIface.mac}</span>
                          </h4>
                          
                          {/* 智能总结 */}
                          <div className="mb-4 p-3 bg-gradient-to-r from-cyan-500/10 to-blue-500/10 border border-cyan-500/30 rounded-lg">
                            <div className="text-sm text-cyber-text font-medium">
                              📊 {generateSummary()}
                            </div>
                          </div>
                          
                          {/* 统计卡片 - 上传和下载 */}
                          <div className="grid grid-cols-2 gap-3 mb-4">
                            {/* 下载统计 */}
                            <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-3">
                              <div className="text-xs text-green-400 font-semibold mb-2 flex items-center gap-1">
                                <span>↓</span> 下载带宽
                              </div>
                              <div className="grid grid-cols-3 gap-2 text-xs">
                                <div>
                                  <div className="text-cyber-muted/70 mb-1">当前</div>
                                  <div className="text-green-300 font-semibold">{formatBandwidth(downloadCurrent)}</div>
                                </div>
                                <div>
                                  <div className="text-cyber-muted/70 mb-1">平均</div>
                                  <div className="text-green-400 font-bold">{formatBandwidth(downloadAvg)}</div>
                                </div>
                                <div>
                                  <div className="text-cyber-muted/70 mb-1">峰值</div>
                                  <div className="text-green-500 font-semibold">{formatBandwidth(downloadMax)}</div>
                                </div>
                              </div>
                            </div>
                            
                            {/* 上传统计 */}
                            <div className="bg-orange-500/10 border border-orange-500/30 rounded-lg p-3">
                              <div className="text-xs text-orange-400 font-semibold mb-2 flex items-center gap-1">
                                <span>↑</span> 上传带宽
                              </div>
                              <div className="grid grid-cols-3 gap-2 text-xs">
                                <div>
                                  <div className="text-cyber-muted/70 mb-1">当前</div>
                                  <div className="text-orange-300 font-semibold">{formatBandwidth(uploadCurrent)}</div>
                                </div>
                                <div>
                                  <div className="text-cyber-muted/70 mb-1">平均</div>
                                  <div className="text-orange-400 font-bold">{formatBandwidth(uploadAvg)}</div>
                                </div>
                                <div>
                                  <div className="text-cyber-muted/70 mb-1">峰值</div>
                                  <div className="text-orange-500 font-semibold">{formatBandwidth(uploadMax)}</div>
                                </div>
                              </div>
                            </div>
                          </div>
                          
                          {/* 图表区域 - 双线（上传+下载） */}
                          <ResponsiveContainer width="100%" height={380}>
                            <LineChart data={chartData}>
                              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                              <XAxis 
                                dataKey="time"
                                stroke="#9CA3AF"
                                style={{ fontSize: '10px' }}
                                angle={-45}
                                textAnchor="end"
                                height={80}
                              />
                              <YAxis 
                                stroke="#9CA3AF"
                                style={{ fontSize: '11px' }}
                                label={{ 
                                  value: 'Mbps', 
                                  angle: -90, 
                                  position: 'insideLeft',
                                  style: { fill: '#9CA3AF', fontSize: '12px' }
                                }}
                                tickFormatter={(value) => formatBandwidth(value).replace(/\s.*/, '')}
                              />
                              <Tooltip 
                                contentStyle={{
                                  backgroundColor: '#1F2937',
                                  border: '1px solid #374151',
                                  borderRadius: '8px',
                                  color: '#E5E7EB',
                                  padding: '12px'
                                }}
                                labelStyle={{ color: '#9CA3AF', marginBottom: '8px', fontWeight: 'bold' }}
                                formatter={(value: any, name: string) => [
                                  formatBandwidth(Number(value)), 
                                  name === 'download' ? '↓ 下载' : '↑ 上传'
                                ]}
                              />
                              <Legend 
                                wrapperStyle={{
                                  paddingTop: '15px'
                                }}
                                formatter={(value) => value === 'download' ? '↓ 下载带宽' : '↑ 上传带宽'}
                              />
                              {/* 下载线 - 绿色 */}
                              <Line 
                                type="monotone"
                                dataKey="download"
                                stroke="#10B981"
                                strokeWidth={2.5}
                                dot={false}
                                name="download"
                                animationDuration={800}
                              />
                              {/* 上传线 - 橙色 */}
                              <Line 
                                type="monotone"
                                dataKey="upload"
                                stroke="#F59E0B"
                                strokeWidth={2.5}
                                dot={false}
                                name="upload"
                                animationDuration={800}
                              />
                            </LineChart>
                          </ResponsiveContainer>
                          
                          {/* 底部信息 */}
                          <div className="mt-4 pt-3 border-t border-cyan-500/20 text-center">
                            <div className="text-xs text-cyber-muted/70">
                              数据点: <span className="text-cyan-400 font-semibold">{chartData.length}</span> · 
                              周期: <span className="text-cyan-400 font-semibold">{
                                mrtgPeriod === 'hourly' ? '每小时' :
                                mrtgPeriod === 'daily' ? '每天' :
                                mrtgPeriod === 'weekly' ? '每周' :
                                mrtgPeriod === 'monthly' ? '每月' : '每年'
                              }</span>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-center py-12">
                    <BarChart3 className="w-12 h-12 text-cyber-muted/30 mx-auto mb-2" />
                    <p className="text-cyber-muted text-sm">暂无流量数据</p>
                    <p className="text-cyber-muted/70 text-xs mt-1">请选择时间周期后查看</p>
                  </div>
                )}
              </div>
              </>
            )}
          </>
        )}
      </motion.div>

      {/* Task 3: 重装系统对话框 */}
      {createPortal(
        <AnimatePresence>
          {showReinstallDialog && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="cyber-card max-w-2xl w-full max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                  <HardDrive className="w-5 h-5 text-orange-400" />
                  <h3 className="text-xl font-semibold text-cyber-text">
                    重装系统 - {selectedServer?.name}
                  </h3>
                </div>
                <button
                  onClick={() => setShowReinstallDialog(false)}
                  className="text-cyber-muted hover:text-cyber-text transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <p className="text-cyber-muted text-sm mb-4">
                选择要安装的操作系统模板。此操作将清空服务器所有数据。
              </p>

              <div className="space-y-4">
                <div>
                  <label className="block text-cyber-text font-medium mb-2">操作系统模板</label>
                  
                  {/* 搜索框 */}
                  <div className="mb-3">
                    <input
                      type="text"
                      placeholder="搜索系统模板... (如: ubuntu, debian, centos)"
                      value={templateSearchQuery}
                      onChange={(e) => setTemplateSearchQuery(e.target.value)}
                      className="w-full px-4 py-2.5 bg-cyber-bg border border-cyber-accent/30 rounded-lg text-cyber-text placeholder-cyber-muted focus:border-cyan-400 focus:ring-2 focus:ring-cyan-400/30 transition-all"
                    />
                    {templateSearchQuery && (
                      <p className="text-xs text-cyber-muted mt-1">
                        找到 {osTemplates.filter(t => 
                          t.distribution.toLowerCase().includes(templateSearchQuery.toLowerCase()) ||
                          t.templateName.toLowerCase().includes(templateSearchQuery.toLowerCase()) ||
                          t.family.toLowerCase().includes(templateSearchQuery.toLowerCase())
                        ).length} 个匹配的模板
                      </p>
                    )}
                  </div>
                  
                  <select
                    value={selectedTemplate}
                    onChange={(e) => {
                      const template = e.target.value;
                      setSelectedTemplate(template);
                    }}
                    className="w-full px-4 py-3 bg-cyber-bg border-2 border-cyber-accent/40 rounded-lg text-cyber-text focus:border-cyber-accent focus:ring-2 focus:ring-cyber-accent/30 hover:border-cyber-accent/60 transition-all cursor-pointer"
                    style={{
                      background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)'
                    }}>
                    <option value="" className="bg-cyber-bg text-cyber-muted">选择系统模板</option>
                    {osTemplates
                      .filter(template => {
                        if (!templateSearchQuery) return true;
                        const query = templateSearchQuery.toLowerCase();
                        return template.distribution.toLowerCase().includes(query) ||
                               template.templateName.toLowerCase().includes(query) ||
                               template.family.toLowerCase().includes(query);
                      })
                      .map((template) => (
                        <option 
                          key={template.templateName} 
                          value={template.templateName}
                          className="bg-cyber-bg text-cyber-text hover:bg-cyber-accent/20 py-2"
                          style={{
                            background: 'rgba(15, 23, 42, 0.98)',
                            padding: '8px 12px'
                          }}>
                          {template.distribution} - {template.family} - {template.bitFormat}位
                        </option>
                      ))}
                  </select>
                </div>

                <div>
                  <label className="block text-cyber-text font-medium mb-2">自定义主机名（可选）</label>
                  <input
                    type="text"
                    placeholder="例如: server1.example.com"
                    value={customHostname}
                    onChange={(e) => setCustomHostname(e.target.value)}
                    className="w-full px-4 py-3 bg-cyber-bg border-2 border-cyber-accent/40 rounded-lg text-cyber-text placeholder-cyber-muted focus:border-cyber-accent focus:ring-2 focus:ring-cyber-accent/30 hover:border-cyber-accent/60 transition-all"
                    style={{
                      background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)'
                    }}
                  />
                </div>

                {/* 高级存储配置 */}
                <div className="border-t border-cyber-accent/30 pt-4">
                  <div className="flex items-center justify-between mb-3">
                    <label className="flex items-center gap-2 text-cyber-text font-medium cursor-pointer">
                      <input
                        type="checkbox"
                        checked={useCustomStorage}
                        onChange={(e) => setUseCustomStorage(e.target.checked)}
                        className="w-4 h-4 accent-cyan-500"
                      />
                      <HardDrive className="w-4 h-4 text-cyan-400" />
                      <span>启用高级存储配置（RAID & 分区）</span>
                    </label>
                    
                    {/* 智能配置按钮 */}
                    {Object.keys(diskGroups).length > 0 && (
                      <button
                        onClick={() => {
                          const diskGroupsArray = Object.entries(diskGroups);
                          const groupCount = diskGroupsArray.length;
                          const diskCount = diskGroupsArray[0]?.[1]?.disks?.length || 0;
                          
                          // 生成磁盘详细信息
                          console.log('[SmartConfig] 完整磁盘组数据:', diskGroups);
                          console.log('[SmartConfig] entries后:', diskGroupsArray);
                          
                          let diskDetails = '';
                          diskGroupsArray.forEach(([groupId, group]) => {
                            console.log(`[SmartConfig] 磁盘组${groupId}:`, group);
                            
                            const disks = group.disks || [];
                            if (disks.length === 0) {
                              diskDetails += `磁盘组${groupId}: 无磁盘信息\n`;
                              return;
                            }
                            
                            const firstDisk = disks[0];
                            console.log(`[SmartConfig] 磁盘组${groupId} 第一块盘:`, firstDisk);
                            console.log(`[SmartConfig] capacity:`, firstDisk?.capacity, 'unit:', firstDisk?.unit);
                            
                            const diskSize = firstDisk?.capacity || '???';
                            const diskUnit = firstDisk?.unit || 'GB';
                            const diskType = firstDisk?.technology || firstDisk?.interface || '';
                            
                            diskDetails += `磁盘组${groupId}: ${disks.length}×${diskSize}${diskUnit} ${diskType}\n`;
                          });
                          
                          let scenarioText = '';
                          if (groupCount === 1 && diskCount === 1) {
                            scenarioText = '单磁盘：默认系统分区（无RAID）';
                          } else if (groupCount === 1 && diskCount > 1) {
                            scenarioText = `单磁盘组多盘（${diskCount}盘）：RAID0系统盘（最大容量）`;
                          } else {
                            scenarioText = '多磁盘组：使用默认分区（OVH自动智能分配）';
                          }
                          
                          setSmartConfigInfo({ groupCount, diskCount, scenario: scenarioText, diskDetails });
                          setShowSmartConfigDialog(true);
                        }}
                        className="px-3 py-1.5 bg-gradient-to-r from-purple-500 to-pink-500 text-white text-sm rounded-lg hover:from-purple-600 hover:to-pink-600 transition-all flex items-center gap-2 shadow-lg">
                        <Activity className="w-4 h-4" />
                        智能配置
                      </button>
                    )}
                  </div>

                  {useCustomStorage && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      className="space-y-4 bg-cyber-grid/30 rounded-lg p-4 border border-cyan-500/30">
                      
                      {/* 磁盘信息展示 */}
                      {loadingDiskInfo ? (
                        <div className="text-center py-4 text-cyber-muted">
                          <RefreshCw className="w-5 h-5 animate-spin mx-auto mb-2" />
                          <p>加载磁盘信息中...</p>
                        </div>
                      ) : Object.keys(diskGroups).length === 0 ? (
                        <div className="text-center py-4 text-cyber-muted">
                          <AlertCircle className="w-5 h-5 mx-auto mb-2" />
                          <p>未检测到磁盘组信息</p>
                        </div>
                      ) : (
                        <div className="space-y-4">
                          <h4 className="text-sm font-semibold text-cyber-text mb-2">磁盘组配置</h4>
                          
                          {Object.entries(diskGroups).map(([groupId, group]) => (
                            <div key={groupId} className="bg-cyber-bg/50 rounded-lg p-3 border border-cyan-500/20">
                              <div className="flex items-center gap-2 mb-2">
                                <HardDrive className="w-4 h-4 text-cyan-400" />
                                <span className="text-sm font-semibold text-cyber-text">
                                  磁盘组 {groupId}
                                </span>
                                {group.raidController && (
                                  <span className="text-xs text-cyan-400 bg-cyan-500/10 px-2 py-0.5 rounded">
                                    {group.raidController}
                                  </span>
                                )}
                              </div>
                              
                              {/* 磁盘列表 */}
                              <div className="grid grid-cols-2 gap-2 mb-3 text-xs">
                                {group.disks.map((disk, idx) => (
                                  <div key={idx} className="flex items-center gap-1 text-cyber-muted">
                                    <div className="w-2 h-2 rounded-full bg-cyan-400"></div>
                                    <span>
                                      {disk.capacity}{disk.unit} {disk.technology || ''} {disk.interface || ''}
                                    </span>
                                  </div>
                                ))}
                              </div>

                              {/* 硬件RAID模式选择 */}
                              <div>
                                <label className="block text-xs text-cyber-muted mb-1">硬件RAID模式</label>
                                {!raidSupported ? (
                                  <div className="bg-yellow-500/10 border border-yellow-500/30 rounded p-2">
                                    <p className="text-xs text-yellow-400 mb-2">
                                      此服务器不支持硬件RAID配置
                                    </p>
                                    {!useSoftwareRaid && (
                                      <button
                                        onClick={() => setUseSoftwareRaid(true)}
                                        className="text-xs px-2 py-1 bg-purple-500/20 border border-purple-500/30 rounded text-purple-400 hover:bg-purple-500/30">
                                        改用软RAID →
                                      </button>
                                    )}
                                  </div>
                                ) : (
                                  <select
                                    value={selectedRaidConfigs[parseInt(groupId)] || ''}
                                    onChange={(e) => {
                                      setSelectedRaidConfigs({
                                        ...selectedRaidConfigs,
                                        [parseInt(groupId)]: e.target.value
                                      });
                                    }}
                                    className="w-full px-3 py-2 bg-cyber-dark border border-cyan-500/30 rounded text-cyber-text text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/30">
                                    <option value="">默认（无RAID）</option>
                                    <option value="raid0">RAID 0 - 条带化（性能优先，无冗余）</option>
                                    <option value="raid1">RAID 1 - 镜像（冗余优先）</option>
                                    <option value="raid5">RAID 5 - 分布式奇偶校验（平衡）</option>
                                    <option value="raid6">RAID 6 - 双重奇偶校验（高冗余）</option>
                                    <option value="raid10">RAID 10 - 镜像+条带化（高性能+冗余）</option>
                                  </select>
                                )}
                              </div>
                            </div>
                          ))}

                          {/* 软RAID配置 */}
                          <div className="border-t border-cyan-500/20 pt-4 mt-4">
                            <div className="flex items-center justify-between mb-3">
                              <label className="flex items-center gap-2 text-cyber-text font-medium cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={useSoftwareRaid}
                                  onChange={(e) => setUseSoftwareRaid(e.target.checked)}
                                  className="w-4 h-4 accent-purple-500"
                                />
                                <HardDrive className="w-4 h-4 text-purple-400" />
                                <span className="text-sm">使用软RAID（Software RAID）</span>
                              </label>
                            </div>

                            {useSoftwareRaid && (
                              <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                className="space-y-3 bg-purple-500/10 rounded-lg p-3 border border-purple-500/30">
                                <p className="text-xs text-purple-300">
                                  软RAID通过操作系统层面实现，不需要硬件RAID控制器，适用于所有服务器。
                                </p>
                                
                                <div>
                                  <label className="block text-xs text-cyber-muted mb-1">软RAID级别</label>
                                  <select
                                    value={softwareRaidLevel}
                                    onChange={(e) => setSoftwareRaidLevel(e.target.value)}
                                    className="w-full px-3 py-2 bg-cyber-dark border border-purple-500/30 rounded text-cyber-text text-sm focus:border-purple-500 focus:ring-1 focus:ring-purple-500/30">
                                    <option value="raid0">RAID 0 - 条带化（性能优先，2+磁盘）</option>
                                    <option value="raid1">RAID 1 - 镜像（推荐，2+磁盘）</option>
                                    <option value="raid5">RAID 5 - 分布式奇偶校验（3+磁盘）</option>
                                    <option value="raid6">RAID 6 - 双重奇偶校验（4+磁盘）</option>
                                    <option value="raid10">RAID 10 - 镜像+条带化（4+磁盘）</option>
                                  </select>
                                </div>

                                <div className="bg-purple-500/10 border border-purple-500/30 rounded p-2 text-xs text-purple-300">
                                  <div className="flex items-start gap-2">
                                    <AlertCircle className="w-3 h-3 flex-shrink-0 mt-0.5" />
                                    <div>
                                      <p className="font-semibold mb-1">软RAID说明：</p>
                                      <ul className="list-disc list-inside space-y-0.5">
                                        <li>软RAID由Linux mdadm管理，性能略低于硬件RAID</li>
                                        <li>适用于不支持硬件RAID的服务器</li>
                                        <li>RAID 1推荐用于系统盘（数据安全）</li>
                                        <li>RAID 0适合临时数据（最大性能和容量）</li>
                                        <li>所有磁盘将自动加入软RAID阵列</li>
                                      </ul>
                                    </div>
                                  </div>
                                </div>
                              </motion.div>
                            )}
                          </div>

                          {/* 分区配置 */}
                          <div className="border-t border-cyan-500/20 pt-4 mt-4">
                            <h4 className="text-sm font-semibold text-cyber-text mb-2">自定义分区方案（可选）</h4>
                            <p className="text-xs text-cyber-muted mb-3">
                              留空则使用默认分区方案。点击下方按钮添加自定义分区。
                            </p>
                            
                            {customPartitions.length > 0 && (
                              <div className="space-y-2 mb-3">
                                {customPartitions.map((partition, idx) => (
                                  <div key={idx} className="flex items-center gap-2 bg-cyber-dark/50 rounded p-2 text-xs">
                                    <span className="text-cyan-400 font-mono">{partition.mountpoint}</span>
                                    <span className="text-cyber-muted">|</span>
                                    <span className="text-cyber-muted">{partition.filesystem}</span>
                                    <span className="text-cyber-muted">|</span>
                                    <span className="text-cyber-muted">{partition.size === 0 ? '剩余空间' : `${partition.size}MB`}</span>
                                    {partition.diskGroupId !== undefined && (
                                      <>
                                        <span className="text-cyber-muted">|</span>
                                        <span className="text-cyan-400">磁盘组{partition.diskGroupId}</span>
                                      </>
                                    )}
                                    {partition.raid && (
                                      <>
                                        <span className="text-cyber-muted">|</span>
                                        <span className="text-purple-400">{partition.raid.toUpperCase()}</span>
                                      </>
                                    )}
                                    <button
                                      onClick={() => {
                                        setEditingPartition(partition);
                                        setEditingPartitionIndex(idx);
                                        setShowPartitionEditor(true);
                                      }}
                                      className="ml-auto text-blue-400 hover:text-blue-300">
                                      编辑
                                    </button>
                                    <button
                                      onClick={() => setCustomPartitions(customPartitions.filter((_, i) => i !== idx))}
                                      className="text-red-400 hover:text-red-300">
                                      <X className="w-3 h-3" />
                                    </button>
                                  </div>
                                ))}
                              </div>
                            )}

                            <div className="flex gap-2">
                              <button
                                onClick={() => {
                                  // 添加默认分区，如果启用了软RAID则包含raid参数
                                  const newPartition: CustomPartition = {
                                    mountpoint: '/',
                                    filesystem: 'ext4',
                                    size: 50000,
                                    order: customPartitions.length + 1,
                                    type: 'primary'
                                  };
                                  
                                  // 如果启用软RAID，添加raid参数
                                  if (useSoftwareRaid) {
                                    newPartition.raid = softwareRaidLevel;
                                  }
                                  
                                  setCustomPartitions([...customPartitions, newPartition]);
                                }}
                                className="text-xs px-3 py-1.5 bg-cyan-500/20 border border-cyan-500/30 rounded text-cyan-400 hover:bg-cyan-500/30">
                                + 快速添加 {useSoftwareRaid && `(${softwareRaidLevel.toUpperCase()})`}
                              </button>
                              
                              <button
                                onClick={() => {
                                  // 打开高级编辑器
                                  setEditingPartition({
                                    mountpoint: '/',
                                    filesystem: 'ext4',
                                    size: 0,
                                    order: customPartitions.length + 1,
                                    type: 'primary',
                                    raid: useSoftwareRaid ? softwareRaidLevel : undefined,
                                    diskGroupId: Object.keys(diskGroups).length > 0 ? parseInt(Object.keys(diskGroups)[0]) : undefined
                                  });
                                  setEditingPartitionIndex(-1);
                                  setShowPartitionEditor(true);
                                }}
                                className="text-xs px-3 py-1.5 bg-green-500/20 border border-green-500/30 rounded text-green-400 hover:bg-green-500/30">
                                ⚙️ 高级添加
                              </button>
                            </div>
                          </div>

                          {/* 提示信息 */}
                          <div className="bg-cyan-500/10 border border-cyan-500/30 rounded-lg p-3">
                            <div className="flex items-start gap-2">
                              <AlertCircle className="w-4 h-4 text-cyan-400 flex-shrink-0 mt-0.5" />
                              <div className="text-xs text-cyan-300">
                                <p className="font-semibold mb-1">高级存储配置说明：</p>
                                <ul className="list-disc list-inside space-y-0.5">
                                  <li><strong>硬件RAID</strong>：由RAID控制器管理，性能最佳</li>
                                  <li><strong>软RAID</strong>：由操作系统管理，适用于所有服务器</li>
                                  <li>RAID 0: 所有磁盘条带化，无冗余，最大容量和性能</li>
                                  <li>RAID 1: 磁盘镜像，50%可用容量，完整冗余（推荐）</li>
                                  <li>RAID 5: 分布式奇偶校验，(n-1)磁盘容量，1个磁盘冗余</li>
                                  <li>自定义分区为高级功能，请谨慎使用</li>
                                </ul>
                              </div>
                            </div>
                          </div>
                        </div>
                      )}
                    </motion.div>
                  )}
                </div>

                <div className="bg-orange-500/10 border border-orange-500/30 rounded-lg p-4">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="w-5 h-5 text-orange-400 flex-shrink-0 mt-0.5" />
                    <div className="text-sm text-orange-300">
                      <p className="font-semibold mb-1">警告：</p>
                      <ul className="list-disc list-inside space-y-1">
                        <li>此操作将删除服务器上的所有数据</li>
                        <li>重装过程中服务器将无法访问</li>
                      </ul>
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex justify-end gap-3 mt-6">
                <button
                  onClick={() => setShowReinstallDialog(false)}
                  disabled={isProcessing}
                  className="px-4 py-2 bg-cyber-grid/50 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-accent/10">
                  取消
                </button>
                <button
                  onClick={handleReinstall}
                  disabled={!selectedTemplate || isProcessing}
                  className="px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 flex items-center gap-2">
                  {isProcessing && <RefreshCw className="w-4 h-4 animate-spin" />}
                  确认重装
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>,
      document.body
      )}

      {/* 任务可用时间段对话框 */}
      {createPortal(
        <AnimatePresence>
          {showTimeslotsDialog && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="cyber-card max-w-3xl w-full max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <Calendar className="w-5 h-5 text-cyber-accent" />
                    <h3 className="text-xl font-semibold text-cyber-text">
                      可用时间段 - 任务 {selectedTaskForTimeslots?.taskId}
                    </h3>
                  </div>
                  <button
                    onClick={() => setShowTimeslotsDialog(false)}
                    className="text-cyber-muted hover:text-cyber-text transition-colors">
                    <X className="w-5 h-5" />
                  </button>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
                  <div>
                    <label className="text-sm text-cyber-muted">开始时间 (ISO8601)</label>
                    <input
                      className="w-full mt-1 bg-transparent border border-cyber-accent/30 rounded px-3 py-2 text-sm"
                      value={periodStart}
                      onChange={(e) => setPeriodStart(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-sm text-cyber-muted">结束时间 (ISO8601)</label>
                    <input
                      className="w-full mt-1 bg-transparent border border-cyber-accent/30 rounded px-3 py-2 text-sm"
                      value={periodEnd}
                      onChange={(e) => setPeriodEnd(e.target.value)}
                    />
                  </div>
                </div>

                <div className="flex items-center gap-3 mb-4">
                  <button
                    onClick={() => selectedTaskForTimeslots && fetchAvailableTimeslots(selectedTaskForTimeslots.taskId, periodStart, periodEnd)}
                    disabled={loadingTimeslots}
                    className="px-3 py-2 bg-cyber-accent text-white rounded hover:bg-cyber-accent/80 disabled:opacity-60"
                  >
                    {loadingTimeslots ? '查询中...' : '查询时间段'}
                  </button>
                </div>

                {loadingTimeslots ? (
                  <div className="flex items-center gap-2 text-cyber-muted">
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    加载中...
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    {timeslots.length === 0 ? (
                      <p className="text-sm text-cyber-muted">暂无可用时间段</p>
                    ) : (
                      <table className="w-full">
                        <thead>
                          <tr className="border-b border-cyber-accent/30">
                            <th className="text-left py-3 px-4 text-cyber-text font-semibold">开始</th>
                            <th className="text-left py-3 px-4 text-cyber-text font-semibold">结束</th>
                          </tr>
                        </thead>
                        <tbody>
                          {timeslots.map((s, idx) => (
                            <tr key={idx} className="border-b border-cyber-accent/10">
                              <td className="py-3 px-4 text-sm text-cyber-text">{new Date(s.startDate).toLocaleString('zh-CN')}</td>
                              <td className="py-3 px-4 text-sm text-cyber-text">{new Date(s.endDate).toLocaleString('zh-CN')}</td>
                            </tr>)
                          )}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}

                <div className="flex justify-end mt-6">
                  <button
                    onClick={() => setShowTimeslotsDialog(false)}
                    className="px-4 py-2 bg-cyber-accent text-white rounded-lg hover:bg-cyber-accent/80 transition-all">
                    关闭
                  </button>
                </div>
              </motion.div>
            </div>
          )}
        </AnimatePresence>,
        document.body
      )}

      {/* 分区编辑器对话框 */}
      {createPortal(
        <AnimatePresence>
          {showPartitionEditor && editingPartition && (
            <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60] p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="cyber-card max-w-lg w-full max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-semibold text-cyber-text">
                    {editingPartitionIndex === -1 ? '添加分区' : '编辑分区'}
                  </h3>
                  <button
                    onClick={() => {
                      setShowPartitionEditor(false);
                      setEditingPartition(null);
                      setEditingPartitionIndex(-1);
                    }}
                    className="text-cyber-muted hover:text-cyber-text transition-colors">
                    <X className="w-5 h-5" />
                  </button>
                </div>

                <div className="space-y-3">
                  {/* 挂载点 */}
                  <div>
                    <label className="block text-xs text-cyber-muted mb-1">挂载点</label>
                    <input
                      type="text"
                      value={editingPartition.mountpoint}
                      onChange={(e) => setEditingPartition({...editingPartition, mountpoint: e.target.value})}
                      placeholder="例如: / 或 /home 或 swap"
                      className="w-full px-3 py-2 bg-cyber-dark border border-cyan-500/30 rounded text-cyber-text text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/30"
                    />
                  </div>

                  {/* 文件系统 */}
                  <div>
                    <label className="block text-xs text-cyber-muted mb-1">文件系统</label>
                    <select
                      value={editingPartition.filesystem}
                      onChange={(e) => setEditingPartition({...editingPartition, filesystem: e.target.value})}
                      className="w-full px-3 py-2 bg-cyber-dark border border-cyan-500/30 rounded text-cyber-text text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/30">
                      <option value="ext4">ext4</option>
                      <option value="ext3">ext3</option>
                      <option value="xfs">xfs</option>
                      <option value="btrfs">btrfs</option>
                      <option value="swap">swap</option>
                    </select>
                  </div>

                  {/* 大小 */}
                  <div>
                    <label className="block text-xs text-cyber-muted mb-1">大小（MB，0=使用剩余空间）</label>
                    <input
                      type="number"
                      value={editingPartition.size}
                      onChange={(e) => setEditingPartition({...editingPartition, size: parseInt(e.target.value) || 0})}
                      className="w-full px-3 py-2 bg-cyber-dark border border-cyan-500/30 rounded text-cyber-text text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/30"
                    />
                  </div>

                  {/* 磁盘组选择 */}
                  {Object.keys(diskGroups).length > 1 && (
                    <div>
                      <label className="block text-xs text-cyber-muted mb-1">磁盘组（指定分区使用哪个磁盘组）</label>
                      <select
                        value={editingPartition.diskGroupId ?? ''}
                        onChange={(e) => setEditingPartition({
                          ...editingPartition, 
                          diskGroupId: e.target.value ? parseInt(e.target.value) : undefined
                        })}
                        className="w-full px-3 py-2 bg-cyber-dark border border-cyan-500/30 rounded text-cyber-text text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/30">
                        <option value="">自动选择</option>
                        {Object.entries(diskGroups).map(([groupId, group]) => (
                          <option key={groupId} value={groupId}>
                            磁盘组 {groupId} ({group.disks.length}x{group.disks[0]?.capacity}{group.disks[0]?.unit} {group.disks[0]?.technology})
                          </option>
                        ))}
                      </select>
                      <p className="text-xs text-cyan-400 mt-1">
                        用于配置多磁盘组场景（如NVMe系统盘 + HDD数据盘）
                      </p>
                    </div>
                  )}

                  {/* 软RAID级别 */}
                  {(useSoftwareRaid || editingPartition.raid) && (
                    <div>
                      <label className="block text-xs text-cyber-muted mb-1">软RAID级别</label>
                      <select
                        value={editingPartition.raid ?? ''}
                        onChange={(e) => setEditingPartition({
                          ...editingPartition, 
                          raid: e.target.value || undefined
                        })}
                        className="w-full px-3 py-2 bg-cyber-dark border border-purple-500/30 rounded text-cyber-text text-sm focus:border-purple-500 focus:ring-1 focus:ring-purple-500/30">
                        <option value="">不使用RAID</option>
                        <option value="raid0">RAID 0 - 条带化</option>
                        <option value="raid1">RAID 1 - 镜像</option>
                        <option value="raid5">RAID 5 - 奇偶校验</option>
                        <option value="raid6">RAID 6 - 双重奇偶校验</option>
                        <option value="raid10">RAID 10 - 镜像+条带化</option>
                      </select>
                    </div>
                  )}

                  {/* 提示 */}
                  <div className="bg-blue-500/10 border border-blue-500/30 rounded p-2 text-xs text-blue-300">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="w-3 h-3 flex-shrink-0 mt-0.5" />
                      <div>
                        <p className="font-semibold mb-1">分区配置提示：</p>
                        <ul className="list-disc list-inside space-y-0.5">
                          <li>挂载点: 根分区使用 /，交换分区使用 swap</li>
                          <li>大小: 设置为 0 将使用所有剩余空间</li>
                          <li>磁盘组: 多磁盘组时可指定分区位置（如系统盘 vs 数据盘）</li>
                        </ul>
                      </div>
                    </div>
                  </div>
                </div>

                {/* 操作按钮 */}
                <div className="flex justify-end gap-3 mt-4">
                  <button
                    onClick={() => {
                      setShowPartitionEditor(false);
                      setEditingPartition(null);
                      setEditingPartitionIndex(-1);
                    }}
                    className="px-4 py-2 bg-cyber-grid/50 border border-cyber-accent/30 rounded text-cyber-text hover:bg-cyber-accent/10">
                    取消
                  </button>
                  <button
                    onClick={() => {
                      if (editingPartitionIndex === -1) {
                        // 添加新分区
                        setCustomPartitions([...customPartitions, editingPartition]);
                      } else {
                        // 更新现有分区
                        const newPartitions = [...customPartitions];
                        newPartitions[editingPartitionIndex] = editingPartition;
                        setCustomPartitions(newPartitions);
                      }
                      setShowPartitionEditor(false);
                      setEditingPartition(null);
                      setEditingPartitionIndex(-1);
                    }}
                    className="px-4 py-2 bg-cyan-500 text-white rounded hover:bg-cyan-600">
                    确认
                  </button>
                </div>
              </motion.div>
            </div>
          )}
        </AnimatePresence>,
        document.body
      )}

      {/* Task 4: 任务列表对话框 */}
      {createPortal(
        <AnimatePresence>
          {showTasksDialog && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="cyber-card max-w-3xl w-full max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <Activity className="w-5 h-5 text-cyber-accent" />
                    <h3 className="text-xl font-semibold text-cyber-text">
                    任务列表 - {selectedServer?.name}
                  </h3>
                </div>
                <button
                  onClick={() => setShowTasksDialog(false)}
                  className="text-cyber-muted hover:text-cyber-text transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>

              {serverTasks.length === 0 ? (
                <div className="text-center py-8 text-cyber-muted">
                  暂无任务记录
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-cyber-accent/30">
                        <th className="text-left py-3 px-4 text-cyber-text font-semibold">任务ID</th>
                        <th className="text-left py-3 px-4 text-cyber-text font-semibold">操作</th>
                        <th className="text-left py-3 px-4 text-cyber-text font-semibold">状态</th>
                        <th className="text-left py-3 px-4 text-cyber-text font-semibold">开始时间</th>
                        <th className="text-left py-3 px-4 text-cyber-text font-semibold">完成时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {serverTasks.map((task) => (
                        <tr key={task.taskId} className="border-b border-cyber-accent/10">
                          <td className="py-3 px-4 font-mono text-sm text-cyber-text">
                            {task.taskId}
                          </td>
                          <td className="py-3 px-4 text-cyber-text">
                            <div className="flex items-center gap-3">
                              <span>{task.function}</span>
                              <button
                                onClick={() => openTimeslots(task)}
                                className="px-2 py-1 text-xs border border-cyber-accent/40 rounded hover:bg-cyber-accent/10"
                              >
                                可用时间段
                              </button>
                            </div>
                          </td>
                          <td className="py-3 px-4">
                            <span className={`text-sm capitalize ${
                              task.status === 'done' ? 'text-green-400' : 
                              task.status === 'error' ? 'text-red-400' : 
                              'text-yellow-400'
                            }`}>
                              {task.status}
                            </span>
                          </td>
                          <td className="py-3 px-4 text-sm text-cyber-muted">
                            {task.startDate ? new Date(task.startDate).toLocaleString('zh-CN') : '-'}
                          </td>
                          <td className="py-3 px-4 text-sm text-cyber-muted">
                            {task.doneDate ? new Date(task.doneDate).toLocaleString('zh-CN') : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="flex justify-end mt-6">
                <button
                  onClick={() => setShowTasksDialog(false)}
                  className="px-4 py-2 bg-cyber-accent text-white rounded-lg hover:bg-cyber-accent/80 transition-all">
                  关闭
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>,
      document.body
      )}

      {/* Task 10: 启动模式对话框 */}
      {createPortal(
        <AnimatePresence>
          {showBootModeDialog && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="cyber-card max-w-2xl w-full max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                  <HardDrive className="w-5 h-5 text-orange-400" />
                  <h3 className="text-xl font-semibold text-cyber-text">
                    启动模式 - {selectedServer?.name}
                  </h3>
                </div>
                <button
                  onClick={() => setShowBootModeDialog(false)}
                  className="text-cyber-muted hover:text-cyber-text transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <p className="text-cyber-muted text-sm mb-4">
                选择服务器的启动模式。切换后需要重启服务器才能生效。
              </p>

              <div className="space-y-3">
                {bootModes.map((mode) => (
                  <div
                    key={mode.id}
                    className={`p-4 border-2 rounded-lg transition-all cursor-pointer ${
                      mode.active
                        ? 'border-cyber-accent bg-cyber-accent/10'
                        : 'border-cyber-accent/20 hover:border-cyber-accent/40 hover:bg-cyber-grid/30'
                    }`}
                    onClick={() => !mode.active && changeBootMode(mode.id)}>
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <h4 className="text-lg font-semibold text-cyber-text">{mode.bootType}</h4>
                          {mode.active && (
                            <span className="px-2 py-1 bg-cyber-accent text-white text-xs rounded">当前</span>
                          )}
                        </div>
                        <p className="text-sm text-cyber-muted mt-1">{mode.description}</p>
                        {mode.kernel && (
                          <p className="text-xs text-cyber-muted/70 mt-1 font-mono">{mode.kernel}</p>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              <div className="flex justify-end mt-6">
                <button
                  onClick={() => setShowBootModeDialog(false)}
                  className="px-4 py-2 bg-cyber-accent text-white rounded-lg hover:bg-cyber-accent/80 transition-all">
                  关闭
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>,
      document.body
      )}

      {/* IPMI倒计时加载模态框 */}
      {createPortal(
        <AnimatePresence>
          {ipmiLoading && (
            <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-cyber-dark border border-cyber-accent rounded-lg p-8 max-w-md w-full text-center">
                
                <div className="flex justify-center mb-6">
                  <div className="relative">
                    <div className="w-24 h-24 rounded-full border-4 border-cyber-accent/30"></div>
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-4xl font-bold text-cyber-accent">{ipmiCountdown}</span>
                  </div>
                  <svg className="absolute inset-0 w-24 h-24 -rotate-90">
                    <circle
                      cx="48"
                      cy="48"
                      r="44"
                      stroke="currentColor"
                      strokeWidth="4"
                      fill="none"
                      className="text-cyber-accent"
                      strokeDasharray={`${(ipmiCountdown / 20) * 276.46} 276.46`}
                      strokeLinecap="round"
                    />
                  </svg>
                </div>
              </div>

              <h3 className="text-xl font-bold text-cyber-text mb-2">
                正在生成IPMI访问
              </h3>
              <p className="text-cyber-muted text-sm mb-4">
                请耐心等待，预计需要 20 秒
              </p>
              <div className="flex items-center justify-center gap-2 text-cyber-accent">
                <RefreshCw className="w-4 h-4 animate-spin" />
                <span className="text-sm">连接中...</span>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>,
      document.body
      )}

      {/* IPMI链接模态框 */}
      {createPortal(
        <AnimatePresence>
          {showIpmiLinkDialog && (
            <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className="bg-cyber-dark border border-cyber-accent rounded-lg p-6 max-w-2xl w-full">
                
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <Monitor className="w-6 h-6 text-cyber-accent" />
                    <h2 className="text-xl font-bold text-cyber-text">IPMI控制台</h2>
                </div>
                <button
                  onClick={() => setShowIpmiLinkDialog(false)}
                  className="p-2 hover:bg-cyber-grid/50 rounded-lg transition-all text-cyber-muted hover:text-cyber-text">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <p className="text-cyber-muted mb-6">
                IPMI控制台访问链接已生成，点击下方按钮打开控制台。
              </p>

              <div className="flex items-center gap-3 text-xs text-cyber-muted mb-6">
                <AlertCircle className="w-4 h-4" />
                <span>会话有效期: 15分钟 | 访问可能需要允许弹窗</span>
              </div>

              <div className="flex gap-3 justify-end">
                <button
                  onClick={() => setShowIpmiLinkDialog(false)}
                  className="px-4 py-2 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-grid/50 transition-all">
                  关闭
                </button>
                <a
                  href={ipmiLink}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => setShowIpmiLinkDialog(false)}
                  className="px-6 py-2 bg-cyber-accent text-white rounded-lg hover:bg-cyber-accent/80 transition-all flex items-center gap-2">
                  <Monitor className="w-4 h-4" />
                  打开IPMI控制台
                </a>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>,
      document.body
      )}

      {/* 安装进度模态框 */}
      {createPortal(
        <AnimatePresence>
          {showInstallProgress && (
            <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                className={`bg-cyber-dark border border-cyber-accent rounded-lg max-w-3xl w-full ${
                  installCompleted ? 'p-6 overflow-hidden' : 'p-6 max-h-[90vh] overflow-y-auto'
                }`}>
                
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center gap-3">
                    <HardDrive className="w-6 h-6 text-cyber-accent" />
                    <h2 className="text-2xl font-bold text-cyber-text">系统安装进度</h2>
                </div>
                {!installCompleted && (
                  <button
                    onClick={closeInstallProgress}
                    className="p-2 hover:bg-cyber-grid/50 rounded-lg transition-all text-cyber-muted hover:text-cyber-text">
                    <X className="w-5 h-5" />
                  </button>
                )}
              </div>

              {installCompleted ? (
                // 安装完成页面 - 在同一模态框内显示
                <div className="flex flex-col items-center justify-center py-6 px-8">
                  {/* 成功图标 - 带脉冲动画 */}
                  <motion.div 
                    className="mb-4 relative"
                    initial={{ scale: 0 }}
                    animate={{ scale: 1 }}
                    transition={{ type: "spring", duration: 0.6 }}>
                    {/* 外圈脉冲效果 */}
                    <div className="absolute inset-0 rounded-full bg-green-500/20 animate-ping" style={{ animationDuration: '2s' }}></div>
                    <div className="relative w-20 h-20 rounded-full bg-gradient-to-br from-green-400 to-green-600 flex items-center justify-center shadow-lg shadow-green-500/50">
                      <svg className="w-10 h-10 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                  </motion.div>

                  {/* 标题 */}
                  <h3 className="text-3xl font-bold text-cyber-text mb-2">
                    安装完成
                  </h3>
                  <p className="text-cyber-muted text-sm">系统已成功部署，请查收邮件获取登录信息</p>
                </div>
              ) : !installProgress ? (
                // 加载中
                <div className="flex flex-col items-center justify-center py-12">
                  <RefreshCw className="w-12 h-12 text-cyber-accent animate-spin mb-4" />
                  <p className="text-cyber-muted">正在获取安装进度...</p>
                </div>
              ) : (
                <>
                  {/* 进度条 */}
                  <div className="mb-6">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-cyber-text font-semibold">总体进度</span>
                      <span className="text-cyber-accent font-bold text-xl">{installProgress.progressPercentage}%</span>
                    </div>
                    <div className="w-full h-4 bg-cyber-grid/30 rounded-full overflow-hidden">
                      <motion.div
                        className="h-full bg-gradient-to-r from-cyber-accent to-blue-500 rounded-full"
                        initial={{ width: 0 }}
                        animate={{ width: `${installProgress.progressPercentage}%` }}
                        transition={{ duration: 0.5 }}
                      />
                    </div>
                    <div className="mt-2 text-sm text-cyber-muted text-center">
                      <span>{installProgress.completedSteps} / {installProgress.totalSteps} 步骤完成</span>
                    </div>
                  </div>

                  {/* 状态提示 */}
                  {installProgress.allDone && (
                    <div className="mb-4 p-4 bg-green-500/10 border border-green-500/30 rounded-lg flex items-center gap-3">
                      <Activity className="w-5 h-5 text-green-500" />
                      <span className="text-green-500 font-semibold">✅ 系统安装已完成！</span>
                    </div>
                  )}

                  {installProgress.hasError && (
                    <div className="mb-4 p-4 bg-red-500/10 border border-red-500/30 rounded-lg flex items-center gap-3">
                      <AlertCircle className="w-5 h-5 text-red-500" />
                      <span className="text-red-500 font-semibold">❌ 安装过程中出现错误</span>
                    </div>
                  )}

                  {/* 当前步骤 - 只显示正在执行的步骤 */}
                  <div>
                    <h3 className="text-lg font-semibold text-cyber-text mb-3">当前步骤</h3>
                    {(() => {
                      // 查找正在执行的步骤
                      const currentStep = installProgress.steps.find(s => s.status === 'doing');
                      // 如果没有正在执行的，显示最后完成的步骤
                      const lastDoneStep = [...installProgress.steps].reverse().find(s => s.status === 'done');
                      const stepToShow = currentStep || lastDoneStep;
                      
                      if (!stepToShow) return (
                        <div className="p-4 bg-cyber-grid/20 border border-cyber-accent/20 rounded-lg text-center text-cyber-muted">
                          准备中...
                        </div>
                      );
                      
                      return (
                        <div className={`p-4 rounded-lg border ${
                          stepToShow.status === 'done'
                            ? 'bg-green-500/10 border-green-500/30'
                            : stepToShow.status === 'doing'
                            ? 'bg-blue-500/10 border-blue-500/30'
                            : stepToShow.status === 'error'
                            ? 'bg-red-500/10 border-red-500/30'
                            : 'bg-cyber-grid/20 border-cyber-accent/20'
                        }`}>
                          <div className="flex items-center gap-3">
                            {stepToShow.status === 'done' && (
                              <span className="text-green-500 text-xl">✓</span>
                            )}
                            {stepToShow.status === 'doing' && (
                              <RefreshCw className="w-5 h-5 text-blue-500 animate-spin" />
                            )}
                            {stepToShow.status === 'error' && (
                              <AlertCircle className="w-5 h-5 text-red-500" />
                            )}
                            
                            <div className="flex-1">
                              <p className={`font-medium text-base ${
                                stepToShow.status === 'done'
                                  ? 'text-green-400'
                                  : stepToShow.status === 'doing'
                                  ? 'text-blue-400'
                                  : stepToShow.status === 'error'
                                  ? 'text-red-400'
                                  : 'text-cyber-muted'
                              }`}>
                                {stepToShow.comment || '处理中'}
                              </p>
                              {stepToShow.error && (
                                <p className="text-sm text-red-400 mt-1">错误: {stepToShow.error}</p>
                              )}
                            </div>

                            <span className={`text-xs px-3 py-1 rounded ${
                              stepToShow.status === 'done'
                                ? 'bg-green-500/20 text-green-400'
                                : stepToShow.status === 'doing'
                                ? 'bg-blue-500/20 text-blue-400'
                                : stepToShow.status === 'error'
                                ? 'bg-red-500/20 text-red-400'
                                : 'bg-cyber-grid/30 text-cyber-muted'
                            }`}>
                              {stepToShow.status === 'done' ? '完成' : 
                               stepToShow.status === 'doing' ? '进行中' : 
                               stepToShow.status === 'error' ? '错误' : '待处理'}
                            </span>
                          </div>
                        </div>
                      );
                    })()}
                  </div>

                  {/* 底部按钮 */}
                  <div className="flex justify-end mt-6">
                    {installProgress.allDone || installProgress.hasError ? (
                      <button
                        onClick={closeInstallProgress}
                        className="px-6 py-2 bg-cyber-accent text-white rounded-lg hover:bg-cyber-accent/80 transition-all">
                        关闭
                      </button>
                    ) : (
                      <button
                        onClick={closeInstallProgress}
                        className="px-6 py-2 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-grid/50 transition-all">
                        后台运行
                      </button>
                    )}
                  </div>
                </>
              )}
            </motion.div>
          </div>
        )}
      </AnimatePresence>,
      document.body
      )}

      {/* 硬件更换对话框 */}
      {createPortal(
        <AnimatePresence>
          {showHardwareReplaceDialog && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="cyber-card max-w-lg w-full max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <Cpu className="w-5 h-5 text-purple-400" />
                    <h3 className="text-xl font-semibold text-cyber-text">
                      硬件更换申请
                    </h3>
                  </div>
                  <button
                    onClick={() => {
                      setShowHardwareReplaceDialog(false);
                      setHardwareReplaceType('');
                      setHardwareReplaceComment('');
                      setHardwareReplaceDetails('');
                    }}
                    className="text-cyber-muted hover:text-cyber-text transition-colors">
                    <X className="w-5 h-5" />
                  </button>
                </div>

                <p className="text-cyber-muted text-sm mb-4">
                  为 {selectedServer?.name} 提交硬件更换申请
                </p>

                {!hardwareReplaceType ? (
                  /* 硬件类型选择界面 */
                  <div className="space-y-3">
                    <p className="text-cyber-text font-medium mb-3">请选择要更换的硬件类型：</p>
                    
                    <button
                      onClick={() => setHardwareReplaceType('hardDiskDrive')}
                      className="w-full p-4 bg-red-500/10 border-2 border-red-500/30 rounded-lg text-left hover:bg-red-500/20 hover:border-red-500/50 transition-all group">
                      <div className="flex items-center gap-3">
                        <HardDrive className="w-6 h-6 text-red-400" />
                        <div>
                          <h4 className="text-lg font-semibold text-red-400 group-hover:text-red-300">硬盘驱动器</h4>
                          <p className="text-sm text-cyber-muted mt-1">申请更换故障或损坏的硬盘</p>
                        </div>
                      </div>
                    </button>

                    <button
                      onClick={() => setHardwareReplaceType('memory')}
                      className="w-full p-4 bg-orange-500/10 border-2 border-orange-500/30 rounded-lg text-left hover:bg-orange-500/20 hover:border-orange-500/50 transition-all group">
                      <div className="flex items-center gap-3">
                        <Cpu className="w-6 h-6 text-orange-400" />
                        <div>
                          <h4 className="text-lg font-semibold text-orange-400 group-hover:text-orange-300">内存（RAM）</h4>
                          <p className="text-sm text-cyber-muted mt-1">申请更换故障的内存模块</p>
                        </div>
                      </div>
                    </button>

                    <button
                      onClick={() => setHardwareReplaceType('cooling')}
                      className="w-full p-4 bg-blue-500/10 border-2 border-blue-500/30 rounded-lg text-left hover:bg-blue-500/20 hover:border-blue-500/50 transition-all group">
                      <div className="flex items-center gap-3">
                        <Activity className="w-6 h-6 text-blue-400" />
                        <div>
                          <h4 className="text-lg font-semibold text-blue-400 group-hover:text-blue-300">散热系统</h4>
                          <p className="text-sm text-cyber-muted mt-1">申请更换风扇或散热器</p>
                        </div>
                      </div>
                    </button>

                    <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-4 mt-4">
                      <div className="flex items-start gap-2">
                        <AlertCircle className="w-5 h-5 text-purple-400 flex-shrink-0 mt-0.5" />
                        <div className="text-sm text-purple-300">
                          <p className="font-semibold mb-1">提示：</p>
                          <p>选择硬件类型后，您需要填写详细的故障信息以便OVH技术团队处理。</p>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  /* 详细信息表单 */
                  <div className="space-y-4">
                    {/* 组件类型显示（带返回按钮） */}
                    <div>
                      <label className="block text-cyber-text font-medium mb-2">组件类型</label>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 px-4 py-3 bg-cyber-grid/30 border border-cyber-accent/30 rounded-lg text-cyber-text">
                          {hardwareReplaceType === 'hardDiskDrive' && '硬盘驱动器'}
                          {hardwareReplaceType === 'memory' && '内存（RAM）'}
                          {hardwareReplaceType === 'cooling' && '散热系统'}
                        </div>
                        <button
                          onClick={() => setHardwareReplaceType('')}
                          className="px-3 py-3 bg-cyber-grid/50 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-accent/10 transition-all"
                          title="重新选择">
                          <RefreshCw className="w-4 h-4" />
                        </button>
                      </div>
                    </div>

                    {/* Comment输入 */}
                    <div>
                      <label className="block text-cyber-text font-medium mb-2">
                        备注说明（可选，建议使用英文）
                      </label>
                      <textarea
                        placeholder="Describe the issue in English (optional)..."
                        value={hardwareReplaceComment}
                        onChange={(e) => setHardwareReplaceComment(e.target.value)}
                        rows={3}
                        className="w-full px-4 py-3 bg-cyber-bg border-2 border-cyber-accent/40 rounded-lg text-cyber-text placeholder-cyber-muted focus:border-cyber-accent focus:ring-2 focus:ring-cyber-accent/30 hover:border-cyber-accent/60 transition-all resize-none"
                        style={{
                          background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)'
                        }}
                      />
                      <p className="text-xs text-cyber-muted mt-1">提交给OVH的工单内容，建议使用英文描述</p>
                    </div>

                    {/* Details 输入（仅 memory 和 cooling 需要） */}
                    {(hardwareReplaceType === 'memory' || hardwareReplaceType === 'cooling') && (
                      <div>
                        <label className="block text-cyber-text font-medium mb-2">
                          故障详情（{hardwareReplaceType === 'memory' ? '内存必填' : '散热必填'}，建议使用英文）
                        </label>
                        <input
                          type="text"
                          placeholder={
                            hardwareReplaceType === 'memory' 
                              ? 'e.g., Memory module failure, slot 1' 
                              : 'e.g., Fan noise, overheating issue'
                          }
                          value={hardwareReplaceDetails}
                          onChange={(e) => setHardwareReplaceDetails(e.target.value)}
                          className="w-full px-4 py-3 bg-cyber-bg border-2 border-cyber-accent/40 rounded-lg text-cyber-text placeholder-cyber-muted focus:border-cyber-accent focus:ring-2 focus:ring-cyber-accent/30 hover:border-cyber-accent/60 transition-all"
                          style={{
                            background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)'
                          }}
                        />
                        <p className="text-xs text-cyber-muted mt-1">提交给OVH的技术详情，建议使用英文描述</p>
                      </div>
                    )}

                    {/* 警告提示 */}
                    <div className="bg-orange-500/10 border border-orange-500/30 rounded-lg p-4">
                      <div className="flex items-start gap-2">
                        <AlertCircle className="w-5 h-5 text-orange-400 flex-shrink-0 mt-0.5" />
                        <div className="text-sm text-orange-300">
                          <p className="font-semibold mb-1">重要提示：</p>
                          <ul className="list-disc list-inside space-y-1">
                            <li>系统将创建工单提交给OVH客服</li>
                            <li>OVH将安排硬件更换时间</li>
                            <li>更换期间服务器可能离线</li>
                            <li>进度更新将通过邮件通知</li>
                          </ul>
                        </div>
                      </div>
                    </div>

                    {/* 提交按钮 */}
                    <div className="flex justify-end gap-3 mt-6">
                      <button
                        onClick={() => setHardwareReplaceType('')}
                        disabled={isProcessing}
                        className="px-4 py-2 bg-cyber-grid/50 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-accent/10 disabled:opacity-50">
                        返回
                      </button>
                      <button
                        onClick={handleHardwareReplace}
                        disabled={isProcessing}
                        className="px-4 py-2 bg-purple-500 text-white rounded-lg hover:bg-purple-600 disabled:opacity-50 flex items-center gap-2">
                        {isProcessing && <RefreshCw className="w-4 h-4 animate-spin" />}
                        提交申请
                      </button>
                    </div>
                  </div>
                )}
              </motion.div>
            </div>
          )}

          {/* 变更联系人对话框 */}
          {showChangeContactDialog && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="cyber-card max-w-4xl w-full max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <Mail className="w-5 h-5 text-green-400" />
                    <h3 className="text-xl font-semibold text-cyber-text">
                      变更服务器联系人
                    </h3>
                  </div>
                  <button
                    onClick={() => {
                      setShowChangeContactDialog(false);
                      setContactAdmin('');
                      setContactTech('');
                      setContactBilling('');
                      setContactDialogTab('submit');
                    }}
                    className="text-cyber-muted hover:text-cyber-text transition-colors">
                    <X className="w-5 h-5" />
                  </button>
                </div>

                {/* 标签页 */}
                <div className="flex gap-2 mb-4 border-b border-cyber-accent/20">
                  <button
                    onClick={() => setContactDialogTab('submit')}
                    className={`px-4 py-2 font-medium transition-colors ${
                      contactDialogTab === 'submit'
                        ? 'text-green-400 border-b-2 border-green-400'
                        : 'text-cyber-muted hover:text-cyber-text'
                    }`}>
                    提交变更
                  </button>
                  <button
                    onClick={() => {
                      setContactDialogTab('requests');
                      fetchContactChangeRequests();
                    }}
                    className={`px-4 py-2 font-medium transition-colors ${
                      contactDialogTab === 'requests'
                        ? 'text-green-400 border-b-2 border-green-400'
                        : 'text-cyber-muted hover:text-cyber-text'
                    }`}>
                    管理请求 ({contactChangeRequests.length})
                  </button>
                </div>

                {/* 提交变更标签页 */}
                {contactDialogTab === 'submit' && (
                <div className="space-y-4">
                  {/* 管理员联系人 */}
                  <div>
                    <label className="block text-cyber-text font-medium mb-2">
                      管理员联系人 (Contact Admin)
                    </label>
                    <input
                      type="text"
                      placeholder="例如: lp1234567-ovh"
                      value={contactAdmin}
                      onChange={(e) => setContactAdmin(e.target.value)}
                      className="w-full px-4 py-3 bg-cyber-bg border-2 border-cyber-accent/40 rounded-lg text-cyber-text placeholder-cyber-muted focus:border-cyber-accent focus:ring-2 focus:ring-cyber-accent/30 hover:border-cyber-accent/60 transition-all"
                      style={{
                        background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)'
                      }}
                    />
                    <p className="text-xs text-cyber-muted mt-1">OVH账户NIC handle（留空则不修改）</p>
                  </div>

                  {/* 技术联系人 */}
                  <div>
                    <label className="block text-cyber-text font-medium mb-2">
                      技术联系人 (Contact Tech)
                    </label>
                    <input
                      type="text"
                      placeholder="例如: lp1234567-ovh"
                      value={contactTech}
                      onChange={(e) => setContactTech(e.target.value)}
                      className="w-full px-4 py-3 bg-cyber-bg border-2 border-cyber-accent/40 rounded-lg text-cyber-text placeholder-cyber-muted focus:border-cyber-accent focus:ring-2 focus:ring-cyber-accent/30 hover:border-cyber-accent/60 transition-all"
                      style={{
                        background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)'
                      }}
                    />
                    <p className="text-xs text-cyber-muted mt-1">OVH账户NIC handle（留空则不修改）</p>
                  </div>

                  {/* 计费联系人 */}
                  <div>
                    <label className="block text-cyber-text font-medium mb-2">
                      计费联系人 (Contact Billing)
                    </label>
                    <input
                      type="text"
                      placeholder="例如: lp1234567-ovh"
                      value={contactBilling}
                      onChange={(e) => setContactBilling(e.target.value)}
                      className="w-full px-4 py-3 bg-cyber-bg border-2 border-cyber-accent/40 rounded-lg text-cyber-text placeholder-cyber-muted focus:border-cyber-accent focus:ring-2 focus:ring-cyber-accent/30 hover:border-cyber-accent/60 transition-all"
                      style={{
                        background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)'
                      }}
                    />
                    <p className="text-xs text-cyber-muted mt-1">OVH账户NIC handle（留空则不修改）</p>
                  </div>

                  {/* 信息提示 */}
                  <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-4">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="w-5 h-5 text-green-400 flex-shrink-0 mt-0.5" />
                      <div className="text-sm text-green-300">
                        <p className="font-semibold mb-1">说明：</p>
                        <ul className="list-disc list-inside space-y-1">
                          <li>至少需要填写一个联系人信息</li>
                          <li>使用OVH账户的NIC handle格式</li>
                          <li>变更后需要通过邮件验证确认</li>
                          <li>联系人需要是有效的OVH账户</li>
                        </ul>
                      </div>
                    </div>
                  </div>

                  {/* 警告提示 */}
                  <div className="bg-orange-500/10 border border-orange-500/30 rounded-lg p-4">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="w-5 h-5 text-orange-400 flex-shrink-0 mt-0.5" />
                      <div className="text-sm text-orange-300">
                        <p className="font-semibold mb-1">重要提示：</p>
                        <ul className="list-disc list-inside space-y-1">
                          <li>变更联系人需要双方确认</li>
                          <li>确认邮件将发送至新旧联系人</li>
                          <li>完成验证后才会生效</li>
                        </ul>
                      </div>
                    </div>
                  </div>

                  {/* 提交按钮 */}
                  <div className="flex justify-end gap-3 mt-6">
                    <button
                      onClick={() => {
                        setShowChangeContactDialog(false);
                        setContactAdmin('');
                        setContactTech('');
                        setContactBilling('');
                        setContactDialogTab('submit');
                      }}
                      disabled={loadingChangeContact}
                      className="px-4 py-2 bg-cyber-grid/50 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-accent/10 disabled:opacity-50">
                      取消
                    </button>
                    <button
                      onClick={handleChangeContact}
                      disabled={loadingChangeContact}
                      className="px-4 py-2 bg-green-500 text-white rounded-lg hover:bg-green-600 disabled:opacity-50 flex items-center gap-2">
                      {loadingChangeContact && <RefreshCw className="w-4 h-4 animate-spin" />}
                      提交变更
                    </button>
                  </div>
                </div>
                )}

                {/* 管理请求标签页 */}
                {contactDialogTab === 'requests' && (
                  <div className="space-y-4">
                    {/* 刷新按钮 */}
                    <div className="flex justify-end">
                      <button
                        onClick={fetchContactChangeRequests}
                        disabled={loadingContactRequests}
                        className="px-3 py-2 bg-cyber-grid/50 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-accent/10 disabled:opacity-50 flex items-center gap-2 text-sm">
                        <RefreshCw className={`w-4 h-4 ${loadingContactRequests ? 'animate-spin' : ''}`} />
                        刷新
                      </button>
                    </div>

                    {/* 请求列表 */}
                    {loadingContactRequests ? (
                      <div className="flex justify-center items-center py-8">
                        <RefreshCw className="w-6 h-6 animate-spin text-cyber-accent" />
                      </div>
                    ) : contactChangeRequests.length === 0 ? (
                      <div className="text-center py-8 text-cyber-muted">
                        暂无联系人变更请求
                      </div>
                    ) : (
                      <div className="space-y-3 max-h-[500px] overflow-y-auto">
                        {contactChangeRequests.map((request) => (
                          <div
                            key={request.id}
                            className="bg-cyber-grid/30 border border-cyber-accent/30 rounded-lg p-4">
                            <div className="flex items-start justify-between mb-3">
                              <div className="flex-1">
                                <div className="flex items-center gap-2 mb-2">
                                  <span className="text-sm font-semibold text-cyber-text">
                                    请求 ID: {request.id}
                                  </span>
                                  <span className={`px-2 py-1 rounded text-xs ${
                                    request.state === 'done' ? 'bg-green-500/20 text-green-400' :
                                    request.state === 'todo' ? 'bg-yellow-500/20 text-yellow-400' :
                                    request.state === 'doing' ? 'bg-blue-500/20 text-blue-400' :
                                    request.state === 'validatingByCustomers' ? 'bg-yellow-500/20 text-yellow-400' :
                                    'bg-red-500/20 text-red-400'
                                  }`}>
                                    {request.state === 'done' ? '已完成' :
                                     request.state === 'todo' ? '待处理' :
                                     request.state === 'doing' ? '处理中' :
                                     request.state === 'validatingByCustomers' ? '等待验证' :
                                     request.state}
                                  </span>
                                </div>
                                <div className="text-sm text-cyber-muted space-y-1">
                                  {request.serviceDomain && (
                                    <div>服务: {request.serviceDomain}</div>
                                  )}
                                  {request.fromAccount && (
                                    <div>原账户: {request.fromAccount}</div>
                                  )}
                                  {request.toAccount && (
                                    <div>目标账户: {request.toAccount}</div>
                                  )}
                                  {request.askingAccount && (
                                    <div>请求账户: {request.askingAccount}</div>
                                  )}
                                  <div>联系人类型: {request.contactTypes.join(', ')}</div>
                                  <div>请求时间: {new Date(request.dateRequest).toLocaleString('zh-CN')}</div>
                                  {request.dateDone && (
                                    <div>完成时间: {new Date(request.dateDone).toLocaleString('zh-CN')}</div>
                                  )}
                                </div>
                              </div>
                            </div>
                            {/* 操作按钮 - 显示在待处理或等待验证状态 */}
                            {(request.state === 'todo' || request.state === 'validatingByCustomers') && (
                              <>
                                {/* 提示信息 */}
                                <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3 mt-3">
                                  <div className="flex items-start gap-2">
                                    <AlertCircle className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" />
                                    <div className="text-xs text-blue-300">
                                      <p className="font-semibold mb-1">操作提示：</p>
                                      <p>点击"接受"或"拒绝"按钮后，需要输入从邮件中获取的 token 值。如果未收到邮件，请点击"重发邮件"按钮。</p>
                                    </div>
                                  </div>
                                </div>
                                
                                <div className="flex gap-2 mt-3 pt-3 border-t border-cyber-accent/20">
                                  <button
                                    onClick={() => openTokenDialog(request, 'accept')}
                                    className="flex-1 px-3 py-2 bg-green-500/20 border border-green-500/50 rounded-lg text-green-400 hover:bg-green-500/30 text-sm transition-colors">
                                    接受
                                  </button>
                                  <button
                                    onClick={() => openTokenDialog(request, 'refuse')}
                                    className="flex-1 px-3 py-2 bg-red-500/20 border border-red-500/50 rounded-lg text-red-400 hover:bg-red-500/30 text-sm transition-colors">
                                    拒绝
                                  </button>
                                  <button
                                    onClick={() => handleResendEmail(request)}
                                    className="px-3 py-2 bg-blue-500/20 border border-blue-500/50 rounded-lg text-blue-400 hover:bg-blue-500/30 text-sm transition-colors">
                                    重发邮件
                                  </button>
                                </div>
                              </>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </motion.div>
            </div>
          )}

          {/* Token 输入对话框 */}
          {showTokenDialog && selectedRequest && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="cyber-card max-w-md w-full">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <Mail className="w-5 h-5 text-green-400" />
                    <h3 className="text-xl font-semibold text-cyber-text">
                      {tokenAction === 'accept' ? '接受' : '拒绝'}联系人变更请求
                    </h3>
                  </div>
                  <button
                    onClick={() => {
                      setShowTokenDialog(false);
                      setToken('');
                      setTokenAction(null);
                      setSelectedRequest(null);
                    }}
                    className="text-cyber-muted hover:text-cyber-text transition-colors">
                    <X className="w-5 h-5" />
                  </button>
                </div>

                <div className="space-y-4">
                  <div className="bg-cyber-grid/30 border border-cyber-accent/30 rounded-lg p-3 mb-4">
                    <div className="text-sm text-cyber-muted space-y-1">
                      <div>请求 ID: {selectedRequest.id}</div>
                      {selectedRequest.serviceDomain && (
                        <div>服务: {selectedRequest.serviceDomain}</div>
                      )}
                    </div>
                  </div>

                  <div>
                    <label className="block text-cyber-text font-medium mb-2">
                      验证 Token（从邮件中获取）
                    </label>
                    <input
                      type="text"
                      placeholder="请输入邮件中的 token"
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                      className="w-full px-4 py-3 bg-cyber-bg border-2 border-cyber-accent/40 rounded-lg text-cyber-text placeholder-cyber-muted focus:border-cyber-accent focus:ring-2 focus:ring-cyber-accent/30 hover:border-cyber-accent/60 transition-all"
                      style={{
                        background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)'
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && token && !loadingTokenAction) {
                          handleTokenAction();
                        }
                      }}
                    />
                    <p className="text-xs text-cyber-muted mt-1">
                      请在收到的确认邮件中查找 token 值
                    </p>
                  </div>

                  <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="w-5 h-5 text-yellow-400 flex-shrink-0 mt-0.5" />
                      <div className="text-sm text-yellow-300">
                        <p className="font-semibold mb-2">如何获取 Token：</p>
                        <ol className="list-decimal list-inside space-y-1 ml-2">
                          <li>检查您的邮箱，查找 OVH 发送的联系人变更确认邮件</li>
                          <li>在邮件中找到 "using the following token:" 后面的 token 值</li>
                          <li>或者从邮件中的确认链接 URL 中提取 token（URL 参数中的 token=xxx）</li>
                          <li>将 token 值复制并粘贴到上面的输入框中</li>
                        </ol>
                        <p className="mt-2 text-xs opacity-90">
                          示例：邮件中会显示类似 "PIgjlgCopAaisey3gexfq8fukvQFKAMW" 的 token 值
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="flex justify-end gap-3 mt-6">
                    <button
                      onClick={() => {
                        setShowTokenDialog(false);
                        setToken('');
                        setTokenAction(null);
                        setSelectedRequest(null);
                      }}
                      disabled={loadingTokenAction}
                      className="px-4 py-2 bg-cyber-grid/50 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-accent/10 disabled:opacity-50">
                      取消
                    </button>
                    <button
                      onClick={handleTokenAction}
                      disabled={loadingTokenAction || !token}
                      className={`px-4 py-2 rounded-lg text-white disabled:opacity-50 flex items-center gap-2 ${
                        tokenAction === 'accept'
                          ? 'bg-green-500 hover:bg-green-600'
                          : 'bg-red-500 hover:bg-red-600'
                      }`}>
                      {loadingTokenAction && <RefreshCw className="w-4 h-4 animate-spin" />}
                      {tokenAction === 'accept' ? '确认接受' : '确认拒绝'}
                    </button>
                  </div>
                </div>
              </motion.div>
            </div>
          )}

        </AnimatePresence>,
        document.body
      )}

      {/* 智能配置确认对话框 */}
      {createPortal(
        <AnimatePresence>
          {showSmartConfigDialog && (
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
              {/* 背景遮罩 */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setShowSmartConfigDialog(false)}
                className="absolute inset-0 bg-black/60 backdrop-blur-sm"
              />

              {/* 对话框内容 */}
              <motion.div
                initial={{ opacity: 0, scale: 0.9, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.9, y: 20 }}
                className="relative bg-gradient-to-br from-cyber-bg via-cyber-grid to-cyber-bg border border-cyber-accent rounded-xl shadow-2xl max-w-md w-full overflow-hidden">
                
                {/* 顶部装饰线 */}
                <div className="h-1 bg-gradient-to-r from-cyan-500 via-purple-500 to-pink-500" />
                
                <div className="p-6">
                  {/* 标题 */}
                  <div className="flex items-center gap-3 mb-4">
                    <div className="p-2 bg-gradient-to-r from-purple-500/20 to-pink-500/20 rounded-lg">
                      <Activity className="w-6 h-6 text-purple-400" />
                    </div>
                    <h3 className="text-xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-purple-400">
                      智能配置将根据磁盘组自动生成最佳方案：
                    </h3>
                  </div>

                  {/* 磁盘信息 */}
                  <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-4 mb-4">
                    <div className="flex items-start gap-3">
                      <div className="p-1.5 bg-gradient-to-r from-purple-500 to-pink-500 rounded-md">
                        <HardDrive className="w-4 h-4 text-white" />
                      </div>
                      <div className="flex-1">
                        <p className="text-xs text-purple-300 font-semibold mb-2">检测到的磁盘配置</p>
                        <div className="text-sm text-cyber-text font-mono whitespace-pre-line">
                          {smartConfigInfo.diskDetails}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 配置方案 */}
                  <div className="bg-cyber-grid/30 border border-cyber-accent/30 rounded-lg p-4 mb-4">
                    <div className="flex items-start gap-3">
                      <div className="p-1.5 bg-gradient-to-r from-cyan-500 to-blue-500 rounded-md">
                        <Activity className="w-4 h-4 text-white" />
                      </div>
                      <div className="flex-1">
                        <p className="text-xs text-cyan-300 font-semibold mb-2">智能推荐方案</p>
                        <p className="text-cyber-text font-medium">{smartConfigInfo.scenario}</p>
                      </div>
                    </div>
                  </div>

                  {/* 提示信息 */}
                  {smartConfigInfo.groupCount >= 2 ? (
                    <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3 mb-6">
                      <div className="flex items-start gap-2">
                        <AlertCircle className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" />
                        <div className="text-xs text-blue-300">
                          <p className="font-semibold mb-1">推荐方案</p>
                          <p>多磁盘组服务器建议使用默认分区，OVH会自动优化分配：</p>
                          <ul className="list-disc list-inside mt-2 space-y-1">
                            <li>系统分区自动分配到最快磁盘</li>
                            <li>数据盘自动配置为可用空间</li>
                            <li>无需手动配置，安全可靠</li>
                          </ul>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="bg-cyan-500/10 border border-cyan-500/30 rounded-lg p-3 mb-6">
                      <div className="flex items-start gap-2">
                        <AlertCircle className="w-4 h-4 text-cyan-400 flex-shrink-0 mt-0.5" />
                        <p className="text-xs text-cyan-300">
                          是否应用智能配置？
                        </p>
                      </div>
                    </div>
                  )}

                  {/* 按钮 */}
                  <div className="flex justify-end gap-3">
                    <button
                      onClick={() => setShowSmartConfigDialog(false)}
                      className="px-6 py-2.5 bg-cyber-grid/50 border border-cyber-accent/30 rounded-lg text-cyber-text hover:bg-cyber-accent/10 transition-all">
                      取消
                    </button>
                    <button
                      onClick={() => {
                        applySmartConfig();
                        setShowSmartConfigDialog(false);
                      }}
                      className="px-6 py-2.5 bg-gradient-to-r from-cyan-500 to-purple-500 text-white rounded-lg hover:from-cyan-600 hover:to-purple-600 transition-all shadow-lg shadow-cyan-500/20 flex items-center gap-2">
                      <Check className="w-4 h-4" />
                      确定
                    </button>
                  </div>
                </div>
              </motion.div>
            </div>
          )}
        </AnimatePresence>,
        document.body
      )}

      {/* 网络规格对话框 */}
      {createPortal(
        <AnimatePresence>
          {showNetworkSpecsDialog && (
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
              {/* 背景遮罩 */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setShowNetworkSpecsDialog(false)}
                className="absolute inset-0 bg-black/70 backdrop-blur-md"
              />

              {/* 对话框内容 */}
              <motion.div
                initial={{ opacity: 0, scale: 0.96, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.96, y: 20 }}
                className="relative bg-cyber-bg/95 backdrop-blur-xl border border-cyan-500/30 rounded-xl shadow-2xl shadow-cyan-500/10 max-w-6xl w-full max-h-[88vh] overflow-hidden">
                
                {/* 顶部装饰线 */}
                <div className="h-0.5 bg-gradient-to-r from-cyan-500 via-blue-500 to-purple-500" />
                
                {/* 标题栏 */}
                <div className="flex items-center justify-between px-4 py-2.5 border-b border-cyan-500/20 bg-gradient-to-r from-cyan-500/5 to-transparent">
                  <div className="flex items-center gap-2">
                    <div className="p-1.5 bg-gradient-to-br from-cyan-500/20 to-blue-500/20 rounded-lg border border-cyan-500/30">
                      <Wifi className="w-4 h-4 text-cyan-400" />
                    </div>
                    <div>
                      <h3 className="text-base font-bold text-cyber-text flex items-center gap-1.5">
                        网络规格
                        {loadingNetworkSpecs && (
                          <RefreshCw className="w-3 h-3 animate-spin text-cyan-400" />
                        )}
                      </h3>
                      <p className="text-[10px] text-cyber-muted/80 leading-none">
                        {selectedServer?.name} · {selectedServer?.serviceName}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => selectedServer && fetchNetworkSpecs(selectedServer.serviceName)}
                      disabled={loadingNetworkSpecs}
                      className="relative w-8 h-8 sm:w-9 sm:h-9 flex items-center justify-center bg-cyber-bg/60 hover:bg-cyan-500/10 border border-cyan-500/40 hover:border-cyan-500/60 rounded-lg transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed active:scale-95 group overflow-hidden"
                      title="刷新">
                      <RefreshCw className={`relative z-10 w-3.5 h-3.5 sm:w-4 sm:h-4 text-cyan-400 group-hover:text-cyan-300 transition-colors duration-200 ${loadingNetworkSpecs ? 'animate-spin' : ''}`} />
                      <div className="absolute inset-0 rounded-lg bg-gradient-to-br from-cyan-500/0 via-cyan-500/0 to-blue-500/0 group-hover:from-cyan-500/10 group-hover:via-cyan-500/5 group-hover:to-blue-500/10 transition-all duration-200 pointer-events-none" />
                      <div className="absolute inset-0 rounded-lg opacity-0 group-hover:opacity-100 bg-cyan-500/5 blur-sm transition-opacity duration-200 pointer-events-none" />
                    </button>
                    <button
                      onClick={() => setShowNetworkSpecsDialog(false)}
                      className="relative w-8 h-8 sm:w-9 sm:h-9 flex items-center justify-center bg-cyber-bg/60 hover:bg-red-500/10 border border-red-500/40 hover:border-red-500/60 rounded-lg transition-all duration-200 group active:scale-95 overflow-hidden"
                      title="关闭">
                      <X className="relative z-10 w-3.5 h-3.5 sm:w-4 sm:h-4 text-red-400 group-hover:text-red-300 transition-colors duration-200" />
                      <div className="absolute inset-0 rounded-lg bg-gradient-to-br from-red-500/0 via-red-500/0 to-orange-500/0 group-hover:from-red-500/10 group-hover:via-red-500/5 group-hover:to-orange-500/10 transition-all duration-200 pointer-events-none" />
                      <div className="absolute inset-0 rounded-lg opacity-0 group-hover:opacity-100 bg-red-500/5 blur-sm transition-opacity duration-200 pointer-events-none" />
                    </button>
                  </div>
                </div>

                {/* 内容区域 */}
                <div className="p-4 overflow-y-auto max-h-[calc(88vh-48px)] custom-scrollbar">
                  {loadingNetworkSpecs ? (
                    <div className="flex items-center justify-center py-12">
                      <RefreshCw className="w-8 h-8 animate-spin text-cyber-accent" />
                    </div>
                  ) : networkSpecs ? (
                    <div className="space-y-3.5">
                      {/* 带宽类型提示 */}
                      {networkSpecs.bandwidth?.type && (
                        <div className="flex items-center gap-2 px-3 py-2 bg-gradient-to-r from-green-500/10 to-cyan-500/10 border border-green-500/30 rounded-lg">
                          <div className="p-1 bg-green-500/10 rounded">
                            <BarChart3 className="w-3.5 h-3.5 text-green-400" />
                          </div>
                          <span className="text-xs text-cyber-muted">带宽类型:</span>
                          <span className="text-sm font-semibold text-green-400 capitalize">
                            {networkSpecs.bandwidth.type === 'improved' ? '升级带宽' : 
                             networkSpecs.bandwidth.type === 'included' ? '标准带宽' : 
                             networkSpecs.bandwidth.type}
                          </span>
                        </div>
                      )}

                      {/* 核心指标卡片组 */}
                      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                        {/* 出站带宽 */}
                        {networkSpecs.bandwidth?.OvhToInternet && (
                          <div className="bg-gradient-to-br from-cyan-500/5 to-blue-500/5 hover:from-cyan-500/10 hover:to-blue-500/10 border border-cyan-500/30 rounded-lg p-3.5 transition-all">
                            <div className="flex items-center justify-between mb-2.5">
                              <div className="p-1.5 bg-cyan-500/10 rounded">
                                <BarChart3 className="w-3.5 h-3.5 text-cyan-400" />
                              </div>
                              <span className="text-xs text-cyan-400/80 font-medium">出站</span>
                            </div>
                            <div className="text-xl font-bold text-cyan-400 leading-none mb-1.5">
                              {networkSpecs.bandwidth.OvhToInternet.value}
                              <span className="text-xs ml-1 text-cyan-400/70">{networkSpecs.bandwidth.OvhToInternet.unit}</span>
                            </div>
                            <div className="text-xs text-cyber-muted">OVH → Internet</div>
                          </div>
                        )}
                        
                        {/* 入站带宽 */}
                        {networkSpecs.bandwidth?.InternetToOvh && (
                          <div className="bg-gradient-to-br from-blue-500/5 to-indigo-500/5 hover:from-blue-500/10 hover:to-indigo-500/10 border border-blue-500/30 rounded-lg p-3.5 transition-all">
                            <div className="flex items-center justify-between mb-2.5">
                              <div className="p-1.5 bg-blue-500/10 rounded">
                                <BarChart3 className="w-3.5 h-3.5 text-blue-400" />
                              </div>
                              <span className="text-xs text-blue-400/80 font-medium">入站</span>
                            </div>
                            <div className="text-xl font-bold text-blue-400 leading-none mb-1.5">
                              {networkSpecs.bandwidth.InternetToOvh.value}
                              <span className="text-xs ml-1 text-blue-400/70">{networkSpecs.bandwidth.InternetToOvh.unit}</span>
                            </div>
                            <div className="text-xs text-cyber-muted">Internet → OVH</div>
                          </div>
                        )}
                        
                        {/* 连接速度 */}
                        {networkSpecs.connection && (
                          <div className="bg-gradient-to-br from-purple-500/5 to-pink-500/5 hover:from-purple-500/10 hover:to-pink-500/10 border border-purple-500/30 rounded-lg p-3.5 transition-all">
                            <div className="flex items-center justify-between mb-2.5">
                              <div className="p-1.5 bg-purple-500/10 rounded">
                                <Wifi className="w-3.5 h-3.5 text-purple-400" />
                              </div>
                              <span className="text-xs text-purple-400/80 font-medium">端口</span>
                            </div>
                            <div className="text-xl font-bold text-purple-400 leading-none mb-1.5">
                              {networkSpecs.connection.value}
                              <span className="text-xs ml-1 text-purple-400/70">{networkSpecs.connection.unit}</span>
                            </div>
                            <div className="text-xs text-cyber-muted">连接速度</div>
                          </div>
                        )}
                        
                        {/* 内网带宽 */}
                        {networkSpecs.bandwidth?.OvhToOvh && (
                          <div className="bg-gradient-to-br from-pink-500/5 to-rose-500/5 hover:from-pink-500/10 hover:to-rose-500/10 border border-pink-500/30 rounded-lg p-3.5 transition-all">
                            <div className="flex items-center justify-between mb-2.5">
                              <div className="p-1.5 bg-pink-500/10 rounded">
                                <Activity className="w-3.5 h-3.5 text-pink-400" />
                              </div>
                              <span className="text-xs text-pink-400/80 font-medium">内网</span>
                            </div>
                            <div className="text-xl font-bold text-pink-400 leading-none mb-1.5">
                              {networkSpecs.bandwidth.OvhToOvh.value}
                              <span className="text-xs ml-1 text-pink-400/70">{networkSpecs.bandwidth.OvhToOvh.unit}</span>
                            </div>
                            <div className="text-xs text-cyber-muted">OVH 内部</div>
                          </div>
                        )}
                      </div>

                      {/* 路由配置 */}
                      {networkSpecs.routing && (
                        <div className="bg-gradient-to-br from-green-500/5 to-cyan-500/5 border border-green-500/30 rounded-lg p-3.5">
                          <h4 className="text-sm font-semibold text-green-400 mb-3 flex items-center gap-2">
                            <div className="p-1 bg-green-500/10 rounded">
                              <Wifi className="w-3.5 h-3.5" />
                            </div>
                            路由配置
                          </h4>
                          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                            {networkSpecs.routing.ipv4 && (
                              <div className="bg-cyber-bg/30 backdrop-blur-sm border border-green-500/20 rounded-lg p-3">
                                <div className="text-xs font-medium text-green-400 mb-2.5 flex items-center gap-1.5">
                                  <div className="w-2 h-2 rounded-full bg-green-400"></div>
                                  IPv4
                                </div>
                                <div className="space-y-2 font-mono text-xs">
                                  <div className="flex justify-between items-center">
                                    <span className="text-cyber-muted">IP地址</span>
                                    <span className="text-cyber-text font-medium">{networkSpecs.routing.ipv4.ip}</span>
                                  </div>
                                  <div className="flex justify-between items-center">
                                    <span className="text-cyber-muted">网关</span>
                                    <span className="text-cyber-text font-medium">{networkSpecs.routing.ipv4.gateway}</span>
                                  </div>
                                  <div className="flex justify-between items-center">
                                    <span className="text-cyber-muted">网络</span>
                                    <span className="text-cyber-text font-medium">{networkSpecs.routing.ipv4.network}</span>
                                  </div>
                                </div>
                              </div>
                            )}
                            {networkSpecs.routing.ipv6 && (
                              <div className="bg-cyber-bg/30 backdrop-blur-sm border border-cyan-500/20 rounded-lg p-3">
                                <div className="text-xs font-medium text-cyan-400 mb-2.5 flex items-center gap-1.5">
                                  <div className="w-2 h-2 rounded-full bg-cyan-400"></div>
                                  IPv6
                                </div>
                                <div className="space-y-2 font-mono text-xs">
                                  <div className="flex flex-col gap-1">
                                    <span className="text-cyber-muted">IP地址</span>
                                    <span className="text-cyber-text break-all leading-relaxed">{networkSpecs.routing.ipv6.ip}</span>
                                  </div>
                                  <div className="flex flex-col gap-1">
                                    <span className="text-cyber-muted">网关</span>
                                    <span className="text-cyber-text break-all leading-relaxed">{networkSpecs.routing.ipv6.gateway}</span>
                                  </div>
                                  <div className="flex flex-col gap-1">
                                    <span className="text-cyber-muted">网络</span>
                                    <span className="text-cyber-text break-all leading-relaxed">{networkSpecs.routing.ipv6.network}</span>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      )}

                      {/* 流量配额 */}
                      {networkSpecs.traffic && (
                        <div className="bg-gradient-to-br from-orange-500/5 to-red-500/5 border border-orange-500/30 rounded-lg p-3.5">
                          <h4 className="text-sm font-semibold text-orange-400 mb-3 flex items-center gap-2">
                            <div className="p-1 bg-orange-500/10 rounded">
                              <BarChart3 className="w-3.5 h-3.5" />
                            </div>
                            流量配额
                          </h4>
                          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3">
                            {networkSpecs.traffic.inputQuotaSize && (
                              <div className="bg-cyber-bg/30 backdrop-blur-sm border border-orange-500/20 rounded-lg p-3">
                                <div className="text-xs text-cyber-muted mb-2">入站流量</div>
                                <div className="flex items-end justify-between">
                                  <div>
                                    <div className="text-xl font-bold text-orange-400 leading-none">
                                      {networkSpecs.traffic.inputQuotaSize.value}
                                    </div>
                                    <div className="text-xs text-orange-400/70 mt-0.5">{networkSpecs.traffic.inputQuotaSize.unit}</div>
                                  </div>
                                  {networkSpecs.traffic.inputQuotaUsed && (
                                    <div className="text-right">
                                      <div className="text-base font-semibold text-cyber-text leading-none">
                                        {networkSpecs.traffic.inputQuotaUsed.value}
                                      </div>
                                      <div className="text-xs text-cyber-muted mt-0.5">已使用</div>
                                    </div>
                                  )}
                                </div>
                              </div>
                            )}
                            {networkSpecs.traffic.outputQuotaSize && (
                              <div className="bg-cyber-bg/30 backdrop-blur-sm border border-red-500/20 rounded-lg p-3">
                                <div className="text-xs text-cyber-muted mb-2">出站流量</div>
                                <div className="flex items-end justify-between">
                                  <div>
                                    <div className="text-xl font-bold text-red-400 leading-none">
                                      {networkSpecs.traffic.outputQuotaSize.value}
                                    </div>
                                    <div className="text-xs text-red-400/70 mt-0.5">{networkSpecs.traffic.outputQuotaSize.unit}</div>
                                  </div>
                                  {networkSpecs.traffic.outputQuotaUsed && (
                                    <div className="text-right">
                                      <div className="text-base font-semibold text-cyber-text leading-none">
                                        {networkSpecs.traffic.outputQuotaUsed.value}
                                      </div>
                                      <div className="text-xs text-cyber-muted mt-0.5">已使用</div>
                                    </div>
                                  )}
                                </div>
                              </div>
                            )}
                          </div>
                          <div className="flex flex-wrap items-center gap-4 text-xs">
                            {networkSpecs.traffic.isThrottled !== undefined && (
                              <div className="flex items-center gap-2">
                                <div className={`w-2 h-2 rounded-full ${networkSpecs.traffic.isThrottled ? 'bg-red-400' : 'bg-green-400'}`}></div>
                                <span className="text-cyber-muted">状态:</span>
                                <span className={networkSpecs.traffic.isThrottled ? "text-red-400 font-medium" : "text-green-400 font-medium"}>
                                  {networkSpecs.traffic.isThrottled ? '已限流' : '正常'}
                                </span>
                              </div>
                            )}
                            {networkSpecs.traffic.resetQuotaDate && (
                              <div className="flex items-center gap-2">
                                <Calendar className="w-3.5 h-3.5 text-cyber-muted/70" />
                                <span className="text-cyber-muted">重置:</span>
                                <span className="text-cyber-text font-medium">{new Date(networkSpecs.traffic.resetQuotaDate).toLocaleDateString('zh-CN')}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      )}

                      {/* 高级功能 */}
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                        {/* OLA */}
                        {networkSpecs.ola && (
                          <div className="bg-gradient-to-br from-purple-500/5 to-pink-500/5 border border-purple-500/30 rounded-lg p-3.5">
                            <h4 className="text-sm font-semibold text-purple-400 mb-3 flex items-center gap-2">
                              <div className="p-1 bg-purple-500/10 rounded">
                                <Activity className="w-3.5 h-3.5" />
                              </div>
                              OVH Link Aggregation
                            </h4>
                            <div className="flex items-center gap-2 mb-3">
                              <div className={`w-2 h-2 rounded-full ${networkSpecs.ola.available ? 'bg-green-400' : 'bg-red-400'}`}></div>
                              <span className="text-xs text-cyber-muted">状态:</span>
                              <span className={`text-xs font-medium ${networkSpecs.ola.available ? 'text-green-400' : 'text-red-400'}`}>
                                {networkSpecs.ola.available ? '支持' : '不支持'}
                              </span>
                            </div>
                            {networkSpecs.ola.supportedModes && networkSpecs.ola.supportedModes.length > 0 && (
                              <div>
                                <div className="text-xs text-cyber-muted mb-2">支持模式</div>
                                <div className="flex flex-wrap gap-2">
                                  {networkSpecs.ola.supportedModes.map((mode, idx) => (
                                    <span key={idx} className="px-2 py-1 bg-purple-500/20 border border-purple-500/40 rounded text-purple-300 text-xs font-mono">
                                      {mode}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        )}

                        {/* vRack */}
                        {networkSpecs.vrack && (
                          <div className="bg-gradient-to-br from-indigo-500/5 to-purple-500/5 border border-indigo-500/30 rounded-lg p-3.5">
                            <h4 className="text-sm font-semibold text-indigo-400 mb-3 flex items-center gap-2">
                              <div className="p-1 bg-indigo-500/10 rounded">
                                <Server className="w-3.5 h-3.5" />
                              </div>
                              vRack 虚拟机架
                            </h4>
                            <div className="space-y-2.5">
                              {networkSpecs.vrack.bandwidth && (
                                <div className="flex justify-between items-center">
                                  <span className="text-xs text-cyber-muted">带宽</span>
                                  <span className="text-sm font-semibold text-indigo-400">
                                    {networkSpecs.vrack.bandwidth.value} {networkSpecs.vrack.bandwidth.unit}
                                  </span>
                                </div>
                              )}
                              {networkSpecs.vrack.type && (
                                <div className="flex justify-between items-center">
                                  <span className="text-xs text-cyber-muted">类型</span>
                                  <span className="text-sm font-semibold text-purple-400 capitalize">
                                    {networkSpecs.vrack.type}
                                  </span>
                                </div>
                              )}
                            </div>
                          </div>
                        )}

                        {/* VMAC */}
                        {networkSpecs.vmac && (
                          <div className="bg-gradient-to-br from-pink-500/5 to-rose-500/5 border border-pink-500/30 rounded-lg p-3.5">
                            <h4 className="text-sm font-semibold text-pink-400 mb-3 flex items-center gap-2">
                              <div className="p-1 bg-pink-500/10 rounded">
                                <Cpu className="w-3.5 h-3.5" />
                              </div>
                              虚拟MAC地址
                            </h4>
                            <div className="space-y-2.5">
                              <div className="flex justify-between items-center">
                                <span className="text-xs text-cyber-muted">支持</span>
                                <div className="flex items-center gap-2">
                                  <div className={`w-2 h-2 rounded-full ${networkSpecs.vmac.supported ? 'bg-green-400' : 'bg-red-400'}`}></div>
                                  <span className={`text-sm font-medium ${networkSpecs.vmac.supported ? 'text-green-400' : 'text-red-400'}`}>
                                    {networkSpecs.vmac.supported ? '是' : '否'}
                                  </span>
                                </div>
                              </div>
                              <div className="flex justify-between items-center">
                                <span className="text-xs text-cyber-muted">配额</span>
                                <span className="text-sm font-semibold text-pink-400">{networkSpecs.vmac.quota}</span>
                              </div>
                            </div>
                          </div>
                        )}

                        {/* 交换机 */}
                        {networkSpecs.switching?.name && (
                          <div className="bg-gradient-to-br from-cyan-500/5 to-blue-500/5 border border-cyan-500/30 rounded-lg p-3.5">
                            <h4 className="text-sm font-semibold text-cyan-400 mb-3 flex items-center gap-2">
                              <div className="p-1 bg-cyan-500/10 rounded">
                                <Wifi className="w-3.5 h-3.5" />
                              </div>
                              交换机信息
                            </h4>
                            <div className="font-mono text-xs text-cyber-text bg-cyber-bg/30 backdrop-blur-sm border border-cyan-500/20 rounded p-2.5 break-all leading-relaxed">
                              {networkSpecs.switching.name}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="text-center py-12 text-cyber-muted">
                      <AlertCircle className="w-12 h-12 mx-auto mb-3 opacity-30" />
                      <p className="text-sm">无法加载网络规格信息</p>
                    </div>
                  )}
                </div>
              </motion.div>
            </div>
          )}
        </AnimatePresence>,
        document.body
      )}

      {/* 高级功能管理对话框 */}
      {createPortal(
        <AnimatePresence>
          {showAdvancedDialog && (
            <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4">
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setShowAdvancedDialog(false)}
                className="absolute inset-0 bg-black/70 backdrop-blur-md"
              />

              <motion.div
                initial={{ opacity: 0, scale: 0.96, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.96, y: 20 }}
                onClick={(e) => e.stopPropagation()}
                className="relative bg-cyber-bg/95 backdrop-blur-xl border border-yellow-500/30 rounded-xl shadow-2xl shadow-yellow-500/10 max-w-5xl w-full max-h-[95vh] sm:max-h-[88vh] overflow-hidden flex flex-col">
                
                <div className="h-0.5 bg-gradient-to-r from-yellow-500 via-orange-500 to-red-500" />
                
                {/* 标题栏 */}
                <div className="flex items-center justify-between px-3 sm:px-4 py-2 sm:py-2.5 border-b border-yellow-500/20 bg-gradient-to-r from-yellow-500/5 to-transparent flex-shrink-0">
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <div className="p-1.5 bg-gradient-to-br from-yellow-500/20 to-orange-500/20 rounded-lg border border-yellow-500/30 flex-shrink-0">
                      <Settings className="w-4 h-4 text-yellow-400" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <h3 className="text-sm sm:text-base font-bold text-cyber-text truncate">高级功能管理</h3>
                      <p className="text-[10px] text-cyber-muted/80 leading-none truncate">
                        {selectedServer?.name} · {selectedServer?.serviceName}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => setShowAdvancedDialog(false)}
                    className="relative w-8 h-8 sm:w-9 sm:h-9 flex items-center justify-center bg-cyber-bg/60 hover:bg-red-500/10 border border-red-500/40 hover:border-red-500/60 rounded-lg transition-all duration-200 group active:scale-95 flex-shrink-0 ml-2 overflow-hidden"
                    title="关闭">
                    <X className="relative z-10 w-3.5 h-3.5 sm:w-4 sm:h-4 text-red-400 group-hover:text-red-300 transition-colors duration-200" />
                    <div className="absolute inset-0 rounded-lg bg-gradient-to-br from-red-500/0 via-red-500/0 to-orange-500/0 group-hover:from-red-500/10 group-hover:via-red-500/5 group-hover:to-orange-500/10 transition-all duration-200 pointer-events-none" />
                    <div className="absolute inset-0 rounded-lg opacity-0 group-hover:opacity-100 bg-red-500/5 blur-sm transition-opacity duration-200 pointer-events-none" />
                  </button>
                </div>

                {/* 标签页导航 */}
                <div className="flex items-center gap-1 px-2 sm:px-4 py-2 border-b border-cyber-accent/20 overflow-x-auto flex-shrink-0 scrollbar-hide">
                  <button
                    onClick={() => { setAdvancedTab('burst'); selectedServer && fetchBurst(selectedServer.serviceName); }}
                    className={`px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-xs font-medium transition-all duration-200 whitespace-nowrap flex items-center gap-1 active:scale-95 ${
                      advancedTab === 'burst'
                        ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/40 shadow-sm shadow-yellow-500/20'
                        : 'text-cyber-muted hover:text-cyber-text hover:bg-cyber-accent/10 border border-transparent'
                    }`}>
                    <Zap className="w-3 h-3 flex-shrink-0" />
                    <span className="hidden sm:inline">突发带宽</span>
                    <span className="sm:hidden">突发</span>
                  </button>
                  <button
                    onClick={() => { setAdvancedTab('firewall'); selectedServer && fetchFirewall(selectedServer.serviceName); }}
                    className={`px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-xs font-medium transition-all duration-200 whitespace-nowrap flex items-center gap-1 active:scale-95 ${
                      advancedTab === 'firewall'
                        ? 'bg-red-500/20 text-red-400 border border-red-500/40 shadow-sm shadow-red-500/20'
                        : 'text-cyber-muted hover:text-cyber-text hover:bg-cyber-accent/10 border border-transparent'
                    }`}>
                    <Shield className="w-3 h-3 flex-shrink-0" />
                    <span className="hidden sm:inline">防火墙</span>
                    <span className="sm:hidden">防火墙</span>
                  </button>
                  <button
                    onClick={() => { setAdvancedTab('backup'); selectedServer && fetchBackupFtp(selectedServer.serviceName); }}
                    className={`px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-xs font-medium transition-all duration-200 whitespace-nowrap flex items-center gap-1 active:scale-95 ${
                      advancedTab === 'backup'
                        ? 'bg-blue-500/20 text-blue-400 border border-blue-500/40 shadow-sm shadow-blue-500/20'
                        : 'text-cyber-muted hover:text-cyber-text hover:bg-cyber-accent/10 border border-transparent'
                    }`}>
                    <Database className="w-3 h-3 flex-shrink-0" />
                    <span className="hidden sm:inline">备份FTP</span>
                    <span className="sm:hidden">备份</span>
                  </button>
                  <button
                    onClick={() => { setAdvancedTab('dns'); selectedServer && fetchSecondaryDns(selectedServer.serviceName); }}
                    className={`px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-xs font-medium transition-all duration-200 whitespace-nowrap flex items-center gap-1 active:scale-95 ${
                      advancedTab === 'dns'
                        ? 'bg-green-500/20 text-green-400 border border-green-500/40 shadow-sm shadow-green-500/20'
                        : 'text-cyber-muted hover:text-cyber-text hover:bg-cyber-accent/10 border border-transparent'
                    }`}>
                    <Globe className="w-3 h-3 flex-shrink-0" />
                    <span className="hidden sm:inline">从DNS</span>
                    <span className="sm:hidden">DNS</span>
                  </button>
                  <button
                    onClick={() => { setAdvancedTab('vmac'); selectedServer && fetchVirtualMacs(selectedServer.serviceName); }}
                    className={`px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-xs font-medium transition-all duration-200 whitespace-nowrap flex items-center gap-1 active:scale-95 ${
                      advancedTab === 'vmac'
                        ? 'bg-purple-500/20 text-purple-400 border border-purple-500/40 shadow-sm shadow-purple-500/20'
                        : 'text-cyber-muted hover:text-cyber-text hover:bg-cyber-accent/10 border border-transparent'
                    }`}>
                    <Network className="w-3 h-3 flex-shrink-0" />
                    <span className="hidden sm:inline">虚拟MAC</span>
                    <span className="sm:hidden">MAC</span>
                  </button>
                  <button
                    onClick={() => { setAdvancedTab('vrack'); selectedServer && fetchVracks(selectedServer.serviceName); }}
                    className={`px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-xs font-medium transition-all duration-200 whitespace-nowrap flex items-center gap-1 active:scale-95 ${
                      advancedTab === 'vrack'
                        ? 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/40 shadow-sm shadow-indigo-500/20'
                        : 'text-cyber-muted hover:text-cyber-text hover:bg-cyber-accent/10 border border-transparent'
                    }`}>
                    <Server className="w-3 h-3 flex-shrink-0" />
                    <span>vRack</span>
                  </button>
                  <button
                    onClick={() => { setAdvancedTab('orderable'); selectedServer && fetchOrderable(selectedServer.serviceName); }}
                    className={`px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-xs font-medium transition-all duration-200 whitespace-nowrap flex items-center gap-1 active:scale-95 ${
                      advancedTab === 'orderable'
                        ? 'bg-pink-500/20 text-pink-400 border border-pink-500/40 shadow-sm shadow-pink-500/20'
                        : 'text-cyber-muted hover:text-cyber-text hover:bg-cyber-accent/10 border border-transparent'
                    }`}>
                    <BarChart3 className="w-3 h-3 flex-shrink-0" />
                    <span className="hidden sm:inline">可订购</span>
                    <span className="sm:hidden">订购</span>
                  </button>
                  <button
                    onClick={() => { setAdvancedTab('options'); selectedServer && fetchServerOptions(selectedServer.serviceName); }}
                    className={`px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-xs font-medium transition-all duration-200 whitespace-nowrap flex items-center gap-1 active:scale-95 ${
                      advancedTab === 'options'
                        ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/40 shadow-sm shadow-cyan-500/20'
                        : 'text-cyber-muted hover:text-cyber-text hover:bg-cyber-accent/10 border border-transparent'
                    }`}>
                    <Cog className="w-3 h-3 flex-shrink-0" />
                    <span>选项</span>
                  </button>
                  <button
                    onClick={() => { setAdvancedTab('ip'); selectedServer && fetchIpSpecs(selectedServer.serviceName); }}
                    className={`px-2 sm:px-3 py-1.5 rounded-lg text-[10px] sm:text-xs font-medium transition-all duration-200 whitespace-nowrap flex items-center gap-1 active:scale-95 ${
                      advancedTab === 'ip'
                        ? 'bg-teal-500/20 text-teal-400 border border-teal-500/40 shadow-sm shadow-teal-500/20'
                        : 'text-cyber-muted hover:text-cyber-text hover:bg-cyber-accent/10 border border-transparent'
                    }`}>
                    <Wifi className="w-3 h-3 flex-shrink-0" />
                    <span className="hidden sm:inline">IP规格</span>
                    <span className="sm:hidden">IP</span>
                  </button>
                </div>

                {/* 内容区域 */}
                <div className="p-2 sm:p-4 overflow-y-auto flex-1 min-h-0 custom-scrollbar">
                  {/* Burst突发带宽标签页 */}
                  {advancedTab === 'burst' && (
                    <div className="space-y-3">
                      {loadingBurst ? (
                        <div className="flex items-center justify-center py-12">
                          <RefreshCw className="w-8 h-8 animate-spin text-yellow-400" />
                        </div>
                      ) : burst ? (
                        <div>
                          {burst.notAvailable ? (
                            <div className="text-center py-12">
                              <Zap className="w-16 h-16 mx-auto mb-4 text-cyber-muted/30" />
                              <p className="text-sm text-cyber-muted mb-2">{burst.error || '该服务器不支持突发带宽功能'}</p>
                              <p className="text-xs text-cyber-muted/70">部分服务器型号可能不支持此功能</p>
                            </div>
                          ) : (
                            <>
                              <div className="bg-gradient-to-br from-yellow-500/5 to-orange-500/5 border border-yellow-500/30 rounded-lg p-4">
                                <h4 className="text-sm font-semibold text-yellow-400 mb-3 flex items-center gap-2">
                                  <Zap className="w-4 h-4" />
                                  突发带宽状态
                                </h4>
                                <div className="grid grid-cols-2 gap-4 mb-4">
                                  <div>
                                    <span className="text-xs text-cyber-muted">状态</span>
                                    <div className="text-lg font-semibold text-yellow-400 capitalize mt-1">
                                      {burst.status === 'active' ? '激活' : burst.status === 'inactive' ? '未激活' : burst.status}
                                    </div>
                                  </div>
                                  {burst.capacity && (
                                    <div>
                                      <span className="text-xs text-cyber-muted">容量</span>
                                      <div className="text-lg font-semibold text-orange-400 mt-1">
                                        {burst.capacity.value} {burst.capacity.unit}
                                      </div>
                                    </div>
                                  )}
                                </div>
                                <div className="flex gap-2">
                                  <button
                                    onClick={() => updateBurstStatus('active')}
                                    disabled={burst.status === 'active'}
                                    className="flex-1 px-4 py-2 bg-green-500/20 border border-green-500/40 rounded-lg text-green-400 hover:bg-green-500/30 disabled:opacity-50 transition-all text-sm">
                                    激活
                                  </button>
                                  <button
                                    onClick={() => updateBurstStatus('inactive')}
                                    disabled={burst.status === 'inactive'}
                                    className="flex-1 px-4 py-2 bg-red-500/20 border border-red-500/40 rounded-lg text-red-400 hover:bg-red-500/30 disabled:opacity-50 transition-all text-sm">
                                    停用
                                  </button>
                                </div>
                              </div>
                              <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-3 text-xs text-yellow-300">
                                <AlertCircle className="w-4 h-4 inline mr-1" />
                                突发带宽可以临时提升服务器带宽性能，适用于流量突增场景。
                              </div>
                            </>
                          )}
                        </div>
                      ) : (
                        <div className="text-center py-12 text-cyber-muted">
                          <AlertCircle className="w-12 h-12 mx-auto mb-3 opacity-30" />
                          <p className="text-sm">无法加载突发带宽信息</p>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Firewall防火墙标签页 */}
                  {advancedTab === 'firewall' && (
                    <div className="space-y-3">
                      {loadingFirewall ? (
                        <div className="flex items-center justify-center py-12">
                          <RefreshCw className="w-8 h-8 animate-spin text-red-400" />
                        </div>
                      ) : firewall ? (
                        <div>
                          {firewall.notAvailable ? (
                            <div className="text-center py-12">
                              <Shield className="w-16 h-16 mx-auto mb-4 text-cyber-muted/30" />
                              <p className="text-sm text-cyber-muted mb-2">{firewall.error || '该服务器不支持防火墙功能'}</p>
                              <p className="text-xs text-cyber-muted/70">部分服务器型号可能不支持此功能</p>
                            </div>
                          ) : (
                            <>
                              <div className="bg-gradient-to-br from-red-500/5 to-orange-500/5 border border-red-500/30 rounded-lg p-4">
                                <h4 className="text-sm font-semibold text-red-400 mb-3 flex items-center gap-2">
                                  <Shield className="w-4 h-4" />
                                  防火墙状态
                                </h4>
                                <div className="mb-4">
                                  <div className="flex items-center gap-2 mb-2">
                                    <div className={`w-3 h-3 rounded-full ${firewall.enabled ? 'bg-green-400' : 'bg-red-400'}`}></div>
                                    <span className="text-sm text-cyber-muted">当前状态:</span>
                                    <span className={`text-base font-semibold ${firewall.enabled ? 'text-green-400' : 'text-red-400'}`}>
                                      {firewall.enabled ? '已启用' : '已禁用'}
                                    </span>
                                  </div>
                                  {firewall.mode && (
                                    <div className="text-xs text-cyber-muted">
                                      模式: <span className="text-cyber-text">{firewall.mode}</span>
                                    </div>
                                  )}
                                  {firewall.model && (
                                    <div className="text-xs text-cyber-muted">
                                      型号: <span className="text-cyber-text">{firewall.model}</span>
                                    </div>
                                  )}
                                </div>
                                <div className="flex gap-2">
                                  <button
                                    onClick={() => updateFirewallStatus(true)}
                                    disabled={firewall.enabled}
                                    className="flex-1 px-4 py-2 bg-green-500/20 border border-green-500/40 rounded-lg text-green-400 hover:bg-green-500/30 disabled:opacity-50 transition-all text-sm">
                                    启用防火墙
                                  </button>
                                  <button
                                    onClick={() => updateFirewallStatus(false)}
                                    disabled={!firewall.enabled}
                                    className="flex-1 px-4 py-2 bg-red-500/20 border border-red-500/40 rounded-lg text-red-400 hover:bg-red-500/30 disabled:opacity-50 transition-all text-sm">
                                    禁用防火墙
                                  </button>
                                </div>
                              </div>
                              <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-xs text-red-300">
                                <AlertCircle className="w-4 h-4 inline mr-1" />
                                防火墙提供基础网络保护。更多规则请前往OVH控制面板配置。
                              </div>
                            </>
                          )}
                        </div>
                      ) : (
                        <div className="text-center py-12 text-cyber-muted">
                          <AlertCircle className="w-12 h-12 mx-auto mb-3 opacity-30" />
                          <p className="text-sm">无法加载防火墙信息</p>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Backup FTP标签页 */}
                  {advancedTab === 'backup' && (
                    <div className="space-y-3">
                      {loadingBackupFtp ? (
                        <div className="flex items-center justify-center py-12">
                          <RefreshCw className="w-8 h-8 animate-spin text-blue-400" />
                        </div>
                      ) : backupFtp?.notActivated ? (
                        <div className="text-center py-12">
                          <Database className="w-16 h-16 mx-auto mb-4 text-cyber-muted/30" />
                          <p className="text-sm text-cyber-muted mb-4">备份FTP未激活</p>
                          <button
                            onClick={async () => {
                              try {
                                const response = await api.post(`/server-control/${selectedServer?.serviceName}/backup-ftp`);
                                if (response.data.success) {
                                  showToast({ type: 'success', title: '备份FTP已激活' });
                                  selectedServer && fetchBackupFtp(selectedServer.serviceName);
                                }
                              } catch (error: any) {
                                if (error?.response?.data?.notAvailable) {
                                  showToast({ type: 'warning', title: '无法激活', message: error.response.data.error });
                                } else {
                                  showToast({ type: 'error', title: '激活失败', message: error.message });
                                }
                              }
                            }}
                            className="px-4 py-2 bg-blue-500/20 border border-blue-500/40 rounded-lg text-blue-400 hover:bg-blue-500/30 transition-all">
                            激活备份FTP
                          </button>
                        </div>
                      ) : backupFtp?.notAvailable ? (
                        <div className="text-center py-12">
                          <Database className="w-16 h-16 mx-auto mb-4 text-cyber-muted/30" />
                          <p className="text-sm text-cyber-muted mb-2">{backupFtp.error || '该服务器无法使用备份FTP服务'}</p>
                          <p className="text-xs text-cyber-muted/70">部分服务器型号或套餐可能不支持此功能</p>
                        </div>
                      ) : backupFtp ? (
                        <div className="space-y-3">
                          <div className="bg-gradient-to-br from-blue-500/5 to-cyan-500/5 border border-blue-500/30 rounded-lg p-4">
                            <h4 className="text-sm font-semibold text-blue-400 mb-3">备份FTP信息</h4>
                            <div className="grid grid-cols-2 gap-3 text-xs mb-3">
                              <div>
                                <span className="text-cyber-muted">FTP服务器:</span>
                                <div className="text-cyber-text font-mono mt-1">{backupFtp.ftpBackupName || 'N/A'}</div>
                              </div>
                              <div>
                                <span className="text-cyber-muted">配额:</span>
                                <div className="text-blue-400 font-semibold mt-1">
                                  {backupFtp.quota?.value} {backupFtp.quota?.unit}
                                </div>
                              </div>
                              {backupFtp.usage && (
                                <div>
                                  <span className="text-cyber-muted">已使用:</span>
                                  <div className="text-cyan-400 font-semibold mt-1">
                                    {backupFtp.usage?.value} {backupFtp.usage?.unit}
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                          
                          <div className="bg-cyber-grid/30 border border-cyber-accent/30 rounded-lg p-3">
                            <h5 className="text-xs font-semibold text-cyber-text mb-2">访问控制列表</h5>
                            {backupFtpAccess.length > 0 ? (
                              <div className="space-y-2">
                                {backupFtpAccess.map((access, idx) => (
                                  <div key={idx} className="bg-cyber-bg/50 border border-cyber-accent/20 rounded p-2 text-xs">
                                    <div className="font-mono text-cyber-text">{access.ipBlock}</div>
                                    {access.ftp && <span className="text-green-400 mr-2">FTP</span>}
                                    {access.nfs && <span className="text-blue-400 mr-2">NFS</span>}
                                    {access.cifs && <span className="text-purple-400">CIFS</span>}
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <p className="text-xs text-cyber-muted">暂无访问控制规则</p>
                            )}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  )}

                  {/* Secondary DNS标签页 */}
                  {advancedTab === 'dns' && (
                    <div className="space-y-3">
                      {loadingSecondaryDns ? (
                        <div className="flex items-center justify-center py-12">
                          <RefreshCw className="w-8 h-8 animate-spin text-green-400" />
                        </div>
                      ) : (
                        <div>
                          {secondaryDns.length > 0 ? (
                            <div className="space-y-2">
                              {secondaryDns.map((dns, idx) => (
                                <div key={idx} className="bg-gradient-to-br from-green-500/5 to-cyan-500/5 border border-green-500/30 rounded-lg p-3">
                                  <div className="font-mono text-sm text-green-400">{dns.domain}</div>
                                  {dns.dns && <div className="text-xs text-cyber-muted mt-1">DNS: {dns.dns}</div>}
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className="text-center py-12 text-cyber-muted">
                              <Globe className="w-16 h-16 mx-auto mb-3 opacity-30" />
                              <p className="text-sm">暂无从DNS域名</p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Virtual MAC标签页 */}
                  {advancedTab === 'vmac' && (
                    <div className="space-y-3">
                      {loadingVirtualMacs ? (
                        <div className="flex items-center justify-center py-12">
                          <RefreshCw className="w-8 h-8 animate-spin text-purple-400" />
                        </div>
                      ) : (
                        <div>
                          {virtualMacs.length > 0 ? (
                            <div className="space-y-2">
                              {virtualMacs.map((vmac, idx) => (
                                <div key={idx} className="bg-gradient-to-br from-purple-500/5 to-pink-500/5 border border-purple-500/30 rounded-lg p-3">
                                  <div className="font-mono text-sm text-purple-400">{vmac.macAddress}</div>
                                  <div className="text-xs text-cyber-muted mt-1">
                                    类型: {vmac.type} | IP: {vmac.ipAddress}
                                  </div>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className="text-center py-12 text-cyber-muted">
                              <Network className="w-16 h-16 mx-auto mb-3 opacity-30" />
                              <p className="text-sm">暂无虚拟MAC地址</p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* vRack标签页 */}
                  {advancedTab === 'vrack' && (
                    <div className="space-y-3">
                      {loadingVracks ? (
                        <div className="flex items-center justify-center py-12">
                          <RefreshCw className="w-8 h-8 animate-spin text-indigo-400" />
                        </div>
                      ) : (
                        <div>
                          {vracks.length > 0 ? (
                            <div className="space-y-2">
                              {vracks.map((vrack, idx) => (
                                <div key={idx} className="bg-gradient-to-br from-indigo-500/5 to-purple-500/5 border border-indigo-500/30 rounded-lg p-3">
                                  <div className="font-mono text-sm text-indigo-400">{vrack.vrackName}</div>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className="text-center py-12 text-cyber-muted">
                              <Server className="w-16 h-16 mx-auto mb-3 opacity-30" />
                              <p className="text-sm">未连接vRack</p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* 可订购服务标签页 */}
                  {advancedTab === 'orderable' && (
                    <div className="space-y-3">
                      {loadingOrderable ? (
                        <div className="flex items-center justify-center py-12">
                          <RefreshCw className="w-8 h-8 animate-spin text-pink-400" />
                        </div>
                      ) : (
                        <div className="space-y-2 sm:space-y-3">
                          {orderableBandwidth && (
                            <div className="bg-gradient-to-br from-cyan-500/5 to-blue-500/5 border border-cyan-500/30 rounded-lg p-3 sm:p-4">
                              <h5 className="text-xs sm:text-sm font-semibold text-cyan-400 mb-2 sm:mb-3 flex items-center gap-2">
                                <BarChart3 className="w-3.5 sm:w-4 h-3.5 sm:h-4" />
                                可订购带宽
                              </h5>
                              {orderableBandwidth.orderable ? (
                                <div className="space-y-1.5 sm:space-y-2">
                                  {orderableBandwidth.platinum && orderableBandwidth.platinum.length > 0 && (
                                    <div className="bg-cyber-bg/50 rounded p-1.5 sm:p-2">
                                      <div className="text-[10px] sm:text-xs font-medium text-cyan-300 mb-0.5 sm:mb-1">Platinum</div>
                                      <div className="text-[10px] sm:text-xs text-cyber-muted">{orderableBandwidth.platinum.length} 个套餐可用</div>
                                    </div>
                                  )}
                                  {orderableBandwidth.premium && orderableBandwidth.premium.length > 0 && (
                                    <div className="bg-cyber-bg/50 rounded p-1.5 sm:p-2">
                                      <div className="text-[10px] sm:text-xs font-medium text-blue-300 mb-0.5 sm:mb-1">Premium</div>
                                      <div className="text-[10px] sm:text-xs text-cyber-muted">{orderableBandwidth.premium.length} 个套餐可用</div>
                                    </div>
                                  )}
                                  {orderableBandwidth.ultimate && orderableBandwidth.ultimate.length > 0 && (
                                    <div className="bg-cyber-bg/50 rounded p-1.5 sm:p-2">
                                      <div className="text-[10px] sm:text-xs font-medium text-purple-300 mb-0.5 sm:mb-1">Ultimate</div>
                                      <div className="text-[10px] sm:text-xs text-cyber-muted">{orderableBandwidth.ultimate.length} 个套餐可用</div>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <div className="text-[10px] sm:text-xs text-cyber-muted py-1 sm:py-2">当前不可订购带宽升级</div>
                              )}
                            </div>
                          )}
                          {orderableTraffic && (
                            <div className="bg-gradient-to-br from-orange-500/5 to-red-500/5 border border-orange-500/30 rounded-lg p-3 sm:p-4">
                              <h5 className="text-xs sm:text-sm font-semibold text-orange-400 mb-2 sm:mb-3 flex items-center gap-2">
                                <Activity className="w-3.5 sm:w-4 h-3.5 sm:h-4" />
                                可订购流量
                              </h5>
                              {orderableTraffic.orderable ? (
                                orderableTraffic.traffic && orderableTraffic.traffic.length > 0 ? (
                                  <div className="text-[10px] sm:text-xs text-cyber-muted py-1 sm:py-2">
                                    {orderableTraffic.traffic.length} 个流量套餐可用
                                  </div>
                                ) : (
                                  <div className="text-[10px] sm:text-xs text-cyber-muted py-1 sm:py-2">暂无可用流量套餐</div>
                                )
                              ) : (
                                <div className="text-[10px] sm:text-xs text-cyber-muted py-1 sm:py-2">当前不可订购流量升级</div>
                              )}
                            </div>
                          )}
                          {orderableIp && (
                            <div className="bg-gradient-to-br from-purple-500/5 to-pink-500/5 border border-purple-500/30 rounded-lg p-3 sm:p-4">
                              <h5 className="text-xs sm:text-sm font-semibold text-purple-400 mb-2 sm:mb-3 flex items-center gap-2">
                                <Wifi className="w-3.5 sm:w-4 h-3.5 sm:h-4" />
                                可订购IP
                              </h5>
                              <div className="space-y-2 sm:space-y-3">
                                {orderableIp.ipv4 && orderableIp.ipv4.length > 0 && (
                                  <div>
                                    <div className="text-[10px] sm:text-xs font-medium text-green-400 mb-1.5 sm:mb-2">IPv4地址</div>
                                    {orderableIp.ipv4.map((ip: any, idx: number) => (
                                      <div key={idx} className="bg-cyber-bg/50 rounded p-2 sm:p-3 mb-1.5 sm:mb-2">
                                        <div className="flex items-center justify-between mb-1 sm:mb-2 flex-wrap gap-1">
                                          <span className="text-[10px] sm:text-xs font-medium text-cyber-text">
                                            {ip.type === 'failover' ? '故障转移IP' : ip.type === 'static' ? '静态IP' : ip.type}
                                          </span>
                                          {ip.included && (
                                            <span className="text-[10px] sm:text-xs px-1.5 sm:px-2 py-0.5 bg-green-500/20 text-green-400 rounded">已包含</span>
                                          )}
                                        </div>
                                        {ip.blockSizes && ip.blockSizes.length > 0 && (
                                          <div className="text-[10px] sm:text-xs text-cyber-muted mb-0.5 sm:mb-1">
                                            可用块大小: <span className="text-cyber-text font-mono break-all">{ip.blockSizes.join(', ')}</span> IP
                                          </div>
                                        )}
                                        {ip.ipNumber !== undefined && (
                                          <div className="text-[10px] sm:text-xs text-cyber-muted mb-0.5 sm:mb-1">
                                            IP数量: <span className="text-cyber-text">{ip.ipNumber}</span>
                                            {ip.number !== undefined && (
                                              <span className="ml-1.5 sm:ml-2">| 数量: <span className="text-cyber-text">{ip.number}</span></span>
                                            )}
                                          </div>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                )}
                                {orderableIp.ipv6 && orderableIp.ipv6.length > 0 && (
                                  <div>
                                    <div className="text-[10px] sm:text-xs font-medium text-blue-400 mb-1.5 sm:mb-2">IPv6地址</div>
                                    {orderableIp.ipv6.map((ip: any, idx: number) => (
                                      <div key={idx} className="bg-cyber-bg/50 rounded p-2 sm:p-3 mb-1.5 sm:mb-2">
                                        <div className="text-[10px] sm:text-xs text-cyber-text">
                                          {ip.type || 'IPv6'}
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                                {(!orderableIp.ipv4 || orderableIp.ipv4.length === 0) && 
                                 (!orderableIp.ipv6 || orderableIp.ipv6.length === 0) && (
                                  <div className="text-[10px] sm:text-xs text-cyber-muted py-1 sm:py-2">暂无可用IP选项</div>
                                )}
                              </div>
                              <div className="mt-2 sm:mt-3 pt-2 sm:pt-3 border-t border-purple-500/20 text-[10px] sm:text-xs text-purple-300/70">
                                <AlertCircle className="w-3 h-3 inline mr-1" />
                                此页面仅显示可订购选项，实际订购请前往OVH控制面板
                              </div>
                            </div>
                          )}
                          {!orderableBandwidth && !orderableTraffic && !orderableIp && (
                            <div className="text-center py-8 sm:py-12 text-cyber-muted">
                              <BarChart3 className="w-12 sm:w-16 h-12 sm:h-16 mx-auto mb-3 opacity-30" />
                              <p className="text-xs sm:text-sm">暂无可订购服务</p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* 服务器选项标签页 */}
                  {advancedTab === 'options' && (
                    <div className="space-y-3">
                      {loadingOptions ? (
                        <div className="flex items-center justify-center py-12">
                          <RefreshCw className="w-8 h-8 animate-spin text-cyan-400" />
                        </div>
                      ) : (
                        <div>
                          {serverOptions.length > 0 ? (
                            <div className="space-y-1.5 sm:space-y-2">
                              {serverOptions.map((option, idx) => {
                                // 选项名称翻译
                                const optionNames: Record<string, string> = {
                                  'BANDWIDTH': '带宽',
                                  'TRAFFIC': '流量',
                                  'BACKUP_STORAGE': '备份存储',
                                  'KVM': 'KVM',
                                  'KVM_EXPRESS': 'KVM Express',
                                  'USB_KEY': 'USB密钥',
                                  'PROFESSIONAL_USE': '专业用途',
                                  'IP': 'IP地址',
                                  'IPFO': '故障转移IP'
                                };
                                
                                // 状态翻译
                                const stateNames: Record<string, string> = {
                                  'subscribed': '已订阅',
                                  'released': '已释放',
                                  'releasing': '释放中',
                                  'toDelete': '待删除'
                                };
                                
                                const optionName = optionNames[option.option] || option.option;
                                const stateName = option.state ? (stateNames[option.state] || option.state) : '';
                                
                                return (
                                  <div key={idx} className="bg-gradient-to-br from-cyan-500/5 to-blue-500/5 border border-cyan-500/30 rounded-lg p-2.5 sm:p-3">
                                    <div className="flex items-center justify-between mb-1.5 sm:mb-2 flex-wrap gap-1">
                                      <div className="text-xs sm:text-sm text-cyan-400 font-medium">{optionName}</div>
                                      {option.state && (
                                        <span className={`text-[10px] sm:text-xs px-1.5 sm:px-2 py-0.5 rounded ${
                                          option.state === 'subscribed' 
                                            ? 'bg-green-500/20 text-green-400' 
                                            : option.state === 'releasing' || option.state === 'toDelete'
                                            ? 'bg-yellow-500/20 text-yellow-400'
                                            : 'bg-red-500/20 text-red-400'
                                        }`}>
                                          {stateName}
                                        </span>
                                      )}
                                    </div>
                                    {option.option && (
                                      <div className="text-[10px] sm:text-xs text-cyber-muted font-mono">
                                        {option.option}
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          ) : (
                            <div className="text-center py-12 text-cyber-muted">
                              <Cog className="w-16 h-16 mx-auto mb-3 opacity-30" />
                              <p className="text-sm">暂无服务器选项</p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* IP规格标签页 */}
                  {advancedTab === 'ip' && (
                    <div className="space-y-3">
                      {loadingIpSpecs ? (
                        <div className="flex items-center justify-center py-12">
                          <RefreshCw className="w-8 h-8 animate-spin text-teal-400" />
                        </div>
                      ) : ipSpecs ? (
                        <div className="bg-gradient-to-br from-teal-500/5 to-cyan-500/5 border border-teal-500/30 rounded-lg p-3 sm:p-4">
                          <h4 className="text-xs sm:text-sm font-semibold text-teal-400 mb-2 sm:mb-3 flex items-center gap-2">
                            <Wifi className="w-3.5 sm:w-4 h-3.5 sm:h-4" />
                            IP规格详情
                          </h4>
                          <div className="space-y-2 sm:space-y-3">
                            {ipSpecs.ipv4 && ipSpecs.ipv4.length > 0 && (
                              <div>
                                <div className="text-[10px] sm:text-xs font-medium text-green-400 mb-1.5 sm:mb-2">IPv4地址</div>
                                {ipSpecs.ipv4.map((ip: any, idx: number) => (
                                  <div key={idx} className="bg-cyber-bg/50 rounded p-2 sm:p-3 mb-1.5 sm:mb-2">
                                    <div className="flex items-center justify-between mb-1 sm:mb-2 flex-wrap gap-1">
                                      <span className="text-[10px] sm:text-xs font-medium text-cyber-text">
                                        {ip.type === 'failover' ? '故障转移IP' : ip.type === 'static' ? '静态IP' : ip.type || 'IPv4'}
                                      </span>
                                      {ip.included && (
                                        <span className="text-[10px] sm:text-xs px-1.5 sm:px-2 py-0.5 bg-green-500/20 text-green-400 rounded">已包含</span>
                                      )}
                                    </div>
                                    {ip.blockSizes && ip.blockSizes.length > 0 && (
                                      <div className="text-[10px] sm:text-xs text-cyber-muted mb-0.5 sm:mb-1">
                                        可用块大小: <span className="text-cyber-text font-mono break-all">{ip.blockSizes.join(', ')}</span> IP
                                      </div>
                                    )}
                                    {ip.ipNumber !== undefined && (
                                      <div className="text-[10px] sm:text-xs text-cyber-muted mb-0.5 sm:mb-1">
                                        IP数量: <span className="text-cyber-text">{ip.ipNumber}</span>
                                      </div>
                                    )}
                                    {ip.number !== undefined && (
                                      <div className="text-[10px] sm:text-xs text-cyber-muted">
                                        数量: <span className="text-cyber-text">{ip.number}</span>
                                      </div>
                                    )}
                                    {ip.optionRequired && (
                                      <div className="text-[10px] sm:text-xs text-yellow-400 mt-0.5 sm:mt-1">
                                        需要选项: {ip.optionRequired}
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            )}
                            {ipSpecs.ipv6 && ipSpecs.ipv6.length > 0 && (
                              <div>
                                <div className="text-[10px] sm:text-xs font-medium text-blue-400 mb-1.5 sm:mb-2">IPv6地址</div>
                                {ipSpecs.ipv6.map((ip: any, idx: number) => (
                                  <div key={idx} className="bg-cyber-bg/50 rounded p-2 sm:p-3 mb-1.5 sm:mb-2">
                                    <div className="text-[10px] sm:text-xs text-cyber-text">
                                      {ip.type || 'IPv6'}
                                    </div>
                                    {ip.blockSizes && ip.blockSizes.length > 0 && (
                                      <div className="text-[10px] sm:text-xs text-cyber-muted mt-0.5 sm:mt-1">
                                        可用块大小: <span className="text-cyber-text font-mono break-all">{ip.blockSizes.join(', ')}</span>
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            )}
                            {(!ipSpecs.ipv4 || ipSpecs.ipv4.length === 0) && 
                             (!ipSpecs.ipv6 || ipSpecs.ipv6.length === 0) && (
                              <div className="text-[10px] sm:text-xs text-cyber-muted py-1 sm:py-2">暂无IP规格信息</div>
                            )}
                          </div>
                        </div>
                      ) : (
                        <div className="text-center py-12 text-cyber-muted">
                          <AlertCircle className="w-12 h-12 mx-auto mb-3 opacity-30" />
                          <p className="text-sm">无法加载IP规格信息</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </motion.div>
            </div>
          )}
        </AnimatePresence>,
        document.body
      )}

    </div>
  );
};

export default ServerControlPage;
