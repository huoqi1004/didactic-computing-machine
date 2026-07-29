# Agent-Native ERP System

智能体原生 ERP 系统 —— 以自主 AI Agent 为基本业务单元的企业数字化大脑。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.10 / FastAPI / SQLAlchemy 2.0 (async) / Pydantic 2 |
| 前端 | React 18 / TypeScript / Vite 5 / Tailwind CSS / Zustand |
| 数据库 | PostgreSQL 16 / Redis 7 |
| 监控 | Prometheus / Grafana |
| 部署 | Docker / Docker Compose |
| CI/CD | GitHub Actions |

## 快速开始

### 前置条件

- Docker 20.10+
- Docker Compose v2
- Git

### 一键启动

```bash
# 1. 克隆项目
git clone <repository-url>
cd 企业数字化大脑

# 2. 准备环境配置
cp backend/.env.example backend/.env

# 3. 启动所有服务 (后端 + 前端 + 数据库 + 缓存 + 监控)
docker compose up -d

# 4. 验证服务状态
curl http://localhost:8000/health
# 前端访问: http://localhost:3000
# API 文档: http://localhost:8000/docs
```

### 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| Backend API | 8000 | FastAPI 后端服务 |
| Frontend | 3000 | Nginx 托管前端静态资源 |
| PostgreSQL | 5432 | 数据库 |
| Redis | 6379 | 缓存 |
| Prometheus | 9090 | 指标采集 |
| Grafana | 3001 | 监控面板 (admin/admin) |

## 项目结构

```
企业数字化大脑/
├── .github/
│   └── workflows/
│       ├── ci.yml              # CI 持续集成流水线
│       └── deploy.yml          # CD 部署流水线
├── backend/                    # 后端服务 (FastAPI)
│   ├── app/
│   │   ├── agents/             # 业务 Agent (库存、记账等)
│   │   ├── api/                # API 路由
│   │   ├── core/               # 核心模块 (配置、数据库、事件、安全)
│   │   ├── db/                 # 数据模型
│   │   └── main.py             # 应用入口
│   ├── tests/                  # 单元测试
│   ├── Dockerfile
│   ├── pyproject.toml           # 代码质量配置 (ruff/black/bandit)
│   ├── pytest.ini
│   └── requirements.txt
├── frontend/                   # 前端服务 (React + Vite)
│   ├── src/
│   ├── Dockerfile
│   ├── .eslintrc.cjs           # ESLint 配置
│   └── package.json
├── infra/                      # 基础设施配置
│   ├── docker/                 # Docker 初始化脚本
│   ├── monitoring/             # Prometheus/Grafana 配置
│   └── scripts/                # 启动脚本
├── docker-compose.yml          # 容器编排
├── .env.example                # 环境变量模板
├── .gitignore
└── README.md
```

## 开发指南

### 后端开发

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate    # Linux/Mac
# .venv\Scripts\activate       # Windows

# 安装依赖
pip install -r requirements.txt
pip install pytest pytest-asyncio  # 测试依赖

# 运行测试
python -m pytest -v

# 代码检查
ruff check .
black --check .

# 启动开发服务器
uvicorn app.main:app --reload --port 8000
```

### 前端开发

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器 (自动代理 /api 到 localhost:8000)
npm run dev

# 类型检查
npx tsc --noEmit

# 构建生产版本
npm run build
```

### CI/CD

- **CI 流水线** (`.github/workflows/ci.yml`): push 到 main/develop 或 PR 到 main 时触发
  - 后端测试 (pytest)
  - 后端代码检查 (ruff + black)
  - 前端构建 (tsc + vite build)
  - 安全扫描 (bandit + npm audit)
  - Docker 镜像构建
- **部署流水线** (`.github/workflows/deploy.yml`): main 分支 CI 通过后自动部署到 Staging
