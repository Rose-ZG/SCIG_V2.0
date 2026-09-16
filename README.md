<div align="center">
  <img src="static/assets/logo.png" alt="知构引擎标志" width="112" />

  <h1>知构引擎（SCIG）</h1>
  <p><strong>面向小样本科研数据的证据感知科学判断与下一实验决策引擎</strong></p>

<p>
  <img alt="Version" src="https://img.shields.io/badge/version-0.5.0-1f6feb" />
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" />
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white" />
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-Required_in_Production-4169E1?logo=postgresql&logoColor=white" />
  <img alt="Tests" src="https://img.shields.io/badge/tests-11_passing-2ea44f" />
</p>

<p>
  <a href="#核心能力">核心能力</a> ·
  <a href="#系统架构">系统架构</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#api-参考">API</a> ·
  <a href="#生产部署">生产部署</a> ·
  <a href="#安全与责任边界">安全边界</a>
</p>

</div>

---

## 项目简介

知构引擎（Scientific Cognition & Inference Graph，SCIG）用于辅助研究人员从有限实验数据中完成模型比较、证据审查、异常复核和下一实验规划。系统不会把“拟合最好”等同于“科学规律成立”，而是依次检查：

1. **科学约束**：边界、单调性、可行域与机制先验是否成立；
2. **统计证据**：AICc、BIC 与交叉验证是否支持候选模型；
3. **稳定性证据**：Bootstrap 下的可识别性与预测稳定性是否可靠；
4. **开放集风险**：候选机制不完整或证据冲突时，是否应拒绝形成唯一结论；
5. **行动价值**：在安全、温度、预算和成本约束下，哪个实验点最有信息增益。

> [!IMPORTANT]
> 本项目是科研决策辅助系统，不替代研究人员、实验负责人或安全负责人的最终判断，也不直接控制实验设备。进入真实设备或高风险实验流程前，必须完成领域校准、专家验收和安全联锁集成。

### 项目状态

| 项目 | 当前状态 |
| --- | --- |
| 当前版本 | `0.5.0` |
| 成熟度 | 可运行、可测试的科研决策原型 |
| 默认工作模式 | 本地规则代理 + 物理约束优化 |
| 生产数据存储 | 外部 PostgreSQL |
| 可选大模型 | DeepSeek API；不可用时可配置为本地规则兜底 |
| 自动化验证 | 11 项 `unittest`，覆盖核心分析、HTTP、报告和公式安全 |

## 核心能力

| 能力域 | 实现内容 | 关键输出 |
| --- | --- | --- |
| 数据契约 | CSV、JSON、行式表格；摄氏度、开尔文、华氏度；比例与百分比归一化 | `data_contract`、标准化数据、原始值快照 |
| 数据追溯 | 样本号、批次、仪器、操作员、重复编号、记录时间和数据来源 | `rows[].raw`、`rows[].provenance` |
| 科学约束 | 边界、单调性、可行域投影、反向求解和残差诊断 | `physics_constraints` |
| 候选机制 | 幂律、Arrhenius、Langmuir-Hinshelwood、显式符号回归 | `models`、`symbolic_regression_layer` |
| 证据门控 | 科学约束、AICc/BIC/交叉验证、Bootstrap 稳定性 | `evidence_gates`、`hypothesis_ranking` |
| 异常归因 | 录入问题、测量误差、模型失配与机制变化的后验归因 | `anomaly_attribution` |
| 开放集判断 | 保留 `H_other`，在高不确定性或候选库不足时拒答 | `open_set_decision` |
| 主动实验设计 | 在温度、安全、预算和成本权重约束下推荐下一实验点 | `experiment_design` |
| 报告与审计 | Markdown/DOCX 报告；数据哈希、随机种子、版本和决策链 | `audit`、可下载报告 |
| 交互工作台 | 静态科研工作台、本地问答、可选 DeepSeek 适配 | `/api/bootstrap`、`/api/chat` |

异常样本只会被**标记与解释**，不会被静默删除；所有模型选择均保留证据链和拒答路径。

## 系统架构

