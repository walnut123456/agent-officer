CREATE DATABASE IF NOT EXISTS reactor_agent 
    DEFAULT CHARACTER SET utf8mb4 
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE reactor_agent;

DROP TABLE IF EXISTS visitor_identity;
CREATE TABLE visitor_identity (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    visitor_id VARCHAR(64) NOT NULL UNIQUE COMMENT '访客ID',
    token_digest VARCHAR(128) NOT NULL COMMENT 'token摘要',
    status INT NOT NULL DEFAULT 1 COMMENT '状态(0:无效,1:有效)',
    first_seen_at DATETIME NOT NULL COMMENT '首次访问时间',
    last_seen_at DATETIME NOT NULL COMMENT '最后访问时间',
    last_ip VARCHAR(64) COMMENT '最后访问IP',
    last_user_agent VARCHAR(512) COMMENT '最后访问UserAgent',
    username VARCHAR(32) COMMENT '用户名',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted TINYINT NOT NULL DEFAULT 0 COMMENT '删除标识(0:未删除,1:已删除)',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识(0:无效,1:有效)',
    INDEX idx_visitor_id (visitor_id),
    INDEX idx_token_digest (token_digest),
    INDEX idx_yn (yn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='匿名访客身份表';

DROP TABLE IF EXISTS dialogue_session;
CREATE TABLE dialogue_session (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    session_id VARCHAR(64) NOT NULL UNIQUE COMMENT '会话ID',
    visitor_id VARCHAR(64) NOT NULL COMMENT '访客ID',
    title VARCHAR(128) COMMENT '会话标题',
    status INT NOT NULL DEFAULT 0 COMMENT '会话状态',
    latest_request_id VARCHAR(64) COMMENT '最新请求ID',
    latest_query_text TEXT COMMENT '最新查询文本',
    latest_summary_text TEXT COMMENT '最新摘要文本',
    run_count INT DEFAULT 0 COMMENT '运行次数',
    finished_run_count INT DEFAULT 0 COMMENT '完成次数',
    failed_run_count INT DEFAULT 0 COMMENT '失败次数',
    started_at DATETIME COMMENT '开始时间',
    last_active_at DATETIME COMMENT '最后活跃时间',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted TINYINT NOT NULL DEFAULT 0 COMMENT '删除标识',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    INDEX idx_session_id (session_id),
    INDEX idx_visitor_id (visitor_id),
    INDEX idx_yn (yn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='对话会话表';

DROP TABLE IF EXISTS dialogue_run;
CREATE TABLE dialogue_run (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    run_uid VARCHAR(64) COMMENT '运行UID',
    request_id VARCHAR(64) NOT NULL UNIQUE COMMENT '请求ID',
    session_id VARCHAR(64) NOT NULL COMMENT '会话ID',
    visitor_id VARCHAR(64) COMMENT '访客ID',
    entry_agent VARCHAR(64) COMMENT '入口执行链',
    status INT NOT NULL DEFAULT 0 COMMENT '运行状态',
    query_text TEXT COMMENT '用户原始问题',
    final_summary_text TEXT COMMENT '最终总结文本',
    llm_call_count INT DEFAULT 0 COMMENT 'LLM调用次数',
    tool_call_count INT DEFAULT 0 COMMENT '工具调用次数',
    artifact_count INT DEFAULT 0 COMMENT '产物数量',
    prompt_tokens_total INT DEFAULT 0 COMMENT 'LLM输入token总量',
    completion_tokens_total INT DEFAULT 0 COMMENT 'LLM输出token总量',
    total_tokens_total INT DEFAULT 0 COMMENT 'LLM token总量',
    error_code VARCHAR(64) COMMENT '失败码',
    error_msg TEXT COMMENT '失败信息',
    started_at DATETIME COMMENT '开始时间',
    finished_at DATETIME COMMENT '结束时间',
    duration_ms BIGINT DEFAULT 0 COMMENT '耗时(毫秒)',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted TINYINT NOT NULL DEFAULT 0 COMMENT '删除标识',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    INDEX idx_request_id (request_id),
    INDEX idx_session_id (session_id),
    INDEX idx_status (status),
    INDEX idx_yn (yn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='对话运行表';

DROP TABLE IF EXISTS llm_invocation;
CREATE TABLE llm_invocation (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    run_id BIGINT NOT NULL COMMENT '运行ID',
    invocation_seq INT DEFAULT 0 COMMENT 'run内递增序号',
    agent_name VARCHAR(128) COMMENT '当前agent名称',
    step_no INT DEFAULT 0 COMMENT '当前步号',
    call_kind VARCHAR(32) COMMENT 'ask/askTool',
    streaming TINYINT DEFAULT 0 COMMENT '是否流式',
    model_name VARCHAR(128) NOT NULL COMMENT '模型名称',
    response_text TEXT COMMENT '完整响应文本',
    tool_call_count INT DEFAULT 0 COMMENT '工具调用数量',
    prompt_tokens INT DEFAULT 0 COMMENT 'prompt token',
    completion_tokens INT DEFAULT 0 COMMENT 'completion token',
    total_tokens INT DEFAULT 0 COMMENT 'total token',
    finish_reason VARCHAR(64) COMMENT '完成原因',
    status INT NOT NULL DEFAULT 0 COMMENT '状态',
    error_msg TEXT COMMENT '错误信息',
    started_at DATETIME COMMENT '开始时间',
    finished_at DATETIME COMMENT '结束时间',
    duration_ms BIGINT DEFAULT 0 COMMENT '耗时',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted TINYINT NOT NULL DEFAULT 0 COMMENT '删除标识',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    INDEX idx_run_id (run_id),
    INDEX idx_model_name (model_name),
    INDEX idx_yn (yn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='LLM调用记录表';

DROP TABLE IF EXISTS tool_invocation;
CREATE TABLE tool_invocation (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    run_id BIGINT NOT NULL COMMENT '运行ID',
    llm_invocation_id BIGINT COMMENT 'LLM调用ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    dispatch_index INT DEFAULT 0 COMMENT '原始分发顺序',
    agent_name VARCHAR(128) COMMENT '当前agent名称',
    step_no INT DEFAULT 0 COMMENT '当前步号',
    tool_name VARCHAR(128) NOT NULL COMMENT '工具名称',
    tool_provider VARCHAR(32) COMMENT 'local/mcp',
    input_json TEXT COMMENT '入参JSON',
    llm_observation TEXT COMMENT '主智能体observation',
    status INT NOT NULL DEFAULT 0 COMMENT '状态',
    error_msg TEXT COMMENT '错误信息',
    started_at DATETIME COMMENT '开始时间',
    finished_at DATETIME COMMENT '结束时间',
    duration_ms BIGINT DEFAULT 0 COMMENT '耗时',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted TINYINT NOT NULL DEFAULT 0 COMMENT '删除标识',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    INDEX idx_run_id (run_id),
    INDEX idx_llm_invocation_id (llm_invocation_id),
    INDEX idx_tool_name (tool_name),
    INDEX idx_yn (yn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='工具调用记录表';

DROP TABLE IF EXISTS artifact_record;
CREATE TABLE artifact_record (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    run_id BIGINT NOT NULL COMMENT '运行ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    artifact_role VARCHAR(32) COMMENT 'input/output',
    visibility VARCHAR(32) COMMENT 'visible/internal',
    source_type VARCHAR(32) COMMENT 'user_upload/tool_output',
    source_name VARCHAR(256) COMMENT '来源名称',
    file_name VARCHAR(256) NOT NULL COMMENT '文件名',
    storage_key VARCHAR(256) COMMENT '稳定资源key',
    download_url VARCHAR(512) COMMENT '下载地址',
    preview_url VARCHAR(512) COMMENT '预览地址',
    mime_type VARCHAR(128) COMMENT 'MIME类型',
    file_size BIGINT DEFAULT 0 COMMENT '文件大小',
    file_hash VARCHAR(128) COMMENT '文件哈希',
    metadata_json TEXT COMMENT '扩展元数据',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted TINYINT NOT NULL DEFAULT 0 COMMENT '删除标识',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    INDEX idx_run_id (run_id),
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_artifact_role (artifact_role),
    INDEX idx_yn (yn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='产物记录表';

DROP TABLE IF EXISTS admin_user;
CREATE TABLE admin_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    user_id VARCHAR(64) NOT NULL UNIQUE COMMENT '用户ID',
    username VARCHAR(64) NOT NULL UNIQUE COMMENT '用户名',
    password VARCHAR(128) NOT NULL COMMENT '密码',
    status INT DEFAULT 1 COMMENT '状态(0:禁用,1:启用,2:锁定)',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_user_id (user_id),
    INDEX idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='管理员用户表';

INSERT INTO admin_user (user_id, username, password, status) VALUES
('admin-001', 'admin', '$2a$10$N9qo8uLOickgx2ZMRZoMye.IjzqAKL9xL5jvMFVdNJHvGCgTq/VEq', 1);

DROP TABLE IF EXISTS ai_agent;
CREATE TABLE ai_agent (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    agent_id VARCHAR(64) NOT NULL UNIQUE COMMENT '智能体ID',
    agent_name VARCHAR(128) NOT NULL COMMENT '智能体名称',
    description VARCHAR(512) COMMENT '描述',
    channel VARCHAR(64) COMMENT '渠道',
    strategy VARCHAR(64) NOT NULL COMMENT '策略(fix/react/plan_solve)',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    flow_step_count INT DEFAULT 0 COMMENT '流程步骤数',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_agent_id (agent_id),
    INDEX idx_strategy (strategy),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体配置表';

INSERT INTO ai_agent (agent_id, agent_name, description, channel, strategy, status, flow_step_count) VALUES
('default-fix', '默认助手', '一个通用的AI助手，可以回答各种问题', 'web', 'fix', 1, 1),
('react-fix', '推理助手', '具备推理能力的AI助手，适合复杂问题分析', 'web', 'fix', 1, 1);

DROP TABLE IF EXISTS ai_client;
CREATE TABLE ai_client (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    client_id VARCHAR(64) NOT NULL UNIQUE COMMENT '客户端ID',
    client_name VARCHAR(128) NOT NULL COMMENT '客户端名称',
    description VARCHAR(512) COMMENT '描述',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_client_id (client_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI客户端表';

DROP TABLE IF EXISTS ai_client_config;
CREATE TABLE ai_client_config (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    source_type VARCHAR(32) COMMENT '源类型',
    source_id VARCHAR(64) COMMENT '源ID',
    target_type VARCHAR(32) COMMENT '目标类型',
    target_id VARCHAR(64) COMMENT '目标ID',
    ext_param TEXT COMMENT '扩展参数',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_source (source_type, source_id),
    INDEX idx_target (target_type, target_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI客户端统一关联配置表';

DROP TABLE IF EXISTS ai_client_model;
CREATE TABLE ai_client_model (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    model_id VARCHAR(64) NOT NULL UNIQUE COMMENT '模型ID',
    model_name VARCHAR(128) NOT NULL COMMENT '模型名称',
    api_id VARCHAR(64) COMMENT '关联API配置ID',
    model_type VARCHAR(32) COMMENT '模型类型',
    context_window INT COMMENT '上下文窗口',
    temperature DECIMAL(4,2) DEFAULT 0.7 COMMENT '温度',
    max_tokens INT COMMENT '最大token数',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_model_id (model_id),
    INDEX idx_api_id (api_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI客户端模型配置表';

DROP TABLE IF EXISTS ai_client_api;
CREATE TABLE ai_client_api (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    api_id VARCHAR(64) NOT NULL UNIQUE COMMENT 'API配置ID',
    api_name VARCHAR(128) NOT NULL COMMENT 'API名称',
    client_id VARCHAR(64) COMMENT '关联客户端ID',
    provider VARCHAR(32) COMMENT '提供商(openai/ollama/custom)',
    base_url VARCHAR(512) COMMENT '基础URL',
    api_key VARCHAR(256) COMMENT 'API密钥',
    timeout INT DEFAULT 60 COMMENT '超时时间(秒)',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_api_id (api_id),
    INDEX idx_client_id (client_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI客户端API配置表';

DROP TABLE IF EXISTS ai_client_advisor;
CREATE TABLE ai_client_advisor (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    advisor_id VARCHAR(64) NOT NULL UNIQUE COMMENT '顾问ID',
    advisor_name VARCHAR(128) NOT NULL COMMENT '顾问名称',
    advisor_type VARCHAR(64) COMMENT '顾问类型',
    order_num INT DEFAULT 0 COMMENT '顺序号',
    ext_param TEXT COMMENT '扩展参数',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_advisor_id (advisor_id),
    INDEX idx_advisor_type (advisor_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='顾问配置表';

DROP TABLE IF EXISTS ai_client_system_prompt;
CREATE TABLE ai_client_system_prompt (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    prompt_id VARCHAR(64) NOT NULL UNIQUE COMMENT '提示词ID',
    prompt_name VARCHAR(128) NOT NULL COMMENT '提示词名称',
    prompt_content TEXT COMMENT '提示词内容',
    description VARCHAR(512) COMMENT '描述',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_prompt_id (prompt_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统提示词配置表';

DROP TABLE IF EXISTS ai_client_rag_order;
CREATE TABLE ai_client_rag_order (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    rag_id VARCHAR(64) NOT NULL UNIQUE COMMENT '知识库ID',
    rag_name VARCHAR(128) NOT NULL COMMENT '知识库名称',
    knowledge_tag VARCHAR(128) COMMENT '知识标签',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_rag_id (rag_id),
    INDEX idx_knowledge_tag (knowledge_tag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库配置表';

DROP TABLE IF EXISTS ai_client_tool_mcp;
CREATE TABLE ai_client_tool_mcp (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    mcp_id VARCHAR(64) NOT NULL UNIQUE COMMENT 'MCP ID',
    mcp_name VARCHAR(128) NOT NULL COMMENT 'MCP名称',
    transport_type VARCHAR(32) COMMENT '传输类型',
    transport_config TEXT COMMENT '传输配置',
    request_timeout INT DEFAULT 30 COMMENT '请求超时时间(分钟)',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_mcp_id (mcp_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='MCP客户端配置表';

DROP TABLE IF EXISTS ai_agent_draw_config;
CREATE TABLE ai_agent_draw_config (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    config_id VARCHAR(64) NOT NULL UNIQUE COMMENT '配置ID',
    config_name VARCHAR(128) NOT NULL COMMENT '配置名称',
    agent_id VARCHAR(64) COMMENT '智能体ID',
    description VARCHAR(512) COMMENT '描述',
    config_data TEXT COMMENT '配置数据',
    version INT DEFAULT 1 COMMENT '版本号',
    status TINYINT DEFAULT 1 COMMENT '状态(0:禁用,1:启用)',
    create_by VARCHAR(64) COMMENT '创建人',
    update_by VARCHAR(64) COMMENT '更新人',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_config_id (config_id),
    INDEX idx_agent_id (agent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI智能体拖拉拽配置主表';

DROP TABLE IF EXISTS ai_agent_flow_config;
CREATE TABLE ai_agent_flow_config (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    agent_id VARCHAR(64) COMMENT '智能体ID',
    client_id VARCHAR(64) COMMENT '客户端ID',
    client_name VARCHAR(128) COMMENT '客户端名称',
    client_type VARCHAR(32) COMMENT '客户端类型',
    sequence INT DEFAULT 0 COMMENT '执行顺序',
    step_prompt TEXT COMMENT '步骤提示词',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_agent_id (agent_id),
    INDEX idx_client_id (client_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体-客户端关联表';

DROP TABLE IF EXISTS ai_agent_task_schedule;
CREATE TABLE ai_agent_task_schedule (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    agent_id VARCHAR(64) COMMENT '智能体ID',
    task_name VARCHAR(128) NOT NULL COMMENT '任务名称',
    description VARCHAR(512) COMMENT '任务描述',
    cron_expression VARCHAR(64) COMMENT 'Cron表达式',
    task_param TEXT COMMENT '任务参数',
    status TINYINT DEFAULT 1 COMMENT '状态(0:无效,1:有效)',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_agent_id (agent_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体任务调度配置表';

DROP TABLE IF EXISTS chat_model_info;
CREATE TABLE chat_model_info (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    code VARCHAR(64) NOT NULL UNIQUE COMMENT '模型代码',
    type VARCHAR(32) COMMENT '类型',
    content TEXT COMMENT '内容',
    name VARCHAR(128) NOT NULL COMMENT '模型名称',
    use_prompt TEXT COMMENT '使用提示',
    business_prompt TEXT COMMENT '业务提示',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    INDEX idx_code (code),
    INDEX idx_yn (yn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='聊天模型信息表';

INSERT INTO chat_model_info (code, name) VALUES
('glm-5.1', 'GLM-5.1');

DROP TABLE IF EXISTS chat_model_schema;
CREATE TABLE chat_model_schema (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    model_code VARCHAR(64) NOT NULL COMMENT '模型代码',
    column_id VARCHAR(64) COMMENT '字段ID',
    column_name VARCHAR(128) COMMENT '字段名称',
    column_comment VARCHAR(512) COMMENT '字段注释',
    few_shot TEXT COMMENT '示例',
    data_type VARCHAR(32) COMMENT '数据类型',
    synonyms TEXT COMMENT '同义词',
    vector_uuid VARCHAR(64) COMMENT '向量UUID',
    default_recall INT DEFAULT 0 COMMENT '默认召回',
    analyze_suggest INT DEFAULT 0 COMMENT '分析建议',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    INDEX idx_model_code (model_code),
    INDEX idx_yn (yn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='聊天模型Schema表';

DROP TABLE IF EXISTS tool_output_code_interpreter;
CREATE TABLE tool_output_code_interpreter (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    code_language VARCHAR(32) COMMENT '代码语言',
    code_content TEXT COMMENT '代码内容',
    code_output TEXT COMMENT '代码输出',
    exit_code INT COMMENT '退出码',
    status INT DEFAULT 0 COMMENT '状态',
    error_message TEXT COMMENT '错误信息',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='代码解释器输出表';

DROP TABLE IF EXISTS tool_output_data_analysis;
CREATE TABLE tool_output_data_analysis (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    data_source VARCHAR(256) COMMENT '数据源',
    data_query TEXT COMMENT '数据查询',
    analysis_result TEXT COMMENT '分析结果',
    analysis_summary TEXT COMMENT '分析摘要',
    status INT DEFAULT 0 COMMENT '状态',
    error_message TEXT COMMENT '错误信息',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='数据分析输出表';

DROP TABLE IF EXISTS tool_output_deep_search;
CREATE TABLE tool_output_deep_search (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    search_query TEXT COMMENT '搜索查询',
    search_results TEXT COMMENT '搜索结果',
    status INT DEFAULT 0 COMMENT '状态',
    error_message TEXT COMMENT '错误信息',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='深度搜索输出表';

DROP TABLE IF EXISTS tool_output_file_tool;
CREATE TABLE tool_output_file_tool (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    file_operation VARCHAR(32) COMMENT '文件操作',
    file_path VARCHAR(512) COMMENT '文件路径',
    file_content TEXT COMMENT '文件内容',
    status INT DEFAULT 0 COMMENT '状态',
    error_message TEXT COMMENT '错误信息',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='文件工具输出表';

DROP TABLE IF EXISTS tool_output_image_generation;
CREATE TABLE tool_output_image_generation (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    prompt TEXT COMMENT '生成提示',
    image_url VARCHAR(512) COMMENT '图片URL',
    image_data LONGBLOB COMMENT '图片数据',
    status INT DEFAULT 0 COMMENT '状态',
    error_message TEXT COMMENT '错误信息',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='图像生成输出表';

DROP TABLE IF EXISTS tool_output_multimodal_agent;
CREATE TABLE tool_output_multimodal_agent (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    input_data TEXT COMMENT '输入数据',
    output_data TEXT COMMENT '输出数据',
    status INT DEFAULT 0 COMMENT '状态',
    error_message TEXT COMMENT '错误信息',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='多模态代理输出表';

DROP TABLE IF EXISTS tool_output_planning;
CREATE TABLE tool_output_planning (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    plan_text TEXT COMMENT '计划文本',
    plan_steps TEXT COMMENT '计划步骤',
    status INT DEFAULT 0 COMMENT '状态',
    error_message TEXT COMMENT '错误信息',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='规划输出表';

DROP TABLE IF EXISTS tool_output_report_tool;
CREATE TABLE tool_output_report_tool (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    report_title VARCHAR(256) COMMENT '报告标题',
    report_content TEXT COMMENT '报告内容',
    report_format VARCHAR(32) COMMENT '报告格式',
    status INT DEFAULT 0 COMMENT '状态',
    error_message TEXT COMMENT '错误信息',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='报告工具输出表';

DROP TABLE IF EXISTS tool_output_script_runner;
CREATE TABLE tool_output_script_runner (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    tool_invocation_id BIGINT COMMENT '工具调用ID',
    request_id VARCHAR(64) COMMENT '请求ID',
    tool_call_id VARCHAR(64) COMMENT '工具调用ID',
    script_content TEXT COMMENT '脚本内容',
    script_output TEXT COMMENT '脚本输出',
    exit_code INT COMMENT '退出码',
    status INT DEFAULT 0 COMMENT '状态',
    error_message TEXT COMMENT '错误信息',
    yn TINYINT NOT NULL DEFAULT 1 COMMENT '有效标识',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_tool_invocation_id (tool_invocation_id),
    INDEX idx_request_id (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='脚本运行器输出表';