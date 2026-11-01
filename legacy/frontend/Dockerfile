# ============================================================
# Agent-Native ERP Frontend - Node 20 构建 + Nginx 托管
# ============================================================

# ----------------------------------------------------------
# 阶段1: 构建阶段 - 编译前端应用
# ----------------------------------------------------------
FROM node:20-slim AS builder

WORKDIR /app

# 设置npm镜像源 (可选, 国内构建加速)
# RUN npm config set registry https://registry.npmmirror.com

# 复制依赖文件
COPY package*.json ./

# 安装依赖 (使用 npm install 兼容无 lock 文件的情况)
RUN npm install

# 复制源代码
COPY . .

# 构建生产版本
RUN npm run build

# ----------------------------------------------------------
# 阶段2: 运行阶段 - Nginx 托管静态资源
# ----------------------------------------------------------
FROM nginx:alpine

# 安装curl用于健康检查
RUN apk add --no-cache curl

# 复制构建产物到Nginx静态目录
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制Nginx配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 暴露端口
EXPOSE 3000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:3000/ || exit 1

# 启动Nginx
CMD ["nginx", "-g", "daemon off;"]
