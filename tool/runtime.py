import json
import os
import signal
import tempfile
import time


def _tool_dir():
    return os.path.dirname(os.path.abspath(__file__))


def repo_root():
    return os.path.abspath(os.path.join(_tool_dir(), os.pardir))


def _abs_from_root(path):
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(repo_root(), path))


def read_config(config_path=None):
    if config_path is None:
        config_path = os.path.join(_tool_dir(), "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def runtime_paths(config_path=None):
    config = read_config(config_path)
    db_path = _abs_from_root(config.get("db_path", "getchu.db"))
    status_dir = _abs_from_root(config.get("status_dir", "status"))
    log_path = _abs_from_root(config.get("log_path", os.path.join("logs", "app.log")))

    return {
        "config": config,
        "db_path": db_path,
        "status_dir": status_dir,
        "log_path": log_path,
        "spider_status_path": os.path.join(status_dir, "spider_status.json"),
        "download_status_path": os.path.join(status_dir, "download_status.json"),
    }


def ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def now_ts():
    return int(time.time())


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json_atomic(path, data):
    ensure_parent_dir(path)
    dir_path = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=dir_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def pid_is_running(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_pid(pid, sig=signal.SIGTERM):
    os.kill(pid, sig)

