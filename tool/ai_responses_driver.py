"""Responses API driver for AI-led 115 organization.

The model receives narrow business tools. It must inspect the live 115 listing
itself; Python only supplies pagination, scope guards and deterministic actions.
"""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path

from .egs_core import ensure_egs_schema
from .egs_organize import organize_single
from .p115_client import list_directory, offline_list
from .runtime import read_config, repo_root

MAX_TOOL_ROUNDS = 80
SAFE_EXECUTE_PREVIEWS = {
    "would_rename", "would_move", "would_rename_moved",
    "would_set_downloaded", "already_ok",
}


def _unquote(value) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def provider_settings(*, include_key=False) -> dict:
    """Load Provider/Key using the same split config pattern as phpAiAPI."""
    local = read_config()
    configured = (
        os.environ.get("PYGAL_AI_PROVIDER_CONFIG")
        or local.get("ai_provider_config")
        or str(Path(repo_root()).parent / "phpAiAPI" / "config" / "global.ini")
    )
    config_path = Path(configured)
    if not config_path.is_absolute():
        config_path = Path(repo_root()) / config_path
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    if config_path.is_file():
        parser.read(config_path, encoding="utf-8")
    default_provider = _unquote(parser.get("global", "default_provider", fallback=""))
    provider = _unquote(
        os.environ.get("PYGAL_AI_PROVIDER") or local.get("ai_provider") or default_provider
    )
    section = parser[provider] if provider and parser.has_section(provider) else {}
    base_url = _unquote(
        os.environ.get("PYGAL_AI_BASE_URL") or local.get("ai_base_url")
        or section.get("base_url", "")
    )
    model = _unquote(
        os.environ.get("PYGAL_AI_MODEL") or local.get("ai_model")
        or section.get("default_model", "")
    )
    key_path = config_path.parent / "key" / f"{provider}.key"
    api_key = _unquote(os.environ.get("OPENAI_API_KEY") or "")
    if not api_key and key_path.is_file():
        try:
            api_key = _unquote(key_path.read_text(encoding="utf-8"))
        except OSError:
            api_key = ""
    result = {
        "driver": "responses",
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "config_path": str(config_path),
        "configured": bool(provider and model and api_key),
        "message": None,
    }
    if not config_path.is_file():
        result["message"] = f"Provider 配置不存在: {config_path}"
    elif not provider or not parser.has_section(provider):
        result["message"] = f"Provider 未配置: {provider or '(empty)'}"
    elif not model:
        result["message"] = f"Provider {provider} 未配置 default_model"
    elif not api_key:
        result["message"] = f"Provider {provider} 的 API Key 不存在"
    else:
        result["message"] = f"Responses API 已配置: {provider} / {model}"
    if include_key:
        result["api_key"] = api_key
    return result


def driver_status() -> dict:
    result = provider_settings(include_key=False)
    try:
        import openai
        result["available"] = bool(result["configured"])
        result["logged_in"] = bool(result["configured"])
        result["version"] = getattr(openai, "__version__", None)
    except ImportError:
        result.update({
            "available": False,
            "logged_in": False,
            "version": None,
            "message": "缺少 openai Python SDK",
        })
    result.pop("configured", None)
    result.pop("config_path", None)
    return result


def _folder_join(conn):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='egs_115_folders'"
    ).fetchone()
    if exists:
        return (
            "LEFT JOIN egs_115_folders f ON f.date=g.date AND f.name=g.name",
            "f.cid folder_cid,f.folder_name,f.folder_path,f.target_name,f.status folder_status",
        )
    return ("", "NULL folder_cid,NULL folder_name,NULL folder_path,NULL target_name,NULL folder_status")


