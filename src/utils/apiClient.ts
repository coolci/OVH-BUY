/**
 * API客户端 - 统一的HTTP请求工具
 * 自动添加API密钥验证，防止后端被直接调用
 */

import axios, { AxiosInstance, AxiosRequestConfig, AxiosError } from 'axios';
import { API_URL, API_TIMEOUT } from '@/config/constants';
import { toast } from 'sonner';

// localStorage 密钥
const API_SECRET_KEY_STORAGE = 'api_secret_key';

/**
 * 获取存储的 API 密钥
 */
export const getApiSecretKey = (): string => {
  return localStorage.getItem(API_SECRET_KEY_STORAGE) || '';
};

/**
 * 设置 API 密钥
 */
export const setApiSecretKey = (key: string): void => {
  localStorage.setItem(API_SECRET_KEY_STORAGE, key);
};

/**
 * 清除 API 密钥
 */
export const clearApiSecretKey = (): void => {
  localStorage.removeItem(API_SECRET_KEY_STORAGE);
};

/**
 * 创建axios实例
 */
const apiClient: AxiosInstance = axios.create({
  baseURL: API_URL,
  timeout: API_TIMEOUT,
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * 请求拦截器 - 自动添加API密钥
 */
apiClient.interceptors.request.use(
  (config) => {
    // 从 localStorage 获取 API 密钥
    const apiKey = getApiSecretKey();
    if (apiKey) {
      config.headers['X-API-Key'] = apiKey;
    }
    
    // 添加时间戳（可选，用于防重放攻击）
    config.headers['X-Request-Time'] = Date.now().toString();
    
    return config;
  },
  (error) => {
    console.error('请求错误:', error);
    return Promise.reject(error);
  }
);

/**
 * 响应拦截器 - 统一错误处理
 */
apiClient.interceptors.response.use(
  (response) => {
    return response;
  },
  (error: AxiosError) => {
    // 统一错误处理
    if (error.response) {
      const status = error.response.status;
      const message = (error.response.data as any)?.error || error.message;
      
      switch (status) {
        case 401:
          // 401 错误静默处理，不显示 toast
          // 因为首次访问时没有密钥是正常情况
          console.log('API 认证失败，请在设置页面配置安全密钥');
          break;
        case 403:
          toast.error('访问被拒绝：权限不足');
          break;
        case 404:
          // 404完全静默，让各组件自行处理
          // 因为404可能是正常情况（如：检查安装进度时没有进行中的安装）
          // 或者服务器不支持某些功能（notAvailable标志）
          const notAvailable404 = (error.response.data as any)?.notAvailable;
          if (!notAvailable404) {
            // 只有非notAvailable的404才可能记录（但通常也静默）
          }
          break;
        case 400:
          // 400错误：检查是否是"功能不支持"的情况
          const notAvailable400 = (error.response.data as any)?.notAvailable;
          if (!notAvailable400) {
            // 只有非notAvailable的400才显示错误
            const message = (error.response.data as any)?.error || error.message;
            console.error(`API错误 [400]:`, message);
          }
          // notAvailable的400完全静默，让组件自行显示友好提示
          break;
        case 500:
          toast.error('服务器错误');
          break;
        default:
          // 其他状态码才输出错误
          const defaultMessage = (error.response.data as any)?.error || error.message;
          console.error(`API错误 [${status}]:`, defaultMessage);
      }
    } else if (error.request) {
      console.error('网络错误:', error.request);
      toast.error('网络连接失败，请检查网络');
    } else {
      console.error('请求配置错误:', error.message);
    }
    
    return Promise.reject(error);
  }
);

/**
 * 导出API客户端实例
 */
export default apiClient;

/**
 * 便捷方法
 */
export const api = {
  get: <T = any>(url: string, config?: AxiosRequestConfig) => 
    apiClient.get<T>(url, config),
  
  post: <T = any>(url: string, data?: any, config?: AxiosRequestConfig) => 
    apiClient.post<T>(url, data, config),
  
  put: <T = any>(url: string, data?: any, config?: AxiosRequestConfig) => 
    apiClient.put<T>(url, data, config),
  
  delete: <T = any>(url: string, config?: AxiosRequestConfig) => 
    apiClient.delete<T>(url, config),
  
  patch: <T = any>(url: string, data?: any, config?: AxiosRequestConfig) => 
    apiClient.patch<T>(url, data, config),
};