```mermaid
flowchart LR
    A[CSV / JSON / 表格数据] --> B[数据契约与单位归一化]
    B --> C[科学约束与可行域检查]
    C --> D[机制模型 + 符号回归]
    D --> E[统计证据门控]
    E --> F[Bootstrap 稳定性]
    F --> G{开放集判断}
    G -->|证据充分| H[候选机制排序]
    G -->|证据冲突| I[拒答与补证建议]
    H --> J[受约束的下一实验设计]
    I --> J
    J --> K[Markdown / DOCX 报告]
    J --> L[(PostgreSQL 审计记录)]
```

### 运行时分层

```text
┌──────────────────────────────────────────────────────────────┐
│  展示层：HTML + CSS + JavaScript 科研工作台                  │
├──────────────────────────────────────────────────────────────┤
│  接口层：本地 ThreadingHTTPServer / 生产 FastAPI              │
├──────────────────────────────────────────────────────────────┤
│  决策层：科学约束 → 统计证据 → 稳定性 → 开放集 → 实验设计    │
├──────────────────────────────────────────────────────────────┤
│  模型层：机制模型、符号回归、异常归因、可行域投影            │
├──────────────────────────────────────────────────────────────┤
│  基础设施：PostgreSQL、DOCX、可选 DeepSeek、Vercel            │
└──────────────────────────────────────────────────────────────┘
```

## 技术栈

### 后端与接口

| 技术 | 版本/要求 | 用途 |
| --- | --- | --- |
| Python | `>= 3.10`，推荐 `3.12` | 分析引擎、接口服务与报告生成 |
| FastAPI | `>= 0.115` | 生产 API、静态资源服务与 OpenAPI 文档 |
| Uvicorn | `>= 0.30` | ASGI 服务运行时 |
| Python 标准库 HTTP Server | Python 内置 | 零额外 Web 框架的本地演示入口 |

### 科学计算与决策

| 技术/方法 | 用途 |
| --- | --- |
| Python `math` / `statistics` | 轻量数值计算、拟合评分和统计摘要 |
| 机制模型 | 幂律、Arrhenius、Langmuir-Hinshelwood 候选机制 |
| 显式符号回归 | 安全受限的基函数组合、公式搜索与重拟合 |
| AICc / BIC / 交叉验证 | 小样本模型比较与泛化证据 |
| Bootstrap | 参数可识别性、排名稳定性与预测区间检查 |
| 开放集 `H_other` | 候选库不完备时的拒答与风险控制 |
| 受约束信息增益 | 下一实验点推荐 |

### 数据、报告与集成

| 技术 | 版本/要求 | 用途 |
| --- | --- | --- |
| PostgreSQL | 生产环境必需 | 对话、分析结果和报告审计记录 |
| Psycopg 3 | `>= 3.2` | PostgreSQL 驱动与 JSONB 数据访问 |
| pg0-embedded | `>= 0.15.1` | 本地演示的内嵌 PostgreSQL |
| python-docx | `>= 1.1` | DOCX 科研报告生成 |
| DeepSeek API | 可选 | 对话解释与结果表达增强 |
| Vercel Python Runtime | 配置见 `vercel.json` | Serverless 部署入口 |

### 前端与工程质量

| 技术 | 用途 |
| --- | --- |
| HTML5 / CSS3 / Vanilla JavaScript | 无构建步骤的轻量科研工作台 |
| `unittest` | 核心分析与接口自动化测试 |
| `pyproject.toml` / Setuptools | Python 包元数据、依赖和可编辑安装 |
| Git | 源码版本管理与审计 |

> [!NOTE]
> 当前核心数值流程刻意保持轻量，不依赖 NumPy、SciPy 或 scikit-learn。若未来引入大规模矩阵计算或更复杂优化器，应同步补充基准测试、数值容差和依赖治理策略。

## 代码结构