def _egs_rows(conn, year: int, *, offset=0, limit=100, query=None) -> dict:
    ensure_egs_schema(conn)
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 100))
    join, folder_fields = _folder_join(conn)
    where = ["substr(g.date,1,4)=?"]
    params: list[object] = [str(year)]
    if query:
        where.append("(g.name LIKE ? OR g.company LIKE ? OR g.torrent_name LIKE ?)")
        needle = f"%{str(query)[:120]}%"
        params.extend((needle, needle, needle))
    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT count(*) FROM egs_games g WHERE {where_sql}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT g.egs_id,g.date,g.name,g.company,g.resource_kind,g.release_ts,
                   g.actual_release_ts,g.torrent_name,g.infohash_hex,g.link,
                   COALESCE(g.downloaded,0) downloaded,
                   COALESCE(g.submitted_115,0) submitted_115,
                   COALESCE(g.download_failed,0) download_failed,
                   COALESCE(g.magnet_duplicate,0) magnet_duplicate,
                   COALESCE(g.submission_excluded,0) submission_excluded,
                   {folder_fields}
              FROM egs_games g {join}
             WHERE {where_sql}
             ORDER BY g.date,g.egs_id LIMIT ? OFFSET ?""",
        (*params, limit, offset),
    ).fetchall()
    return {
        "success": True, "year": int(year), "offset": offset, "limit": limit,
        "total": int(total), "has_more": offset + len(rows) < total,
        "records": [dict(row) for row in rows],
    }


def _egs_record(conn, egs_id: int) -> dict:
    ensure_egs_schema(conn)
    join, folder_fields = _folder_join(conn)
    row = conn.execute(
        f"""SELECT g.*, {folder_fields}
              FROM egs_games g {join} WHERE g.egs_id=?""", (int(egs_id),)
    ).fetchone()
    return {"success": bool(row), "record": dict(row) if row else None}


def _normalize_offline_tasks(offset=0, limit=100) -> dict:
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 100))
    result = offline_list()
    if not result.get("success"):
        return {"success": False, "message": result.get("message"), "tasks": []}
    tasks = []
    for task in result.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        tasks.append({
            "name": task.get("name") or task.get("file_name") or task.get("n"),
            "infohash": task.get("info_hash") or task.get("infohash") or task.get("sha1"),
            "status": task.get("status"),
            "display_status": task.get("display_status"),
            "percent": task.get("percent"),
            "file_id": task.get("file_id") or task.get("cid"),
            "save_path": task.get("save_path") or task.get("wp_path"),
        })
    total = len(tasks)
    page = tasks[offset:offset + limit]
    return {
        "success": True, "offset": offset, "limit": limit, "total": total,
        "has_more": offset + len(page) < total, "tasks": page,
    }


def tool_definitions() -> list[dict]:
    obj = {"type": "object", "additionalProperties": False}
    return [
        {
            "type": "function", "name": "list_115_path", "strict": True,
            "description": "分页读取实时115目录。只允许 /GAL/GAL-任务年份 与 /GAL/todo。",
            "parameters": {**obj, "properties": {
                "path": {"type": "string"}, "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            }, "required": ["path", "offset", "limit"]},
        },
        {
            "type": "function", "name": "list_115_children", "strict": True,
            "description": "读取先前清单中已出现目录CID的子项，用于判断合集和重复内容。",
            "parameters": {**obj, "properties": {
                "cid": {"type": "string"}, "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            }, "required": ["cid", "offset", "limit"]},
        },
        {
            "type": "function", "name": "list_115_offline_tasks", "strict": True,
            "description": "分页读取115实时离线任务清单，用来判断等待、完成或失败。",
            "parameters": {**obj, "properties": {
                "offset": {"type": "integer"}, "limit": {"type": "integer"},
            }, "required": ["offset", "limit"]},
        },
        {
            "type": "function", "name": "list_egs_year", "strict": True,
            "description": "分页读取任务年份全部EGS记录。记录不是AI候选清单，需与115实况自行比对。",
            "parameters": {**obj, "properties": {
                "year": {"type": "integer"}, "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            }, "required": ["year", "offset", "limit"]},
        },
        {
            "type": "function", "name": "search_egs_year", "strict": True,
            "description": "按115目录名线索搜索任务年份EGS名称、公司或torrent名称。",
            "parameters": {**obj, "properties": {
                "year": {"type": "integer"}, "query": {"type": "string"},
                "limit": {"type": "integer"},
            }, "required": ["year", "query", "limit"]},
        },
        {
            "type": "function", "name": "get_egs_record", "strict": True,
            "description": "读取一条属于任务年份的完整EGS及115映射记录。",
            "parameters": {**obj, "properties": {"egs_id": {"type": "integer"}},
                           "required": ["egs_id"]},
        },
        {
            "type": "function", "name": "preview_egs_organization", "strict": True,
            "description": "对AI选中的EGS记录运行确定性安全预览；不会修改115或数据库。",
            "parameters": {**obj, "properties": {"egs_id": {"type": "integer"}},
                           "required": ["egs_id"]},
        },
        {
            "type": "function", "name": "execute_egs_organization", "strict": True,
            "description": "执行已预览且无需人工确认的改名、移动或状态同步。不会越过跨年、搬月、冲突等护栏。",
            "parameters": {**obj, "properties": {"egs_id": {"type": "integer"}},
                           "required": ["egs_id"]},
        },
    ]


def _scoped_game(conn, egs_id: int, year: int):
    row = conn.execute(
        "SELECT egs_id,date,name FROM egs_games WHERE egs_id=?", (int(egs_id),)
    ).fetchone()
    if not row:
        raise ValueError("EGS 记录不存在")
    if str(row["date"] or "")[:4] != str(year):
        raise ValueError(f"EGS #{egs_id} 不属于任务年份 {year}")
    return row


def execute_tool(conn, *, year: int, name: str, arguments: dict, state: dict) -> dict:
    if name == "list_115_path":
        path = str(arguments["path"]).rstrip("/")
        allowed = {f"/GAL/GAL-{year}", "/GAL/todo"}
        if path not in allowed:
            raise ValueError(f"目录超出任务范围: {path}")
        offset = int(arguments["offset"])
        expected = state.setdefault("path_offsets", {}).get(path, 0)
        if offset != expected:
            raise ValueError(f"目录 {path} 必须连续分页读取，下一页 offset={expected}")
        result = list_directory(path=path, offset=offset, limit=arguments["limit"])
        if result.get("success"):
            state["saw_live_115"] = True
            state["known_cids"].update(
                item["id"] for item in result.get("items", []) if item.get("is_dir")
            )
            state["path_offsets"][path] = offset + len(result.get("items", []))
            if not result.get("has_more"):
                state.setdefault("completed_paths", set()).add(path)
        return result
    if name == "list_115_children":
        cid = str(arguments["cid"])
        if cid not in state["known_cids"]:
            raise ValueError("只能读取先前115清单中出现的目录CID")
        result = list_directory(cid=cid, offset=arguments["offset"], limit=arguments["limit"])
        if result.get("success"):
            if not str(result.get("path") or "").startswith("/GAL"):
                raise ValueError("目录不在 /GAL 范围内")
            state["known_cids"].update(
                item["id"] for item in result.get("items", []) if item.get("is_dir")
            )
        return result
    if name == "list_115_offline_tasks":
        return _normalize_offline_tasks(
            offset=arguments["offset"], limit=arguments["limit"],
        )
    if name == "list_egs_year":
        if int(arguments["year"]) != year:
            raise ValueError("只能读取任务年份")
        return _egs_rows(conn, year, offset=arguments["offset"], limit=arguments["limit"])
    if name == "search_egs_year":
        if int(arguments["year"]) != year:
            raise ValueError("只能搜索任务年份")
        return _egs_rows(conn, year, query=arguments["query"], limit=arguments["limit"])
    if name == "get_egs_record":
        _scoped_game(conn, arguments["egs_id"], year)
        return _egs_record(conn, arguments["egs_id"])
    if name in ("preview_egs_organization", "execute_egs_organization"):
        row = _scoped_game(conn, arguments["egs_id"], year)
        egs_id = int(row["egs_id"])
        if name == "preview_egs_organization":
            result = organize_single(row["date"], row["name"], dry_run=True, conn=conn)
            state["previews"][egs_id] = result.get("status")
            return result
        if f"/GAL/GAL-{year}" not in state.get("completed_paths", set()):
            raise ValueError("执行前必须完整分页读取年度115目录")
        preview = state["previews"].get(egs_id)
        if preview not in SAFE_EXECUTE_PREVIEWS:
            raise ValueError(f"该记录没有安全的预览结果: {preview or 'not_previewed'}")
        return organize_single(row["date"], row["name"], dry_run=False, conn=conn)
    raise ValueError(f"未知工具: {name}")


def _instructions(year: int) -> str:
    return f"""你是 pyGal 的115整理代理，本任务年份是 {year}。

