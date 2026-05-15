const form = document.querySelector("#analysisForm");
const statusBadge = document.querySelector("#statusBadge");
const resultTitle = document.querySelector("#resultTitle");
const signalPill = document.querySelector("#signalPill");
const decision = document.querySelector("#decision");
const meta = document.querySelector("#meta");
const submitButton = document.querySelector("#submitButton");
const historyList = document.querySelector("#historyList");
const refreshHistory = document.querySelector("#refreshHistory");
const agentStatus = document.querySelector("#agentStatus");
const liveEvents = document.querySelector("#liveEvents");

let appConfig = null;
let eventSource = null;
let currentJobId = sessionStorage.getItem("tradingagents.currentJobId") || "";
let lastEventId = Number(sessionStorage.getItem("tradingagents.lastEventId") || "0");
let seenEventIds = new Set();
let followLiveEvents = true;

function closeEventSource() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

function setStatus(status) {
  statusBadge.textContent = status;
  statusBadge.className = `status ${status.toLowerCase()}`;
}

function option(select, label, value, selected = false) {
  const node = document.createElement("option");
  node.textContent = label;
  node.value = value;
  node.selected = selected;
  select.appendChild(node);
}

function fillDatalist(id, models) {
  const list = document.querySelector(id);
  list.innerHTML = "";
  models.forEach((model) => {
    const node = document.createElement("option");
    node.value = model.value;
    node.label = model.label;
    list.appendChild(node);
  });
}

function selectedProviderModels(mode) {
  const provider = form.provider.value;
  return appConfig.provider_models[provider]?.[mode] || [];
}