```text
SCIG/
├─ main.py                  # 推荐的本地启动入口
├─ app.py                   # 本地 HTTP 服务与内嵌数据库运行时
├─ backend/
│  └─ server.py             # FastAPI / Vercel 生产入口
├─ zhi_engine/
│  ├─ analysis.py           # 数据标准化、物理约束、模型拟合与总流程
│  ├─ decision.py           # 证据门控、Bootstrap、开放集与实验设计
│  ├─ symbolic.py           # 安全公式求值、符号搜索与基函数重拟合
│  ├─ reporting.py          # Markdown / DOCX 报告生成
│  ├─ store.py              # PostgreSQL 数据访问与记录持久化
│  └─ deepseek_client.py    # 可选 DeepSeek 对话适配器
├─ static/
│  ├─ index.html            # 工作台页面
│  ├─ styles.css            # 视觉样式
│  ├─ app.js                # 前端交互
│  └─ assets/               # 标志与静态资源
├─ tests/                   # 自动化测试
├─ .env.production.example  # 生产环境变量模板
├─ pyproject.toml           # 包与依赖配置
└─ vercel.json              # Vercel 路由与运行时配置
```

## 快速开始

### 1. 环境要求

- Python 3.10 或更高版本；推荐 Python 3.12；
- Windows PowerShell、macOS 或 Linux 终端；
- 首次安装依赖时可访问 Python 包索引；
- 生产部署需提供外部 PostgreSQL。

确认 Python 版本：

```shell
python --version
```

### 2. 创建并激活虚拟环境

