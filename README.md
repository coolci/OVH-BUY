# 🎯 OVH Phantom Sniper - 服务器自动抢购系统

一个强大的 OVH 服务器监控和自动抢购工具。

---

## ⚡ 快速开始

### 方式一：Docker 部署（推荐）

```bash
# 一键启动
docker-compose up -d --build

# 访问
http://localhost:8080
```

### 方式二：本地运行

```bash
# 后端（终端1）
cd backend
pip install -r requirements.txt
python app.py

# 前端（终端2）
npm install
npm run dev
```

**访问：** http://localhost:8080

---

## 🔧 配置说明

### 首次使用配置

1. 访问：http://localhost:8080/settings
2. 填写两项：
   - **网站安全密钥**：复制 `backend/.env` 中的 `API_SECRET_KEY`
   - **OVH API 凭据**：从 https://api.ovh.com/createToken/ 获取

---

## 📝 配置文件位置

### 前端配置（2个位置）

| 配置项 | 文件 | 行号 |
|--------|------|------|
| 前端端口 | `vite.config.ts` | 10 |
| 后端地址 | `src/config/constants.ts` | 68 |

### 后端配置（1个文件）

**`backend/.env`：**
```env
API_SECRET_KEY=ovh-phantom-sniper-2024-secret-key
PORT=5000
DEBUG=false
ENABLE_API_KEY_AUTH=true
```

---

## 🐳 Docker 使用

### 启动

```bash
docker-compose up -d --build
```

### 端口

- 前端：http://localhost:8080
- 后端：http://localhost:5000

### 管理

```bash
# 查看日志
docker-compose logs -f

# 停止
docker-compose stop

# 重启
docker-compose restart
```

---

## 📊 端口说明

```
浏览器 → http://localhost:8080 (前端)
              ↓
        API 请求
              ↓
        http://localhost:5000 (后端)
```

**前后端端口都直接暴露，无需反向代理** ✅

---

## 🔒 生产环境

### 修改密钥

```bash
# 生成强密钥
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 修改 backend/.env
API_SECRET_KEY=<生成的密钥>
```

### 修改后端地址（如果部署在服务器上）

**文件：** `src/config/constants.ts` 第 68 行

```typescript
// 本地
export const API_URL = 'http://localhost:5000/api';

// 服务器
export const API_URL = 'http://your-server-ip:5000/api';
```

---

## 📚 详细文档

- **DOCKER使用说明.md** - Docker 部署详细指南
- **启动说明.md** - 快速启动指南

---

## ✨ 功能特性

- 🔍 实时监控服务器可用性
- ⚡ 自动抢购
- 📊 数据统计
- 🔔 Telegram 通知
- 💾 智能缓存（2小时有效期）

---

## 🛠️ 技术栈

- **前端：** React + Vite + TypeScript + Tailwind CSS
- **后端：** Python + Flask
- **部署：** Docker / 本地运行

---

## 📞 常见问题

**Q: 如何修改端口？**
- 前端：`vite.config.ts` 第 10 行
- 后端：`backend/.env` 的 `PORT`

**Q: 首次访问看到 401 错误？**
- 正常！去设置页面配置密钥即可

**Q: 如何查看日志？**
- 本地：`backend/logs/app.log`
- Docker：`docker-compose logs -f`

---

*版本：v2.1.0*  
*更新：2024-10-29*
