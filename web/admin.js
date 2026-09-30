(() => {
  "use strict";

  const LS_KEY = "dataveda_admin_key";
  // Deliberately not persisted (no localStorage) - every visit requires
  // logging in again rather than auto-resuming a saved session, and this
  // key in particular (the platform owner's master key) shouldn't sit in
  // browser storage. LS_KEY is only used to clear out any key a
  // pre-existing browser saved before this behavior changed.
  let adminKey = null;
  localStorage.removeItem(LS_KEY);
  let tenantsCache = [];

  const $ = (id) => document.getElementById(id);

  function toast(message, isError = false) {
    const el = $("toast");
    el.textContent = message;
    el.classList.remove("hidden");
    el.style.background = isError ? "#dc2626" : "#1a1d23";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 3500);
  }

  function parseUtcIso(isoString) {
    if (!isoString) return null;
    const withZ = /[Zz]|[+-]\d\d:\d\d$/.test(isoString) ? isoString : `${isoString}Z`;
    return new Date(withZ);
  }

  async function api(path, { method = "GET", body } = {}) {
    const resp = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "X-Admin-Key": adminKey || "" },
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

  $("reset-modal-close-btn").addEventListener("click", () => {
    $("reset-modal").classList.add("hidden");
  });

  function showAuthView() {
    $("auth-view").classList.remove("hidden");
    $("admin-view").classList.add("hidden");
    $("topbar-account").classList.add("hidden");
  }

  function showAdminView() {
    $("auth-view").classList.add("hidden");
    $("admin-view").classList.remove("hidden");
    $("topbar-account").classList.remove("hidden");
    loadTenants();
  }

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("auth-error").classList.add("hidden");
    adminKey = $("login-key").value.trim();
    try {
      await api("/admin/tenants?limit=1");
      showAdminView();
    } catch (err) {
      adminKey = null;
      $("auth-error").textContent = "That admin key isn't valid.";
      $("auth-error").classList.remove("hidden");
    }
  });

  $("logout-btn").addEventListener("click", () => {
    adminKey = null;
    showAuthView();
  });

  function subscriptionPill(t) {
    if (t.subscription_active) {
      const until = parseUtcIso(t.subscription_expires_at).toLocaleDateString();
      if (t.subscription_expiring_soon) {
        const days = t.subscription_days_remaining;
        return `<span class="status-pill status-error">Expires in ${days} day${days === 1 ? "" : "s"} (${until})</span>`;
      }
      return `<span class="status-pill status-accepted">Active until ${until}</span>`;
    }
    if (t.subscription_expires_at) {
      const on = parseUtcIso(t.subscription_expires_at).toLocaleDateString();
      return `<span class="status-pill status-rejected">Expired ${on}</span>`;
    }
    return `<span class="status-pill status-rejected">Never activated</span>`;
  }

  function renderTenants(tenants) {
    const tbody = $("tenants-rows");
    if (tenants.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="muted">No tenants match.</td></tr>';
      return;
    }
    tbody.innerHTML = tenants
      .map(
        (t) => `
      <tr data-tenant-id="${t.tenant_id}">
        <td>${t.email}</td>
        <td>${subscriptionPill(t)}</td>
        <td>${t.kill_switch_engaged ? '<span class="status-pill status-rejected">Paused</span>' : '<span class="status-pill status-accepted">Active</span>'}</td>
        <td class="muted">${parseUtcIso(t.created_at).toLocaleDateString()}</td>
        <td>
          <div class="field-row" style="margin-top:0">
            <input type="number" class="grant-days" value="30" min="1" style="width:70px" />
            <button class="btn btn-sm btn-primary grant-btn">Grant</button>
          </div>
        </td>
        <td>
          <div class="field-row" style="margin-top:0">
            <button class="btn btn-sm btn-ghost revoke-btn">Revoke</button>
            <button class="btn btn-sm btn-ghost reset-creds-btn">Reset credentials</button>
          </div>
        </td>
      </tr>`
      )
      .join("");

    tbody.querySelectorAll(".grant-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const row = btn.closest("tr");
        const tenantId = row.dataset.tenantId;
        const days = parseInt(row.querySelector(".grant-days").value, 10) || 30;
        try {
          await api(`/admin/tenants/${tenantId}/subscription`, { method: "POST", body: { days } });
          toast(`Granted ${days} day(s)`);
          loadTenants();
        } catch (err) {
          toast(err.message, true);
        }
      });
    });

    tbody.querySelectorAll(".revoke-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const row = btn.closest("tr");
        const tenantId = row.dataset.tenantId;
        const email = row.children[0].textContent;
        if (!(await confirmDialog(`Revoke ${email}'s subscription immediately?`, { title: "Revoke subscription?", confirmLabel: "Revoke", danger: true }))) return;
        try {
          await api(`/admin/tenants/${tenantId}/revoke-subscription`, { method: "POST" });
          toast("Revoked");
          loadTenants();
        } catch (err) {
          toast(err.message, true);
        }
      });
    });

    tbody.querySelectorAll(".reset-creds-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const row = btn.closest("tr");
        const tenantId = row.dataset.tenantId;
        const email = row.children[0].textContent;
        if (
          !(await confirmDialog(
            `Reset ${email}'s credentials? Their current api_key, webhook passphrase, and agent token stop working immediately.`,
            { title: "Reset credentials?", confirmLabel: "Reset credentials", danger: true }
          ))
        )
          return;
        try {
          const result = await api(`/admin/tenants/${tenantId}/reset-credentials`, { method: "POST" });
          $("reset-api-key").textContent = result.api_key;
          $("reset-passphrase").textContent = result.webhook_passphrase;
          $("reset-agent-token").textContent = result.agent_token;
          $("reset-modal").classList.remove("hidden");
        } catch (err) {
          toast(err.message, true);
        }
      });
    });
  }

  function applyFilters() {
    const q = $("search-box").value.trim().toLowerCase();
    const expiringOnly = $("expiring-soon-filter").checked;
    let filtered = tenantsCache;
    if (q) filtered = filtered.filter((t) => t.email.toLowerCase().includes(q));
    if (expiringOnly) filtered = filtered.filter((t) => t.subscription_expiring_soon);
    renderTenants(filtered);
  }

  $("search-box").addEventListener("input", applyFilters);
  $("expiring-soon-filter").addEventListener("change", applyFilters);

  $("refresh-btn").addEventListener("click", loadTenants);

  async function loadTenants() {
    try {
      const res = await api("/admin/tenants?limit=200");
      tenantsCache = res.tenants;
      applyFilters();
    } catch (err) {
      if (err.status === 401) {
        adminKey = null;
        showAuthView();
      } else {
        toast(err.message, true);
      }
    }
  }

  showAuthView();
})();
