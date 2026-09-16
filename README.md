# 知构引擎（SCIG）

知构引擎是面向小样本科研数据的科学判断与下一实验决策工具。它不把“拟合最好”等同于“规律成立”，而是依次检查科学约束、统计证据与可识别性/稳定性，再给出异常复核、开放集拒答和下一实验建议。

> 本项目用于科研决策辅助，不替代研究人员、实验负责人或安全负责人的最终判断，也不直接控制实验设备。

## 当前能力

- CSV、JSON和表格行数据导入，支持摄氏度、开尔文、华氏度以及比例/百分比归一化。
- 保留原始值、样本编号、批次、仪器、操作员、重复编号和数据来源等追溯信息。
- 边界、单调性、可行域投影、残差和批次线索诊断；异常值只标记，不静默删除。
- 幂律、Arrhenius、Langmuir-Hinshelwood及显式符号回归候选模型。
- 三道证据门控：科学约束；AICc、BIC及交叉验证；Bootstrap可识别性与预测稳定性。
- 对录入问题、测量误差、模型失配和候选机制变化进行后验归因；证据冲突时允许拒答。
- 保留 `H_other` 开放集假设，避免候选库不完整时强行形成唯一结论。
- 在温度、安全边界、预算和成本权重约束下，以信息增益推荐下一实验点。
- 生成Markdown和DOCX分析报告，并记录数据摘要、随机种子、引擎版本与决策链。
- 本地规则代理、可选DeepSeek对话适配、PostgreSQL记录以及静态科研工作台。

## 代码结构

```text
SCIG/
├─ main.py                  # 本地启动入口
├─ app.py                   # 本地HTTP服务
├─ backend/server.py        # FastAPI/Vercel入口
├─ zhi_engine/
│  ├─ analysis.py           # 数据标准化、物理约束、模型拟合与总流程
│  ├─ decision.py           # 三道门控、Bootstrap、异常归因、开放集和实验设计
│  ├─ symbolic.py           # 安全公式计算与符号回归
│  ├─ reporting.py          # Markdown/DOCX报告
│  ├─ store.py              # PostgreSQL对话、分析和报告记录
│  └─ deepseek_client.py    # 可选对话服务适配
├─ static/                  # 网页工作台
└─ tests/                   # 本地自动化测试
```

## 环境要求

- Python 3.10或更高版本；推荐Python 3.12。
- 本地演示可使用内嵌PostgreSQL；生产环境必须使用外部PostgreSQL。

当前代码使用了现代类型标注，不支持旧的Python 3.7环境。请先确认：

```powershell
python --version
```

## 本地安装与启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python main.py
```

打开 `http://127.0.0.1:8000`。

也可以使用FastAPI入口：

```powershell
uvicorn backend.server:app --reload --port 8001
```

FastAPI入口默认按生产方式检查数据库配置；本地无外部数据库时，优先使用 `python main.py`。

## 运行测试

测试使用Python标准库 `unittest`，不需要额外测试框架：

```powershell
python -m unittest discover -s tests -v
```

当前测试覆盖单位归一化、原始值保留、三道模型门控、Bootstrap稳定性、异常后验归因、开放集 `H_other`、受约束实验推荐、审计摘要、安全公式求值以及Markdown/DOCX报告。

## 数据输入

最简输入字段为 `temperature` 和 `conversion`，可选 `batch`。推荐同时提供误差和追溯字段：

```json
{
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
}
```

主要配置如下：

| 配置 | 默认值 | 说明 |
| --- | ---: | --- |
| `temperature_unit` | `C` | 全局温度单位：`C`、`K`或`F` |
| `conversion_unit` | `fraction` | `fraction`或`percent` |
| `cv_folds` | `4` | 交叉验证折数，小样本时自动下调 |
| `bootstrap_samples` | `36` | Bootstrap次数，限制为12–200 |
| `random_seed` | `2026` | 确保稳定性分析可重复 |
| `lambda` | `10` | 物理约束在初始排行中的权重 |
| `symbolic_max_depth` | `2` | 符号表达式搜索深度 |
| `symbolic_basis_limit` | `16` | 符号基函数数量上限 |
| `symbolic_max_terms` | `3` | 单个符号公式的项数上限 |
| `experiment_constraints` | 见上例 | 实验温度、安全、预算与成本约束 |

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/bootstrap` | 获取工作台初始化信息 |
| `POST` | `/api/analyze` | 执行完整科学判断流程 |
| `POST` | `/api/chat` | 分析后生成本地或外部对话回复 |
| `POST` | `/api/report` | 下载Markdown或DOCX报告 |
| `GET` | `/api/conversations/{id}` | 读取对话与分析记录 |

`/api/analyze` 的关键输出包括：

- `data_contract`：归一化单位和追溯约定；
- `physics_constraints`：数据与模型的科学约束检查；
- `evidence_gates`：科学、统计、稳定性三道门控；
- `anomaly_attribution`：异常原因后验与复核动作；
- `open_set_decision`：`H_other`、后验熵和拒答状态；
- `experiment_design`：满足约束的下一实验候选；
- `audit`：数据哈希、版本、随机种子与决策链。

## 生产环境

生产部署必须把密钥和数据库连接放入环境变量，不要写进前端或仓库：

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

更多变量见 `.env.production.example`。Vercel入口为 `backend.server:app`，Serverless环境必须连接外部PostgreSQL。

## 当前边界与后续工作

本轮已经补齐核心科学判断链和自动化测试，但以下能力仍需结合真实科研数据继续建设：

- 异常原因后验与 `H_other` 是可解释的启发式概率，不应当作经领域校准后的真实因果概率。
- 主动实验设计目前针对单一主变量温度；多变量、离散工艺条件和批量选点需要继续扩展。
- 约束DSL已有内部规则结构，但尚未提供面向用户的完整解析语言和版本化规则仓库。
- 知识图谱目前主要用于结果组织，尚未接入经过审校的文献知识库和证据引用系统。
- 尚需增加多租户权限、科研数据脱敏、超算任务队列、模型插件注册和更多领域模板。
- 进入真实设备或高风险实验流程前，必须增加领域专家验收、校准数据集和安全联锁接口。

## 安全原则

1. 原始实验值只读保留，异常仅标记，不自动删除。
2. 所有归一化、模型版本、随机种子和判断链均进入审计信息。
3. 高不确定性、模型冲突或候选库不足时允许拒答。
4. 下一实验推荐必须经过科研人员或安全负责人确认。
