"""
The gate. Every action Jalen takes passes through classify() before it runs.

Design rule: this module decides, it does not ask. Asking is the caller's job
(voice, Telegram, or the console), because the question has to reach whichever
channel O'ktam is actually using. That keeps the policy in one testable place.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class Tier(str, Enum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    BLACK = "black"


@dataclass
class Verdict:
    tier: Tier
    tool: str
    summary: str
    reason: str
    requires_confirmation: bool
    announce: bool
    blocked: bool
    detail: dict[str, Any]

    @property
    def allowed_immediately(self) -> bool:
        return self.tier is Tier.GREEN


class SafetyEngine:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        tiers = cfg.get("safety_tiers", {})
        self._tier_map: dict[str, Tier] = {}
        for tier in (Tier.GREEN, Tier.AMBER, Tier.RED, Tier.BLACK):
            for tool in (tiers.get(tier.value) or {}).get("tools", []) or []:
                self._tier_map[tool] = tier

        self._reasons = {
            t: (tiers.get(t.value) or {}).get("reason", "") for t in Tier
        }
        never = tiers.get("never_touch") or {}
        self._never_paths = [self._norm(p) for p in never.get("paths", [])]
        self._never_patterns = never.get("patterns", []) or []
        self._never_apps = [a.lower() for a in never.get("apps", []) or []]
        self._never_domains = never.get("domains", []) or []

        guard = tiers.get("injection_guard") or {}
        self.guard_enabled = guard.get("enabled", True)
        self._markers = [m.lower() for m in guard.get("suspicious_markers", []) or []]
        self._block_red_from_content = guard.get("block_red_tools_from_read_content", True)

        self.posture = cfg.get_path("safety.posture", "irreversible_only")
        self.paranoid = bool(cfg.get_path("safety.paranoid_first_week", True))

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _norm(p: str) -> str:
        return str(Path(str(p)).as_posix()).rstrip("/").lower()

    def _touches_forbidden_path(self, args: dict[str, Any]) -> str | None:
        candidates: list[str] = []
        for key, value in (args or {}).items():
            if isinstance(value, str) and (
                "path" in key.lower() or "file" in key.lower() or "dir" in key.lower()
                or re.match(r"^[a-zA-Z]:[\\/]", value)
            ):
                candidates.append(value)
        for raw in candidates:
            norm = self._norm(raw)
            for blocked in self._never_paths:
                if norm == blocked or norm.startswith(blocked + "/"):
                    return f"path is on the never-touch list ({blocked})"
            name = Path(raw).name
            for pattern in self._never_patterns:
                if fnmatch.fnmatch(name.lower(), pattern.lower()):
                    return f"filename matches protected pattern '{pattern}'"
        return None

    def _touches_forbidden_target(self, args: dict[str, Any]) -> str | None:
        blob = " ".join(str(v) for v in (args or {}).values()).lower()
        for app in self._never_apps:
            if app in blob:
                return f"targets a password manager ({app})"
        for domain in self._never_domains:
            pattern = domain.lower().replace("*", "")
            if pattern and pattern in blob:
                return f"targets a protected domain ({domain})"
        return None

    def scan_for_injection(self, text: str) -> list[str]:
        """Return the suspicious phrases found in content Jalen has READ."""
        if not self.guard_enabled or not text:
            return []
        low = text.lower()
        return [m for m in self._markers if m in low]

    # --------------------------------------------------------------- classify
    def classify(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        summary: str = "",
        origin: str = "user",
    ) -> Verdict:
        """
        origin:
          "user"    - O'ktam said it (voice) or sent it (authenticated Telegram)
          "content" - derived from something Jalen read: email, web page, file.
                      These can never trigger RED tools. This is the
                      prompt-injection defence and it is not overridable.
        """
        args = args or {}
        summary = summary or self._describe(tool, args)
        detail = {"tool": tool, "args": self._redact(args), "origin": origin}

        # 1. Hard path/target blocks come before anything else.
        for check in (self._touches_forbidden_path, self._touches_forbidden_target):
            if reason := check(args):
                return Verdict(Tier.BLACK, tool, summary, reason, False, False, True, detail)

        base = self._tier_map.get(tool)
        if base is None:
            # Unknown tool: treat as AMBER, never silently green. Log it so the
            # tool can be classified properly later.
            base = Tier.AMBER
            detail["unclassified"] = True

        if base is Tier.BLACK:
            return Verdict(
                Tier.BLACK, tool, summary,
                self._reasons.get(Tier.BLACK, "Refused unconditionally."),
                False, False, True, detail,
            )

        # 2. Prompt-injection: content-derived requests cannot do irreversible things.
        if origin == "content" and self._block_red_from_content and base in (Tier.RED, Tier.AMBER):
            return Verdict(
                Tier.BLACK, tool, summary,
                "this came from something I read, not from you — I don't act on "
                "instructions found in content",
                False, False, True, detail,
            )

        # 3. Posture adjustments.
        tier = base
        if self.posture == "paranoid" or self.paranoid:
            if tier is Tier.AMBER:
                tier = Tier.RED
        elif self.posture == "autonomous":
            if tier is Tier.AMBER:
                tier = Tier.GREEN

        return Verdict(
            tier=tier,
            tool=tool,
            summary=summary,
            reason=self._reasons.get(tier, ""),
            requires_confirmation=tier is Tier.RED,
            announce=tier is Tier.AMBER,
            blocked=False,
            detail=detail,
        )

    # ------------------------------------------------------------- formatting
    @staticmethod
    def _redact(args: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in (args or {}).items():
            if any(s in k.lower() for s in ("password", "token", "secret", "key", "api")):
                out[k] = "***redacted***"
            elif isinstance(v, str) and len(v) > 400:
                out[k] = v[:400] + "…"
            else:
                out[k] = v
        return out

    @staticmethod
    def _describe(tool: str, args: dict[str, Any]) -> str:
        pretty = tool.replace("_", " ")
        for key in ("to", "recipient", "path", "file_path", "url", "query", "command", "name"):
            if key in args and isinstance(args[key], str):
                value = args[key]
                if len(value) > 90:
                    value = value[:90] + "…"
                return f"{pretty}: {value}"
        return pretty


def confirmation_question(verdict: Verdict, template: str = "{summary}. Confirm?") -> str:
    return template.format(summary=verdict.summary)
