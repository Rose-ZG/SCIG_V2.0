const THEME_ORDER = ["light", "dark", "blue"];
const THEME_NAMES = {
  light: "白蓝主题",
  dark: "暗色主题",
  blue: "深蓝主题",
};
const DEFAULT_PLAN = "专业版";

const demoData = `temperature,conversion,batch
40,0.16,A01
50,0.205,A01
60,0.251,A01
70,0.305,A02
80,0.356,A02
90,0.411,A02
100,0.465,A03
110,0.517,A03
120,0.566,A03
130,0.611,A04
140,0.655,A04
150,0.694,A04`;

const state = {
  bootstrap: null,
  result: null,
  conversationId: null,
  conversation: null,
  datasetText: "",
  plan: localStorage.getItem("zhigou-plan") || DEFAULT_PLAN,
  theme: localStorage.getItem("zhigou-theme") || "light",
  subscriptionExpanded: false,
  inspectorOpen: true,
  allConversations: [],
  loading: false,
};

const el = {};

document.addEventListener("DOMContentLoaded", async () => {
  bindElements();
  bindEvents();
  renderIcons();
  setTheme(state.theme, false);
  setInspectorTab("analysis");
  if (window.innerWidth <= 1180) setInspector(false);
  setDatasetText(demoData, "当前使用示例数据");
  await bootstrap();
});

function bindElements() {
  [
    "historyList",
    "historySearch",
    "messages",
    "datasetInput",
    "chart",
    "modelGrid",
    "anomalyList",
    "reportPreview",
    "physicsSummary",
    "physicsViolationList",
    "subscriptionCards",
    "subscriptionDock",
    "toast",
    "planBadge",
    "subscriptionToggleText",
    "themeToggleText",
    "llmStatusText",
    "llmStatusBtn",
    "chatInput",
    "sendBtn",
    "downloadReportBtn",
    "loadDemoBtn",
    "runAnalysisBtn",
    "clearDataBtn",
    "importFileBtn",
    "fileInput",
    "newChatBtn",
    "quickActions",
    "analysisStatus",
    "datasetHint",
    "bestModel",
    "confidenceValue",
    "anomalyValue",
    "themeToggleBtn",
    "subscriptionToggleBtn",
    "subscriptionExpandBtn",
    "reportModal",
    "reportModalBody",
    "reportModalDownloadBtn",
    "reportPanelDownloadBtn",
    "closeReportModalBtn",
    "composerForm",
    "reportPreviewBtn",
    "inspectorPanel",
    "inspectorToggleBtn",
    "inspectorTabs",
    "mobileSidebarBtn",
    "closeInspectorBtn",
  ].forEach((id) => {
    el[id] = document.getElementById(id);
  });
}

function bindEvents() {
  on(el.composerForm, "submit", (event) => {
    event.preventDefault();
    onSendMessage();
  });
  on(el.sendBtn, "click", (event) => {
    event.preventDefault();
    onSendMessage();
  });
  on(el.downloadReportBtn, "click", onDownloadReport);
  on(el.reportPreviewBtn, "click", () => {
    if (state.result) {
      setInspectorTab("report", true);
      openReportModal(state.result);
    }
    else showToast("先完成一次分析，再预览报告");
  });
  on(el.reportModalDownloadBtn, "click", onDownloadReport);
  on(el.reportPanelDownloadBtn, "click", onDownloadReport);
  on(el.loadDemoBtn, "click", async () => {
    setDatasetText(demoData, "已加载示例数据");
    await analyzeCurrentDataset(false);
  });
  on(el.runAnalysisBtn, "click", () => analyzeCurrentDataset(false));
  on(el.clearDataBtn, "click", () => {
    setDatasetText("", "暂无数据");
    showToast("已清空输入数据");
  });
  on(el.importFileBtn, "click", () => el.fileInput?.click());
  on(el.fileInput, "change", onImportFile);
  on(el.newChatBtn, "click", newConversation);
  on(el.historySearch, "input", () => filterHistory(el.historySearch.value));
  on(el.datasetInput, "input", () => {
    state.datasetText = el.datasetInput.value;
    setDatasetHint("数据已修改，等待重新分析");
  });
  on(el.chatInput, "input", autosizeComposer);
  on(el.chatInput, "keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSendMessage();
    }
  });
  on(el.quickActions, "click", (event) => {
    const btn = event.target.closest("button");
    if (!btn) return;
    el.chatInput.value = btn.textContent.trim();
    autosizeComposer();
    onSendMessage();
  });
  on(el.themeToggleBtn, "click", cycleTheme);
  on(el.subscriptionToggleBtn, "click", cyclePlan);
  on(el.subscriptionExpandBtn, "click", toggleSubscriptionPanel);
  on(el.inspectorTabs, "click", (event) => {
    const tabButton = event.target.closest("[data-inspector-tab]");
    if (!tabButton) return;
    setInspectorTab(tabButton.dataset.inspectorTab, true);
  });
  on(el.inspectorToggleBtn, "click", toggleInspector);
  on(el.closeInspectorBtn, "click", () => setInspector(false));
  on(el.mobileSidebarBtn, "click", () => document.body.classList.toggle("nav-open"));
  on(el.closeReportModalBtn, "click", closeReportModal);
  on(el.reportModal, "click", (event) => {
    if (event.target === el.reportModal) closeReportModal();
  });
}

