"""The agent loop: persona + memory + Ollama tool calling."""
import json
import re

from . import patterns, tools, watcher
from .ollama_client import Ollama, OllamaError

PERSONAS = {
    "butler": "You speak like a refined, loyal British butler: polite, composed, lightly witty, never servile. "
              "Address the user as {user}.",
    "friendly": "You are warm, upbeat and casual, like a helpful friend. Call the user {user}.",
    "concise": "You are efficient and to the point. Minimal small talk. Call the user {user}.",
    "witty": "You are clever and dryly sarcastic, but always genuinely helpful. Call the user {user}.",
}

SYSTEM = """You are {name}, a personal AI assistant running entirely on {user}'s Windows PC (private, offline, via Ollama).
{persona}

You are the user's computer butler. You can act on the computer through tools: open/close apps, open websites,
search, control media, manage files and notes, set reminders, run PowerShell (with permission), and perform
SKILLS — tasks you learned by watching the user do them. You also passively notice the user's habits and can
suggest routines.

Rules:
- When the user asks you to DO something, use a tool rather than explaining how. Then confirm briefly.
- Your replies are usually spoken aloud: keep them short (1-3 sentences) unless asked for detail. No markdown tables.
- If a learned skill matches the request, run it with run_skill.
- If the user wants you to learn a task, call start_teaching with a short name for it.
- Use remember for lasting personal facts (preferences, names, schedule). Don't remember trivia.
- Never invent tool results. If something fails, say so plainly.
- NEVER say you opened, closed, started, searched, played or changed anything unless a tool result in THIS turn
  confirms it. Earlier messages describe the past, not the PC's current state: if asked to open an app again,
  call open_app again.
- ALWAYS tell the user the result out loud: after an action, say what happened; after get_weather, look_up,
  system_status, list_files etc., summarize the actual findings conversationally (numbers, names, the answer) in
  1-3 sentences - don't just say "here are the results".
- For questions about weather, facts, people or events, use get_weather / look_up and answer from the result.
- To open a website in a specific browser ("go to facebook in Chrome"), use open_url with the browser argument.
- Current time: {now}

What you know about {user}:
{facts}

Skills you have learned:
{skills}

Active routines:
{routines}
"""


ACTION_RE = re.compile(
    r"\b(open|launch|start|run|close|quit|exit|kill|play|pause|resume|skip|go to|get to|take me to|navigate|browse|visit|search|"
    r"look up|google|youtube|type|remind|timer|alarm|lock|mute|unmute|volume|turn (up|down)|save|note|remember|"
    r"forget|create|make|delete|minimi[sz]e|maximi[sz]e|switch to|bring up|pull up|load|fire up|boot up)\b", re.I)

NUDGE = ("[SYSTEM CHECK] You did not call any tool, so NOTHING happened on the computer. If the user asked you to "
         "do something, call the right tool(s) now. Do not claim it is done unless a tool result confirms it. "
         "If it was only a question or chit-chat, just answer it.")