决策必须由你根据115实时返回的清单作出，不能把数据库状态或Python预筛选当作候选结论。第一步必须分页读取 /GAL/GAL-{year}；还应读取 /GAL/todo，并按需查看目录子项和离线任务。然后读取或搜索 {year} 年 EGS 记录，逐项判断115内容的归属、合集关系、重复与整理需要。

EGS磁链审核不在本任务范围。机械改名、移动与状态同步只能通过 preview_egs_organization 后调用 execute_egs_organization；工具会保留跨年、搬月、不同哈希、共享CID和冲突护栏。没有直接证据时不要猜测，不要删除115内容。无归属的历史内容保持在 /GAL/todo，并在结论中说明。

如果分页结果 has_more=true，继续读取下一页，不能只看第一页便宣称年度完成。最终以结构化结果总结你实际读取的115范围、判断和执行结果。

所有需要用户决定的事项必须逐条写入 human_review，不能只在 summary 中笼统说明。每项使用稳定且可读的 review_key，category 标明问题类型，target 写 EGS ID、115 CID/路径等目标，question 写用户需要回答的单一问题，reason 说明为何不能自动处理，evidence 列出具体 InfoHash、CID、路径或文件证据，recommended_action 给出你的首选建议，alternatives 列出其它可选处理。不得把建议操作当成已执行动作。

