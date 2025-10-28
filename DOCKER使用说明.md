# 🐳 Docker 使用说明

## 🚀 启动

```bash
docker-compose up -d --build
```

---

## 🌐 访问

**前端地址：** http://localhost:8080

**说明：**
- 前端运行在 **8080** 端口
- 后端运行在 **5000** 端口（前端自动连接）

---

## ⚙️ 配置

### 修改配置文件

直接编辑：`backend/.env`

```env
API_SECRET_KEY=123456
PORT=5000
DEBUG=false
ENABLE_API_KEY_AUTH=true
```

### 使配置生效

```bash
docker-compose restart
```

---

## 🔧 修改前端端口

**文件：** `vite.config.ts` 第 10 行

```typescript
server: {
  port: 8080,  // 改成其他端口，如 3000, 5173 等
}
```

修改后重新构建：
```bash
docker-compose up -d --build
```

---

## 📋 常用命令

```bash
# 启动
docker-compose up -d

# 停止
docker-compose stop

# 重启
docker-compose restart

# 查看日志
docker-compose logs -f

# 删除容器
docker-compose down
```

---

## 📁 文件位置

所有文件都在宿主机（您的项目目录）：

```
backend/
├── .env          ← 修改配置
├── data/         ← 数据文件
├── logs/         ← 日志文件
└── cache/        ← 缓存文件
```

**直接修改，重启容器生效！** ✅

---

## 🎯 完整流程

```bash
# 1. 启动容器
docker-compose up -d --build

# 2. 访问前端
http://localhost:8080

# 3. 配置密钥
访问设置页面配置 API 密钥

# 4. 修改配置（如需要）
编辑 backend/.env
docker-compose restart
```

**就这么简单！** 🎉