function on(node, event, handler) {
  if (node) node.addEventListener(event, handler);
}

async function bootstrap() {
  try {
    updateStatus("正在连接引擎");
    const response = await fetch("/api/bootstrap");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "初始化失败");

    state.bootstrap = data;
    state.allConversations = data.conversations || [];
    normalizePlanFromBootstrap(data.subscription_plans || []);
    renderHistory(state.allConversations);
    renderSubscriptionDock(data.subscription_plans || []);
    renderSubscriptionCards(data.subscription_plans || []);
    renderPlanSummary();
    renderLlmStatus(data.llm_provider);

    renderWelcome();
    await analyzeCurrentDataset(true);
    updateStatus(`已连接 · ${databaseModeText(data.database_mode, data.deployment_mode)}`);
  } catch (error) {
    renderWelcome();
    updateStatus("离线预览");
    showToast(`初始化失败：${error.message}`);
  }
}

function normalizePlanFromBootstrap(plans) {
  if (!plans.length) {
    state.plan = DEFAULT_PLAN;
    return;
  }
  if (!plans.some((plan) => plan.name === state.plan)) {
    const pro = plans.find((plan) => plan.key === "pro" || plan.name.includes("专业"));
    state.plan = pro?.name || plans[0].name;
  }
  localStorage.setItem("zhigou-plan", state.plan);
}

function databaseModeText(mode, deploymentMode = "development") {
  const isProd = ["prod", "production"].includes(deploymentMode);
  if (mode === "embedded") return isProd ? "生产配置异常：内置 PostgreSQL" : "开发内置 PostgreSQL";
  if (mode === "external") return isProd ? "生产外部 PostgreSQL" : "外部 PostgreSQL";
  return "本地模式";
}

function pendingAssistantText() {
  return "正在分析，并同步执行物理约束校验、模型拟合和报告节点...";
}

function renderLlmStatus(provider) {
  if (!el.llmStatusText || !el.llmStatusBtn) return;
  const hasError = provider?.mode === "error";
  el.llmStatusBtn.classList.remove("connected", "warning");
  el.llmStatusText.textContent = "智能对话";
  el.llmStatusBtn.title = hasError ? "对话分析已切换为基础模式" : "对话分析已就绪";
}

function setTheme(theme, persist = true) {
  const next = THEME_ORDER.includes(theme) ? theme : "light";
  state.theme = next;
  document.documentElement.dataset.theme = next;
  if (persist) localStorage.setItem("zhigou-theme", next);

  const currentIndex = THEME_ORDER.indexOf(next);
  const nextTheme = THEME_ORDER[(currentIndex + 1) % THEME_ORDER.length];
  if (el.themeToggleText) el.themeToggleText.textContent = THEME_NAMES[next];
  if (el.themeToggleBtn) el.themeToggleBtn.title = `切换到${THEME_NAMES[nextTheme]}`;
}

function cycleTheme() {
  const index = THEME_ORDER.indexOf(state.theme);
  const next = THEME_ORDER[(index + 1) % THEME_ORDER.length];
  setTheme(next);
  showToast(`已切换到${THEME_NAMES[next]}`);
}

function setPlan(plan, persist = true) {
  const plans = state.bootstrap?.subscription_plans || [];
  const next = plans.some((item) => item.name === plan) ? plan : plans[0]?.name || DEFAULT_PLAN;
  state.plan = next;
  if (persist) localStorage.setItem("zhigou-plan", next);
  renderSubscription();
  showToast(`已切换到 ${next}`);
}

function cyclePlan() {
  const plans = state.bootstrap?.subscription_plans || [];
  if (!plans.length) return;
  const names = plans.map((plan) => plan.name);
  const index = names.indexOf(state.plan);
  setPlan(names[(index + 1) % names.length]);
}

function toggleSubscriptionPanel() {
  state.subscriptionExpanded = !state.subscriptionExpanded;
  if (el.subscriptionCards) {
    el.subscriptionCards.classList.toggle("hidden", !state.subscriptionExpanded);
  }
  if (el.subscriptionExpandBtn) {
    el.subscriptionExpandBtn.title = state.subscriptionExpanded ? "收起订阅方案" : "展开订阅方案";
  }
}

function setInspector(open) {
  state.inspectorOpen = open;
  document.body.classList.toggle("inspector-closed", !open);
  if (window.innerWidth <= 1180) {
    document.body.classList.toggle("inspector-open", open);
  }
}

function toggleInspector() {
  setInspector(!state.inspectorOpen);
}

function setInspectorTab(name = "analysis", reveal = false) {
  const next = ["analysis", "data", "report", "plan"].includes(name) ? name : "analysis";
  el.inspectorTabs?.querySelectorAll("[data-inspector-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.inspectorTab === next);
  });
  document.querySelectorAll("[data-inspector-view]").forEach((section) => {
    section.classList.toggle("active", section.dataset.inspectorView === next);
  });
  if (reveal && window.innerWidth <= 1180) setInspector(true);
}

