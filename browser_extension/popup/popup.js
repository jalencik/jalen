// The popup asks the worker whether the native port is up, and shows the
// current tab's domain. Read-only: the popup never drives anything, so a
// page cannot reach the app through it.
async function refresh() {
  const dot = document.getElementById("dot");
  const conn = document.getElementById("conn");
  const page = document.getElementById("page");

  try {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tabs.length && tabs[0].url) {
      try { page.textContent = new URL(tabs[0].url).hostname || "—"; }
      catch (e) { page.textContent = "—"; }
    }
  } catch (e) { /* leave as — */ }

  // The worker answers a status request over runtime messaging.
  chrome.runtime.sendMessage({ __jalen: "status" }, (resp) => {
    const up = resp && resp.connected;
    dot.className = "dot " + (up ? "up" : "down");
    conn.textContent = up ? "connected" : "not connected";
    if (chrome.runtime.lastError) {
      conn.textContent = "not connected";
      dot.className = "dot down";
    }
  });
}

refresh();
