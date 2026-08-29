import json
import os
import signal
import tempfile
import time


def _tool_dir():
    return os.path.dirname(os.path.abspath(__file__))


def repo_root():
    return os.path.abspath(os.path.join(_tool_dir(), os.pardir))


def ensure_home_env(home_dir=None):
    home_dir = home_dir or repo_root()
    if not os.environ.get("HOME"):
        os.environ["HOME"] = home_dir
    if os.name == "nt":
        if not os.environ.get("USERPROFILE"):
            os.environ["USERPROFILE"] = home_dir
        drive, tail = os.path.splitdrive(home_dir)
        if drive and not os.environ.get("HOMEDRIVE"):
            os.environ["HOMEDRIVE"] = drive
        if tail and not os.environ.get("HOMEPATH"):
            os.environ["HOMEPATH"] = tail


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
    log_dir = _abs_from_root(config.get("log_dir", os.path.dirname(log_path) or "logs"))
    log_retention_days = int(config.get("log_retention_days", 14) or 14)
    log_auto_cleanup = bool(config.get("log_auto_cleanup", True))
    thumbnail_dir = _abs_from_root(config.get("thumbnail_dir", "thumbnails"))

    return {
        "config": config,
        "db_path": db_path,
        "status_dir": status_dir,
        "log_path": log_path,
        "log_dir": log_dir,
        "log_retention_days": log_retention_days,
        "log_auto_cleanup": log_auto_cleanup,
        "thumbnail_dir": thumbnail_dir,
        "spider_status_path": os.path.join(status_dir, "spider_status.json"),
        "download_status_path": os.path.join(status_dir, "download_status.json"),
        "check_all_status_path": os.path.join(status_dir, "check_all_status.json"),
    }


def daily_log_path(prefix, config_path=None):
    paths = runtime_paths(config_path)
    d = time.strftime("%Y%m%d", time.localtime())
    return os.path.join(paths["log_dir"], f"{prefix}_{d}.log")


def cleanup_old_logs(config_path=None, retention_days=None):
    paths = runtime_paths(config_path)
    days = paths["log_retention_days"] if retention_days is None else int(retention_days)
    if days <= 0:
        return 0
    log_dir = paths["log_dir"]
    if not log_dir or not os.path.isdir(log_dir):
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for name in os.listdir(log_dir):
        if not name.endswith(".log"):
            continue
        p = os.path.join(log_dir, name)
        try:
            st = os.stat(p)
            if st.st_mtime < cutoff:
                os.remove(p)
                removed += 1
        except Exception:
            pass
    return removed


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