没有待确认事项时 human_review 必须是空数组且 requires_human=false。有待确认事项时 requires_human=true；如果同时完成了安全动作，outcome=partial；如果没有可继续执行的安全动作，outcome=blocked。"""


def _normalize_final_result(result: dict) -> dict:
    """Keep terminal state consistent with the structured review queue."""
    reviews = result.get("human_review")
    if not isinstance(reviews, list):
        reviews = []
    result["human_review"] = reviews
    result["requires_human"] = bool(reviews)
    if reviews and result.get("outcome") == "completed":
        result["outcome"] = "partial" if result.get("actions") else "blocked"
    return result


def run_responses_job(conn, job, *, record_event, is_cancelled) -> dict:
    from openai import OpenAI

    settings = provider_settings(include_key=True)
    if not settings.get("configured"):
        raise RuntimeError(settings.get("message") or "Responses API 未配置")
    client_kwargs = {"api_key": settings["api_key"]}
    if settings.get("base_url"):
        client_kwargs["base_url"] = settings["base_url"]
    client = OpenAI(**client_kwargs)
    year = int(job["scope_year"])
    schema = json.loads((Path(repo_root()) / "tool" / "ai_organizer_output.schema.json").read_text(encoding="utf-8"))
    inputs = [{"role": "user", "content": str(job["prompt"])}]
    state = {
        "saw_live_115": False, "known_cids": set(), "previews": {},
        "path_offsets": {}, "completed_paths": set(),
    }

    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        if is_cancelled():
            raise InterruptedError("任务已请求停止")
        response = client.responses.create(
            model=settings["model"], instructions=_instructions(year), input=inputs,
            tools=tool_definitions(), parallel_tool_calls=False,
            text={"format": {
                "type": "json_schema", "name": "ai_organizer_result",
                "strict": True, "schema": schema,
            }},
            max_output_tokens=10000,
        )
        record_event(
            "api.response", f"Responses API 第 {round_no} 轮完成",
            payload={"response_id": response.id, "model": settings["model"]},
        )
        output_items = [item.model_dump(mode="json", exclude_none=True) for item in response.output]
        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        inputs.extend(output_items)
        if not calls:
            try:
                result = json.loads(response.output_text)
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("AI 未返回有效的结构化整理结果") from exc
            result = _normalize_final_result(result)
            required_paths = {f"/GAL/GAL-{year}", "/GAL/todo"}
            missing = required_paths - state["completed_paths"]
            if missing:
                raise RuntimeError(f"AI 未完整读取115目录: {sorted(missing)}")
            return result
        for call in calls:
            try:
                arguments = json.loads(call.arguments or "{}")
                record_event("tool.started", f"AI 调用 {call.name}", payload=arguments)
                output = execute_tool(
                    conn, year=year, name=call.name, arguments=arguments, state=state,
                )
                record_event(
                    "tool.finished", f"{call.name} 完成",
                    payload={
                        "success": output.get("success", True) if isinstance(output, dict) else True,
                        "status": output.get("status") if isinstance(output, dict) else None,
                        "total": output.get("total") if isinstance(output, dict) else None,
                    },
                )
            except Exception as exc:
                output = {"success": False, "error": str(exc)}
                record_event("tool.failed", f"{call.name}: {exc}", level="error")
            inputs.append({
                "type": "function_call_output", "call_id": call.call_id,
                "output": json.dumps(output, ensure_ascii=False),
            })
    raise RuntimeError(f"Responses API 工具调用超过 {MAX_TOOL_ROUNDS} 轮")
