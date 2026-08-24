// Jalen's extension worker: the browser end of the bridge.
//
// It holds the Native Messaging port to the Jalen app (through the little
// host process the app registered), receives COMMANDS the app has already
// decided are allowed, carries them out with Chrome's own APIs, and sends
// back a response for each. It never decides an action is safe on its own and
// it never originates a command - the page it runs beside does not get a vote.
//
// Tab and window commands it handles itself. Page commands - reading fields,
// clicking, filling - it runs INSIDE the target tab via chrome.scripting, so
// the work happens with the page's own DOM rather than a screenshot.

const HOST = "com.jalen.bridge";
const PROTOCOL_VERSION = 1;
// Bumped whenever this file changes. Chrome CACHES loaded extension code -
// editing the file does nothing until the extension is reloaded - so a probe
// that cannot see the build number it expects knows the browser is running
// stale code, instead of concluding the feature is broken.
const BUILD = 2;

let port = null;
let reconnectTimer = null;

// --------------------------------------------------------------- connection
//
// AUTO-ACTIVE is the whole requirement here: the moment his Chrome is running
// and Jalen is up, this must connect itself and STAY connected, with no
// manual step ever. Two mechanisms together get there:
//
//   1. A live connectNative port keeps the MV3 service worker alive, so while
//      the app is up the worker does not sleep and the link holds.
//   2. When the app is down (port drops), the worker WILL eventually sleep -
//      MV3's rule, not ours. A chrome.alarm wakes it on a fixed cadence to
//      retry, so the instant the app comes back the link re-forms on its own.
//
// So he never reconnects anything: start Jalen, and within a wake-cycle the
// popup says connected.
function connect() {
  if (port) return;
  try {
    port = chrome.runtime.connectNative(HOST);
  } catch (e) {
    port = null;
    scheduleReconnect();
    return;
  }
  port.onMessage.addListener(onCommand);
  port.onDisconnect.addListener(() => {
    port = null;
    notifyPanel({ __jalen: "connection", connected: false });
    scheduleReconnect();
  });
  // Announce ourselves so the app knows a browser is live.
  send({
    version: PROTOCOL_VERSION,
    request_id: rid(),
    type: "event",
    event: "connected",
    payload: { at: Date.now() },
  });
  notifyPanel({ __jalen: "connection", connected: true });
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, 2000);
}

// Wake-and-retry backstop for when the worker has slept. 0.5 min is the
// smallest period Chrome honours; the connectNative port covers the gaps in
// between while the app is up.
chrome.alarms.create("jalen-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "jalen-keepalive" && !port) connect();
});
// Reconnect on the events that spin a fresh worker up.
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
// Clicking the toolbar icon opens the chat panel (like Claude's extension).
chrome.action.onClicked.addListener((tab) => {
  try { chrome.sidePanel.open({ windowId: tab.windowId }); } catch (e) {}
});

function send(message) {
  if (port) {
    try { port.postMessage(message); } catch (e) { /* dropped on disconnect */ }
  }
}

function rid() {
  return Math.random().toString(36).slice(2, 18);
}

function ok(id, result) {
  send({ version: PROTOCOL_VERSION, request_id: id, type: "response", ok: true, result });
}

function fail(id, code, message) {
  send({ version: PROTOCOL_VERSION, request_id: id, type: "response", ok: false,
         error: { code, message: String(message).slice(0, 300) } });
}

// ----------------------------------------------------------------- dispatch
async function onCommand(msg) {
  if (!msg || msg.type !== "command" || msg.version !== PROTOCOL_VERSION) return;
  const id = msg.request_id;
  const command = msg.command;
  const payload = msg.payload || {};
  try {
    const result = await run(command, payload);
    ok(id, result);
  } catch (e) {
    fail(id, e.code || "COMMAND_FAILED", e.message || String(e));
  }
}

async function activeTab() {
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tabs.length) {
    const err = new Error("no active tab"); err.code = "NO_ACTIVE_TAB"; throw err;
  }
  return tabs[0];
}

