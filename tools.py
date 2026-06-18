"""
tools.py — Agent Tool Registry
================================
Author  : Kirit Reddy Daida
Project : ai-agent-production-ready

Every tool:
  1. Defined as JSON schema (sent to OpenAI function-calling)
  2. Implemented as a Python function
  3. Registered in TOOL_REGISTRY
  4. Called via execute_tool(name, args)

Built-in tools
  calculator      safe math expression evaluator (no exec, ast-only)
  web_search      DuckDuckGo search, no API key needed
  python_executor sandboxed Python code runner
  data_analyst    CSV/JSON statistics summary
  file_reader     read local text files safely
  datetime_tool   current date/time/timezone
  weather         OpenWeatherMap current weather (optional key)

Adding a new tool:
  1. Write: def my_tool(arg: str) -> str
  2. Add JSON schema to TOOL_SCHEMAS
  3. Add to TOOL_REGISTRY
  Agent discovers it automatically.
"""

from __future__ import annotations

import ast
import contextlib
import csv
import datetime
import io
import json
import logging
import math
import os
import re
import textwrap
import traceback
from typing import Callable, Dict

import requests

from config import settings

log = logging.getLogger("tools")


# ─── TOOL 1: Calculator ───────────────────────────────────────────────────────

def calculator(expression: str) -> str:
    """Safely evaluate a math expression using Python ast (no code injection)."""
    SAFE = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    SAFE.update({"abs": abs, "round": round, "min": min, "max": max,
                 "sum": sum, "pow": pow, "len": len})
    expr = expression.strip()
    # Validate function names
    funcs = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*(?=\s*\()", expr)
    bad   = [f for f in funcs if f not in SAFE]
    if bad:
        return f"Error: unknown functions {bad}"
    try:
        tree   = ast.parse(expr, mode="eval")
        result = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, SAFE)
        return str(result)
    except ZeroDivisionError:
        return "Error: division by zero"
    except Exception as e:
        return f"Math error: {e}"


# ─── TOOL 2: Web Search ───────────────────────────────────────────────────────

def web_search(query: str, max_results: int = 5) -> str:
    """Search internet via DuckDuckGo Instant Answer API. No API key needed."""
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=10,
        )
        resp.raise_for_status()
        data    = resp.json()
        results = []
        if data.get("AbstractText"):
            results.append(f"**Summary:** {data['AbstractText']}")
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(f"• {topic['Text']}")
        if results:
            return "\n".join(results)
        # Fallback
        return _ddg_fallback(query, max_results)
    except Exception as e:
        return f"Search error: {e}"


def _ddg_fallback(query: str, max_results: int = 5) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (AI Agent)"}
        resp    = requests.get("https://html.duckduckgo.com/html/",
                               params={"q": query}, headers=headers, timeout=10)
        snippets = re.findall(r'class="result__snippet"[^>]*>([^<]+)', resp.text)
        return "\n\n".join(snippets[:max_results]) or f"No results for: {query}"
    except Exception as e:
        return f"Search fallback error: {e}"


# ─── TOOL 3: Python Executor (sandboxed) ──────────────────────────────────────

_BLOCKED = {"os","sys","subprocess","socket","shutil","pathlib","importlib",
            "ctypes","multiprocessing","threading"}

