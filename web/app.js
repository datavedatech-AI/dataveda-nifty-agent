(() => {
  "use strict";

  const LS_KEY = "dataveda_api_key";
  let apiKey = localStorage.getItem(LS_KEY);
  let pollHandle = null;
  // Kept in memory only (never persisted) - populated right after signup or
  // a "regenerate agent token" call, so we can show the real bridge-agent
  // command once. Lost on reload, same as the server never showing it again.
  let lastKnownAgentToken = null;

  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- utils

  function toast(message, isError = false) {
    const el = $("toast");
    el.textContent = message;
    el.classList.remove("hidden");
    el.style.background = isError ? "#dc2626" : "#1a1d23";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 3500);
  }

  async function api(path, { method = "GET", body, auth = true, params } = {}) {
    let url = path;
    if (params) {
      const qs = new URLSearchParams(params).toString();
      if (qs) url += `?${qs}`;
    }
    const headers = { "Content-Type": "application/json" };
    if (auth && apiKey) headers.Authorization = `Bearer ${apiKey}`;

    const resp = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    let data = null;
    try {
      data = await resp.json();
    } catch {
      // no body
    }

    if (!resp.ok) {
      const detail = data && data.detail ? data.detail : resp.statusText;
      const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  function wsBaseUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}`;
  }

  function agentCommand(token) {
    const t = token || "<YOUR_AGENT_TOKEN>";
    return `python bridge_agent/agent.py --server ${wsBaseUrl()}/agent/ws --token ${t}`;
  }

  // The backend serializes naive-UTC timestamps (no trailing "Z" or
  // offset) - `new Date("...")` on a string like that is parsed as LOCAL
  // time per spec, not UTC, which would shift every displayed time by the
  // viewer's UTC offset. Force UTC interpretation here instead.
  function parseUtcIso(isoString) {
    if (!isoString) return null;
    const withZ = /[Zz]|[+-]\d\d:\d\d$/.test(isoString) ? isoString : `${isoString}Z`;
    return new Date(withZ);
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".copy-btn");
    if (!btn) return;
    const src = $(btn.dataset.copy);
    if (!src) return;
    navigator.clipboard
      .writeText(src.textContent)
      .then(() => toast("Copied"))
      .catch(() => toast("Could not copy - select and copy manually", true));
  });

  // ---------------------------------------------------------------- auth view

  function showAuthView() {
    $("auth-view").classList.remove("hidden");
    $("dashboard-view").classList.add("hidden");
    $("topbar-account").classList.add("hidden");
    if (pollHandle) clearInterval(pollHandle);
  }

  function showDashboardView() {
    $("auth-view").classList.add("hidden");
    $("dashboard-view").classList.remove("hidden");
    $("topbar-account").classList.remove("hidden");
    loadDashboard();
    if (pollHandle) clearInterval(pollHandle);
    pollHandle = setInterval(loadDashboard, 5000);
  }

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("tab-active"));
      tab.classList.add("tab-active");
      $("signup-form").classList.toggle("hidden", tab.dataset.tab !== "signup");
      $("login-form").classList.toggle("hidden", tab.dataset.tab !== "login");
      $("auth-error").classList.add("hidden");
    });
  });

  $("signup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("auth-error").classList.add("hidden");
    const email = $("signup-email").value.trim();
    try {
      const account = await api("/signup", { method: "POST", body: { email }, auth: false });
      lastKnownAgentToken = account.agent_token;
      $("secret-api-key").textContent = account.api_key;
      $("secret-webhook-url").textContent = account.webhook_url;
      $("secret-passphrase").textContent = account.webhook_passphrase;
      $("secret-agent-token").textContent = account.agent_token;
      $("secrets-modal").classList.remove("hidden");
      $("secrets-confirm-btn").onclick = () => {
        apiKey = account.api_key;
        localStorage.setItem(LS_KEY, apiKey);
        $("secrets-modal").classList.add("hidden");
        showDashboardView();
      };
    } catch (err) {
      $("auth-error").textContent = err.message;
      $("auth-error").classList.remove("hidden");
    }
  });

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("auth-error").classList.add("hidden");
    const key = $("login-key").value.trim();
    apiKey = key;
    try {
      await api("/me");
      localStorage.setItem(LS_KEY, apiKey);
      showDashboardView();
    } catch (err) {
      apiKey = null;
      $("auth-error").textContent = "That API key isn't valid.";
      $("auth-error").classList.remove("hidden");
    }
  });

  $("logout-btn").addEventListener("click", () => {
    apiKey = null;
    lastKnownAgentToken = null;
    localStorage.removeItem(LS_KEY);
    showAuthView();
  });

  // ---------------------------------------------------------------- dashboard

  function statusPillClass(status) {
    return `status-pill status-${status}`;
  }

  function renderMe(me) {
    $("account-email").textContent = me.email;

    const agentEl = $("stat-agent");
    agentEl.innerHTML = me.bridge_agent_connected
      ? '<span class="dot dot-green"></span> Connected'
      : '<span class="dot dot-red"></span> Offline';

    const killEl = $("stat-kill-switch");
    const banner = $("kill-switch-banner");
    if (me.kill_switch_engaged) {
      killEl.innerHTML = '<span class="dot dot-red"></span> Paused';
      banner.classList.remove("hidden");
      $("kill-switch-reason-text").textContent = me.kill_switch_reason
        ? `Trading is paused: ${me.kill_switch_reason}`
        : "Trading is paused.";
    } else {
      killEl.innerHTML = '<span class="dot dot-green"></span> Active';
      banner.classList.add("hidden");
    }

    const pnl = me.today_realized_pnl;
    const pnlEl = $("stat-pnl");
    pnlEl.textContent = pnl === null || pnl === undefined ? "-" : pnl.toFixed(2);
    pnlEl.style.color = pnl < 0 ? "var(--danger)" : pnl > 0 ? "var(--success)" : "var(--text)";

    $("stat-plan").textContent = me.plan;

    const subEl = $("stat-subscription");
    const subBanner = $("subscription-banner");
    if (me.subscription_active) {
      const until = parseUtcIso(me.subscription_expires_at);
      if (me.subscription_expiring_soon) {
        const days = me.subscription_days_remaining;
        subEl.innerHTML = `<span class="dot dot-amber"></span> ${days} day${days === 1 ? "" : "s"} left`;
        subBanner.classList.remove("hidden", "banner-danger");
        subBanner.classList.add("banner-warning");
        $("subscription-banner-text").textContent =
          `Your subscription expires ${days === 0 ? "today" : `in ${days} day${days === 1 ? "" : "s"}`} ` +
          `(${until.toLocaleDateString()}). Renew soon to avoid an interruption in trading.`;
      } else {
        subEl.innerHTML = `<span class="dot dot-green"></span> Until ${until.toLocaleDateString()}`;
        subBanner.classList.add("hidden");
      }
    } else {
      subEl.innerHTML = '<span class="dot dot-red"></span> Inactive';
      subBanner.classList.remove("hidden", "banner-warning");
      subBanner.classList.add("banner-danger");
      $("subscription-banner-text").textContent = me.subscription_expires_at
        ? `Your subscription expired on ${parseUtcIso(me.subscription_expires_at).toLocaleDateString()}. Signals will be rejected until it's renewed - contact us to renew.`
        : "Your account hasn't been activated yet. Signals will be rejected until a subscription is granted - contact us to get started.";
    }

    $("dash-webhook-url").textContent = `${window.location.origin}${me.webhook_url}`;
    $("agent-command").textContent = agentCommand(lastKnownAgentToken);

    renderSymbolMap(me.symbol_map || {});
    const risk = me.risk_settings || {};
    $("risk-max-qty").value = risk.max_qty_per_order ?? "";
    $("risk-max-daily-loss").value = risk.max_daily_loss ?? "";
    $("risk-allowed-symbols").value = (risk.allowed_symbols || []).join(", ");
  }

  function renderSymbolMap(map) {
    const tbody = $("symbol-map-rows");
    tbody.innerHTML = "";
    const entries = Object.entries(map);
    if (entries.length === 0) entries.push(["", ""]);
    entries.forEach(([tv, mt5]) => addSymbolRow(tv, mt5));
  }

  function addSymbolRow(tv = "", mt5 = "") {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input class="sym-tv" type="text" value="${tv}" placeholder="EURUSD" /></td>
      <td><input class="sym-mt5" type="text" value="${mt5}" placeholder="EURUSD" /></td>
      <td><button class="btn btn-sm btn-ghost remove-row-btn">Remove</button></td>
    `;
    tr.querySelector(".remove-row-btn").addEventListener("click", () => tr.remove());
    $("symbol-map-rows").appendChild(tr);
  }

  $("add-symbol-row-btn").addEventListener("click", () => addSymbolRow());

  $("save-symbol-map-btn").addEventListener("click", async () => {
    const rows = [...document.querySelectorAll("#symbol-map-rows tr")];
    const symbol_map = {};
    for (const row of rows) {
      const tv = row.querySelector(".sym-tv").value.trim();
      const mt5 = row.querySelector(".sym-mt5").value.trim();
      if (tv && mt5) symbol_map[tv] = mt5;
    }
    try {
      await api("/me/symbol-map", { method: "PUT", body: { symbol_map } });
      toast("Symbol map saved");
      loadDashboard();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("save-risk-settings-btn").addEventListener("click", async () => {
    const body = {};
    const maxQty = $("risk-max-qty").value;
    const maxLoss = $("risk-max-daily-loss").value;
    if (maxQty !== "") body.max_qty_per_order = parseFloat(maxQty);
    if (maxLoss !== "") body.max_daily_loss = parseFloat(maxLoss);
    body.allowed_symbols = $("risk-allowed-symbols")
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      await api("/me/risk-settings", { method: "PUT", body });
      toast("Risk settings saved");
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("engage-kill-switch-btn").addEventListener("click", async () => {
    const reason = $("kill-switch-reason").value.trim();
    try {
      await api("/me/kill-switch", { method: "POST", params: { engaged: "true", reason } });
      toast("Trading paused");
      loadDashboard();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("resume-btn").addEventListener("click", async () => {
    try {
      await api("/me/kill-switch", { method: "POST", params: { engaged: "false" } });
      toast("Trading resumed");
      loadDashboard();
    } catch (err) {
      toast(err.message, true);
    }
  });

  function showRegenReveal(label, value) {
    $("regen-reveal-label").textContent = label;
    $("regen-reveal-value").textContent = value;
    $("regen-reveal").classList.remove("hidden");
  }

  $("regen-passphrase-btn").addEventListener("click", async () => {
    if (!confirm("This invalidates your current webhook passphrase. Continue?")) return;
    try {
      const res = await api("/me/regenerate-webhook-passphrase", { method: "POST" });
      showRegenReveal("New webhook passphrase (shown once)", res.webhook_passphrase);
      toast("Passphrase regenerated");
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("regen-agent-token-btn").addEventListener("click", async () => {
    if (!confirm("This invalidates your current agent token - your running bridge agent will need to reconnect with the new one. Continue?")) return;
    try {
      const res = await api("/me/regenerate-agent-token", { method: "POST" });
      lastKnownAgentToken = res.agent_token;
      showRegenReveal("New agent token (shown once)", res.agent_token);
      $("agent-command").textContent = agentCommand(res.agent_token);
      toast("Agent token regenerated");
    } catch (err) {
      toast(err.message, true);
    }
  });

  function renderTrades(trades) {
    const tbody = $("trades-rows");
    if (!trades || trades.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="muted">No trades yet.</td></tr>';
      return;
    }
    tbody.innerHTML = trades
      .map(
        (t) => `
      <tr>
        <td>${parseUtcIso(t.created_at).toLocaleString()}</td>
        <td>${t.symbol}</td>
        <td>${t.action}</td>
        <td>${t.side || "-"}</td>
        <td>${t.quantity}</td>
        <td><span class="${statusPillClass(t.status)}">${t.status}</span></td>
        <td class="muted">${t.message || ""}</td>
      </tr>`
      )
      .join("");
  }

  $("refresh-trades-btn").addEventListener("click", loadTrades);

  async function loadTrades() {
    try {
      const res = await api("/me/trades", { params: { limit: 50 } });
      renderTrades(res.trades);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function loadDashboard() {
    try {
      const me = await api("/me");
      renderMe(me);
      await loadTrades();
    } catch (err) {
      if (err.status === 401) {
        toast("Session expired - please log in again", true);
        apiKey = null;
        localStorage.removeItem(LS_KEY);
        showAuthView();
      }
    }
  }

  // ---------------------------------------------------------------- boot

  if (apiKey) {
    api("/me")
      .then(() => showDashboardView())
      .catch(() => {
        apiKey = null;
        localStorage.removeItem(LS_KEY);
        showAuthView();
      });
  } else {
    showAuthView();
  }
})();