function refreshModelInputs() {
  const quick = selectedProviderModels("quick");
  const deep = selectedProviderModels("deep");
  fillDatalist("#quickModels", quick);
  fillDatalist("#deepModels", deep);
  if (quick.length && !quick.some((m) => m.value === form.quick_model.value)) {
    form.quick_model.value = quick[0].value;
  }
  if (deep.length && !deep.some((m) => m.value === form.deep_model.value)) {
    form.deep_model.value = deep[0].value;
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderMarkdownish(text) {
  const escaped = escapeHtml(text);

  return escaped
    .replace(/^### (.*)$/gm, "<h3>$1</h3>")
    .replace(/^\*\*(.*?)\*\*: ?(.*)$/gm, "<p><strong>$1:</strong> $2</p>")
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n{2,}/g, "</p><p>")
    .replace(/\n/g, "<br>");
}

function renderAgentStatus(statuses = {}) {
  const entries = Object.entries(statuses);
  if (!entries.length) {
    agentStatus.innerHTML = "";
    return;
  }

  agentStatus.innerHTML = entries
    .map(([agent, status]) => `
      <div class="agent-chip ${escapeHtml(status)}">
        <span>${escapeHtml(agent)}</span>
        <strong>${escapeHtml(String(status).replace("_", " "))}</strong>
      </div>
    `)
    .join("");
}

function renderLiveEvents(events = []) {
  if (!events.length) {
    liveEvents.innerHTML = '<div class="event-empty">No live events yet.</div>';
    seenEventIds = new Set();
    return;
  }

  const visibleEvents = events.slice(-80);
  liveEvents.innerHTML = visibleEvents
    .map((event) => `
      <article class="event-item ${escapeHtml(event.type || "system")}">
        <div class="event-meta">
          <span>${escapeHtml(event.time || "")}</span>
          <strong>${escapeHtml(event.type || "system")}</strong>
        </div>
        <h4>${escapeHtml(event.title || "Event")}</h4>
        ${event.content ? `<div class="event-content">${renderMarkdownish(event.content)}</div>` : ""}
      </article>
    `)
    .join("");
  syncSeenEvents(visibleEvents);
  scrollLiveEventsToBottom();
}

function syncSeenEvents(events = []) {
  seenEventIds = new Set(events.map((event) => Number(event.id || 0)).filter(Boolean));
  if (!events.length) {
    return;
  }
  const ids = events.map((event) => Number(event.id || 0)).filter(Boolean);
  if (ids.length) {
    updateLastEventId(Math.max(...ids));
  }
}

function updateLastEventId(eventId) {
  if (!eventId || eventId <= lastEventId) {
    return;
  }
  lastEventId = eventId;
  sessionStorage.setItem("tradingagents.lastEventId", String(lastEventId));
}

function shouldFollowLiveEvents() {
  const gap = liveEvents.scrollHeight - liveEvents.scrollTop - liveEvents.clientHeight;
  return gap < 36;
}

function scrollLiveEventsToBottom() {
  if (followLiveEvents) {
    liveEvents.scrollTop = liveEvents.scrollHeight;
  }
}

function appendLiveEvent(event) {
  if (!event || !event.id) {
    return;
  }
  const eventId = Number(event.id);
  if (seenEventIds.has(eventId)) {
    return;
  }
  seenEventIds.add(eventId);
  updateLastEventId(eventId);

  const empty = liveEvents.querySelector(".event-empty");
  if (empty) {
    empty.remove();
  }

  const item = document.createElement("article");
  item.className = `event-item ${event.type || "system"}`;
  item.innerHTML = `
    <div class="event-meta">
      <span>${escapeHtml(event.time || "")}</span>
      <strong>${escapeHtml(event.type || "system")}</strong>
    </div>
    <h4>${escapeHtml(event.title || "Event")}</h4>
    ${event.content ? `<div class="event-content">${renderMarkdownish(event.content)}</div>` : ""}
  `;
  liveEvents.appendChild(item);

  while (liveEvents.querySelectorAll(".event-item").length > 80) {
    const first = liveEvents.querySelector(".event-item");
    if (!first) {
      break;
    }
    first.remove();
  }

  scrollLiveEventsToBottom();
}

function stage(title, subtitle, content, open = false) {
  if (!content || !String(content).trim()) {
    return "";
  }
  return `
    <details class="stage" ${open ? "open" : ""}>
      <summary>
        <span>
          <strong>${escapeHtml(title)}</strong>
          <small>${escapeHtml(subtitle)}</small>
        </span>
      </summary>
      <div class="stage-body">${renderMarkdownish(content)}</div>
    </details>
  `;
}

function renderAnalysisProcess(state) {
  if (!state) {
    return "";
  }
  const debate = state.investment_debate_state || {};
  const risk = state.risk_debate_state || {};
  const traderPlan = state.trader_investment_plan || state.trader_investment_decision || "";
  const finalDecision = state.final_trade_decision || "";

  const sections = [
    stage("Portfolio Manager", "Final decision", finalDecision, true),
    stage("Market Analyst", "Market and technical report", state.market_report),
    stage("Social Analyst", "Sentiment report", state.sentiment_report),
    stage("News Analyst", "News and macro report", state.news_report),
    stage("Fundamentals Analyst", "Company fundamentals report", state.fundamentals_report),
    stage("Bull Researcher", "Bull-side debate history", debate.bull_history),
    stage("Bear Researcher", "Bear-side debate history", debate.bear_history),
    stage("Research Manager", "Investment plan", debate.judge_decision || state.investment_plan),
    stage("Trader", "Transaction proposal", traderPlan),
    stage("Aggressive Risk Analyst", "Aggressive risk case", risk.aggressive_history),
    stage("Conservative Risk Analyst", "Conservative risk case", risk.conservative_history),
    stage("Neutral Risk Analyst", "Neutral risk case", risk.neutral_history),
  ].filter(Boolean);

  if (!sections.length) {
    return "<pre>No agent process was saved for this record.</pre>";
  }

  return `<div class="process">${sections.join("")}</div>`;
}

function renderJob(job) {
  setStatus(job.status);
  resultTitle.textContent = `${job.request.ticker} · ${job.request.date}`;
  signalPill.textContent = job.signal || "-";
  renderAgentStatus(job.agent_status || {});
  renderLiveEvents(job.events || []);
  meta.innerHTML = "";
  [
    job.request.provider,
    job.request.data_vendor,
    `${job.request.research_depth} round`,
    job.request.language,
  ].forEach((item) => {
    const node = document.createElement("span");
    node.textContent = item;
    meta.appendChild(node);
  });

  if (job.status === "failed") {
    decision.innerHTML = `<pre>${escapeHtml(job.error || "Analysis failed.")}</pre>`;
    return;
  }

  if (job.status !== "completed") {
    decision.innerHTML = "<pre>Analysis is running. The live process panel above shows visible Agent outputs, tool calls, debate updates, and report drafts as they arrive.</pre>";
    return;
  }

  decision.innerHTML = renderAnalysisProcess(job.final_state) || renderMarkdownish(job.decision || "No final decision returned.");
  loadHistory();
}

function resetLiveState() {
  seenEventIds = new Set();
  lastEventId = 0;
  sessionStorage.removeItem("tradingagents.lastEventId");
}

function rememberCurrentJob(jobId) {
  currentJobId = jobId;
  sessionStorage.setItem("tradingagents.currentJobId", jobId);
}

function clearCurrentJob() {
  currentJobId = "";
  sessionStorage.removeItem("tradingagents.currentJobId");
  sessionStorage.removeItem("tradingagents.lastEventId");
}

function applyJobUpdate(job) {
  setStatus(job.status);
  resultTitle.textContent = `${job.request.ticker} · ${job.request.date}`;
  signalPill.textContent = job.signal || "-";
  renderAgentStatus(job.agent_status || {});
  meta.innerHTML = "";
  [
    job.request.provider,
    job.request.data_vendor,
    `${job.request.research_depth} round`,
    job.request.language,
  ].forEach((item) => {
    const node = document.createElement("span");
    node.textContent = item;
    meta.appendChild(node);
  });

  if (job.status === "failed") {
    decision.innerHTML = `<pre>${escapeHtml(job.error || "Analysis failed.")}</pre>`;
    return;
  }

  if (job.status !== "completed") {
    decision.innerHTML = "<pre>Analysis is running. The live process panel above shows visible Agent outputs, tool calls, debate updates, and report drafts as they arrive.</pre>";
    return;
  }

  decision.innerHTML = renderAnalysisProcess(job.final_state) || renderMarkdownish(job.decision || "No final decision returned.");
}

function finishJob(job) {
  applyJobUpdate(job);
  closeEventSource();
  submitButton.disabled = false;
  submitButton.textContent = "Run Analysis";
  clearCurrentJob();
  if (job.status === "completed") {
    loadHistory();
  }
}

function connectJobEvents(jobId, after = 0) {
  closeEventSource();
  rememberCurrentJob(jobId);

  const source = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events?after=${encodeURIComponent(after)}`);
  eventSource = source;

  source.addEventListener("snapshot", (event) => {
    const job = JSON.parse(event.data);
    applyJobUpdate(job);
    renderLiveEvents(job.events || []);
    if (job.status === "completed" || job.status === "failed") {
      finishJob(job);
    }
  });

  source.addEventListener("job_event", (event) => {
    appendLiveEvent(JSON.parse(event.data));
  });

  source.addEventListener("job_update", (event) => {
    applyJobUpdate(JSON.parse(event.data));
  });

  source.addEventListener("completed", (event) => {
    finishJob(JSON.parse(event.data));
  });

  source.addEventListener("failed", (event) => {
    finishJob(JSON.parse(event.data));
  });

  source.addEventListener("error", () => {
    if (source.readyState === EventSource.CLOSED) {
      return;
    }
  });
}

function renderHistoryState(title, detail = "") {
  historyList.innerHTML = `<div class="history-empty"><strong>${escapeHtml(title)}</strong>${detail ? `<br>${escapeHtml(detail)}` : ""}</div>`;
}

function renderHistory(items) {
  if (!items.length) {
    renderHistoryState("No saved analysis records yet.");
    return;
  }

  historyList.innerHTML = "";
  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    button.innerHTML = `
      <span class="history-main">
        <span class="history-title">${escapeHtml(item.ticker)} · ${escapeHtml(item.date)}</span>
        <span class="history-sub">${escapeHtml(item.updated_at)}</span>
      </span>
      <span class="history-signal">${escapeHtml(item.signal || "-")}</span>
    `;
    button.addEventListener("click", () => loadHistoryDetail(item.id));
    historyList.appendChild(button);
  });
}

async function loadHistory() {
  try {
    const response = await fetch("/api/history");
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || "Unable to load history.");
    }
    renderHistory(body.items || []);
  } catch (error) {
    renderHistoryState("History unavailable.", error.message);
  }
}

async function loadHistoryDetail(id) {
  try {
    closeEventSource();
    clearCurrentJob();
    const response = await fetch(`/api/history/${encodeURIComponent(id)}`);
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || "Unable to load history detail.");
    }
    const state = body.final_state || {};
    const ticker = state.company_of_interest || "-";
    const tradeDate = state.trade_date || "-";
    const finalDecision = state.final_trade_decision || "";
    resultTitle.textContent = `${ticker} · ${tradeDate}`;
    const match = finalDecision.match(/\*\*Rating\*\*:\s*([A-Za-z]+)/i) || finalDecision.match(/Rating:\s*\**([A-Za-z]+)/i);
    signalPill.textContent = match ? match[1] : "Saved";
    setStatus("completed");
    renderAgentStatus({});
    renderLiveEvents([]);
    meta.innerHTML = "";
    [
      "Saved analysis",
      body.updated_at,
      body.path,
    ].forEach((item) => {
      const node = document.createElement("span");
      node.textContent = item;
      meta.appendChild(node);
    });
    decision.innerHTML = renderAnalysisProcess(state) || renderMarkdownish(finalDecision || "No final decision in this record.");
  } catch (error) {
    setStatus("failed");
    renderAgentStatus({});
    renderLiveEvents([]);
    decision.innerHTML = `<pre>${escapeHtml(error.message)}</pre>`;
  }
}

function collectPayload() {
  const analysts = Array.from(document.querySelectorAll("input[name='analysts']:checked")).map((node) => node.value);
  return {
    ticker: form.ticker.value,
    date: form.date.value,
    provider: form.provider.value,
    data_vendor: form.data_vendor.value,
    quick_model: form.quick_model.value,
    deep_model: form.deep_model.value,
    language: form.language.value,
    research_depth: Number(form.research_depth.value),
    checkpoint: form.checkpoint.checked,
    analysts,
  };
}

async function loadConfig() {
  const response = await fetch("/api/config");
  appConfig = await response.json();

  form.date.value = appConfig.defaults.date;
  form.ticker.value = appConfig.defaults.ticker;
  form.quick_model.value = appConfig.defaults.quick_model;
  form.deep_model.value = appConfig.defaults.deep_model;
  form.research_depth.value = appConfig.defaults.research_depth;
  form.checkpoint.checked = Boolean(appConfig.defaults.checkpoint);

  appConfig.providers.forEach((provider) => {
    option(form.provider, provider, provider, provider === appConfig.defaults.provider);
  });
  appConfig.data_vendors.forEach((vendor) => {
    option(form.data_vendor, vendor, vendor, vendor === appConfig.defaults.data_vendor);
  });
  appConfig.languages.forEach((language) => {
    option(form.language, language, language, language === appConfig.defaults.language);
  });

  const analysts = document.querySelector("#analysts");
  appConfig.analysts.forEach((analyst) => {
    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" name="analysts" value="${analyst}" checked /><span>${analyst}</span>`;
    analysts.appendChild(label);
  });

  refreshModelInputs();
  form.provider.addEventListener("change", refreshModelInputs);
}