def python_executor(code: str, timeout_s: int = 10) -> str:
    """Run Python in a restricted sandbox. Filesystem/network/subprocess blocked."""
    imports = re.findall(r"(?:import|from)\s+([\w.]+)", code)
    blocked = [m for m in imports if m.split(".")[0] in _BLOCKED]
    if blocked:
        return f"Blocked modules: {blocked}"

    out_buf = io.StringIO()
    lvars: dict = {}
    safe_builtins = {
        "print":print,"range":range,"len":len,"str":str,"int":int,"float":float,
        "bool":bool,"list":list,"dict":dict,"tuple":tuple,"set":set,"type":type,
        "isinstance":isinstance,"enumerate":enumerate,"zip":zip,"map":map,
        "filter":filter,"sorted":sorted,"reversed":reversed,"min":min,"max":max,
        "sum":sum,"abs":abs,"round":round,"pow":pow,"divmod":divmod,
        "any":any,"all":all,"repr":repr,"format":format,
    }
    safe_globals = {"__builtins__": safe_builtins, "math": math,
                    "json": json, "datetime": datetime, "re": re}
    try:
        with contextlib.redirect_stdout(out_buf):
            exec(compile(textwrap.dedent(code), "<sandbox>", "exec"),
                 safe_globals, lvars)
    except Exception:
        return f"Execution error:\n{traceback.format_exc(limit=3)}"

    output  = out_buf.getvalue().strip()
    result  = lvars.get("result") or lvars.get("output") or lvars.get("answer")
    parts   = []
    if output:
        parts.append(f"Output:\n{output}")
    if result is not None:
        parts.append(f"Result: {result}")
    return "\n".join(parts) or "Executed (no output)."


# ─── TOOL 4: Data Analyst ─────────────────────────────────────────────────────

def data_analyst(data: str, data_format: str = "json") -> str:
    """Analyse CSV or JSON data, return descriptive statistics."""
    import statistics as stat
    try:
        if data_format.lower() == "csv":
            rows = list(csv.DictReader(io.StringIO(data)))
        else:
            parsed = json.loads(data)
            rows   = parsed if isinstance(parsed, list) else [parsed]
    except Exception as e:
        return f"Parse error: {e}"

    if not rows:
        return "Dataset is empty."

    n    = len(rows)
    cols = list(rows[0].keys())
    out  = [f"**Records:** {n}  |  **Columns:** {len(cols)}", ""]

    for col in cols:
        vals = [r.get(col) for r in rows if r.get(col) is not None]
        out.append(f"**{col}** ({len(vals)} non-null)")
        nums = []
        for v in vals:
            try: nums.append(float(v))
            except: pass
        if nums and len(nums) > len(vals) * 0.5:
            out += [f"  min={min(nums):.4g}  max={max(nums):.4g}  "
                    f"mean={stat.mean(nums):.4g}  median={stat.median(nums):.4g}"
                    + (f"  std={stat.stdev(nums):.4g}" if len(nums)>1 else "")]
        else:
            uniq = sorted(set(str(v) for v in vals))
            out += [f"  unique={len(uniq)}" +
                    (f"  values={', '.join(uniq[:8])}" if len(uniq)<=10 else "")]
        out.append("")
    return "\n".join(out)


# ─── TOOL 5: File Reader ──────────────────────────────────────────────────────

_ALLOWED_EXT = {".txt",".csv",".json",".md",".py",".yaml",".yml",".log",".html",".xml"}

def file_reader(filepath: str, max_chars: int = 4000) -> str:
    """Read a local text file safely (restricted to cwd, allowed extensions)."""
    real = os.path.realpath(filepath)
    ext  = os.path.splitext(real)[1].lower()
    if ext not in _ALLOWED_EXT:
        return f"Extension '{ext}' not allowed. Use: {sorted(_ALLOWED_EXT)}"
    if not real.startswith(os.path.realpath(os.getcwd())):
        return "Access denied: path outside working directory."
    if not os.path.isfile(real):
        return f"File not found: {filepath}"
    try:
        with open(real, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars)
        size = os.path.getsize(real)
        hdr  = f"File: {filepath} ({size:,} bytes)\n{'─'*40}\n"
        ftr  = f"\n[truncated {size-max_chars:,} chars]" if size > max_chars else ""
        return hdr + content + ftr
    except Exception as e:
        return f"Read error: {e}"


# ─── TOOL 6: Datetime ─────────────────────────────────────────────────────────

def datetime_tool(action: str = "now", timezone: str = "UTC") -> str:
    """Return current date/time information."""
    try:
        import zoneinfo
        tz  = zoneinfo.ZoneInfo(timezone)
        now = datetime.datetime.now(tz)
    except Exception:
        now = datetime.datetime.utcnow()
    actions = {
        "now":       now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "date":      now.strftime("%A, %B %d, %Y"),
        "time":      now.strftime("%I:%M %p %Z"),
        "utc":       datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timestamp": str(int(now.timestamp())),
        "weekday":   now.strftime("%A"),
        "iso":       now.isoformat(),
    }
    return actions.get(action.lower(), f"Unknown action: {action}. Options: {list(actions)}")


