# 知构引擎科研分析与决策辅助平台

这是一个可本地运行并支持上线部署的 Python Web 项目，定位来自项目材料中的共识：

> 基于知识图谱与物理约束算法的科研数据分析辅助工具平台。

## 当前版本

- 对话式科研分析工作台
- 三大核心技术：物理约束校验层、智能公式发现层、假设性排行层
- 物理约束模型筛选：幂律、Arrhenius 温度依赖、饱和吸附近似
- 参数拟合与拟合曲线展示
- 异常点检测与批次线索提示
- 下一步实验建议
- PostgreSQL 对话历史、分析运行和报告记录持久化
- 智能对话服务接入，未配置密钥时自动回退到本地规则代理
- DOCX 报告生成与下载
- 小型订阅升级入口与本地演示状态
- 知构引擎 Logo 展示

## 本地开发启动

```powershell
pip install -r requirements.txt
python app.py
```

打开 `http://127.0.0.1:8000`。

开发模式默认使用内置演示数据和内置 PostgreSQL，方便本机快速演示。后续接入真实数据时，调用 `/api/analyze`，传入字段为 `temperature` 和 `conversion`，可选 `batch`。

## 上线部署配置

正式上线不要使用内置本地数据库。设置 `ZHIGOU_ENV=production` 后，应用会强制要求外部 PostgreSQL 和后端对话服务密钥，避免把演示版配置误部署到生产环境。

```powershell
$env:ZHIGOU_ENV="production"
$env:ZHIGOU_HOST="0.0.0.0"
$env:ZHIGOU_PORT="8000"
$env:ZHIGOU_DATABASE_URL="postgresql://user:password@db-host:5432/zhigou_engine?sslmode=require"
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
python app.py
```

部署平台上建议把 `ZHIGOU_DATABASE_URL` 和 `DEEPSEEK_API_KEY` 放到 Secrets / Environment Variables，不要写进前端代码、仓库或打包后的静态文件。配置模板见 `.env.production.example`。

## Vercel 部署

项目已经提供 Vercel 需要的 FastAPI 入口：`backend/server.py` 中的 `app`。`pyproject.toml` 里通过下面配置指向该入口：

```toml
[tool.vercel]
entrypoint = "backend.server:app"
```

部署步骤：

1. 将仓库导入 Vercel。
2. 在项目环境变量中配置 `ZHIGOU_ENV=production`。
3. 配置外部 PostgreSQL 连接串：`ZHIGOU_DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require`。
4. 配置后端对话服务密钥：`DEEPSEEK_API_KEY=你的密钥`。
5. 保持 `ZHIGOU_REQUIRE_EXTERNAL_DB=true` 和 `ZHIGOU_ALLOW_EMBEDDED_DB=false`。

线上环境推荐使用 Neon、Supabase、Aiven、Render PostgreSQL 或云厂商托管 PostgreSQL。Vercel 的 Serverless Runtime 不适合启动本地数据库进程，因此生产环境必须连接外部数据库。

本地也可以用 ASGI 方式验证入口：

```powershell
pip install -r requirements.txt
uvicorn backend.server:app --reload --port 8001
```

如果本地没有外部 PostgreSQL，可以继续使用 `python app.py` 进行演示运行。

## 对话服务配置

配置密钥后，`/api/chat` 会先执行本地物理约束、模型拟合和假设排行，再把精简分析上下文交给后端对话服务生成真实对话回复。

```powershell
$env:DEEPSEEK_API_KEY="你的 DeepSeek API Key"
$env:DEEPSEEK_MODEL="deepseek-v4-pro"
$env:DEEPSEEK_THINKING="disabled"
python app.py
```

可选环境变量：

- `DEEPSEEK_BASE_URL`：默认 `https://api.deepseek.com`
- `DEEPSEEK_MODEL`：默认 `deepseek-v4-pro`
- `DEEPSEEK_THINKING`：`enabled` 或 `disabled`
- `DEEPSEEK_REASONING_EFFORT`：`low`、`high` 或 `max`
- `DEEPSEEK_MAX_TOKENS`：默认 `1200`
- `DEEPSEEK_TEMPERATURE`：默认 `0.35`