```shell
python -m venv .venv
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

安装项目及其依赖：

```shell
python -m pip install --upgrade pip
python -m pip install -e .
```

### 3. 启动本地工作台

```shell
python main.py
```

浏览器访问：<http://127.0.0.1:8000>

本地入口默认允许使用内嵌 PostgreSQL，并在未配置 DeepSeek 时使用本地规则代理，适合演示和开发验证。

### 4. 运行测试

```shell
python -m unittest discover -s tests -v
```

预期结果：11 项测试全部通过。测试覆盖：

- 单位归一化、原始值保留与追溯字段；
- 三道证据门控、Bootstrap 稳定性和开放集拒答；
- 异常后验归因与受约束实验推荐；
- 审计摘要与结果可重复性；
- 安全公式求值与符号基函数重拟合；
- 本地 HTTP、FastAPI 能力一致性；
- Markdown 与 DOCX 报告生成。

## 数据契约

### 最小要求

- 至少 4 行有效数据；
- 每行必须包含温度和转化率；
- 温度必须是有限数值；
- 转化率建议位于 `[0, 1]`，越界值会进入科学约束诊断；
- 支持行对象、行数组或 CSV 文本。

### 推荐字段

| 标准字段 | 必需 | 支持别名示例 | 说明 |
| --- | :---: | --- | --- |
| `temperature` | 是 | `temp`、`t`、`x`、`温度` | 温度自变量 |
| `conversion` | 是 | `response`、`yield`、`y`、`转化率` | 转化率/响应值 |
| `temperature_unit` | 否 | `temp_unit`、`温度单位` | `C`、`K` 或 `F` |
| `conversion_unit` | 否 | `response_unit`、`转化率单位` | `fraction` 或 `percent` |
| `uncertainty` | 否 | `error`、`sigma`、`测量误差` | 测量不确定度 |
| `batch` | 否 | `批次` | 批次标识 |
| `sample_id` | 否 | `source_id`、`样本号` | 样本追溯标识 |
| `instrument` | 否 | `device`、`仪器` | 仪器信息 |
| `operator` | 否 | `researcher`、`操作员` | 操作人员 |
| `replicate` | 否 | `repeat`、`重复编号` | 重复实验编号 |
| `recorded_at` | 否 | `timestamp`、`记录时间` | 采集时间 |
| `source` | 否 | `data_source`、`数据来源` | 数据来源 |

系统内部统一使用摄氏度和 `[0, 1]` 比例值，同时在 `raw` 字段保留原始输入。

## API 参考

### 接口总览

| 方法 | 路径 | 用途 | 持久化要求 |
| --- | --- | --- | --- |
| `GET` | `/api/bootstrap` | 获取工作台能力、演示结果与配置警告 | 无数据库时可降级返回 |
| `POST` | `/api/analyze` | 执行完整科学判断流程 | 需要可用数据库运行时 |
| `POST` | `/api/chat` | 生成本地规则或 DeepSeek 对话回复 | 需要数据库 |
| `POST` | `/api/report` | 生成 Markdown 或 DOCX 报告 | 需要数据库 |
| `GET` | `/api/conversations/{id}` | 读取对话与分析记录 | 需要数据库 |

FastAPI 部署还提供自动接口文档：`/docs` 和 `/redoc`。

### 分析请求示例

`POST /api/analyze`

```json
{
  "dataset": {
    "data": [
      {
        "sample_id": "S-001",
        "temperature": 313.15,
        "temperature_unit": "K",
        "conversion": 16.0,
        "conversion_unit": "percent",
        "uncertainty": 1.2,
        "batch": "B07",
        "instrument": "reactor-2",
        "operator": "researcher-a",
        "replicate": 1,
        "source": "lab-notebook-2026-09"
      },
      {
        "sample_id": "S-002",
        "temperature": 323.15,
        "temperature_unit": "K",
        "conversion": 22.5,
        "conversion_unit": "percent",
        "batch": "B07"
      },
      {
        "sample_id": "S-003",
        "temperature": 333.15,
        "temperature_unit": "K",
        "conversion": 31.0,
        "conversion_unit": "percent",
        "batch": "B08"
      },
      {
        "sample_id": "S-004",
        "temperature": 343.15,
        "temperature_unit": "K",
        "conversion": 40.2,
        "conversion_unit": "percent",
        "batch": "B08"
      }
    ],
    "config": {
      "bootstrap_samples": 36,
      "cv_folds": 4,
      "random_seed": 2026,
      "experiment_constraints": {
        "temperature_min": 40,
        "temperature_max": 180,
        "safety_max_temperature": 160,
        "budget": 3.0,
        "cost_weight": 0.08
      }
    }
  },
  "conversationId": "optional-conversation-id"
}
```

> [!CAUTION]
> API 顶层字段是 `dataset`。`data` 和 `config` 位于 `dataset` 内部；若省略 `dataset`，服务会使用内置演示数据。

### 主要分析配置

| 配置 | 默认值 | 约束/说明 |
| --- | ---: | --- |
| `temperature_unit` | `C` | 全局温度单位：`C`、`K` 或 `F` |
| `conversion_unit` | `fraction` | `fraction` 或 `percent` |
| `cv_folds` | `4` | 小样本时自动下调 |
| `bootstrap_samples` | `36` | 自动限制在 12–200 |
| `random_seed` | `2026` | 控制稳定性分析的可重复性 |
| `lambda` | `10` | 科学约束在初始排序中的权重 |
| `solver` | `L-BFGS-B` | 记录于审计信息的求解器标识 |
| `max_iter` | `5000` | 最大迭代配置 |
| `symbolic_max_depth` | `2` | 符号表达式搜索深度 |
| `symbolic_basis_limit` | `16` | 符号基函数数量上限 |
| `symbolic_max_terms` | `3` | 单个符号公式的项数上限 |
| `experiment_constraints` | — | 温度、安全、预算和成本权重约束 |

### 关键响应对象

| 字段 | 说明 |
| --- | --- |
| `summary` | 样本量、区间、异常数、置信度和拒答状态摘要 |
| `recommended_model` | 当前证据下排名最高的候选模型 |
| `physics_constraints` | 科学约束、可行域与模型违规详情 |
| `evidence_gates` | 科学、统计和稳定性三道门控 |
| `hypothesis_ranking` | 候选机制的证据排序 |
| `anomaly_attribution` | 异常原因后验与建议复核动作 |
| `open_set_decision` | `H_other` 概率、后验熵和拒答判断 |
| `experiment_design` | 满足约束的下一实验候选 |
| `data_contract` | 标准单位、原始值与追溯约定 |
| `audit` | 数据哈希、引擎版本、随机种子与决策链 |

### 报告请求示例

```json
{
  "dataset": {
    "data": [
      [40, 0.16, "B01"],
      [60, 0.25, "B01"],
      [80, 0.36, "B02"],
      [100, 0.47, "B02"]
    ]
  },
  "title": "催化反应小样本分析报告",
  "format": "docx",
  "conversationId": "optional-conversation-id"
}
```

`format` 支持 `docx` 和 `markdown`。也可以直接传入已有的 `result`，避免重复分析。

## 生产部署

### 部署模式矩阵

| 模式 | 启动入口 | 数据库 | DeepSeek | 适用场景 |
| --- | --- | --- | --- | --- |
| 本地演示 | `python main.py` | 默认内嵌 PostgreSQL | 可选，本地规则可兜底 | 开发、演示、离线验证 |
| 自托管 ASGI | `uvicorn backend.server:app` | 外部 PostgreSQL | 生产默认必需 | 内网服务、容器平台 |
| Vercel | `backend.server:app` | 外部 PostgreSQL | 生产默认必需 | Serverless Web 部署 |

### 环境变量

生产配置模板见 [`.env.production.example`](.env.production.example)。密钥和数据库连接只能放在服务端环境变量或部署平台密钥管理中，禁止写入前端和仓库。

| 变量 | 生产要求 | 默认值 | 说明 |
| --- | :---: | --- | --- |
| `ZHIGOU_ENV` | 是 | 本地为 `development` | 生产使用 `production` |
| `ZHIGOU_HOST` | 否 | `127.0.0.1` | 本地 HTTP 监听地址 |
| `ZHIGOU_PORT` | 否 | `8000` | 本地 HTTP 监听端口 |
| `ZHIGOU_DATABASE_URL` | 是 | 空 | PostgreSQL DSN，建议启用 SSL |
| `ZHIGOU_REQUIRE_EXTERNAL_DB` | 是 | 生产为 `true` | 禁止生产环境回退到内嵌数据库 |
| `ZHIGOU_ALLOW_EMBEDDED_DB` | 是 | 生产为 `false` | 是否允许内嵌 PostgreSQL |
| `DEEPSEEK_API_KEY` | 条件必需 | 空 | DeepSeek 服务端密钥 |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | DeepSeek API 地址 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-pro` | 模型名称 |
| `DEEPSEEK_TIMEOUT` | 否 | `45` | 请求超时秒数 |
| `DEEPSEEK_MAX_TOKENS` | 否 | `1200` | 限制在 256–8192 |
| `DEEPSEEK_TEMPERATURE` | 否 | `0.35` | 限制在 0–2 |
| `ZHIGOU_REQUIRE_DEEPSEEK` | 是 | 生产为 `true` | 是否在生产环境强制要求大模型 |