async function run(command, payload) {
  switch (command) {
    case "ping":
      return { pong: true, at: Date.now(), build: BUILD };

    case "reload_extension":
      // Lets Jalen pick up its own code changes without him clicking
      // Reload in chrome://extensions. The reply is sent first because
      // reload() tears this worker down immediately.
      setTimeout(() => chrome.runtime.reload(), 200);
      return { reloading: true, from: BUILD };

    case "show_message":
      // The app is speaking into its own chat panel.
      notifyPanel({ __jalen: "message", role: payload.role || "jalen",
                    text: payload.text || "" });
      return { shown: true };

    // ---- tabs / windows -------------------------------------------------
    case "list_tabs": {
      const tabs = await chrome.tabs.query({});
      return tabs.map(t => ({
        id: t.id, title: t.title, url: t.url,
        active: t.active, windowId: t.windowId,
      }));
    }
    case "new_tab": {
      const t = await chrome.tabs.create({ url: payload.url || "about:blank",
                                           active: payload.active !== false });
      return { id: t.id, url: t.url };
    }
    case "focus_tab": {
      await chrome.tabs.update(payload.tabId, { active: true });
      const t = await chrome.tabs.get(payload.tabId);
      await chrome.windows.update(t.windowId, { focused: true });
      return { id: t.id };
    }
    case "navigate": {
      const t = payload.tabId ? await chrome.tabs.get(payload.tabId) : await activeTab();
      await chrome.tabs.update(t.id, { url: payload.url });
      return { id: t.id, url: payload.url };
    }
    case "reload": {
      const t = await activeTab(); await chrome.tabs.reload(t.id); return { id: t.id };
    }
    case "go_back": {
      const t = await activeTab(); await chrome.tabs.goBack(t.id); return { id: t.id };
    }
    case "go_forward": {
      const t = await activeTab(); await chrome.tabs.goForward(t.id); return { id: t.id };
    }
    case "close_tab": {
      await chrome.tabs.remove(payload.tabId); return { closed: payload.tabId };
    }

    // ---- page: run inside the tab's DOM ---------------------------------
    case "get_page_state":
    case "get_form_fields":
    case "get_visible_text":
    case "click":
    case "fill":
    case "select":
    case "check":
    case "press":
    case "scroll":
    case "wait_for": {
      const t = payload.tabId ? { id: payload.tabId } : await activeTab();
      return await inPage(t.id, command, payload);
    }

    default: {
      const err = new Error("unknown command " + command);
      err.code = "UNKNOWN_COMMAND"; throw err;
    }
  }
}

// Run one page operation inside the tab, and surface a clean error when
// Chrome refuses (a page the extension has no host permission for, or a
// restricted chrome:// page). "I can't see that page" is a fact the app can
// act on; a swallowed failure is not.
async function inPage(tabId, command, payload) {
  let results;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId },
      func: pageOp,
      args: [command, payload],
    });
  } catch (e) {
    const err = new Error("Chrome won't let me act on this page: " + e.message);
    err.code = "PAGE_UNAVAILABLE"; throw err;
  }
  const out = results && results[0] && results[0].result;
  if (out && out.__error) {
    const err = new Error(out.__error); err.code = out.__code || "PAGE_ERROR"; throw err;
  }
  return out;
}

connect();

// Push a message to the chat panel if it's open. Best-effort: if no panel is
// listening, chrome.runtime.lastError is set and swallowed.
function notifyPanel(message) {
  try { chrome.runtime.sendMessage(message, () => void chrome.runtime.lastError); }
  catch (e) { /* no panel open */ }
}

