# 多阶段构建 - 前端构建阶段
FROM node:18-alpine AS frontend-builder

WORKDIR /app

# 复制前端依赖文件
COPY package*.json ./

# 安装前端依赖
RUN npm ci

# 复制前端源码
COPY src ./src
COPY public ./public
COPY index.html ./
COPY vite.config.ts ./
COPY tsconfig*.json ./
COPY tailwind.config.ts ./
COPY postcss.config.js ./
COPY components.json ./
COPY eslint.config.js ./

# 构建前端（生产环境用构建版本，开发环境跳过）
# RUN npm run build

# =====================================
# 运行阶段 - 包含 Python 和 Node.js
FROM node:18-slim

WORKDIR /app

# 安装 Python 和系统依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制后端依赖文件并安装
COPY backend/requirements.txt ./backend/
RUN pip3 install --no-cache-dir -r backend/requirements.txt

# 复制后端源码
COPY backend/*.py ./backend/
COPY backend/data ./backend/data
COPY backend/.env ./backend/.env

# 创建必要的目录
RUN mkdir -p backend/cache backend/logs

# 复制前端源码（开发模式运行）
COPY --from=frontend-builder /app /app/frontend

# 创建启动脚本
RUN echo '#!/bin/sh\n\
cd /app/backend && python3 app.py &\n\
cd /app/frontend && npm run dev -- --host 0.0.0.0 --port 8080\n\
' > /start.sh && chmod +x /start.sh

# 暴露端口
EXPOSE 5000 8080

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 启动命令
CMD ["/start.sh"]
