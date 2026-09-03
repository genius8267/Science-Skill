#!/usr/bin/env python3
"""Deterministic sci-agents router with numeric fail-closed gates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


T_PRIMARY = 60
M_PRIMARY = 15
T_COMPOSE = 45
T_ASK = 35
ASK_WINDOW = 10
MAX_CANDIDATES = 4
MAX_DISPATCH_BYTES = 65536

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "using",
        "use",
        "via",
        "over",
        "this",
        "that",
        "please",
        "help",
        "need",
        "how",
        "do",
        "i",
        "my",
        "our",
        "task",
        "work",
        "about",
        "by",
        "at",
        "is",
        "are",
        "be",
        "it",
        "as",
        "data",
        "file",
        "files",
        "run",
        "make",
        "create",
        "get",
        "set",
        "new",
        "some",
        "any",
        "can",
        "you",
        "me",
    }
)

TOKEN_EQUIVALENTS = {
    "figure": {"figure", "plot", "visualization", "visualize"},
    "manuscript": {"manuscript", "write", "writing"},
    "presentation": {"deck", "presentation", "slide", "slides"},
}

BROAD_MARKERS = frozenset(
    {
        "pipeline",
        "end-to-end",
        "endtoend",
        "workflow",
        "then",
        "literature",
        "analysis",
        "figure",
        "manuscript",
        "slides",
        "review",
        "write",
        "visualize",
        "design",
        "power",
        "citation",
        "paper",
        "plot",
        "compose",
        "multi",
        "stage",
        "across",
    }
)

FORMAT_MODULES = frozenset({"pdf", "xlsx", "docx", "pptx"})
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
TOKEN_RE = re.compile(r"[a-z0-9]+")
STAGE_SPLIT_RE = re.compile(r"\bthen\b|(?:->|→)", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
REDIRECT_SENTENCE_RE = re.compile(r"\buse\b[^.!?]*\binstead\b", re.IGNORECASE)
USE_RE = re.compile(
    r"^use\s+([A-Za-z0-9][A-Za-z0-9_-]*)(?:\s+|$)(.*)$",
    re.IGNORECASE,
)


class CatalogSchemaError(ValueError):
    """Catalog failed closed schema validation."""


def pack_root_from_script() -> Path:
    return Path(__file__).resolve().parent.parent


def _raw_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower().replace("-", " ").replace("_", " "))


def _has_token_sequence(haystack: list[str], needle: list[str]) -> bool:
    if not needle:
        return False
    n = len(needle)
    if n > len(haystack):
        return False
    for i in range(0, len(haystack) - n + 1):
        if haystack[i : i + n] == needle:
            return True
    return False


def _field_tokens(value: str) -> list[str]:
    return _raw_tokens(value)


def load_catalog(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogSchemaError("catalog.json is invalid") from exc
    if not isinstance(data, dict):
        raise CatalogSchemaError("catalog.json is invalid")
    if data.get("version") != 1:
        raise CatalogSchemaError("catalog version must be 1")
    modules = data.get("modules")
    if not isinstance(modules, list):
        raise CatalogSchemaError("modules must be a list")
    seen: set[str] = set()
    for module in modules:
        if not isinstance(module, dict):
            raise CatalogSchemaError("each module must be an object")
        name = module.get("name")
        rel = module.get("path")
        description = module.get("description")
        if not isinstance(name, str) or not name or not SAFE_NAME_RE.fullmatch(name):
            raise CatalogSchemaError("module name is invalid")
        if name in seen:
            raise CatalogSchemaError(f"duplicate module name: {name}")
        seen.add(name)
        if not isinstance(rel, str) or not rel:
            raise CatalogSchemaError("module path must be a string")
        if not isinstance(description, str) or not description:
            raise CatalogSchemaError("module description must be a string")
        path_obj = Path(rel)
        if path_obj.is_absolute() or ".." in path_obj.parts:
            raise CatalogSchemaError("module path must be relative")
        if rel != f"skills/{name}":
            raise CatalogSchemaError("module path must be exactly skills/<name>")
        for key in ("aliases", "support", "references"):
            if not isinstance(module.get(key), list):
                raise CatalogSchemaError(f"{key} must be a list")
    return data


def tokenize(text: str) -> list[str]:
    return [tok for tok in TOKEN_RE.findall(text.lower()) if tok and tok not in STOPWORDS]


def _token_variants(token: str) -> set[str]:
    variants = {token}
    for equivalent in TOKEN_EQUIVALENTS.values():
        if variants & equivalent:
            variants.update(equivalent)
    return variants


def _overlap(query_tokens: set[str], other_tokens: set[str]) -> list[str]:
    hits: set[str] = set()
    other_vars: set[str] = set()
    for token in other_tokens:
        other_vars.update(_token_variants(token))
    for token in query_tokens:
        if _token_variants(token) & other_vars:
            hits.add(token)
    return sorted(hits)


def _ngrams(tokens: list[str], n: int) -> set[str]:
    if n <= 1:
        return set(tokens)
    return {" ".join(tokens[i : i + n]) for i in range(0, len(tokens) - n + 1)}


def _name_or_alias_hit(query_tokens: list[str], module: dict[str, Any]) -> bool:
    if _has_token_sequence(query_tokens, _field_tokens(module["name"])):
        return True
    for alias in module.get("aliases") or []:
        if isinstance(alias, str) and _has_token_sequence(query_tokens, _field_tokens(alias)):
            return True
    return False


def score_module(
    query: str,
    module: dict[str, Any],
    *,
    suppress_format_name: bool = False,
) -> tuple[int, list[str]]:
    q_tokens = tokenize(query)
    if not q_tokens:
        return 0, []
    q_set = set(q_tokens)
    q_bigrams = _ngrams(q_tokens, 2)
    name = module["name"]
    name_tokens = tokenize(name.replace("-", " "))
    aliases = [a.lower() for a in module.get("aliases", []) if isinstance(a, str)]
    description = module.get("description", "")
    positive_description = " ".join(
        sentence
        for sentence in SENTENCE_SPLIT_RE.split(description)
        if not REDIRECT_SENTENCE_RE.search(sentence)
    )
    desc_tokens = tokenize(positive_description)
    desc_set = set(desc_tokens)
    evidence: list[str] = []
    score = 0
    query_tokens = _raw_tokens(query)
    allow_name = not (suppress_format_name and name in FORMAT_MODULES)
    if allow_name and _has_token_sequence(query_tokens, _field_tokens(name)):
        score += 70
        evidence.append(f"name:{name}")
    if allow_name:
        for alias in aliases:
            if alias and _has_token_sequence(query_tokens, _field_tokens(alias)):
                score += 40
                evidence.append(f"alias:{alias}")
                break
    name_overlap = _overlap(q_set, set(name_tokens))
    if name_overlap:
        score += min(25, 10 * len(name_overlap))
        evidence.append("name-token:" + ",".join(name_overlap))
    desc_overlap = _overlap(q_set, desc_set)
    if desc_overlap:
        coverage = len(desc_overlap) / max(1, len(q_set))
        targeted_bonus = 20 if len(q_set) <= 2 and coverage == 1 else 0
        score += min(
            80,
            12 * len(desc_overlap)
            + int(20 * coverage)
            + 8 * len(q_bigrams & _ngrams(desc_tokens, 2))
            + targeted_bonus,
        )
        evidence.append("description:" + ",".join(desc_overlap[:6]))
    support = module.get("support", [])
    for item in support:
        if item in q_set:
            score += 4
            evidence.append(f"support:{item}")
    refs = module.get("references", [])
    for ref in refs:
        if isinstance(ref, str) and _has_token_sequence(query_tokens, _field_tokens(ref)):
            score += 3
            evidence.append(f"ref:{ref}")
    score = max(0, min(100, score))
    seen: set[str] = set()
    unique: list[str] = []
    for item in evidence:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return score, unique


def is_broad(query: str) -> bool:
    q = query.lower()
    tokens = set(tokenize(query))
    if "end-to-end" in q or "end to end" in q:
        tokens.add("endtoend")
    markers = tokens & BROAD_MARKERS
    return len(markers) >= 3 or ("then" in tokens and len(markers) >= 2)


def apply_gates(
    scored: list[dict[str, Any]],
    broad: bool,
    query: str = "",
    modules: list[dict[str, Any]] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    ranked = sorted(scored, key=lambda item: (-item["score"], item["name"]))
    if not ranked:
        return _result("no-match", [], query=query, modules=modules, root=root)
    top = ranked[0]
    second = ranked[1]["score"] if len(ranked) >= 2 else 0
    if top["score"] >= T_PRIMARY and (top["score"] - second) >= M_PRIMARY:
        return _result("primary", [top], query=query, modules=modules, root=root)
    if top["score"] >= T_ASK:
        window = [c for c in ranked if (top["score"] - c["score"]) <= ASK_WINDOW][:MAX_CANDIDATES]
        if len(window) < 2:
            window = ranked[: min(2, len(ranked))]
        return _result("ask", window[:MAX_CANDIDATES], query=query, modules=modules, root=root)
    return _result("no-match", ranked[:MAX_CANDIDATES], query=query, modules=modules, root=root)


def _score_candidates(
    query: str,
    modules: list[dict[str, Any]],
    *,
    suppress_format_name: bool = False,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for module in modules:
        score, evidence = score_module(query, module, suppress_format_name=suppress_format_name)
        if score > 0:
            scored.append({"name": module["name"], "score": score, "evidence": evidence})
    return sorted(scored, key=lambda item: (-item["score"], item["name"]))


def _score_with_format_policy(query: str, modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_tokens = _raw_tokens(query)
    strong = False
    for module in modules:
        if module["name"] in FORMAT_MODULES:
            continue
        if _name_or_alias_hit(query_tokens, module):
            strong = True
            break
    first = _score_candidates(query, modules, suppress_format_name=False)
    if not strong:
        if any(item["name"] not in FORMAT_MODULES and item["score"] >= T_PRIMARY for item in first):
            strong = True
    if strong:
        return _score_candidates(query, modules, suppress_format_name=True)
    return first


def _unique_stage_top(
    segment: str, modules: list[dict[str, Any]]
) -> dict[str, Any] | None:
    ranked = _score_with_format_policy(segment, modules)
    if not ranked:
        return None
    top = ranked[0]
    second = ranked[1]["score"] if len(ranked) > 1 else 0
    if top["score"] < T_COMPOSE or top["score"] == second:
        return None
    return top


def _explicit_stages(query: str) -> list[str]:
    return [segment.strip(" ,.;:") for segment in STAGE_SPLIT_RE.split(query) if segment.strip(" ,.;:")]


def _compose_or_ask(
    query: str,
    modules: list[dict[str, Any]],
    root: Path | None,
) -> dict[str, Any]:
    segments = _explicit_stages(query)
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    failed = False
    for segment in segments:
        top = _unique_stage_top(segment, modules)
        if top is None or top["name"] in seen:
            failed = True
            break
        chosen.append(top)
        seen.add(top["name"])
    if failed or len(chosen) != len(segments) or len(chosen) > MAX_CANDIDATES or len(chosen) < 2:
        ranked = _score_with_format_policy(query, modules)
        window = ranked[:MAX_CANDIDATES]
        decision = "ask" if window and window[0]["score"] >= T_ASK else "no-match"
        return _result(decision, window, query=query, modules=modules, root=root)
    return _result("compose-hint", chosen, query=query, modules=modules, root=root)


def _module_index(modules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for module in modules:
        index[module["name"].casefold()] = module
    for module in modules:
        for alias in module.get("aliases") or []:
            if not isinstance(alias, str) or not alias:
                continue
            key = alias.casefold()
            if key not in index:
                index[key] = module
    return index


def decide(
    query: str,
    modules: list[dict[str, Any]],
    root: Path | None = None,
) -> dict[str, Any]:
    raw = query.strip()
    root = Path(root).resolve() if root is not None else pack_root_from_script()
    index = _module_index(modules)
    use_match = USE_RE.match(raw)
    if use_match:
        token = use_match.group(1)
        rest = use_match.group(2).strip()
        matched = index.get(token.casefold())
        if matched is not None:
            return _result(
                "exact",
                [{"name": matched["name"], "score": 100, "evidence": [f"use:{matched['name']}"]}],
                query=raw,
                modules=modules,
                root=root,
            )
        if rest:
            return _decide_semantic(rest, modules, root)
        return _result("no-match", [], query=raw, modules=modules, root=root)
    folded = raw.casefold()
    for module in modules:
        if module["name"].casefold() == folded:
            return _result(
                "exact",
                [{"name": module["name"], "score": 100, "evidence": [f"canonical:{module['name']}"]}],
                query=raw,
                modules=modules,
                root=root,
            )
    return _decide_semantic(raw, modules, root)


def _decide_semantic(
    raw: str,
    modules: list[dict[str, Any]],
    root: Path | None,
) -> dict[str, Any]:
    stages = _explicit_stages(raw)
    if len(stages) >= 2:
        return _compose_or_ask(raw, modules, root)
    return apply_gates(
        _score_with_format_policy(raw, modules),
        is_broad(raw),
        query=raw,
        modules=modules,
        root=root,
    )


def _result(
    decision: str,
    candidates: list[dict[str, Any]],
    query: str,
    modules: list[dict[str, Any]] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    by_name = {m["name"]: m for m in (modules or [])}
    resolved_root = Path(root).resolve() if root is not None else pack_root_from_script()
    collection = resolved_root / "skills"
    enriched: list[dict[str, Any]] = []
    for cand in candidates:
        module = by_name.get(cand["name"], {})
        rel = module.get("path") or f"skills/{cand['name']}"
        abs_path = (resolved_root / rel).resolve()
        context = {
            "COLLECTION_DIR": str(collection),
            "MODULE_DIR": str(abs_path),
            "ROUTER_DIR": str(resolved_root),
            "SKILL_PATH": str(abs_path),
        }
        if cand["name"] == "flowio":
            context["FLOWIO_SKILL_DIR"] = str(collection / "flowio")
        item = {
            "context": context,
            "evidence": cand.get("evidence", []),
            "module_path": str(abs_path),
            "name": cand["name"],
            "path": rel,
            "references": list(module.get("references") or []),
            "score": cand.get("score", 0),
            "support": list(module.get("support") or []),
        }
        enriched.append(item)
    return {
        "candidates": enriched,
        "decision": decision,
        "query": query,
        "thresholds": {
            "ASK_WINDOW": ASK_WINDOW,
            "MAX_CANDIDATES": MAX_CANDIDATES,
            "M_PRIMARY": M_PRIMARY,
            "T_ASK": T_ASK,
            "T_COMPOSE": T_COMPOSE,
            "T_PRIMARY": T_PRIMARY,
        },
    }


def cmd_list(catalog: dict[str, Any], query: str | None) -> dict[str, Any]:
    if not query or not str(query).strip():
        raise CatalogSchemaError("list requires a query")
    q = query.lower()
    modules = [
        m
        for m in catalog["modules"]
        if q in m["name"].lower()
        or q in m.get("description", "").lower()
        or any(q in a.lower() for a in m.get("aliases", []) if isinstance(a, str))
    ]
    return {
        "count": len(modules),
        "modules": [{"name": m["name"], "path": m["path"], "description": m["description"]} for m in modules],
    }


def cmd_show(catalog: dict[str, Any], name: str, root: Path | None = None) -> dict[str, Any]:
    key = name.casefold()
    for module in catalog["modules"]:
        names = {module["name"].casefold()}
        names.update(a.casefold() for a in module.get("aliases") or [] if isinstance(a, str))
        if key in names:
            packed = _result(
                "exact",
                [{"name": module["name"], "score": 100, "evidence": [f"show:{module['name']}"]}],
                query=name,
                modules=catalog["modules"],
                root=root,
            )
            shown = dict(module)
            shown.update(
                {
                    "context": packed["candidates"][0]["context"],
                    "module_path": packed["candidates"][0]["module_path"],
                    "path": packed["candidates"][0]["path"],
                }
            )
            return {"module": shown}
    return {"error": "no-match", "name": name}


def dispatch_payload(payload: str) -> tuple[str, list[str]]:
    raw = (payload or "").strip()
    if not raw:
        raise CatalogSchemaError("empty dispatch payload")
    if "\x00" in raw:
        raise CatalogSchemaError("dispatch payload contains NUL")
    first, _, rest = raw.partition(" ")
    key = first.casefold()
    if key == "list":
        query = rest.strip()
        if not query:
            raise CatalogSchemaError("list requires a query")
        return "list", [query]
    if key == "show":
        name = rest.strip()
        if not name:
            raise CatalogSchemaError("show requires a module")
        return "show", [name]
    return "route", [raw]


def read_dispatch_file(path: Path) -> str:
    request = Path(path)
    if request.is_symlink() or not request.is_file():
        raise CatalogSchemaError("dispatch file must be a regular file")
    if request.stat().st_size > MAX_DISPATCH_BYTES:
        raise CatalogSchemaError("dispatch payload is too large")
    try:
        return request.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CatalogSchemaError("dispatch payload must be valid UTF-8") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Route scientific tasks to nested modules")
    parser.add_argument("--catalog", type=Path, default=None)
    parser.add_argument("--root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    list_p = sub.add_parser("list")
    list_p.add_argument("query", nargs="?", default=None)
    show_p = sub.add_parser("show")
    show_p.add_argument("module")
    route_p = sub.add_parser("route")
    route_p.add_argument("task", nargs="+")
    dispatch_p = sub.add_parser("dispatch")
    dispatch_p.add_argument("payload")
    dispatch_file_p = sub.add_parser("dispatch-file")
    dispatch_file_p.add_argument("request_file", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = (args.root or pack_root_from_script()).resolve()
    catalog_path = (args.catalog or (root / "catalog.json")).resolve()
    try:
        catalog = load_catalog(catalog_path)
        command = args.command
        if command in {"dispatch", "dispatch-file"}:
            payload = args.payload if command == "dispatch" else read_dispatch_file(args.request_file)
            command, parts = dispatch_payload(payload)
            if command == "list":
                args.query = parts[0]
            elif command == "show":
                args.module = parts[0]
            else:
                args.task = parts
        if command == "list":
            out = cmd_list(catalog, args.query)
        elif command == "show":
            out = cmd_show(catalog, args.module, root=root)
            if out.get("error") == "no-match":
                print(json.dumps(out, indent=2, sort_keys=True))
                return 1
        else:
            out = decide(" ".join(args.task), catalog["modules"], root=root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
