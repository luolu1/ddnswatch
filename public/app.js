(function () {
  "use strict";

  const DEFAULT_REFRESH_SECONDS = 60;
  const state = { targets: [], checkedAt: null, secondsLeft: DEFAULT_REFRESH_SECONDS, refreshSeconds: DEFAULT_REFRESH_SECONDS, apiUnavailable: false, loading: true };
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  const statusLabel = { normal: "正常", blocked: "不可达", unknown: "未知" };

  function normalize(payload) {
    const refreshSeconds = Number(payload?.refresh_seconds);
    const targets = Array.isArray(payload?.targets) ? payload.targets : [];
    return {
      checkedAt: payload?.checked_at || null,
      refreshSeconds: Number.isFinite(refreshSeconds) && refreshSeconds > 0 ? Math.round(refreshSeconds) : DEFAULT_REFRESH_SECONDS,
      targets: targets.map((target) => ({
        name: target?.name || "监控目标",
        host: target?.host || "未知目标",
        port: target?.port ?? "--",
        resolvedIp: target?.resolved_ip || "未知",
        status: Object.hasOwn(statusLabel, target?.status) ? target.status : "unknown",
        reason: target?.reason || "未提供原因",
        checkedAt: target?.checked_at || payload?.checked_at || null
      }))
    };
  }

  async function loadStatus() {
    state.loading = true;
    render();
    try {
      const response = await fetch("/api/status", { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = normalize(await response.json());
      state.targets = data.targets;
      state.checkedAt = data.checkedAt;
      state.refreshSeconds = data.refreshSeconds;
      state.apiUnavailable = false;
      $("connection-label").textContent = "监控在线";
    } catch (error) {
      state.targets = state.targets.map((target) => ({ ...target, status: "unknown", reason: "暂时无法取得最新检测结果" }));
      state.apiUnavailable = true;
      $("connection-label").textContent = "API 连接失败";
    } finally {
      state.loading = false;
      render();
      resetCountdown();
    }
  }

  function formatTime(value) {
    if (!value) return "未知";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
  }

  function render() {
    const list = $("target-list");
    $("connection-notice").hidden = !state.apiUnavailable;
    $("empty-state").hidden = state.loading || state.targets.length > 0;
    $("empty-state-text").textContent = state.apiUnavailable ? "API 连接失败，暂无可用目标状态" : "暂无配置的监控目标";
    if (state.loading) {
      list.innerHTML = '<div class="loading-state" role="status">正在取得最新检测结果…</div>';
      return;
    }
    list.innerHTML = state.targets.map((target) => {
      const statusClass = target.status === "normal" ? "status-good" : target.status === "blocked" ? "status-bad" : "status-unknown";
      return `<article class="target-row"><div class="target-identity"><span class="target-icon"><i data-lucide="globe-2"></i></span><div><span class="label">${escapeHtml(target.name)}</span><strong class="mono">${escapeHtml(target.host)}</strong></div></div><div class="target-value"><span class="label">解析 IP</span><strong class="mono">${escapeHtml(target.resolvedIp)}</strong></div><div class="target-value"><span class="label">端口</span><strong class="mono">${escapeHtml(target.port)}</strong></div><div class="target-value"><span class="label">检测说明</span><strong class="reason">${escapeHtml(target.reason)}</strong></div><div class="target-value"><span class="label">检测时间</span><strong class="mono">${escapeHtml(formatTime(target.checkedAt))}</strong></div><div class="target-status"><span class="label">状态</span><strong class="${statusClass}">${statusLabel[target.status]}</strong></div></article>`;
    }).join("");
    if (window.lucide) window.lucide.createIcons();
  }

  function resetCountdown() { state.secondsLeft = state.refreshSeconds; $("countdown").textContent = `${state.secondsLeft}s`; $("refresh-label").textContent = `每 ${state.refreshSeconds} 秒检测`; }
  function tick() { state.secondsLeft -= 1; if (state.secondsLeft <= 0) loadStatus(); else $("countdown").textContent = `${state.secondsLeft}s`; }

  $("refresh-button").addEventListener("click", loadStatus);
  window.setInterval(tick, 1000);
  render();
  loadStatus();
}());
