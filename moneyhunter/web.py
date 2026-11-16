"""Flask Web 仪表盘。"""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from . import __version__, analytics, db
from .config import Config
from .logsetup import get_logger


def _int_arg(name: str, default: int, minimum: int = 1, maximum: int = 500) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.json.ensure_ascii = False
    app.config["CONFIG"] = cfg
    logger = get_logger()

    def _auth_required() -> bool:
        if request.path == "/health":
            return False
        if not cfg.web_auth_enabled:
            return False
        auth = request.authorization
        return not auth or auth.username != cfg.web_auth_user or auth.password != cfg.web_auth_password

    @app.before_request
    def _access_log_and_auth():
        if _auth_required():
            logger.warning("unauthorized request path=%s remote=%s", request.path, request.remote_addr)
            return ("需要登录", 401, {"WWW-Authenticate": 'Basic realm="Money Hunter"'})

    def get_conn():
        conn = db.connect(cfg.db_path)
        db.init_db(conn)
        return conn

    @app.after_request
    def _no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        logger.info("%s %s -> %s", request.method, request.path, resp.status_code)
        return resp

    @app.get("/")
    def index():
        return render_template(
            "dashboard.html",
            version=__version__,
            window_days=cfg.growth_window_days,
            new_within_days=cfg.new_within_days,
        )

    @app.get("/api/summary")
    def api_summary():
        conn = get_conn()
        try:
            window = _int_arg("window", cfg.growth_window_days, 1, 365)
            return jsonify(analytics.summary(conn, window_days=window))
        finally:
            conn.close()

    @app.get("/api/leaderboard")
    def api_leaderboard():
        board = (request.args.get("board") or "growth").lower()
        limit = _int_arg("limit", cfg.top_limit, 1, 300)
        window = _int_arg("window", cfg.growth_window_days, 1, 365)
        q = (request.args.get("q") or "").strip() or None
        mode = (request.args.get("mode") or "total").lower()
        sort = (request.args.get("sort") or "stars").lower()

        conn = get_conn()
        try:
            if board == "growth":
                rows = analytics.growth_rows(
                    conn, window_days=window, limit=limit, q=q, mode=mode
                )
            elif board == "stars":
                rows = analytics.stars_rows(conn, limit=limit, q=q)
            elif board == "new":
                rows = analytics.new_rows(
                    conn, within_days=cfg.new_within_days, limit=limit, q=q
                )
            elif board == "money":
                rows = analytics.money_rows(conn, limit=limit, q=q, window_days=window)
            elif board == "all":
                rows = analytics.all_rows(conn, limit=limit, q=q, sort=sort, window_days=window)
            else:
                return jsonify({"error": f"未知榜单: {board}"}), 404
        finally:
            conn.close()

        return jsonify(
            {
                "board": board,
                "window": window,
                "mode": mode,
                "count": len(rows),
                "rows": rows,
            }
        )

    @app.get("/repo/<int:repo_id>")
    def repo_page(repo_id: int):
        conn = get_conn()
        try:
            detail = analytics.repo_detail(conn, repo_id)
        finally:
            conn.close()
        if not detail:
            return render_template("not_found.html", version=__version__), 404
        return render_template(
            "detail.html",
            version=__version__,
            repo=detail,
            window_days=cfg.growth_window_days,
        )

    @app.get("/compare")
    def compare_page():
        raw = request.args.get("ids") or ""
        ids = [int(part) for part in raw.split(",") if part.strip().isdigit()][:6]
        return render_template(
            "compare.html", version=__version__, ids=ids, window_days=cfg.growth_window_days
        )

    @app.get("/api/repos/<int:repo_id>/detail")
    def api_repo_detail(repo_id: int):
        conn = get_conn()
        try:
            detail = analytics.repo_detail(conn, repo_id)
        finally:
            conn.close()
        if not detail:
            return jsonify({"error": "仓库不存在"}), 404
        return jsonify(detail)

    @app.get("/api/compare")
    def api_compare():
        raw = request.args.get("ids") or ""
        ids = [int(part) for part in raw.split(",") if part.strip().isdigit()][:6]
        days_raw = request.args.get("days")
        days = None
        if days_raw:
            try:
                days = max(1, min(730, int(days_raw)))
            except ValueError:
                days = None
        conn = get_conn()
        try:
            repos = analytics.compare_series(conn, ids, days=days)
        finally:
            conn.close()
        return jsonify({"days": days, "count": len(repos), "repos": repos})

    @app.get("/api/repos/<int:repo_id>/history")
    def api_repo_history(repo_id: int):
        conn = get_conn()
        try:
            return jsonify({"repo_id": repo_id, "history": analytics.repo_history(conn, repo_id)})
        finally:
            conn.close()

    @app.get("/api/changes")
    def api_changes():
        board = (request.args.get("board") or "growth").lower()
        limit = _int_arg("limit", 30, 1, 100)
        conn = get_conn()
        try:
            return jsonify(analytics.daily_changes(conn, board=board, limit=limit))
        finally:
            conn.close()

    @app.get("/api/breakdown")
    def api_breakdown():
        dim = (request.args.get("dim") or "language").lower()
        limit = _int_arg("limit", 12, 1, 50)
        window = _int_arg("window", cfg.growth_window_days, 1, 365)
        conn = get_conn()
        try:
            if dim == "topic":
                rows = analytics.topic_stats(conn, window_days=window, limit=limit)
            elif dim == "language":
                rows = analytics.language_stats(conn, window_days=window, limit=limit)
            else:
                return jsonify({"error": f"未知维度: {dim}"}), 404
            return jsonify({"dim": dim, "window": window, "count": len(rows), "rows": rows})
        finally:
            conn.close()

    @app.get("/api/runs")
    def api_runs():
        limit = _int_arg("limit", 20, 1, 200)
        conn = get_conn()
        try:
            return jsonify({"runs": analytics.runs_history(conn, limit=limit)})
        finally:
            conn.close()

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "version": __version__})

    @app.errorhandler(500)
    def _handle_server_error(err):
        logger.exception("unhandled exception: %s", err)
        return jsonify({"error": "服务器内部错误"}), 500

    return app


def serve(cfg: Config, host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    app = create_app(cfg)
    url = f"http://{host}:{port}"
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    print(f"仪表盘已启动: {url}  (Ctrl+C 退出)")
    try:
        from waitress import serve as waitress_serve
    except ImportError:  # 未安装 waitress，降级为 Flask 开发服务器（不推荐用于生产）
        print("提示：未安装 waitress，降级为 Flask 内置开发服务器（生产环境请 pip install waitress）")
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
        return

    print("使用 waitress 生产级服务器运行。")
    waitress_serve(app, host=host, port=port, threads=8, ident="money-hunter")