# ─── TOOL 7: Weather ──────────────────────────────────────────────────────────

def weather(city: str, units: str = "metric") -> str:
    """Get current weather via OpenWeatherMap (add WEATHER_API_KEY to .env)."""
    key = settings.WEATHER_API_KEY
    if not key:
        return "Set WEATHER_API_KEY in .env. Free tier: https://openweathermap.org/api"
    syms = {"metric": "°C", "imperial": "°F", "standard": "K"}
    sym  = syms.get(units, "°C")
    try:
        r = requests.get("https://api.openweathermap.org/data/2.5/weather",
                         params={"q": city, "appid": key, "units": units}, timeout=10)
        if r.status_code == 404:
            return f"City not found: {city}"
        r.raise_for_status()
        d = r.json()
        return (
            f"**{d['name']}, {d['sys']['country']}** — {d['weather'][0]['description'].title()}\n"
            f"Temp: {d['main']['temp']}{sym} (feels {d['main']['feels_like']}{sym}) | "
            f"Humidity: {d['main']['humidity']}% | Wind: {d['wind']['speed']} m/s"
        )
    except Exception as e:
        return f"Weather error: {e}"


# ─── Registry & Schemas ───────────────────────────────────────────────────────

TOOL_REGISTRY: Dict[str, Callable] = {
    "calculator":      calculator,
    "web_search":      web_search,
    "python_executor": python_executor,
    "data_analyst":    data_analyst,
    "file_reader":     file_reader,
    "datetime_tool":   datetime_tool,
    "weather":         weather,
}


def execute_tool(name: str, args: dict) -> str:
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"Unknown tool '{name}'. Available: {list(TOOL_REGISTRY)}")
    return fn(**args)


TOOL_SCHEMAS: list[dict] = [
    {"type":"function","function":{"name":"calculator","description":"Evaluate math expressions safely. Use for all arithmetic.","parameters":{"type":"object","properties":{"expression":{"type":"string","description":"e.g. '2+2', 'sqrt(144)', '(3.14*5**2)'"}},"required":["expression"]}}},
    {"type":"function","function":{"name":"web_search","description":"Search the internet for current info. Use for recent news, facts, data.","parameters":{"type":"object","properties":{"query":{"type":"string"},"max_results":{"type":"integer","default":5}},"required":["query"]}}},
    {"type":"function","function":{"name":"python_executor","description":"Execute Python code in a sandbox. Use for data processing or complex logic.","parameters":{"type":"object","properties":{"code":{"type":"string","description":"Python code. Use print() for output."},"timeout_s":{"type":"integer","default":10}},"required":["code"]}}},
    {"type":"function","function":{"name":"data_analyst","description":"Analyse CSV or JSON data. Returns count, min, max, mean, median, std, unique values.","parameters":{"type":"object","properties":{"data":{"type":"string"},"data_format":{"type":"string","enum":["json","csv"],"default":"json"}},"required":["data"]}}},
    {"type":"function","function":{"name":"file_reader","description":"Read a local text file (.txt .csv .json .md .py .yaml .log).","parameters":{"type":"object","properties":{"filepath":{"type":"string"},"max_chars":{"type":"integer","default":4000}},"required":["filepath"]}}},
    {"type":"function","function":{"name":"datetime_tool","description":"Get current date, time, or timezone info.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["now","date","time","utc","timestamp","weekday","iso"],"default":"now"},"timezone":{"type":"string","default":"UTC"}},"required":[]}}},
    {"type":"function","function":{"name":"weather","description":"Get current weather for any city.","parameters":{"type":"object","properties":{"city":{"type":"string"},"units":{"type":"string","enum":["metric","imperial","standard"],"default":"metric"}},"required":["city"]}}},
]
