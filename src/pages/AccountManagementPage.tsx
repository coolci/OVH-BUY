/**
 * 账户管理页面 - Account Management Page
 * 
 * 功能说明：
 * 1. 账户信息展示 - 显示客户代码、KYC状态、账户状态、货币等信息
 * 2. 邮件历史 - 显示 OVH 发送的所有邮件通知（不包括支持工单）
 * 3. 退款记录 - 显示账户的退款历史记录
 * 
 * API 架构：
 * - GET /api/ovh/account/info -> OVH API: GET /me
 *   用于：右上角客户代码 + 底部三个状态卡片（KYC、账户状态、货币）
 * 
 * - GET /api/ovh/account/email-history -> OVH API: GET /me/notification/email/history + GET /me/notification/email/history/{id}
 *   用于：邮件历史标签页（显示最新50封邮件）
 * 
 * - GET /api/ovh/account/refunds -> OVH API: GET /me/refund + GET /me/refund/{id}
 *   用于：退款记录标签页（显示最多20条退款记录）
 */

import { useEffect, useState } from "react";
import { api } from "@/utils/apiClient";
import { toast } from "sonner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { 
  User, 
  CreditCard, 
  RefreshCw, 
  Wallet, 
  FileText,
  CheckCircle,
  AlertCircle,
  Mail,
  Inbox,
  Clock
} from "lucide-react";

interface AccountInfo {
  nichandle: string;
  customerCode: string;
  email: string;
  firstname: string;
  name: string;
  state: string;
  kycValidated: boolean;
  city: string;
  country: string;
  phone: string;
  currency: {
    code: string;
    symbol: string;
  };
}

interface Refund {
  refundId: string;
  date: string;
  orderId: number;
  originalBillId: string;
  password: string;
  pdfUrl: string;
  priceWithTax: {
    currencyCode: string;
    text: string;
    value: number;
  };
}

interface EmailHistory {
  id: number;
  subject: string;
  date: string;
  body: string;
}


