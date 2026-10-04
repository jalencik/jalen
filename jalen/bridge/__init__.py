"""
The bridge to Jalen's own Chrome extension.

WHY THIS EXISTS, AND WHY IT IS NOT PLAYWRIGHT
---------------------------------------------
Everything under jalen/tools/webagent.py drives Chrome from OUTSIDE - a
plain chrome.exe attached over CDP. That works, and it is his account, but
it is a SEPARATE Chrome from the one he browses in, because Chrome 136+
refuses to let an outside program automate the profile you use every day
(a deliberate anti-cookie-theft control - measured: 150s timeout on the real
profile, 0.8s on a dedicated one).

An extension is the other side of that wall. It runs INSIDE his ordinary
Chrome, where Chrome already trusts it, so it can read and act on the very
tabs he is looking at - the thing the external driver cannot do. The Claude
extension works this way for exactly this reason.

THE SHAPE
---------
    his Chrome  <->  Jalen extension  <->  native_host.py  <->  Jalen app
                     (content+worker)      (Chrome stdio)       (this brain)

The extension speaks Chrome's Native Messaging protocol to a small host
process Chrome launches. That host is a thin relay: it forwards to the
long-running Jalen app over a loopback socket, because a per-connection
subprocess has no other way to reach the brain that holds Gmail, the vault
and the safety tiers.

WHAT STAYS AUTHORITATIVE
------------------------
The Python side decides. The page never does. A website cannot send a
message that says "here is a secret, type it" - the safety layer in the app
classifies every action, exactly as it does for the CDP path, and the
extension only ever executes an action the app authorised. See protocol.py
for the message contract and server.py for where authority lives.
"""