function renderWelcome() {
  state.conversation = null;
  state.conversationId = null;
  renderMessages([]);
}

function setDatasetText(text, hint = "") {
  state.datasetText = text;
  if (el.datasetInput) el.datasetInput.value = text;
  setDatasetHint(hint || (text ? "数据已就绪" : "暂无数据"));
}

function setDatasetHint(text) {
  if (el.datasetHint) el.datasetHint.textContent = text;
}

function parseRows(text) {
  const raw = (text || "").trim();
  if (!raw) return null;
  if (raw.startsWith("[") || raw.startsWith("{")) {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed;
    if (parsed.rows || parsed.data || parsed.dataset || parsed.model_id || parsed.config) return parsed;
    throw new Error("JSON 数据需为数组，或包含 rows / data / dataset 字段");
  }
  return parseCsv(raw);
}

function parseCsv(text) {
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) throw new Error("CSV 至少需要表头和一行数据");
  const headers = splitCsvLine(lines[0]).map((x) => x.trim());
  return lines.slice(1).map((line) => {
    const values = splitCsvLine(line);
    const row = {};
    headers.forEach((header, index) => {
      row[header] = (values[index] || "").trim();
    });
    return row;
  });
}

function splitCsvLine(line) {
  const values = [];
  let current = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (char === '"') {
      if (quoted && line[i + 1] === '"') {
        current += '"';
        i++;
      } else {
        quoted = !quoted;
      }
    } else if (char === "," && !quoted) {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  values.push(current);
  return values;
}

async function analyzeCurrentDataset(silent = false) {
  const text = el.datasetInput?.value.trim() || "";
  if (!text) {
    if (!silent) showToast("请先输入或加载一组数据");
    return null;
  }
  try {
    state.loading = true;
    updateStatus("正在分析");
    const dataset = parseRows(text);
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset, conversationId: state.conversationId }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "分析失败");
    state.result = result;
    renderAnalysis(result);
    updateStatus("分析完成");
    if (!silent) showToast(`已推荐 ${result.recommended_model?.name || "候选模型"}`);
    return result;
  } catch (error) {
    updateStatus("分析失败");
    showToast(error.message);
    return null;
  } finally {
    state.loading = false;
  }
}

async function onSendMessage() {
  const message = el.chatInput?.value.trim() || "";
  if (!message || state.loading) {
    if (!message) showToast("先输入一句话");
    return;
  }
  try {
    state.loading = true;
    updateStatus("正在推理");
    const dataset = parseRows(el.datasetInput?.value.trim() || demoData);
    const optimistic = [
      ...(state.conversation?.messages || []),
      { role: "user", content: message, time: nowText() },
      { role: "assistant", content: pendingAssistantText(), time: nowText(), pending: true },
    ];
    renderMessages(optimistic);

    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        dataset,
        conversationId: state.conversationId,
        plan: state.plan,
        theme: state.theme,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "发送失败");

    state.conversation = data.conversation;
    state.conversationId = data.conversation.id;
    state.result = data.result;
    renderLlmStatus(data.llm || state.bootstrap?.llm_provider);
    el.chatInput.value = "";
    autosizeComposer();
    renderAnalysis(state.result);
    renderMessages(state.conversation.messages || []);
    syncHistoryItem({
      id: state.conversation.id,
      title: state.conversation.title,
      updatedAt: state.conversation.updatedAt,
      preview: state.conversation.preview,
    });
    updateStatus(data.intent === "report" ? "报告已生成" : "已回复");
    if (data.intent === "report") openReportModal(state.result);
  } catch (error) {
    showToast(error.message);
    renderMessages(state.conversation?.messages || []);
    updateStatus("发送失败");
  } finally {
    state.loading = false;
  }
}

