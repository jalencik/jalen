// The chat panel. Sends what the user types to the worker (which forwards it
// to the Jalen app as an untrusted user_message), and shows what the app
// pushes back via show_message. The panel never touches a web page and a web
// page can never reach it, so this is a private line between him and Jalen.

const log = document.getElementById("log");
const hint = document.getElementById("hint");
const form = document.getElementById("f");
const box = document.getElementById("t");
const dot = document.getElementById("dot");
const stat = document.getElementById("stat");

function add(role, text) {
  if (hint) { hint.remove(); }
  const div = document.createElement("div");
  div.className = "msg " + (role === "me" ? "me" : "jalen");
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function setConnected(up) {
  dot.className = "dot" + (up ? " up" : "");
  stat.textContent = up ? "connected" : "not connected";
}

// Auto-grow the input, and submit on Enter (Shift+Enter for a newline).
box.addEventListener("input", () => {
  box.style.height = "auto";
  box.style.height = Math.min(box.scrollHeight, 120) + "px";
});
box.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = box.value.trim();
  if (!text) return;
  add("me", text);
  box.value = ""; box.style.height = "auto";
  chrome.runtime.sendMessage({ __jalen: "chat", text }, (resp) => {
    if (chrome.runtime.lastError || !resp || !resp.ok) {
      add("jalen", "I'm not connected to the Jalen app right now — start Jalen "
        + "and I'll pick this up.");
    }
  });
});

// Messages pushed from the worker: Jalen's replies, and connection changes.
chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || !msg.__jalen) return;
  if (msg.__jalen === "message") add(msg.role === "me" ? "me" : "jalen", msg.text);
  if (msg.__jalen === "connection") setConnected(msg.connected);
});

// Ask the worker for the current connection state on open.
chrome.runtime.sendMessage({ __jalen: "status" }, (resp) => {
  setConnected(resp && resp.connected);
});
