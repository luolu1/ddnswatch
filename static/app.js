(function () {
  "use strict";

  const DEFAULT_REFRESH_SECONDS = 25;
  const state = {
    data: null,
    secondsLeft: DEFAULT_REFRESH_SECONDS,
    refreshSeconds: DEFAULT_REFRESH_SECONDS,
    timer: null,
    apiUnavailable: false
  };

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));

  function normalizeTarget(target) {
    const latest = target.latest || {};
    const history = Array.isArray(target.history) ? target.history : [];
    const status = target.last_status || latest.status;
    return {
      ...target,
      host: target.host || target.name || "未知目标",
      ip: target.resolved_ip || latest.resolved_ip || "未知",
      port: target.port ?? "--",
      status: ["normal", "blocked", "unknown"].includes(status) ? status : "unknown",
      rate: Number.isFinite(Number(target.normal_rate_60m)) ? Number(target.normal_rate_60m) : null,
      checkedAt: target.last_check_at || latest.checked_at,
      history: history.map((item) => typeof item === "string" ? { status: item } : item)
    };
  }

  function normalize(payload) {
    const result = payload?.data || payload?.result || payload || {};
    const targets = Array.isArray(result.targets) ? result.targets : [];
    const config = result.config || payload?.config || {};
    const configuredRefresh = result.refresh_seconds ?? result.refresh_interval_seconds ?? config.refresh_seconds ?? config.refresh_interval_seconds;
    const refreshSeconds = Number(configuredRefresh);
    return { targets: targets.map(normalizeTarget), refreshSeconds: Number.isFinite(refreshSeconds) && refreshSeconds > 0 ? Math.round(refreshSeconds) : DEFAULT_REFRESH_SECONDS };
  }

  async function loadStatus() {
    try {
      const response = await fetch("/api/status", { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.data = normalize(await response.json());
      state.refreshSeconds = state.data.refreshSeconds;
      state.apiUnavailable = false;
      $("connection-label").textContent = "监控在线";
    } catch (error) {
      state.data = state.data || { targets: [] };
      state.data.targets = state.data.targets.map((target) => ({ ...target, status: "unknown", rate: null, checkedAt: null, history: [] }));
      state.apiUnavailable = true;
      $("connection-label").textContent = "API 连接失败";
    }
    render();
    resetCountdown();
  }

  function formatTime(value) {
    if (!value) return "未知";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  }

  function renderTimeline(history, targetIndex) {
    const entries = [...Array(Math.max(0, 60 - history.length)).fill({ status: "unknown" }), ...history].slice(-60);
    return entries.map((item, index) => {
      const status = item.status === "normal" ? "normal" : ["blocked", "unknown"].includes(item.status) ? item.status : "unknown";
      const name = { normal: "正常", blocked: "被墙", unknown: "未知" }[status];
      return `<span class="minute ${status}" role="listitem" aria-label="目标 ${targetIndex + 1}，${59 - index} 分钟前：${name}" title="${escapeHtml(item.checked_at || item.at || `${59 - index} 分钟前`)} · ${name}" ${index === 59 ? 'aria-current="true"' : ""}></span>`;
    }).join("");
  }

  function render() {
    const targets = state.data.targets || [];
    $("empty-state").hidden = targets.length > 0;
    $("empty-state-text").textContent = state.apiUnavailable ? "API 连接失败，暂无可用目标状态" : "暂无配置的监控目标";
    $("connection-notice").hidden = !state.apiUnavailable;
    $("target-list").innerHTML = targets.map((target, targetIndex) => {
      const history = target.history || [];
       const normalCount = history.filter((item) => item.status === "normal").length;
       const blockedCount = history.filter((item) => item.status === "blocked").length;
      const currentStatus = target.status === "normal" ? "正常" : target.status === "blocked" ? "被墙" : "未知";
      const statusClass = target.status === "normal" ? "status-good" : target.status === "blocked" ? "status-bad" : "status-unknown";
      return `<article class="target-row">
        <div class="target-identity"><span class="target-icon"><i data-lucide="globe-2"></i></span><div><span class="label">${escapeHtml(target.name || "监控目标")}</span><strong class="mono">${escapeHtml(target.host)}</strong></div></div>
        <div class="target-value"><span class="label">解析 IP</span><strong class="mono">${escapeHtml(target.ip)}</strong></div>
        <div class="target-value"><span class="label">端口</span><strong class="mono">${escapeHtml(target.port)}</strong></div>
         <div class="target-value"><span class="label">一小时正常率</span><strong class="mono ${target.rate == null ? "status-unknown" : statusClass}">${target.rate == null ? "未知" : `${target.rate.toFixed(1)}%`}</strong></div>
        <div class="target-value"><span class="label">最后检测</span><strong class="mono">${escapeHtml(formatTime(target.checkedAt))}</strong></div>
        <div class="target-status"><span class="label">状态</span><strong class="${statusClass}">${currentStatus}</strong></div>
         <div class="target-history"><div class="timeline" role="list" aria-label="${escapeHtml(target.host)} 过去 60 分钟检测状态">${renderTimeline(history, targetIndex)}</div><div class="time-axis"><span>60 分钟前</span><span>30 分钟前</span><span>现在</span></div><span class="history-summary">${normalCount} / 60 分钟正常${blockedCount ? `，${blockedCount} 分钟被墙` : ""}</span></div>
      </article>`;
    }).join("");
    if (window.lucide) window.lucide.createIcons();
  }

  function resetCountdown() { state.secondsLeft = state.refreshSeconds; $("countdown").textContent = `${state.secondsLeft}s`; $("refresh-label").textContent = `每 ${state.refreshSeconds} 秒检测`; }
  function tick() { state.secondsLeft -= 1; if (state.secondsLeft <= 0) loadStatus(); else $("countdown").textContent = `${state.secondsLeft}s`; }

  $("refresh-button").addEventListener("click", loadStatus);
  state.timer = window.setInterval(tick, 1000);
  loadStatus();
}());