// Messages from the extension's OWN surfaces (popup, side panel) - never from
// a web page, which cannot send here. Two things: a status query, and a line
// the user typed into the chat panel.
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || !msg.__jalen) return;
  if (msg.__jalen === "status") {
    reply({ connected: !!port });
    return true;
  }
  if (msg.__jalen === "chat" && msg.text) {
    // Forward to the app as an untrusted user_message event.
    send({
      version: PROTOCOL_VERSION, request_id: rid(), type: "event",
      event: "user_message", payload: { text: String(msg.text).slice(0, 4000) },
    });
    reply({ ok: !!port });
    return true;
  }
});

// ===========================================================================
// EVERYTHING BELOW RUNS IN THE PAGE, injected by chrome.scripting. It must be
// entirely self-contained: no closures over worker state, only its arguments.
// ===========================================================================
function pageOp(command, payload) {
  const CAP = 200;

  function labelFor(el, index) {
    let label = "";
    if (el.id) {
      const tag = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (tag) label = tag.innerText;
    }
    if (!label && el.closest) { const l = el.closest("label"); if (l) label = l.innerText; }
    if (!label) label = el.getAttribute("aria-label") || "";
    if (!label) label = el.getAttribute("placeholder") || "";
    if (!label) label = el.getAttribute("data-placeholder") || "";
    if (!label) label = el.getAttribute("name") || "";
    label = (label || "").replace(/\s+/g, " ").trim().slice(0, 80);
    return label || ((el.type || el.tagName).toLowerCase() + " field " + (index + 1));
  }

  function visible(el) {
    const s = window.getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  // Modern chat UIs - ChatGPT and Gemini among them - use contenteditable
  // divs, NOT <textarea>. Measured on his real signed-in ChatGPT: scanning
  // only input/textarea/select found two file inputs and MISSED the message
  // box entirely, so the agent could see the page and never type into it.
  const EDITABLE = 'input, textarea, select, [contenteditable="true"], [role="textbox"]';

  function fields() {
    const els = Array.from(document.querySelectorAll(EDITABLE));
    const out = [];
    els.forEach((el, i) => {
      const editable = !el.type && (el.isContentEditable ||
                                    el.getAttribute("role") === "textbox");
      const type = editable ? "richtext" : (el.type || el.tagName).toLowerCase();
      if (type === "hidden" || type === "submit" || type === "button") return;
      out.push({
        index: i,
        label: labelFor(el, i),
        type,
        name: (el.getAttribute("name") || "").toLowerCase(),
        autocomplete: (el.getAttribute("autocomplete") || "").toLowerCase(),
        placeholder: el.getAttribute("placeholder") || "",
        required: el.required === true || el.getAttribute("aria-required") === "true",
        disabled: el.disabled === true,
        visible: visible(el),
        options: el.tagName.toLowerCase() === "select"
          ? Array.from(el.options).slice(0, 20).map(o => o.text.trim()) : [],
      });
    });
    return out;
  }

  function nth(i) {
    // MUST match fields()'s selector exactly - an index into a different set
    // is an index into the wrong element, which is how a value lands in a
    // box nobody asked for.
    return document.querySelectorAll(EDITABLE)[i] || null;
  }

  function pick(p) {
    if (typeof p.index === "number") return nth(p.index);
    if (p.selector) return document.querySelector(p.selector);
    if (p.label) {
      const f = fields().find(x => x.label.toLowerCase() === String(p.label).toLowerCase());
      if (f) return nth(f.index);
    }
    return null;
  }

  function err(message, code) { return { __error: message, __code: code || "PAGE_ERROR" }; }

  try {
    switch (command) {
      case "get_page_state": {
        const h = Array.from(document.querySelectorAll("h1, h2, h3"))
          .filter(visible).slice(0, 20).map(e => e.innerText.trim()).filter(Boolean);
        const buttons = Array.from(document.querySelectorAll("button, [role=button]"))
          .filter(visible).slice(0, CAP).map(b => (b.innerText || b.getAttribute("aria-label") || "").trim())
          .filter(Boolean);
        return {
          url: location.href,
          title: document.title,
          headings: h,
          buttons: buttons.slice(0, 40),
          fieldCount: fields().length,
          hasPasswordField: !!document.querySelector('input[type="password"]'),
        };
      }
      case "get_form_fields":
        return { fields: fields() };
      case "get_visible_text": {
        const main = document.querySelector("main, article, [role=main]") || document.body;
        return { text: (main.innerText || "").trim().slice(0, payload.max || 8000) };
      }
      case "click": {
        const el = payload.text
          ? Array.from(document.querySelectorAll("button, a, [role=button]"))
              .find(e => visible(e) && (e.innerText || "").trim().toLowerCase()
                    .includes(String(payload.text).toLowerCase()))
          : pick(payload);
        if (!el) return err("nothing to click matched", "NOT_FOUND");
        el.click();
        return { clicked: true };
      }
      case "fill": {
        const el = pick(payload);
        if (!el) return err("no field matched", "NOT_FOUND");
        const type = (el.type || "").toLowerCase();
        // Defence in depth: a password box is filled ONLY when the app's
        // credential path explicitly says so. An ordinary fill never can.
        if (type === "password" && !payload.allowSecret) {
          return err("refusing to fill a password field", "SECRET_FIELD");
        }
        el.focus();
        if (el.tagName.toLowerCase() === "select") {
          const opt = Array.from(el.options).find(o =>
            o.text.trim() === payload.value || o.value === payload.value);
          if (!opt) return err("no such option", "NOT_FOUND");
          el.value = opt.value;
        } else if (el.isContentEditable || el.getAttribute("role") === "textbox") {
          // A contenteditable has no .value - assigning one silently does
          // NOTHING, which is how a prompt "sent" to ChatGPT arrives empty.
          // insertText goes through the browser's own editing pipeline, so
          // React-style editors see the keystrokes they are listening for;
          // the textContent path is the fallback for editors that don't.
          const sel = window.getSelection();
          const range = document.createRange();
          range.selectNodeContents(el);
          sel.removeAllRanges(); sel.addRange(range);
          let inserted = false;
          try { inserted = document.execCommand("insertText", false, payload.value); }
          catch (e) { inserted = false; }
          if (!inserted) {
            el.textContent = payload.value;
          }
        } else {
          // Native setter, so frameworks that patch .value still see it.
          const proto = el.tagName.toLowerCase() === "textarea"
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, "value");
          if (setter && setter.set) { setter.set.call(el, payload.value); }
          else { el.value = payload.value; }
        }
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return { filled: true, value: el.isContentEditable ? el.textContent : el.value };
      }
      case "select": {
        const el = pick(payload);
        if (!el || el.tagName.toLowerCase() !== "select") return err("no select matched", "NOT_FOUND");
        const opt = Array.from(el.options).find(o =>
          o.text.trim() === payload.value || o.value === payload.value);
        if (!opt) return err("no such option", "NOT_FOUND");
        el.value = opt.value;
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return { selected: opt.text.trim() };
      }
      case "check": {
        const el = pick(payload);
        if (!el) return err("no checkbox matched", "NOT_FOUND");
        el.checked = payload.checked !== false;
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return { checked: el.checked };
      }
      case "press": {
        const el = payload.selector ? document.querySelector(payload.selector) : document.activeElement;
        if (el) el.dispatchEvent(new KeyboardEvent("keydown",
          { key: payload.key, bubbles: true }));
        return { pressed: payload.key };
      }
      case "scroll": {
        if (payload.selector) {
          const el = document.querySelector(payload.selector);
          if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
        } else {
          window.scrollBy(0, payload.y || window.innerHeight);
        }
        return { scrolled: true };
      }
      case "wait_for": {
        // A single check; the app polls with its own bounded loop, so this
        // stays synchronous and cheap rather than holding the page hostage.
        const el = document.querySelector(payload.selector);
        return { present: !!(el && visible(el)) };
      }
      default:
        return err("unknown page command " + command, "UNKNOWN_COMMAND");
    }
  } catch (e) {
    return err(String(e.message || e), "PAGE_EXCEPTION");
  }
}