PowerShell 启动示例：

```powershell
$env:ZHIGOU_ENV="production"
$env:ZHIGOU_HOST="0.0.0.0"
$env:ZHIGOU_PORT="8000"
$env:ZHIGOU_DATABASE_URL="postgresql://user:password@host:5432/zhigou?sslmode=require"
$env:ZHIGOU_REQUIRE_EXTERNAL_DB="true"
$env:ZHIGOU_ALLOW_EMBEDDED_DB="false"
$env:DEEPSEEK_API_KEY="your-secret"
python main.py
```

### 上线检查清单

- [ ] 使用受支持的 Python 版本并锁定部署依赖；
- [ ] PostgreSQL 使用独立账号、最小权限、TLS 和备份策略；
- [ ] 所有密钥由密钥管理服务注入，仓库和前端中无明文；
- [ ] `ZHIGOU_REQUIRE_EXTERNAL_DB=true` 且 `ZHIGOU_ALLOW_EMBEDDED_DB=false`；
- [ ] 运行完整自动化测试并保存测试记录；
- [ ] 使用真实领域数据完成模型校准、误差评估和专家验收；
- [ ] 在反向代理或平台层配置 TLS、认证、授权、限流和请求体限制；
- [ ] 为数据库、API 错误率、延迟和容量建立监控与告警；
- [ ] 制定数据保留、脱敏、删除和灾难恢复策略；
- [ ] 高风险实验建议必须经过人工审批，不得直接驱动设备。

## 质量保证

### 测试策略

| 层级 | 当前覆盖 |
| --- | --- |
| 单元测试 | 单位转换、公式安全、符号重拟合、开放集与审计 |
| 决策链测试 | 三道门控、异常归因、主动实验设计、可重复性 |
| 接口冒烟测试 | 本地 HTTP 的 Bootstrap 与 Analyze；FastAPI 能力一致性 |
| 文档生成测试 | Markdown 章节与 DOCX 二进制生成 |

每次变更至少执行：

