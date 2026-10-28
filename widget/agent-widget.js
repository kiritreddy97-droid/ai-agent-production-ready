/* agent-widget.js -- drop-in chat bubble for any website.
 * <script src="agent-widget.js" data-api="https://YOUR-API" data-title="Ask Kirit's AI"></script>
 * Optional: data-key (Bearer token; visible to visitors, so avoid for public sites),
 *           data-color (hex), data-greeting.  */
(function () {
  var s = document.currentScript;
  var API = (s.dataset.api || "http://localhost:8000").replace(/\/$/, "");
  var TITLE = s.dataset.title || "AI Assistant";
  var COLOR = s.dataset.color || "#2563eb";
  var GREET = s.dataset.greeting || "Hi! Ask me anything.";
  var SID = "w-" + Math.random().toString(36).slice(2, 10);
  try { SID = localStorage.getItem("agent_sid") || (localStorage.setItem("agent_sid", SID), SID); } catch (e) {}

  var css = document.createElement("style");
  css.textContent =
    ".aw-b{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;border:0;background:" + COLOR + ";color:#fff;font-size:26px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.3);z-index:99998}" +
    ".aw-p{position:fixed;right:20px;bottom:88px;width:360px;max-width:calc(100vw - 40px);height:480px;max-height:70vh;background:#fff;color:#111;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.3);display:none;flex-direction:column;overflow:hidden;font:14px system-ui,sans-serif;z-index:99999}" +
    ".aw-p.o{display:flex}.aw-h{background:" + COLOR + ";color:#fff;padding:12px 14px;font-weight:600}" +
    ".aw-m{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:8px}" +
    ".aw-x{max-width:85%;padding:8px 11px;border-radius:10px;white-space:pre-wrap;word-wrap:break-word}" +
    ".aw-u{align-self:flex-end;background:" + COLOR + ";color:#fff}.aw-a{align-self:flex-start;background:#f1f1f3}" +
    ".aw-f{display:flex;border-top:1px solid #ddd}.aw-f input{flex:1;border:0;padding:12px;font:inherit;outline:0}" +
    ".aw-f button{border:0;background:none;color:" + COLOR + ";font-weight:600;padding:0 14px;cursor:pointer}";
  document.head.appendChild(css);

  var btn = document.createElement("button"); btn.className = "aw-b"; btn.textContent = "💬"; btn.setAttribute("aria-label", "Open chat");
  var p = document.createElement("div"); p.className = "aw-p";
  p.innerHTML = '<div class="aw-h"></div><div class="aw-m"></div><form class="aw-f"><input placeholder="Type a message…" maxlength="2000"><button>Send</button></form>';
  p.firstChild.textContent = TITLE;
  document.body.append(btn, p);
  var msgs = p.querySelector(".aw-m"), form = p.querySelector("form"), inp = p.querySelector("input");

  function add(cls, text) { var d = document.createElement("div"); d.className = "aw-x " + cls; d.textContent = text; msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; return d; }
  add("aw-a", GREET);
  btn.onclick = function () { p.classList.toggle("o"); if (p.classList.contains("o")) inp.focus(); };

  form.onsubmit = async function (e) {
    e.preventDefault();
    var q = inp.value.trim(); if (!q) return; inp.value = "";
    add("aw-u", q); var out = add("aw-a", "…");
    var headers = { "Content-Type": "application/json" };
    if (s.dataset.key) headers.Authorization = "Bearer " + s.dataset.key;
    try {
      var r = await fetch(API + "/chat/stream", { method: "POST", headers: headers, body: JSON.stringify({ question: q, session_id: SID }) });
      if (!r.ok) { var j = await r.json().catch(function () { return {}; }); out.textContent = "Error: " + (j.detail || r.status); return; }
      var rd = r.body.getReader(), dec = new TextDecoder(), txt = "";
      for (;;) { var c = await rd.read(); if (c.done) break; txt += dec.decode(c.value, { stream: true }); out.textContent = txt; msgs.scrollTop = msgs.scrollHeight; }
    } catch (err) { out.textContent = "Could not reach the assistant."; }
  };
})();