class Brain:
    def __init__(self, cfg, memory, ctx: tools.Context):
        self.cfg, self.memory, self.ctx = cfg, memory, ctx
        self.ollama = Ollama(cfg["ollama_host"])
        self.supports_tools = True
        self.supports_think_flag = True
        self.cancelled = False
        self._quiet = False

    # ------------------------------------------------------------------ prompt
    def system_prompt(self):
        c = self.cfg
        facts = self.memory.facts(40)
        sk = watcher.load_skills()
        rs = patterns.load_routines().get("routines", [])
        return SYSTEM.format(
            name=c["assistant_name"], user=c["user_name"],
            persona=PERSONAS.get(c["personality"], PERSONAS["butler"]).format(user=c["user_name"]),
            now=tools.get_time(None),
            facts="\n".join(f"- {f}" for f in facts) or "- (nothing yet)",
            skills="\n".join(f"- {s['name']}: {s.get('description', '')}" for s in sk) or "- (none yet)",
            routines="\n".join(f"- {r['type']} {r.get('target') or r.get('targets')} {r.get('at') or r.get('phrase', '')}"
                               for r in rs if r.get("enabled")) or "- (none)",
        )

    # ------------------------------------------------------------------ chat
    def _chat(self, messages, stream=True, model=None):
        kw = {"tools": tools.schemas() if self.supports_tools else None,
              "options": {"temperature": self.cfg["temperature"]}}
        if self.supports_think_flag:
            kw["think"] = False
        try:
            return self._consume(self.ollama.chat(model or self.cfg["model"], messages, stream=stream, **kw))
        except OllamaError as e:
            msg = str(e).lower()
            if "think" in msg and self.supports_think_flag:
                self.supports_think_flag = False
                return self._chat(messages, stream, model)
            if "does not support tools" in msg and self.supports_tools:
                self.supports_tools = False
                return self._chat(messages, stream, model)
            raise

    def _consume(self, gen):
        content, calls = [], []
        for chunk in gen:
            if self.cancelled:
                break
            m = chunk.get("message", {})
            if m.get("content"):
                content.append(m["content"])
                if not self._quiet:
                    self._on_token(m["content"])
            calls.extend(m.get("tool_calls") or [])
        return "".join(content), calls

    def ask(self, text, on_token=None, on_tool=None, images=None, extra_context="", model=None) -> str:
        """Run one user turn to completion. on_token streams reply text; on_tool(name, args, result).

        images: base64 JPEGs attached to this turn (e.g. the game screen); extra_context joins the system prompt.
        """
        self.cancelled = False
        self._on_token = on_token or (lambda t: None)
        wants_action = bool(ACTION_RE.search(text))
        # Don't stream (or speak) a reply to an action request until an action actually ran:
        # otherwise a model that just *claims* "Chrome is open" reaches the user before the truth check.
        self._quiet = wants_action
        history = self.memory.recent_messages(self.cfg["context_messages"])
        system = self.system_prompt() + ("\n\n" + extra_context if extra_context else "")
        user_msg = {"role": "user", "content": text}
        if images:
            user_msg["images"] = images
        messages = [{"role": "system", "content": system}, *history, user_msg]
        self.memory.add_message("user", text)

        final, acted, nudged, fresh = "", False, False, False
        for _round in range(8):
            reply, calls = self._chat(messages, model=model)
            if not calls:
                calls = self._parse_inline_calls(reply)
                if calls:
                    reply = ""
            if not calls:
                if wants_action and not acted and not nudged and not self.cancelled:
                    # Truth check: the user asked for an action but no tool ran, so nothing happened.
                    nudged = True
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({"role": "user", "content": NUDGE})
                    continue
                if wants_action and not acted and not fresh and not self.cancelled:
                    # Still nothing: past conversation can drown out the tools. Retry once without history.
                    fresh = True
                    messages = [{"role": "system", "content": system}, user_msg]
                    continue
                final = reply
                break
            acted = True
            self._quiet = False
            calls = [self._normalize_call(c) for c in calls]
            messages.append({"role": "assistant", "content": reply, "tool_calls": calls})
            self.memory.add_message("assistant", reply, {"tool_calls": calls})
            for c in calls:
                name, args = c["function"]["name"], c["function"]["arguments"]
                result = tools.call(name, args, self.ctx)
                if on_tool:
                    on_tool(name, args, result)
                messages.append({"role": "tool", "content": result, "tool_name": name})
                self.memory.add_message("tool", result, {"tool_name": name})
            if self.cancelled:
                break
        final = re.sub(r"<think>.*?</think>", "", final, flags=re.S).strip()
        if wants_action and not acted and final:
            # Still no action after the nudge and a clean retry - never pass off a made-up success,
            # and don't store this in history (it would become a pattern for the model to copy).
            return "Sorry — I couldn't work out how to do that, so nothing was changed on your PC. Could you rephrase it?"
        if final:
            self.memory.add_message("assistant", final)
        return final

    @staticmethod
    def _normalize_call(c):
        fn = c.get("function", {})
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return {"function": {"name": fn.get("name", ""), "arguments": args}}

    @staticmethod
    def _parse_inline_calls(text):
        """Some small models print the tool call as JSON instead of using the API. Catch that."""
        m = re.search(r"\{\s*\"name\"\s*:\s*\"(\w+)\"\s*,\s*\"(?:arguments|parameters)\"\s*:\s*(\{.*?\})\s*\}", text, re.S)
        if m and m.group(1) in tools.TOOLS:
            try:
                return [{"function": {"name": m.group(1), "arguments": json.loads(m.group(2))}}]
            except json.JSONDecodeError:
                pass
        return []

    def _oneshot(self, messages, temperature):
        """Non-streaming, tool-free completion."""
        kw = {"think": False} if self.supports_think_flag else {}
        try:
            r = self.ollama.chat(self.cfg["model"], messages, stream=False, options={"temperature": temperature}, **kw)
        except OllamaError as e:
            if "think" in str(e).lower() and self.supports_think_flag:
                self.supports_think_flag = False
                return self._oneshot(messages, temperature)
            raise
        return re.sub(r"<think>.*?</think>", "", r.get("message", {}).get("content", ""), flags=re.S)

    # ------------------------------------------------------------------ learning helpers
    def describe_recording(self, name_hint, steps) -> dict:
        """Ask the model to name/describe a recorded task and spot typed text that should be a variable."""
        typed = [s["text"] for s in steps if s["type"] == "type"]
        prompt = (
            "The user just demonstrated a task on their PC so you can repeat it later. Steps:\n"
            f"{watcher.describe_steps(steps)}\n\n"
            f"The user called it: \"{name_hint}\".\n"
            "Reply with ONLY JSON: {\"name\": short name, \"description\": one sentence on what it does, "
            "\"variables\": {\"<exact SUBSTRING of a typed text that would change each time>\": \"<variable_name>\"}}. "
            "Use the smallest substring that changes (e.g. in 'weather in Austin' only 'Austin' -> city). "
            "Only include clearly changing inputs (search terms, recipients, file names). "
            f"Typed texts were: {json.dumps(typed[:20])}"
        )
        try:
            txt = self._oneshot([{"role": "user", "content": prompt}], 0.2)
            data = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
            return {"name": data.get("name") or name_hint, "description": data.get("description", ""),
                    "variables": data.get("variables") or {}}
        except Exception:
            return {"name": name_hint, "description": f"Recorded task with {len(steps)} steps.", "variables": {}}

    def greeting(self):
        try:
            return self._oneshot([
                {"role": "system", "content": self.system_prompt()},
                {"role": "user", "content": "(The user just started their session. Greet them in one short sentence "
                                            "appropriate to the time of day. No tools.)"}], 0.8).strip()
        except Exception:
            return f"Good day, {self.cfg['user_name']}. {self.cfg['assistant_name']} at your service."