```shell
python -m unittest discover -s tests -v
```

### 可重复性与审计

- 输入数据在标准化后生成摘要哈希；
- 原始值、单位、来源与标准化结果同时保留；
- Bootstrap 随机种子写入分析配置和审计信息；
- 候选模型、门控状态、拒答原因和实验建议保留完整决策链；
- 报告生成动作可记录到 PostgreSQL。

当前尚未内置分布式追踪、指标采集或集中日志后端。生产部署应在平台层补充 OpenTelemetry/指标、结构化日志、告警与审计归档。

## 安全与责任边界

### 已实现的安全原则

1. 原始实验值只读保留，异常仅标记，不自动删除；
2. 公式求值采用受限语法树，不允许任意 Python 执行；
3. 所有归一化、模型版本、随机种子和判断链进入审计信息；
4. 高不确定性、模型冲突或候选库不足时允许拒答；
5. 生产模式禁止默认使用内嵌数据库；
6. DeepSeek 密钥仅由后端环境变量读取；
7. 静态文件访问进行路径边界检查。

### 使用方必须补充的控制

- 身份认证、角色权限、多租户隔离和速率限制；
- 敏感科研数据分级、脱敏、传输加密与访问审计；
- 数据库备份、密钥轮换、依赖漏洞扫描与事件响应；
- 领域基准集、模型漂移监控和定期再校准；
- 实验安全审查、人工审批和设备侧硬联锁。

## 已知限制与演进方向

- 异常原因后验和 `H_other` 当前是可解释的启发式概率，不能视为经领域校准的真实因果概率；
- 主动实验设计目前以单一主变量“温度”为核心，多变量、离散工艺条件和批量选点仍需扩展；
- 约束 DSL 已有内部规则结构，但尚未提供完整用户语法、版本化规则仓库和迁移机制；
- 知识图谱当前用于结果组织，尚未接入经过审校的文献证据库和引用系统；
- 尚未内置用户认证、多租户、任务队列、分布式追踪、限流和模型插件注册；
- 当前自动化测试聚焦功能正确性，仍需增加负载、并发、故障注入、数据库迁移和端到端浏览器测试；
- 在任何真实设备或高风险实验环境中使用前，必须完成领域专家验收与独立安全评估。

## 常见问题

<details>
<summary><strong>为什么启动 FastAPI 后分析接口提示数据库未配置？</strong></summary>

`backend.server:app` 按生产入口设计，分析、对话和报告接口需要 `ZHIGOU_DATABASE_URL`。仅做本地体验时，请使用 `python main.py` 启动内嵌数据库模式。
</details>

<details>
<summary><strong>为什么请求返回的是演示数据结果？</strong></summary>

请确认请求体使用顶层 `dataset` 字段，并把 `data` 与 `config` 放在其中。省略 `dataset` 时，服务会主动使用内置演示数据。
</details>

<details>
<summary><strong>DeepSeek 不可用时还能分析吗？</strong></summary>

核心科学判断不依赖 DeepSeek。本地模式会使用规则代理；生产模式默认要求配置 DeepSeek，可通过显式设置 `ZHIGOU_REQUIRE_DEEPSEEK=false` 允许本地兜底，但应评估服务体验和合规要求。
</details>

<details>
<summary><strong>系统会自动删除异常值吗？</strong></summary>

不会。异常点保留在原始数据与分析结果中，系统只提供严重度、可能原因和复核建议。
</details>

## 参与开发

提交改动前建议遵循以下流程：

1. 基于最新代码创建独立分支；
2. 保持分析逻辑可重复，并为新增行为补充测试；
3. 不提交数据库、密钥、虚拟环境、生成报告或个人实验数据；
4. 运行完整测试并检查文档、配置和接口示例是否同步；
5. 在提交说明中写明变更目的、风险和验证结果。

版本号遵循语义化版本思路：破坏性接口变更提升主版本，兼容性功能提升次版本，兼容性修复提升补丁版本。

## 许可证

当前仓库未包含独立的 `LICENSE` 文件。使用、分发或二次开发前，请先取得仓库所有者的明确授权。

---

<div align="center">
  <strong>让模型给出答案之前，先让证据经得起追问。</strong>
</div>
