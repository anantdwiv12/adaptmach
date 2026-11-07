"""Executes agent-written design code in a separate, isolated Python process.
Reads {"code", "tools", "brief"} JSON on stdin, prints {"design"} or {"error"} JSON."""
import json
import math
import sys
import traceback

SAFE_BUILTINS = {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
                 for k in ["abs", "min", "max", "round", "int", "float", "bool", "str", "len",
                           "range", "sum", "sorted", "enumerate", "zip", "dict", "list", "tuple",
                           "isinstance", "ValueError", "Exception", "print", "pow", "any", "all",
                           "reversed", "map", "filter", "set", "callable", "hasattr", "divmod", "type", "KeyError", "TypeError", "ZeroDivisionError"]}


def _safe_import(name, *args, **kwargs):
    # models often write `import math` despite the prompt; allow only math (already provided)
    if name == "math":
        return math
    raise ImportError(f"import of '{name}' is not allowed (only math is available)")


SAFE_BUILTINS["__import__"] = _safe_import


def main():
    req = json.loads(sys.stdin.read())
    tools = {}
    try:
        for t in req.get("tools", []):
            ns = {"__builtins__": SAFE_BUILTINS, "math": math}
            exec(t["content"], ns)
            fn = ns.get(t["name"])
            if callable(fn):
                tools[t["name"]] = fn
        ns = {"__builtins__": SAFE_BUILTINS, "math": math}
        exec(req["code"], ns)
        if "design" not in ns:
            raise ValueError("code must define design(brief, tools)")
        out = ns["design"](req["brief"], tools)
        if not isinstance(out, dict):
            raise ValueError("design() must return a dict")
        print(json.dumps({"design": out}, default=str))
    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()[-3:]
        print(json.dumps({"error": f"{type(e).__name__}: {e}", "trace": tb}))


if __name__ == "__main__":
    main()
