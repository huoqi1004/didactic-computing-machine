-- ============================================================
-- Agent-Native ERP - MySQL 数据库初始化脚本
-- 由 Docker 在 mysql 容器首次启动时自动执行
-- 位置: /docker-entrypoint-initdb.d/init-db.sql
-- 注意: MySQL 的 docker-entrypoint 会自动创建 MYSQL_DATABASE 环境变量指定的数据库
--       (erp_db) 和 MYSQL_USER 用户 (erp), 此脚本仅创建基础表结构
-- ============================================================

-- ----------------------------------------------------------
-- 1. 创建基础表结构 (核心域表)
-- ----------------------------------------------------------

-- Agent 执行域 - Agent 注册表
CREATE TABLE IF NOT EXISTS agents (
    id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
    name VARCHAR(255) NOT NULL,
    agent_type VARCHAR(100) NOT NULL,
    description TEXT,
    config JSON DEFAULT NULL,
    status VARCHAR(50) DEFAULT 'inactive',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Agent 执行域 - Agent 执行记录
CREATE TABLE IF NOT EXISTS agent_executions (
    id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
    agent_id CHAR(36) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    input_data JSON DEFAULT NULL,
    output_data JSON DEFAULT NULL,
    error_message TEXT,
    duration_ms INT,
    started_at TIMESTAMP NULL DEFAULT NULL,
    completed_at TIMESTAMP NULL DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_agent_executions_agent FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 数据知识域 - 知识文档表
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
    title VARCHAR(500) NOT NULL,
    content TEXT,
    source VARCHAR(255),
    metadata JSON DEFAULT NULL,
    embedding_status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 用户交互域 - 用户表
CREATE TABLE IF NOT EXISTS users (
    id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 用户交互域 - 会话表
CREATE TABLE IF NOT EXISTS user_sessions (
    id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
    user_id CHAR(36) NOT NULL,
    session_token VARCHAR(500) UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------
-- 2. 创建索引
-- ----------------------------------------------------------
CREATE INDEX idx_agents_status ON agents(status);
CREATE INDEX idx_agents_type ON agents(agent_type);
CREATE INDEX idx_agent_executions_agent_id ON agent_executions(agent_id);
CREATE INDEX idx_agent_executions_status ON agent_executions(status);
CREATE INDEX idx_agent_executions_created_at ON agent_executions(created_at);
CREATE INDEX idx_knowledge_documents_title ON knowledge_documents(title);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_user_sessions_token ON user_sessions(session_token);

-- ----------------------------------------------------------
-- 3. 初始化完成
-- ----------------------------------------------------------
SELECT '========================================' AS message
UNION SELECT '  ERP 数据库初始化完成'
UNION SELECT '  数据库: erp_db'
UNION SELECT '  用户: erp'
UNION SELECT '========================================';