async function restoreCurrentJob() {
  if (!currentJobId) {
    return false;
  }

  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(currentJobId)}`);
    const job = await response.json();
    if (!response.ok) {
      throw new Error(job.error || "Unable to restore running job.");
    }

    applyJobUpdate(job);
    renderLiveEvents(job.events || []);
    if (job.status === "completed" || job.status === "failed") {
      clearCurrentJob();
      submitButton.disabled = false;
      submitButton.textContent = "Run Analysis";
      return false;
    }

    submitButton.disabled = true;
    submitButton.textContent = "Running...";
    connectJobEvents(currentJobId, lastEventId);
    return true;
  } catch (error) {
    clearCurrentJob();
    setStatus("failed");
    decision.innerHTML = `<pre>${escapeHtml(error.message)}</pre>`;
    submitButton.disabled = false;
    submitButton.textContent = "Run Analysis";
    return false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  closeEventSource();
  resetLiveState();
  submitButton.disabled = true;
  submitButton.textContent = "Running...";
  setStatus("running");
  renderAgentStatus({});
  renderLiveEvents([]);
  decision.innerHTML = "<pre>Starting analysis...</pre>";

  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || "Request failed.");
    }
    applyJobUpdate(body);
    renderLiveEvents(body.events || []);
    connectJobEvents(body.id, 0);
  } catch (error) {
    setStatus("failed");
    decision.innerHTML = `<pre>${escapeHtml(error.message)}</pre>`;
    submitButton.disabled = false;
    submitButton.textContent = "Run Analysis";
  }
});

loadConfig()
  .then(async () => {
    const restored = await restoreCurrentJob();
    await loadHistory();
    return restored;
  })
  .catch((error) => {
    setStatus("failed");
    decision.innerHTML = `<pre>${escapeHtml(error.message)}</pre>`;
  });

refreshHistory.addEventListener("click", loadHistory);

liveEvents.addEventListener("scroll", () => {
  followLiveEvents = shouldFollowLiveEvents();
});

window.addEventListener("beforeunload", closeEventSource);