const AccountManagementPage = () => {
  const [accountInfo, setAccountInfo] = useState<AccountInfo | null>(null);
  const [refunds, setRefunds] = useState<Refund[]>([]);
  const [emails, setEmails] = useState<EmailHistory[]>([]);
  const [selectedEmail, setSelectedEmail] = useState<EmailHistory | null>(null);
  const [loading, setLoading] = useState({
    account: false,
    refunds: false,
    emails: false
  });
  const [hasLoaded, setHasLoaded] = useState({
    account: false,
    refunds: false,
    emails: false
  });

  // 获取账户信息
  // API: GET /api/ovh/account/info -> OVH API: GET /me
  // 返回数据包括：
  // - nichandle: 账户标识
  // - customerCode: 客户代码
  // - email: 邮箱地址
  // - kycValidated: KYC验证状态（底部"KYC验证"卡片使用）
  // - state: 账户状态（底部"账户状态"卡片使用）
  // - currency: 货币信息（底部"账户货币"卡片使用）
  const fetchAccountInfo = async () => {
    setLoading(prev => ({ ...prev, account: true }));
    try {
      const response = await api.get('/ovh/account/info');
      if (response.data.status === 'success') {
        setAccountInfo(response.data.data);
      }
    } catch (error: any) {
      toast.error('获取账户信息失败: ' + (error.response?.data?.message || error.message));
    } finally {
      setLoading(prev => ({ ...prev, account: false }));
      setHasLoaded(prev => ({ ...prev, account: true }));
    }
  };

  // 获取退款列表
  // API: GET /api/ovh/account/refunds -> OVH API: GET /me/refund + GET /me/refund/{id}
  // 返回退款记录列表，包含退款ID、订单ID、金额、日期等信息
  const fetchRefunds = async () => {
    setLoading(prev => ({ ...prev, refunds: true }));
    try {
      const response = await api.get('/ovh/account/refunds');
      if (response.data.status === 'success') {
        // 按日期降序排序，确保最新的在前面
        const sortedRefunds = response.data.data.sort((a: Refund, b: Refund) => {
          return new Date(b.date).getTime() - new Date(a.date).getTime();
        });
        setRefunds(sortedRefunds);
      }
    } catch (error: any) {
      toast.error('获取退款列表失败: ' + (error.response?.data?.message || error.message));
    } finally {
      setLoading(prev => ({ ...prev, refunds: false }));
      setHasLoaded(prev => ({ ...prev, refunds: true }));
    }
  };

  // 获取邮件历史
  // API: GET /api/ovh/account/email-history -> OVH API: GET /me/notification/email/history + GET /me/notification/email/history/{id}
  // 返回 OVH 发送的邮件通知列表，包含邮件主题、正文、时间等信息
  const fetchEmailHistory = async () => {
    setLoading(prev => ({ ...prev, emails: true }));
    try {
      const response = await api.get('/ovh/account/email-history');
      if (response.data.status === 'success') {
        // 按日期降序排序
        const sortedEmails = response.data.data.sort((a: EmailHistory, b: EmailHistory) => {
          return new Date(b.date).getTime() - new Date(a.date).getTime();
        });
        setEmails(sortedEmails);
      }
    } catch (error: any) {
      toast.error('获取邮件历史失败: ' + (error.response?.data?.message || error.message));
    } finally {
      setLoading(prev => ({ ...prev, emails: false }));
      setHasLoaded(prev => ({ ...prev, emails: true }));
    }
  };


  // 格式化日期时间
  const formatDateTime = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  // 格式化日期
  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    });
  };

  // 格式化邮件内容，将URL转换为可点击的链接
  const formatEmailBody = (body: string) => {
    if (!body) return '';
    
    // 将文本分割成行
    const lines = body.split('\n');
    
    return lines.map((line, index) => {
      // 检测URL
      const urlRegex = /(https?:\/\/[^\s]+)/g;
      const parts = line.split(urlRegex);
      
      return (
        <div key={index} className="mb-2">
          {parts.map((part, i) => {
            if (part.match(urlRegex)) {
              return (
                <a
                  key={i}
                  href={part}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-cyber-accent hover:underline break-all"
                >
                  {part}
                </a>
              );
            }
            return <span key={i}>{part}</span>;
          })}
        </div>
      );
    });
  };

  // 初始加载
  useEffect(() => {
    fetchAccountInfo();
    fetchRefunds();
    fetchEmailHistory();
  }, []);

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div>
        <h1 className="text-3xl font-bold mb-1 cyber-glow-text">账户管理</h1>
        <p className="text-cyber-muted">查看和管理您的 OVH 账户信息</p>
      </div>

      {/* 账户信息卡片 - 顶部主要信息 */}
      <div className="cyber-card p-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* 客户代码 */}
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-lg bg-cyber-accent/20 flex-shrink-0">
                <User className="w-5 h-5 text-cyber-accent" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-cyber-muted font-medium mb-1">客户代码</p>
                {accountInfo ? (
                  <>
                    <p className="text-2xl font-bold text-cyber-accent leading-tight">
                      {accountInfo.customerCode}
                    </p>
                    <p className="text-xs text-cyber-muted/70 pl-0 mt-1">
                      {accountInfo.nichandle}
                    </p>
                  </>
                ) : null}
              </div>
            </div>
          </div>
          
          {/* 邮箱地址 */}
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-lg bg-cyber-accent/20 flex-shrink-0">
                <Mail className="w-5 h-5 text-cyber-accent" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-cyber-muted font-medium mb-1">邮箱地址</p>
                {accountInfo ? (
                  <p className="text-lg font-semibold text-cyber-text leading-tight break-all">
                    {accountInfo.email}
                  </p>
                ) : null}
              </div>
            </div>
          </div>

          {/* 账户持有人 */}
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-lg bg-cyber-accent/20 flex-shrink-0">
                <User className="w-5 h-5 text-cyber-accent" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-cyber-muted font-medium mb-1">账户持有人</p>
                {accountInfo ? (
                  <>
                    <p className="text-lg font-semibold text-cyber-text leading-tight">
                      {accountInfo.firstname} {accountInfo.name}
                    </p>
                    {accountInfo.city && accountInfo.country && (
                      <p className="text-xs text-cyber-muted/70 pl-0 mt-1">
                        {accountInfo.city}, {accountInfo.country}
                      </p>
                    )}
                  </>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 详细信息标签页 */}
      <Tabs defaultValue="emails" className="w-full">
        <TabsList className="grid w-full grid-cols-2 cyber-card">
          <TabsTrigger value="emails" className="data-[state=active]:bg-cyber-accent/20">
            <Mail className="w-4 h-4 mr-2" />
            邮件历史
          </TabsTrigger>
          <TabsTrigger value="refunds" className="data-[state=active]:bg-cyber-accent/20">
            <RefreshCw className="w-4 h-4 mr-2" />
            退款记录
          </TabsTrigger>
        </TabsList>

        {/* 邮件历史 */}
        <TabsContent value="emails">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* 邮件列表 */}
            <Card className="cyber-card">
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  <span>邮件列表</span>
                  <button 
                    onClick={fetchEmailHistory}
                    className="cyber-button text-xs flex items-center gap-2"
                  >
                    <RefreshCw className="w-4 h-4" />
                    <span>刷新</span>
                  </button>
                </CardTitle>
                <CardDescription>OVH 发送给您的邮件通知</CardDescription>
              </CardHeader>
              <CardContent>
                {emails.length > 0 ? (
                  <div className="space-y-2 max-h-[600px] overflow-y-auto pr-2">
                    {emails.map((email) => (
                      <div
                        key={email.id}
                        className={`cyber-panel p-4 cursor-pointer transition-all hover:bg-cyber-accent/10 ${
                          selectedEmail?.id === email.id ? 'bg-cyber-accent/20 border-cyber-accent' : 'bg-cyber-grid/30'
                        }`}
                        onClick={() => setSelectedEmail(email)}
                      >
                        <div className="flex items-start gap-3">
                          <Mail className="w-5 h-5 text-cyber-accent mt-1 flex-shrink-0" />
                          <div className="flex-1 min-w-0">
                            <p className="font-medium text-cyber-text truncate">
                              {email.subject}
                            </p>
                            <div className="flex items-center gap-2 mt-1">
                              <Clock className="w-3 h-3 text-cyber-muted" />
                              <p className="text-xs text-cyber-muted">
                                {formatDateTime(email.date)}
                              </p>
                            </div>
                          </div>
                          <Badge variant="outline" className="text-xs flex-shrink-0">
                            #{email.id}
                          </Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : hasLoaded.emails && !loading.emails && emails.length === 0 ? (
                  <div className="text-center py-8 text-cyber-muted">
                    <Inbox className="w-12 h-12 mx-auto mb-2 opacity-50" />
                    <p>暂无邮件</p>
                  </div>
                ) : null}
              </CardContent>
            </Card>

            {/* 邮件详情 */}
            <Card className="cyber-card">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <FileText className="w-5 h-5" />
                  邮件详情
                </CardTitle>
                <CardDescription>
                  {selectedEmail ? `邮件 #${selectedEmail.id}` : '请从左侧选择一封邮件'}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {selectedEmail ? (
                  <div className="space-y-4">
                    {/* 邮件头部信息 */}
                    <div className="cyber-panel p-4 bg-cyber-grid/30">
                      <div className="space-y-2">
                        <div>
                          <p className="text-xs text-cyber-muted mb-1">主题</p>
                          <p className="font-medium text-cyber-text">{selectedEmail.subject}</p>
                        </div>
                        <div>
                          <p className="text-xs text-cyber-muted mb-1">时间</p>
                          <div className="flex items-center gap-2">
                            <Clock className="w-4 h-4 text-cyber-accent" />
                            <p className="text-sm text-cyber-text">{formatDateTime(selectedEmail.date)}</p>
                          </div>
                        </div>
                        <div>
                          <p className="text-xs text-cyber-muted mb-1">邮件 ID</p>
                          <Badge variant="outline" className="text-xs">
                            #{selectedEmail.id}
                          </Badge>
                        </div>
                      </div>
                    </div>

                    {/* 邮件正文 */}
                    <div className="cyber-panel p-4 bg-cyber-grid/30 max-h-[500px] overflow-y-auto">
                      <p className="text-xs text-cyber-muted mb-3">邮件内容</p>
                      <div className="text-sm text-cyber-text leading-relaxed font-mono">
                        {formatEmailBody(selectedEmail.body)}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="text-center py-12 text-cyber-muted">
                    <Mail className="w-16 h-16 mx-auto mb-4 opacity-50" />
                    <p className="text-lg">请选择一封邮件查看详情</p>
                    <p className="text-sm mt-2">从左侧列表中点击任意邮件</p>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* 退款记录 */}
        <TabsContent value="refunds">
          <Card className="cyber-card">
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>退款记录</span>
                <button 
                  onClick={fetchRefunds}
                  className="cyber-button text-xs flex items-center gap-2"
                >
                  <RefreshCw className="w-4 h-4" />
                  <span>刷新</span>
                </button>
              </CardTitle>
              <CardDescription>查看您的退款记录和状态</CardDescription>
            </CardHeader>
            <CardContent>
              {refunds.length > 0 ? (
                <div className="space-y-3">
                  {refunds.map((refund) => (
                    <div
                      key={refund.refundId}
                      className="cyber-panel p-4 bg-cyber-grid/30"
                    >
                      <div className="flex justify-between items-start gap-4">
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <p className="font-medium text-cyber-text">退款 #{refund.refundId}</p>
                            <Badge variant="outline" className="text-xs">
                              订单 {refund.orderId}
                            </Badge>
                          </div>
                          <p className="text-sm text-cyber-muted mt-2">
                            📅 {formatDateTime(refund.date)}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="text-xs text-cyber-muted mb-1">退款金额</p>
                          <p className="text-xl font-bold text-green-400">
                            {refund.priceWithTax.text}
                          </p>
                          {refund.pdfUrl && (
                            <a 
                              href={refund.pdfUrl} 
                              target="_blank" 
                              rel="noopener noreferrer"
                              className="text-xs text-cyber-accent hover:underline inline-flex items-center gap-1 mt-2"
                            >
                              <FileText className="w-3 h-3" />
                              下载PDF
                            </a>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : hasLoaded.refunds && !loading.refunds && refunds.length === 0 ? (
                <div className="text-center py-8 text-cyber-muted">
                  <CreditCard className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p>没有退款记录</p>
                </div>
              ) : null}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* 账户状态卡片 - 数据来源: accountInfo (API: GET /api/ovh/account/info) */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* KYC验证状态 - 使用字段: accountInfo.kycValidated */}
        <div>
          <Card className="cyber-card">
            <CardContent className="p-5">
              <div className="flex items-start gap-3">
                <div className={`p-2.5 rounded-lg flex-shrink-0 ${
                  accountInfo?.kycValidated === true ? 'bg-green-500/20' : 
                  accountInfo?.kycValidated === false ? 'bg-yellow-500/20' : 
                  'bg-cyber-accent/20'
                }`}>
                  <CheckCircle className={`w-5 h-5 ${
                    accountInfo?.kycValidated === true ? 'text-green-400' : 
                    accountInfo?.kycValidated === false ? 'text-yellow-400' : 
                    'text-cyber-accent'
                  }`} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-cyber-muted mb-2">KYC 验证</p>
                  {accountInfo?.kycValidated !== undefined && (
                    <div>
                      <p className={`text-2xl font-bold mb-1 ${
                        accountInfo.kycValidated ? 'text-green-400' : 'text-yellow-400'
                      }`}>
                        {accountInfo.kycValidated ? '已验证' : '未验证'}
                      </p>
                      <p className="text-xs text-cyber-muted">
                        {accountInfo.kycValidated ? '身份已确认' : '需要验证身份'}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* 账户状态 - 使用字段: accountInfo.state */}
        <div>
          <Card className="cyber-card">
            <CardContent className="p-5">
              <div className="flex items-start gap-3">
                <div className={`p-2.5 rounded-lg flex-shrink-0 ${
                  accountInfo?.state === 'complete' ? 'bg-green-500/20' : 'bg-cyber-accent/20'
                }`}>
                  <AlertCircle className={`w-5 h-5 ${
                    accountInfo?.state === 'complete' ? 'text-green-400' : 'text-cyber-accent'
                  }`} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-cyber-muted mb-2">账户状态</p>
                  {accountInfo?.state !== undefined && (
                    <div>
                      <p className={`text-2xl font-bold mb-1 ${
                        accountInfo.state === 'complete' ? 'text-green-400' : 'text-cyber-text'
                      }`}>
                        {accountInfo.state === 'complete' ? '正常' : accountInfo.state || '-'}
                      </p>
                      {accountInfo.phone && (
                        <p className="text-xs text-cyber-muted break-all">
                          电话: {accountInfo.phone}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* 账户货币 - 使用字段: accountInfo.currency.code, accountInfo.currency.symbol */}
        <div>
          <Card className="cyber-card">
            <CardContent className="p-5">
              <div className="flex items-start gap-3">
                <div className="p-2.5 rounded-lg bg-cyber-accent/20 flex-shrink-0">
                  <Wallet className="w-5 h-5 text-cyber-accent" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-cyber-muted mb-2">账户货币</p>
                  {accountInfo?.currency?.code && (
                    <div>
                      <p className="text-2xl font-bold text-cyber-text mb-1">
                        {accountInfo.currency.code}
                      </p>
                      {accountInfo.currency.symbol && (
                        <p className="text-xs text-cyber-muted">
                          符号: {accountInfo.currency.symbol}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
};

export default AccountManagementPage;

