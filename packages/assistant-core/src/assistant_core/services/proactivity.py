"""Local, bounded event coordination. Never executes model-generated actions."""

import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel, Field
from typing import Literal


class ProactivityRequest(BaseModel):
    operation: Literal["snapshot", "settings", "goal", "complete", "event", "tick", "feedback", "clear"] = "snapshot"
    enabled: bool | None = None
    research: bool | None = None
    review_minutes: int | None = Field(default=None, ge=5, le=240)
    title: str = Field(default="", max_length=240)
    app: str = Field(default="", max_length=120)
    url: str = Field(default="", max_length=2048)
    item_id: str = Field(default="", max_length=80)
    accepted: bool = False


class ProactivityService:
    def __init__(self, path: str | Path, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.Lock()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        self.path.chmod(0o600)

    def handle(self, payload: dict) -> dict:
        request = ProactivityRequest.model_validate(payload)
        with self.lock, closing(sqlite3.connect(self.path)) as db, db:
            if request.operation != "snapshot":
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM state WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else {
                "enabled": False, "research": False, "review_minutes": 30,
                "goals": [], "activity": [], "suggestions": [], "links": [],
                "active_app": "", "last_review": 0, "dismissals": 0,
            }
            now = self.clock()
            op = request.operation
            if op == "snapshot":
                return state
            if op == "settings":
                for key in ("enabled", "research", "review_minutes"):
                    value = getattr(request, key)
                    if value is not None:
                        state[key] = value
                state["last_review"] = now
                self._record(state, "Preferences updated", "Background awareness is on." if state["enabled"] else "Background awareness is paused.", now)
            elif op == "goal":
                title = request.title.strip()
                if not title:
                    raise ValueError("Give the goal a title.")
                if len(state["goals"]) >= 30:
                    raise ValueError("Complete a goal before adding more (limit: 30).")
                state["goals"].append({"id": str(uuid4()), "title": title})
                state["last_review"] = now
                self._record(state, "Goal saved", title, now)
            elif op == "complete":
                goal = next((g for g in state["goals"] if g["id"] == request.item_id), None)
                if goal is None:
                    raise ValueError("That goal no longer exists.")
                state["goals"].remove(goal)
                self._record(state, "Goal completed", goal["title"], now)
            elif op == "event" and state["enabled"]:
                # Private browsing/apps can be excluded by the native collector; no pixels/audio here.
                if request.app and request.app != state["active_app"]:
                    state["active_app"] = request.app
                    self._record(state, "App changed", request.app, now)
                if state["research"] and request.url:
                    parts = urlsplit(request.url)
                    if parts.scheme in {"https", "http"} and parts.hostname and not parts.username and not parts.password:
                        # Drop query strings/fragments, which may contain credentials or personal searches.
                        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
                        if not any(link["url"] == url for link in state["links"]):
                            state["links"].append({"id": str(uuid4()), "title": request.title or parts.hostname, "url": url})
                            state["links"] = state["links"][-100:]
                            self._record(state, "Research link saved", request.title or parts.hostname, now)
            elif op == "tick" and state["enabled"]:
                cooldown = state["review_minutes"] * 60 * (1 + min(state["dismissals"], 3))
                if state["goals"] and now - state["last_review"] >= cooldown and not state["suggestions"]:
                    state["suggestions"].append({"id": str(uuid4()), "title": "Ready for a quick goal check?", "detail": state["goals"][0]["title"]})
                    state["last_review"] = now
                    self._record(state, "Review suggested", state["goals"][0]["title"], now)
            elif op == "feedback":
                suggestion = next((s for s in state["suggestions"] if s["id"] == request.item_id), None)
                if suggestion is None:
                    raise ValueError("That suggestion no longer exists.")
                state["suggestions"].remove(suggestion)
                state["dismissals"] = 0 if request.accepted else min(state["dismissals"] + 1, 3)
                state["last_review"] = now
                self._record(state, "Review accepted" if request.accepted else "Suggestion dismissed", suggestion["detail"], now)
            elif op == "clear":
                state.update(activity=[], links=[], suggestions=[], active_app="")
            db.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (json.dumps(state),))
            return state

    @staticmethod
    def _record(state, title, detail, now):
        state["activity"].insert(0, {"id": str(uuid4()), "title": title, "detail": detail, "time": now})
        state["activity"] = state["activity"][:80]
