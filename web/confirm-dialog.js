// Shared styled confirm dialog, replacing the native browser confirm()
// popup (which looks out of place next to the rest of the app's design).
// Used by both app.js and admin.js.
(() => {
  "use strict";

  const backdrop = document.createElement("div");
  backdrop.id = "confirm-dialog";
  backdrop.className = "modal-backdrop hidden";
  backdrop.innerHTML = `
    <div class="card modal" style="width:380px">
      <h2 id="confirm-dialog-title">Are you sure?</h2>
      <p id="confirm-dialog-message" class="hint" style="margin-top:10px;font-size:14px"></p>
      <div class="field-row" style="margin-top:20px;justify-content:flex-end">
        <button id="confirm-dialog-cancel" class="btn btn-ghost">Cancel</button>
        <button id="confirm-dialog-ok" class="btn btn-primary">Confirm</button>
      </div>
    </div>
  `;
  document.body.appendChild(backdrop);

  let resolvePromise = null;

  function close(result) {
    backdrop.classList.add("hidden");
    if (resolvePromise) {
      resolvePromise(result);
      resolvePromise = null;
    }
  }

  // message: the question to ask. options.title overrides the heading,
  // options.confirmLabel overrides the confirm button text, and
  // options.danger switches it to the red/destructive button style.
  window.confirmDialog = function confirmDialog(message, options = {}) {
    const { title = "Are you sure?", confirmLabel = "Confirm", danger = false } = options;
    return new Promise((resolve) => {
      resolvePromise = resolve;
      backdrop.querySelector("#confirm-dialog-title").textContent = title;
      backdrop.querySelector("#confirm-dialog-message").textContent = message;
      const okBtn = backdrop.querySelector("#confirm-dialog-ok");
      okBtn.textContent = confirmLabel;
      okBtn.className = "btn " + (danger ? "btn-danger" : "btn-primary");
      backdrop.classList.remove("hidden");
    });
  };

  backdrop.addEventListener("click", (e) => {
    if (e.target.id === "confirm-dialog-ok") close(true);
    else if (e.target.id === "confirm-dialog-cancel") close(false);
    else if (e.target === backdrop) close(false);
  });
})();