async function loadConversation(id, announce = true) {
  try {
    const response = await fetch(`/api/conversations/${id}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "无法读取对话");
    state.conversation = data;
    state.conversationId = data.id;
    const lastResult = [...(data.messages || [])].reverse().find((item) => item.role === "assistant" && item.result);
    if (lastResult?.result) {
      state.result = lastResult.result;
      renderAnalysis(state.result);
    }
    renderMessages(data.messages || []);
    highlightHistory(id);
    updateStatus("历史已加载");
    if (announce) showToast("已打开历史对话");
  } catch (error) {
    showToast(error.message);
  }
}

function newConversation() {
  state.conversation = null;
  state.conversationId = null;
  renderWelcome();
  highlightHistory(null);
  updateStatus("新对话");
  showToast("已开启新分析");
}

async function onImportFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  const text = await file.text();
  setDatasetText(text, `已导入 ${file.name}`);
  await analyzeCurrentDataset(false);
  event.target.value = "";
}

async function onDownloadReport() {
  try {
    if (!state.result) await analyzeCurrentDataset(true);
    if (!state.result) return;
    await downloadReportDocx(state.result, "知构引擎科研分析报告");
    showToast("DOCX 报告已生成");
  } catch (error) {
    showToast(error.message);
  }
}

async function downloadReportDocx(result, title) {
  const response = await fetch("/api/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title,
      result,
      format: "docx",
      conversationId: state.conversationId,
    }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || "报告生成失败");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "zhigou-analysis-report.docx";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function renderSubscriptionDock(plans) {
  if (!el.subscriptionDock) return;
  if (!plans.length) {
    el.subscriptionDock.innerHTML = "";
    return;
  }
  el.subscriptionDock.innerHTML = plans
    .map(
      (plan) => `
      <button class="plan-chip ${state.plan === plan.name ? "active" : ""}" type="button" data-plan="${escapeHtml(plan.name)}">
        <span>${escapeHtml(plan.name)}</span>
      </button>
    `
    )
    .join("");
  el.subscriptionDock.querySelectorAll("[data-plan]").forEach((button) => {
    button.addEventListener("click", () => setPlan(button.dataset.plan));
  });
}

function renderSubscriptionCards(plans) {
  if (!el.subscriptionCards) return;
  if (!plans.length) {
    el.subscriptionCards.innerHTML = "";
    return;
  }
  el.subscriptionCards.innerHTML = plans
    .map(
      (plan) => `
      <div class="subscription-card ${state.plan === plan.name ? "active" : ""}">
        <div class="subscription-title">
          <strong>${escapeHtml(plan.name)}</strong>
          <span>${escapeHtml(plan.price)}</span>
        </div>
        <p>${escapeHtml(plan.desc)}</p>
        <button class="mini-button" type="button" data-plan="${escapeHtml(plan.name)}">
          ${state.plan === plan.name ? "当前方案" : "切换"}
        </button>
      </div>
    `
    )
    .join("");
  el.subscriptionCards.querySelectorAll("[data-plan]").forEach((btn) => {
    btn.addEventListener("click", () => setPlan(btn.dataset.plan));
  });
}

function renderSubscription() {
  renderPlanSummary();
  renderSubscriptionDock(state.bootstrap?.subscription_plans || []);
  renderSubscriptionCards(state.bootstrap?.subscription_plans || []);
}

function renderPlanSummary() {
  if (el.planBadge) el.planBadge.textContent = state.plan;
  if (el.subscriptionToggleText) el.subscriptionToggleText.textContent = state.plan;
  const current = state.bootstrap?.subscription_plans?.find((plan) => plan.name === state.plan);
  if (current) {
    if (el.planBadge) el.planBadge.title = current.desc;
    if (el.subscriptionToggleBtn) el.subscriptionToggleBtn.title = current.desc;
  }
}

function renderHistory(items) {
  state.allConversations = items;
  if (!el.historyList) return;
  if (!items.length) {
    el.historyList.innerHTML = `
      <div class="empty-history">
        <strong>暂无历史</strong>
        <span>开始一次分析后会自动保存</span>
      </div>
    `;
    return;
  }
  el.historyList.innerHTML = items
    .map(
      (item) => `
      <button class="history-item" data-id="${escapeHtml(item.id)}" type="button">
        <strong>${escapeHtml(item.title || "未命名对话")}</strong>
        <span>${escapeHtml(cleanPublicText(item.preview || "暂无摘要"))}</span>
      </button>
    `
    )
    .join("");
  el.historyList.querySelectorAll("[data-id]").forEach((button) => {
    button.addEventListener("click", () => loadConversation(button.dataset.id));
  });
  highlightHistory(state.conversationId);
}

function filterHistory(keyword) {
  const query = (keyword || "").trim().toLowerCase();
  if (!query) {
    renderHistory(state.allConversations);
    return;
  }
  renderHistory(
    state.allConversations.filter((item) =>
      `${item.title || ""} ${item.preview || ""}`.toLowerCase().includes(query)
    )
  );
}

function syncHistoryItem(item) {
  const current = state.bootstrap?.conversations || state.allConversations || [];
  const next = current.filter((entry) => entry.id !== item.id);
  next.unshift(item);
  state.allConversations = next;
  if (state.bootstrap) state.bootstrap.conversations = next;
  renderHistory(next);
}

function highlightHistory(id) {
  el.historyList?.querySelectorAll(".history-item").forEach((node) => {
    node.classList.toggle("active", node.dataset.id === id);
  });
}

function renderMessages(messages) {
  if (!el.messages) return;
  if (!messages.length) {
    el.messages.innerHTML = `
      <div class="welcome-panel">
        <div class="welcome-mark">
          <img src="/assets/logo.png" alt="" />
          <span>Knowledge Structure Engine</span>
        </div>
        <h2>有什么科研数据需要分析？</h2>
        <p>像聊天一样发起分析，知构引擎会把物理约束校验、LLM/符号回归候选、假设排行、异常诊断和报告生成串成一个可追踪工作流。</p>
        <div class="welcome-feature-grid">
          <button type="button" data-prompt="请按物理约束校验当前数据，指出边界、单调性和平滑性问题。">
            <strong>物理约束校验</strong>
            <span>检查边界、单调性、平滑性与可行域投影</span>
          </button>
          <button type="button" data-prompt="请用 LLM + 符号回归思路解释当前候选模型，并说明各模型适用场景。">
            <strong>LLM + 符号回归</strong>
            <span>根据数据形态生成候选模型与机理解释</span>
          </button>
          <button type="button" data-prompt="请根据拟合误差、物理约束和可解释性给当前模型做假设性排行。">
            <strong>假设性排行</strong>
            <span>综合拟合误差、约束惩罚和实验可解释性</span>
          </button>
        </div>
        <div class="welcome-prompts">
          <button type="button">分析当前示例数据</button>
          <button type="button">检查物理约束</button>
          <button type="button">生成分析报告</button>
        </div>
      </div>
    `;
    el.messages.querySelectorAll(".welcome-panel button").forEach((button) => {
      button.addEventListener("click", () => {
        el.chatInput.value = button.dataset.prompt || button.textContent.trim();
        autosizeComposer();
        onSendMessage();
      });
    });
    return;
  }
  el.messages.innerHTML = messages.map((message, index) => renderMessage(message, index)).join("");
  el.messages.scrollTop = el.messages.scrollHeight;
  bindMessageActions(messages);
}

function renderMessage(message, index) {
  const isUser = message.role === "user";
  const summary = message.result ? buildMessageSummary(message.result) : "";
  const reportCard = message.intent === "report" && message.result ? buildReportCard(message.result, index) : "";
  const actionCard = !isUser && message.result ? buildAssistantActions(index) : "";
  return `
    <div class="message-row ${isUser ? "user" : "assistant"} ${message.pending ? "pending" : ""}">
      <div class="message-avatar">${isUser ? "你" : `<img src="/assets/logo.png" alt="" />`}</div>
      <div class="message-body-wrap">
        <div class="message-meta">
          <strong>${isUser ? "你" : "知构引擎"}</strong>
          <span>${escapeHtml(displayTime(message.time))}</span>
        </div>
        <div class="message-bubble">${formatMessage(message.content || "", { assistant: !isUser })}</div>
        ${!isUser && summary ? `<div class="result-card">${summary}</div>` : ""}
        ${actionCard}
        ${reportCard}
      </div>
    </div>
  `;
}

function buildMessageSummary(result) {
  const best = result.recommended_model || {};
  const summary = result.summary || {};
  const formula = best.equation ? `<div class="mini-formula">${escapeHtml(best.equation)}</div>` : "";
  return `
    <div class="mini-grid">
      <div class="mini-tile"><span>推荐模型</span><strong>${escapeHtml(best.name || "-")}</strong></div>
      <div class="mini-tile"><span>物理评分</span><strong>${summary.physics_score ?? "-"}</strong></div>
      <div class="mini-tile"><span>异常点</span><strong>${summary.anomaly_count ?? 0}</strong></div>
    </div>
    ${formula}
  `;
}

function buildAssistantActions(index) {
  return `
    <div class="message-actions">
      <button class="mini-button" type="button" data-report-preview="${index}">
        <span class="icon" data-icon="report"></span>
        <span>查看报告</span>
      </button>
      <button class="mini-button" type="button" data-report-download="${index}">
        <span class="icon" data-icon="download"></span>
        <span>下载 DOCX</span>
      </button>
    </div>
  `;
}

function buildReportCard(result, index) {
  const best = result.recommended_model || {};
  const suggestions = (result.suggestions || []).slice(0, 3);
  return `
    <div class="result-card report-card">
      <div class="report-card-head">
        <strong>报告已生成</strong>
        <span>${escapeHtml(best.name || "-")}</span>
      </div>
      <ul>${suggestions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      <div class="message-actions">
        <button class="mini-button" type="button" data-report-preview="${index}">
          <span class="icon" data-icon="report"></span>
          <span>预览</span>
        </button>
        <button class="mini-button" type="button" data-report-download="${index}">
          <span class="icon" data-icon="download"></span>
          <span>DOCX</span>
        </button>
      </div>
    </div>
  `;
}

function bindMessageActions(messages) {
  renderIcons();
  el.messages.querySelectorAll("[data-report-download]").forEach((button) => {
    button.addEventListener("click", async () => {
      const message = messages[Number(button.dataset.reportDownload)];
      if (!message?.result) return;
      try {
        await downloadReportDocx(message.result, "知构引擎科研分析报告");
        showToast("DOCX 报告已生成");
      } catch (error) {
        showToast(error.message);
      }
    });
  });
  el.messages.querySelectorAll("[data-report-preview]").forEach((button) => {
    button.addEventListener("click", () => {
      const message = messages[Number(button.dataset.reportPreview)];
      if (message?.result) openReportModal(message.result);
    });
  });
}

function renderAnalysis(result) {
  if (!result) return;
  el.bestModel.textContent = result.recommended_model?.name || "-";
  el.confidenceValue.textContent = result.summary?.confidence != null ? `${Math.round(result.summary.confidence * 100)}%` : "-";
  el.anomalyValue.textContent = `${result.summary?.anomaly_count ?? 0}`;
  renderModels(result.models || []);
  renderChart(result);
  renderPhysicsPanel(result.physics_constraints || null);
  renderAnomalies(result.anomalies || []);
  renderReportPreview(result);
}

function renderModels(models) {
  if (!el.modelGrid) return;
  el.modelGrid.innerHTML = models
    .map((model) => {
      const score = model.constraint?.score ?? "-";
      const isSelected = state.result?.recommended_model?.key === model.key;
      return `
        <article class="model-item ${isSelected ? "selected" : ""}">
          <div class="model-main">
            <span class="rank-badge">#${model.rank ?? "-"}</span>
            <div>
              <strong>${escapeHtml(model.name)}</strong>
              <span>${model.constraint?.feasible ? "物理可行" : "需要修正"}</span>
            </div>
          </div>
          <div class="model-score">
            <span>约束评分</span>
            <strong>${score}</strong>
          </div>
          <div class="model-stats">
            <span>R² ${model.r2}</span>
            <span>RMSE ${model.rmse}</span>
            <span>BIC ${model.bic}</span>
            ${model.complexity ? `<span>复杂度 ${model.complexity}</span>` : ""}
          </div>
          <code class="model-equation">${escapeHtml(model.equation || "")}</code>
          <p>${escapeHtml(model.physics || "")}</p>
        </article>
      `;
    })
    .join("");
}

function renderPhysicsPanel(physics) {
  if (!el.physicsSummary || !el.physicsViolationList) return;
  if (!physics) {
    el.physicsSummary.textContent = "等待分析";
    el.physicsViolationList.innerHTML = "";
    return;
  }
  const summary = physics.data_profile?.summary || {};
  const best = physics.rankings?.[0];
  el.physicsSummary.textContent = `评分 ${summary.score ?? "-"} · 越界 ${summary.boundary_violations ?? 0} · 单调违例 ${summary.trend_violation_count ?? 0}`;

  const rows = [
    {
      title: "可行域投影",
      detail: `平均修正 ${summary.projection_gap_mean ?? 0}，最大修正 ${summary.projection_gap_max ?? 0}`,
      severity: "low",
    },
    ...(best?.violations || []),
  ];

  el.physicsViolationList.innerHTML = rows
    .map(
      (item) => `
      <div class="physics-item ${item.severity || "low"}">
        <strong>${escapeHtml(item.title)}</strong>
        <p>${escapeHtml(item.detail || item.suggestion || "")}</p>
      </div>
    `
    )
    .join("");
}

function renderAnomalies(anomalies) {
  if (!el.anomalyList) return;
  if (!anomalies.length) {
    el.anomalyList.innerHTML = `
      <div class="anomaly-item stable">
        <strong>当前数据稳定</strong>
        <p>未发现需要重点复核的异常点。</p>
      </div>
    `;
    return;
  }
  el.anomalyList.innerHTML = anomalies
    .map(
      (item) => `
      <div class="anomaly-item ${item.severity === "high" ? "high" : ""}">
        <strong>${item.severity === "high" ? "高风险异常" : "中等异常"}</strong>
        <p>温度 ${item.temperature}°C，转化率 ${item.conversion}，批次 ${escapeHtml(item.batch)}</p>
        <span>${escapeHtml(item.reason)}</span>
      </div>
    `
    )
    .join("");
}

function renderReportPreview(result) {
  if (el.reportPreview) el.reportPreview.innerHTML = buildReportPreviewMarkup(result, true);
}

function buildReportPreviewMarkup(result, compact = false) {
  const best = result.recommended_model || {};
  const summary = result.summary || {};
  const physics = result.physics_constraints || {};
  const physicsSummary = physics.data_profile?.summary || {};
  const rankings = (physics.rankings || []).slice(0, 3);
  const suggestions = (result.suggestions || []).slice(0, compact ? 2 : 5);
  return `
    <h3>知构引擎科研分析报告</h3>
    <p>推荐模型：<strong>${escapeHtml(best.name || "-")}</strong>，R² ${best.r2 ?? "-"}，物理评分 ${summary.physics_score ?? physicsSummary.score ?? "-"}。</p>
    <p class="report-formula">${escapeHtml(best.equation || "")}</p>
    <div class="report-metrics">
      <span>样本 ${summary.sample_count ?? "-"}</span>
      <span>异常 ${summary.anomaly_count ?? 0}</span>
      <span>可行模型 ${summary.feasible_models ?? "-"}</span>
    </div>
    <h4>关键建议</h4>
    <ul>${suggestions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    ${
      compact
        ? ""
        : `
      <h4>物理约束</h4>
      <ul>
        <li>越界点：${physicsSummary.boundary_violations ?? 0}</li>
        <li>单调违例：${physicsSummary.trend_violation_count ?? 0}</li>
        <li>平均投影修正：${physicsSummary.projection_gap_mean ?? 0}</li>
      </ul>
      <h4>假设排行</h4>
      <ol>${rankings.map((item) => `<li>${escapeHtml(item.name)}：约束评分 ${item.constraint_score}，组合评分 ${item.combined_score}</li>`).join("")}</ol>
    `
    }
  `;
}

function openReportModal(result) {
  if (!el.reportModal || !el.reportModalBody) return;
  el.reportModalBody.innerHTML = buildReportPreviewMarkup(result, false);
  el.reportModal.classList.remove("hidden");
  el.reportModal.setAttribute("aria-hidden", "false");
}

function closeReportModal() {
  el.reportModal?.classList.add("hidden");
  el.reportModal?.setAttribute("aria-hidden", "true");
}

function renderChart(result) {
  const curve = result.curve || [];
  const rows = result.rows || [];
  if (!el.chart) return;
  if (!curve.length || !rows.length) {
    el.chart.innerHTML = `<div class="empty-note">暂无可绘制曲线</div>`;
    return;
  }

  const width = 720;
  const height = 260;
  const pad = { top: 20, right: 22, bottom: 36, left: 42 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const xs = curve.map((d) => Number(d.temperature));
  const ys = [...curve.map((d) => Number(d.conversion)), ...rows.map((d) => Number(d.conversion))];
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.max(0, Math.min(...ys) - 0.05);
  const yMax = Math.min(1, Math.max(...ys) + 0.05);
  const xScale = (x) => pad.left + ((x - xMin) / (xMax - xMin || 1)) * plotWidth;
  const yScale = (y) => pad.top + plotHeight - ((y - yMin) / (yMax - yMin || 1)) * plotHeight;
  const line = curve.map((point, index) => `${index === 0 ? "M" : "L"}${xScale(point.temperature)},${yScale(point.conversion)}`).join(" ");
  const points = rows
    .map((row) => `<circle cx="${xScale(Number(row.temperature))}" cy="${yScale(Number(row.conversion))}" r="4" class="chart-point" />`)
    .join("");
  const gridLines = [];

  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (plotHeight * i) / 4;
    const value = yMax - ((yMax - yMin) * i) / 4;
    gridLines.push(`<line class="chart-grid" x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" />`);
    gridLines.push(`<text x="${pad.left - 8}" y="${y + 4}" text-anchor="end">${value.toFixed(2)}</text>`);
  }
  for (let i = 0; i <= 4; i++) {
    const x = pad.left + (plotWidth * i) / 4;
    const value = xMin + ((xMax - xMin) * i) / 4;
    gridLines.push(`<line class="chart-grid subtle" x1="${x}" y1="${pad.top}" x2="${x}" y2="${height - pad.bottom}" />`);
    gridLines.push(`<text x="${x}" y="${height - 12}" text-anchor="middle">${value.toFixed(0)}</text>`);
  }

  el.chart.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="拟合曲线图">
      ${gridLines.join("")}
      <path d="${line}" class="chart-line" />
      ${points}
      <text x="${pad.left}" y="14" class="chart-label">转化率</text>
      <text x="${width - pad.right}" y="${height - 4}" text-anchor="end" class="chart-label">温度</text>
    </svg>
  `;
}

function autosizeComposer() {
  if (!el.chatInput) return;
  el.chatInput.style.height = "auto";
  el.chatInput.style.height = `${Math.min(el.chatInput.scrollHeight, 160)}px`;
}

function updateStatus(text) {
  if (el.analysisStatus) el.analysisStatus.textContent = text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatMessage(text, options = {}) {
  if (options.assistant) {
    const normalized = normalizeAssistantText(text);
    const blocks = normalized
      .split(/\n{2,}/)
      .map((block) => block.trim())
      .filter(Boolean);
    if (!blocks.length) return "";
    return blocks.map((block) => renderRichBlock(block)).join("");
  }

  return escapeHtml(String(text))
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
}

function normalizeAssistantText(text) {
  const normalized = String(text ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/\\\\/g, "\\");
  return unwrapLatexCommand(normalized, "boxed")
    .replace(/\bDeepSeek\b/gi, "智能引擎")
    .replace(/\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}/g, "($1) / ($2)")
    .replace(/\\begin\{aligned\}|\\end\{aligned\}/g, "")
    .replace(/\\\[/g, "")
    .replace(/\\\]/g, "")
    .replace(/\$\$/g, "")
    .replace(/\\times/g, "×")
    .replace(/\\cdot/g, "·")
    .replace(/\\pm/g, "±")
    .replace(/\^\{([^{}]+)\}/g, "^($1)")
    .replace(/_\{([^{}]+)\}/g, "_$1")
    .replace(/\\left|\\right/g, "")
    .replace(/\\[,;]/g, " ")
    .replace(/(?<=\d)([A-Za-z])/g, " * $1")
    .replace(/\)([A-Za-z])/g, ") * $1")
    .trim();
}

function unwrapLatexCommand(text, command) {
  const token = `\\${command}{`;
  let output = "";
  let index = 0;
  while (index < text.length) {
    const start = text.indexOf(token, index);
    if (start === -1) {
      output += text.slice(index);
      break;
    }
    output += text.slice(index, start);
    const bodyStart = start + token.length;
    let cursor = bodyStart;
    let depth = 1;
    while (cursor < text.length) {
      const char = text[cursor];
      const escaped = cursor > 0 && text[cursor - 1] === "\\";
      if (char === "{" && !escaped) depth += 1;
      if (char === "}" && !escaped) depth -= 1;
      if (depth === 0) break;
      cursor += 1;
    }
    if (depth === 0) {
      output += text.slice(bodyStart, cursor);
      index = cursor + 1;
    } else {
      output += text.slice(start);
      break;
    }
  }
  return output;
}

function renderRichBlock(block) {
  const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return "";

  const html = [];
  const textLines = [];
  const flushText = () => {
    if (!textLines.length) return;
    html.push(`<div class="message-block">${textLines.map((line) => formatInlineMessage(line)).join("<br>")}</div>`);
    textLines.length = 0;
  };

  for (let index = 0; index < lines.length; ) {
    if (isMarkdownTableAt(lines, index)) {
      flushText();
      const tableLines = [lines[index], lines[index + 1]];
      index += 2;
      while (index < lines.length && lines[index].includes("|")) {
        tableLines.push(lines[index]);
        index += 1;
      }
      html.push(renderMarkdownTable(tableLines));
      continue;
    }
    textLines.push(lines[index]);
    index += 1;
  }
  flushText();
  return html.join("");
}

function isMarkdownTableAt(lines, index) {
  if (index + 1 >= lines.length) return false;
  const header = splitTableCells(lines[index]);
  const separator = splitTableCells(lines[index + 1]);
  return header.length > 1 && header.length === separator.length && separator.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function splitTableCells(row) {
  return String(row)
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderMarkdownTable(lines) {
  const headerCells = splitTableCells(lines[0]);
  const bodyRows = lines.slice(2).filter((line) => line.includes("|")).map(splitTableCells);
  const width = Math.max(headerCells.length, ...bodyRows.map((row) => row.length));
  return `
    <div class="message-table-wrap">
      <table class="message-table">
        <thead>
          <tr>${headerCells.map((cell) => `<th>${formatInlineMessage(cell)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${bodyRows
            .map(
              (row) =>
                `<tr>${Array.from({ length: width }, (_, index) => `<td>${formatInlineMessage(row[index] || "")}</td>`).join("")}</tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function formatInlineMessage(value) {
  return escapeHtml(String(value ?? ""))
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

function cleanPublicText(value) {
  return normalizeAssistantText(value).replace(/\s+/g, " ").trim();
}

function nowText() {
  const now = new Date();
  return now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function displayTime(value) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function showToast(text) {
  if (!el.toast) return;
  el.toast.textContent = text;
  el.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.toast.classList.remove("show"), 2200);
}

function renderIcons() {
  document.querySelectorAll("[data-icon]").forEach((node) => {
    node.innerHTML = svgIcon(node.dataset.icon);
  });
}

function svgIcon(name) {
  const icons = {
    badge: `<svg viewBox="0 0 24 24"><path d="M12 2l3 4 5 1-2 5 2 5-5 1-3 4-3-4-5-1 2-5-2-5 5-1z"/><path d="M9 12l2 2 4-4"/></svg>`,
    chevrons: `<svg viewBox="0 0 24 24"><path d="M7 7l5 5 5-5"/><path d="M7 12l5 5 5-5"/></svg>`,
    close: `<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>`,
    database: `<svg viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5"/><path d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/></svg>`,
    download: `<svg viewBox="0 0 24 24"><path d="M12 4v12M7 11l5 5 5-5M5 20h14"/></svg>`,
    menu: `<svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></svg>`,
    palette: `<svg viewBox="0 0 24 24"><path d="M12 3a9 9 0 0 0 0 18h2a2 2 0 0 0 0-4h-1a2 2 0 0 1 0-4h2a3 3 0 0 0 0-6h-3Z"/><circle cx="7.5" cy="9" r="1"/><circle cx="6.5" cy="14" r="1"/><circle cx="9.8" cy="16.2" r="1"/></svg>`,
    panel: `<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16M8 9h3M8 13h3"/></svg>`,
    plus: `<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>`,
    report: `<svg viewBox="0 0 24 24"><path d="M6 3h9l3 3v15H6z"/><path d="M9 10h6M9 14h6M9 6h3"/></svg>`,
    route: `<svg viewBox="0 0 24 24"><circle cx="6" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><path d="M8.6 15.4 15.4 8.6"/></svg>`,
    scan: `<svg viewBox="0 0 24 24"><path d="M4 7V5a1 1 0 0 1 1-1h2M20 7V5a1 1 0 0 0-1-1h-2M4 17v2a1 1 0 0 0 1 1h2M20 17v2a1 1 0 0 1-1 1h-2"/><path d="M9 12h6M12 9v6"/></svg>`,
    search: `<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`,
    send: `<svg viewBox="0 0 24 24"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4 20-7Z"/></svg>`,
    sliders: `<svg viewBox="0 0 24 24"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M2 14h4M10 8h4M18 16h4"/></svg>`,
    sparkles: `<svg viewBox="0 0 24 24"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3zM19 3v4M21 5h-4"/></svg>`,
    trash: `<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>`,
    upload: `<svg viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M5 20h14"/></svg>`,
  };
  return icons[name] || `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/></svg>`;
}
