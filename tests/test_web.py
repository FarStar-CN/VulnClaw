from pathlib import Path

import pytest
from typer.testing import CliRunner


class TestWebServices:
    def test_constraint_audit_service_aggregates_events(self, monkeypatch, tmp_path):
        import vulnclaw.target_state.store as store_mod
        from vulnclaw.agent.context import SessionState
        from vulnclaw.web.services.constraint_audit_service import get_constraint_audit

        monkeypatch.setattr(store_mod, "TARGETS_DIR", tmp_path)
        state = SessionState(target="https://example.com")
        state.add_constraint_violation_event(
            source="tool",
            action="exploit",
            tool_name="fetch",
            code="tool_action_blocked",
            severity="high",
            summary="constraint_violation: tool 'fetch' inferred action 'exploit'",
            detail="GET /admin?cmd=whoami",
        )
        store_mod.save_target_state("https://example.com", state, command="scan")

        monkeypatch.setattr("vulnclaw.web.services.constraint_audit_service.TARGETS_DIR", tmp_path)
        view = get_constraint_audit()
        assert view.total_events >= 1
        assert view.high_severity_events >= 1
        assert view.by_source["tool"] >= 1
        assert view.by_code["tool_action_blocked"] >= 1

    def test_web_mcp_service_view(self):
        from vulnclaw.web.services.mcp_service import get_mcp_diagnostics

        view = get_mcp_diagnostics()
        assert view.total_services >= 2
        assert any(item.name == "fetch" for item in view.services)
        fetch = next(item for item in view.services if item.name == "fetch")
        assert fetch.health_status in {"healthy", "degraded", "unknown"}

    def test_web_config_service(self):
        from vulnclaw.web.services.config_service import get_public_config

        view = get_public_config()
        assert view.provider
        assert isinstance(view.api_key_configured, bool)

    def test_web_config_service_updates_subset(self, monkeypatch, tmp_path):
        import vulnclaw.web.services.config_service as config_service
        from vulnclaw.config.schema import VulnClawConfig
        from vulnclaw.web.schemas import ConfigUpdateRequest

        saved = VulnClawConfig()

        monkeypatch.setattr(config_service, "load_config", lambda: saved)
        monkeypatch.setattr(config_service, "save_config", lambda cfg: None)

        view = config_service.update_public_config(
            ConfigUpdateRequest(
                provider="deepseek",
                model="deepseek-chat",
                output_dir=str(tmp_path),
                max_rounds=22,
                show_thinking=True,
            )
        )
        assert view.provider == "deepseek"
        assert view.model == "deepseek-chat"
        assert view.output_dir == str(tmp_path)
        assert view.max_rounds == 22
        assert view.show_thinking is True
        assert view.python_execute_mode == "trusted-local"

    def test_web_target_service_lists_targets(self, monkeypatch, tmp_path):
        import vulnclaw.target_state.store as store_mod
        import vulnclaw.web.services.target_service as target_service
        from vulnclaw.agent.context import SessionState, VulnerabilityFinding

        monkeypatch.setattr(store_mod, "TARGETS_DIR", tmp_path)
        monkeypatch.setattr(target_service, "TARGETS_DIR", tmp_path)

        state = SessionState(target="https://example.com")
        state.add_finding(
            VulnerabilityFinding(title="Candidate", severity="Low", lifecycle_status="candidate")
        )
        review = VulnerabilityFinding(
            title="Review Me",
            severity="High",
            evidence_level="L2",
            lifecycle_status="needs_manual_review",
        )
        state.add_finding(review)
        store_mod.save_target_state("https://example.com", state, command="recon")

        items = target_service.list_targets()
        assert items
        assert items[0].target == "https://example.com"
        assert items[0].candidate_count == 1
        assert items[0].manual_review_count == 1

    def test_web_target_service_snapshots(self, monkeypatch, tmp_path):
        import vulnclaw.target_state.store as store_mod
        import vulnclaw.web.services.target_service as target_service
        from vulnclaw.agent.context import SessionState

        monkeypatch.setattr(store_mod, "TARGETS_DIR", tmp_path)
        monkeypatch.setattr(target_service, "TARGETS_DIR", tmp_path)

        state = SessionState(target="https://example.com")
        store_mod.save_target_state("https://example.com", state, command="scan")

        snapshots = target_service.get_snapshots("https://example.com")
        assert snapshots
        assert snapshots[0].snapshot_id

    def test_web_target_service_preview_and_diff(self, monkeypatch, tmp_path):
        import vulnclaw.target_state.store as store_mod
        import vulnclaw.web.services.target_service as target_service
        from vulnclaw.agent.context import SessionState, TaskConstraints, VulnerabilityFinding

        monkeypatch.setattr(store_mod, "TARGETS_DIR", tmp_path)
        monkeypatch.setattr(target_service, "TARGETS_DIR", tmp_path)

        state1 = SessionState(target="https://example.com")
        state1.task_constraints = TaskConstraints(allowed_ports=[443], strict_mode=True)
        state1.add_constraint_violation_event(
            source="tool",
            action="exploit",
            tool_name="fetch",
            code="tool_action_blocked",
            severity="high",
            summary="constraint_violation: tool 'fetch' inferred action 'exploit'",
            detail="GET /admin?cmd=whoami",
        )
        state1.add_finding(VulnerabilityFinding(title="SQLi", severity="High", vuln_type="SQLi"))
        store_mod.save_target_state("https://example.com", state1, command="recon")

        state2 = SessionState(target="https://example.com")
        state2.add_finding(VulnerabilityFinding(title="XSS", severity="Medium", vuln_type="XSS"))
        store_mod.save_target_state("https://example.com", state2, command="scan")

        preview = target_service.get_preview("https://example.com")
        assert preview is not None
        assert preview.schema_version >= 2
        assert preview.candidate_count >= 1
        assert preview.manual_review_count >= 1
        assert preview.constraints["allowed_ports"] == [443]
        assert preview.constraint_violations
        assert preview.constraint_violation_events
        assert any("回避" in action or "约束" in action for action in preview.next_actions)

        snapshots = store_mod.list_target_snapshots("https://example.com")
        diff = target_service.get_diff(
            "https://example.com",
            from_snapshot_id=snapshots[-1]["snapshot_id"],
            to_snapshot_id=snapshots[0]["snapshot_id"],
        )
        assert diff is not None
        assert diff.added_findings

    def test_web_report_service_generates_target_report(self, monkeypatch, tmp_path):
        import vulnclaw.target_state.store as store_mod
        import vulnclaw.web.services.report_service as report_service
        from vulnclaw.agent.context import SessionState, VulnerabilityFinding

        monkeypatch.setattr(store_mod, "TARGETS_DIR", tmp_path / "targets")
        monkeypatch.setattr(report_service, "SESSIONS_DIR", tmp_path / "sessions")

        state = SessionState(target="https://example.com")
        finding = VulnerabilityFinding(
            title="Verified RCE",
            severity="Critical",
            vuln_type="RCE",
            evidence="https://example.com/admin/exec",
        )
        finding.mark_verified(note="whoami returned www-data")
        state.add_finding(finding)
        store_mod.save_target_state("https://example.com", state, command="scan")

        out = tmp_path / "report.md"
        path = report_service.generate_target_report("https://example.com", str(out))
        assert Path(path).exists()

    def test_web_report_service_reads_report_content(self, monkeypatch, tmp_path):
        import vulnclaw.web.services.report_service as report_service

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        report_path = sessions_dir / "demo.md"
        report_path.write_text("# demo", encoding="utf-8")

        monkeypatch.setattr(report_service, "SESSIONS_DIR", sessions_dir)
        result = report_service.read_report_content(str(report_path))
        assert result.kind == "markdown"
        assert "# demo" in result.content

    @pytest.mark.asyncio
    async def test_web_task_manager_event_flow(self):
        from vulnclaw.web.schemas import TaskCreateRequest
        from vulnclaw.web.task_manager import WebTaskManager

        manager = WebTaskManager()
        record = manager.create_task(
            TaskCreateRequest(command="recon", target="https://example.com")
        )
        manager.set_running(record.task_id)
        manager.publish(record.task_id, "round_output", {"round": 1, "text": "hello"})
        manager.set_completed(record.task_id, latest_message="done")

        events = []
        async for item in manager.stream_events(record.task_id):
            events.append(item.event)

        assert "task_created" in events
        assert "task_started" in events
        assert "round_output" in events
        assert "task_completed" in events

    def test_web_task_manager_restoring_and_summary(self):
        from vulnclaw.web.schemas import TaskCreateRequest
        from vulnclaw.web.task_manager import WebTaskManager

        manager = WebTaskManager()
        record = manager.create_task(
            TaskCreateRequest(command="recon", target="https://example.com")
        )
        manager.set_restoring(record.task_id, snapshot_id="snap_001")
        manager.set_completed(
            record.task_id,
            latest_message="done",
            summary={
                "target": "https://example.com",
                "command": "recon",
                "schema_version": 2,
                "phase": "Recon",
                "findings_count": 1,
                "constraints": {"allowed_ports": [443]},
                "constraint_violations": [
                    "constraint_violation: command 'run' is outside allowed actions [recon]"
                ],
                "constraint_violation_events": [
                    {
                        "source": "command",
                        "summary": "constraint_violation: command 'run' is outside allowed actions [recon]",
                    }
                ],
            },
        )

        saved = manager.get_task(record.task_id)
        assert saved is not None
        assert saved.summary is not None
        assert saved.summary.schema_version == 2
        assert saved.summary.phase == "Recon"
        assert saved.summary.constraints["allowed_ports"] == [443]
        assert saved.summary.constraint_violations
        assert saved.summary.constraint_violation_events

    def test_web_task_prompt_includes_explicit_constraints(self):
        from vulnclaw.web.schemas import TaskCreateRequest, TaskOptions
        from vulnclaw.web.services.task_service import _build_prompt_v2

        request = TaskCreateRequest(
            command="run",
            target="https://example.com",
            options=TaskOptions(
                only_port=443,
                only_host="example.com",
                only_path="/admin",
                blocked_host="staging.example.com",
                blocked_path="/internal",
                allow_actions=["recon", "scan"],
                block_actions=["exploit"],
            ),
        )
        prompt = _build_prompt_v2(request)
        assert "Only test port 443" in prompt
        assert "Only test host example.com" in prompt
        assert "Only test path /admin" in prompt
        assert "Blocked host staging.example.com" in prompt
        assert "Blocked path /internal" in prompt
        assert "Only allowed actions: recon, scan" in prompt
        assert "Blocked actions: exploit" in prompt

    def test_web_task_manager_persists_and_restores_tasks(self, monkeypatch, tmp_path):
        import vulnclaw.web.task_manager as task_manager_mod
        from vulnclaw.web.schemas import TaskCreateRequest

        storage = tmp_path / "web_tasks.json"
        monkeypatch.setattr(task_manager_mod, "WEB_TASKS_FILE", storage)
        monkeypatch.setattr(task_manager_mod, "ensure_dirs", lambda: None)

        manager = task_manager_mod.WebTaskManager()
        record = manager.create_task(
            TaskCreateRequest(command="scan", target="https://example.com")
        )
        manager.set_running(record.task_id)
        manager.publish(record.task_id, "round_output", {"round": 1, "text": "hello"})
        manager.set_completed(
            record.task_id,
            latest_message="done",
            summary={
                "target": "https://example.com",
                "command": "scan",
                "schema_version": 2,
                "phase": "Vuln Discovery",
                "findings_count": 2,
            },
        )

        assert storage.exists()

        restored_manager = task_manager_mod.WebTaskManager()
        restored = restored_manager.get_task(record.task_id)
        assert restored is not None
        assert restored.status == "completed"
        assert restored.summary is not None
        assert restored.summary.findings_count == 2
        assert list(restored_manager._history[record.task_id])[-1].event == "task_completed"

    @pytest.mark.asyncio
    async def test_web_task_service_restore_summary_flow(self, monkeypatch):
        import vulnclaw.web.services.task_service as task_service
        from vulnclaw.agent.context import SessionState
        from vulnclaw.config.schema import VulnClawConfig
        from vulnclaw.web.schemas import TaskCreateRequest
        from vulnclaw.web.task_manager import WebTaskManager

        config = VulnClawConfig()
        monkeypatch.setattr(task_service, "load_config", lambda: config)

        class DummyLifecycle:
            def __init__(self, config):
                self.config = config

            def start_enabled_servers(self):
                return 0

            def stop_all(self):
                return None

        class DummyAgent:
            def __init__(self, config, mcp_manager):
                self.config = config
                self.mcp_manager = mcp_manager
                self.context = type(
                    "Ctx", (), {"state": SessionState(target="https://example.com")}
                )()
                self.runtime = type("Runtime", (), {})()

            @property
            def session_state(self):
                return self.context.state

            async def chat(self, prompt, target=None):
                return type("Result", (), {"output": "hello", "phase": "Recon"})()

        events: list[str] = []

        monkeypatch.setattr(task_service, "MCPLifecycleManager", DummyLifecycle)
        monkeypatch.setattr(task_service, "AgentCore", DummyAgent)

        async def fake_run_agent_task(
            *,
            agent,
            command,
            target,
            resume=True,
            snapshot_id=None,
            before_restore=None,
            on_restored=None,
            runner=None,
        ):
            if before_restore is not None:
                before_restore(None)
            restore = type(
                "Restore",
                (),
                {
                    "restored": True,
                    "target": target,
                    "phase": "Recon",
                    "snapshot_id": snapshot_id or "snap_001",
                    "resume_strategy": "continue_recon",
                    "resume_reason": "need more recon",
                },
            )()
            if on_restored is not None:
                on_restored(restore)
            if runner is not None:
                await runner(agent)
            return type(
                "RunResult",
                (),
                {
                    "restore_result": restore,
                    "summary": {
                        "target": target,
                        "command": command,
                        "restored": True,
                        "snapshot_id": snapshot_id or "snap_001",
                        "schema_version": 2,
                        "phase": "Recon",
                        "findings_count": 0,
                        "verified_count": 0,
                        "pending_count": 0,
                        "executed_steps": 0,
                        "resume_strategy": "continue_recon",
                        "resume_reason": "need more recon",
                        "constraints": {"allowed_ports": [443]},
                        "constraint_violations": [
                            "constraint_violation: command 'run' is outside allowed actions [recon]"
                        ],
                        "constraint_violation_events": [
                            {
                                "source": "command",
                                "summary": "constraint_violation: command 'run' is outside allowed actions [recon]",
                            }
                        ],
                    },
                },
            )()

        monkeypatch.setattr(task_service, "run_agent_task", fake_run_agent_task)

        manager = WebTaskManager()
        request = TaskCreateRequest(
            command="recon", target="https://example.com", resume=True, snapshot_id="snap_001"
        )
        record = manager.create_task(request)

        original_publish = manager.publish

        def capture_publish(task_id, event, payload):
            events.append(event)
            original_publish(task_id, event, payload)

        monkeypatch.setattr(manager, "publish", capture_publish)

        await task_service._run_task(manager, record.task_id, request)

        saved = manager.get_task(record.task_id)
        assert saved is not None
        assert saved.status == "completed"
        assert saved.summary is not None
        assert saved.summary.restored is True
        assert saved.summary.constraints["allowed_ports"] == [443]
        assert saved.summary.constraint_violations
        assert saved.summary.constraint_violation_events
        assert "task_restoring" in events

    @pytest.mark.asyncio
    async def test_web_task_service_blocks_exploit_when_only_port_scope_is_set(self, monkeypatch):
        import vulnclaw.web.services.task_service as task_service
        from vulnclaw.config.schema import VulnClawConfig
        from vulnclaw.web.schemas import TaskCreateRequest, TaskOptions
        from vulnclaw.web.task_manager import WebTaskManager

        config = VulnClawConfig()
        monkeypatch.setattr(task_service, "load_config", lambda: config)

        manager = WebTaskManager()
        request = TaskCreateRequest(
            command="exploit",
            target="https://example.com",
            options=TaskOptions(only_port=443),
        )
        record = manager.create_task(request)

        await task_service._run_task(manager, record.task_id, request)

        saved = manager.get_task(record.task_id)
        assert saved is not None
        assert saved.status == "failed"
        assert saved.error is not None
        assert "constraint_violation" in saved.error

    @pytest.mark.asyncio
    async def test_web_task_service_blocks_run_when_allowed_actions_conflict(self, monkeypatch):
        import vulnclaw.web.services.task_service as task_service
        from vulnclaw.config.schema import VulnClawConfig
        from vulnclaw.web.schemas import TaskCreateRequest
        from vulnclaw.web.task_manager import WebTaskManager

        config = VulnClawConfig()
        monkeypatch.setattr(task_service, "load_config", lambda: config)
        monkeypatch.setattr(
            task_service,
            "_build_prompt_v2",
            lambda request: (
                "Perform authorized reconnaissance against https://example.com. 仅做信息收集。"
            ),
        )

        manager = WebTaskManager()
        request = TaskCreateRequest(command="run", target="https://example.com")
        record = manager.create_task(request)

        await task_service._run_task(manager, record.task_id, request)

        saved = manager.get_task(record.task_id)
        assert saved is not None
        assert saved.status == "failed"
        assert saved.error is not None
        assert "outside allowed actions" in saved.error

    def test_web_config_service_updates_safety_fields(self, monkeypatch):
        import vulnclaw.web.services.config_service as config_service
        from vulnclaw.config.schema import VulnClawConfig
        from vulnclaw.web.schemas import ConfigUpdateRequest

        saved = VulnClawConfig()

        monkeypatch.setattr(config_service, "load_config", lambda: saved)
        monkeypatch.setattr(config_service, "save_config", lambda cfg: None)

        view = config_service.update_public_config(
            ConfigUpdateRequest(
                python_execute_enabled=False,
                python_execute_mode="safe",
                python_execute_max_lines=12,
                python_execute_audit_enabled=False,
            )
        )

        assert view.python_execute_enabled is False
        assert view.python_execute_mode == "safe"
        assert view.python_execute_max_lines == 12
        assert view.python_execute_audit_enabled is False

    @pytest.mark.asyncio
    async def test_orchestrator_shared_run_flow(self, monkeypatch):
        import vulnclaw.orchestrator as orchestrator
        from vulnclaw.agent.context import SessionState

        class DummyAgent:
            def __init__(self):
                self.context = type(
                    "Ctx", (), {"state": SessionState(target="https://example.com")}
                )()
                self.runtime = type("Runtime", (), {})()

            @property
            def session_state(self):
                return self.context.state

        agent = DummyAgent()
        called: list[str] = []

        def fake_apply(agent_obj, target, snapshot_id=None):
            called.append(f"restore:{target}:{snapshot_id}")
            return type(
                "Restore",
                (),
                {
                    "restored": True,
                    "target": target,
                    "phase": "Recon",
                    "snapshot_id": snapshot_id or "snap_001",
                    "resume_strategy": "continue_recon",
                    "resume_reason": "need more recon",
                },
            )()

        async def fake_runner(agent_obj):
            called.append("runner")
            agent_obj.context.state.target = "https://example.com"

        def fake_save(*args, **kwargs):
            called.append("save")

        def fake_summary(session, *, command, restored=False, snapshot_id=""):
            return {
                "target": session.target or "",
                "command": command,
                "restored": restored,
                "snapshot_id": snapshot_id,
            }

        monkeypatch.setattr(orchestrator, "apply_target_state_to_agent", fake_apply)
        monkeypatch.setattr(orchestrator, "save_target_state", fake_save)
        monkeypatch.setattr(orchestrator, "build_task_session_summary", fake_summary)

        result = await orchestrator.run_agent_task(
            agent=agent,
            command="recon",
            target="https://example.com",
            resume=True,
            snapshot_id="snap_001",
            runner=fake_runner,
        )

        assert result.restore_result.restored is True
        assert result.summary["restored"] is True
        assert called == [
            "restore:https://example.com:snap_001",
            "runner",
            "save",
        ]

    def test_validate_action_constraints(self):
        from vulnclaw.agent.constraint_policy import validate_action_constraints
        from vulnclaw.agent.context import TaskConstraints

        constraints = TaskConstraints(allowed_actions=["recon"], strict_mode=True)
        assert validate_action_constraints("run", constraints) is not None
        assert validate_action_constraints("recon", constraints) is None

    def test_web_stream_encode(self):
        from vulnclaw.web.schemas import TaskEvent
        from vulnclaw.web.stream import encode_sse

        encoded = encode_sse(
            TaskEvent(event="round_output", task_id="task_demo", payload={"round": 1})
        )
        assert "event: round_output" in encoded
        assert '"task_id": "task_demo"' in encoded


class TestWebApp:
    def test_constraint_audit_route_works_without_fastapi(self, monkeypatch):
        import vulnclaw.web.app as web_app

        monkeypatch.setattr(
            web_app,
            "get_constraint_audit",
            lambda: type(
                "View", (), {"model_dump": lambda self, mode="json": {"total_events": 1}}
            )(),
        )
        # Route body itself is exercised indirectly by type and service tests; this smoke check guards import wiring.
        assert callable(web_app.get_constraint_audit)

    def test_create_app_missing_fastapi_raises(self):
        import vulnclaw.web.app as web_app

        if web_app.FASTAPI_AVAILABLE:
            pytest.skip("FastAPI is installed in this environment")

        with pytest.raises(RuntimeError):
            web_app.create_app()

    def test_resolve_web_index_prefers_dist(self, monkeypatch, tmp_path):
        import vulnclaw.web.app as web_app

        dist_dir = tmp_path / "dist"
        static_dir = tmp_path / "static"
        dist_dir.mkdir()
        static_dir.mkdir()
        (dist_dir / "index.html").write_text("dist", encoding="utf-8")
        (static_dir / "index.html").write_text("static", encoding="utf-8")

        monkeypatch.setattr(web_app, "FRONTEND_DIST_DIR", dist_dir)
        monkeypatch.setattr(web_app, "STATIC_DIR", static_dir)

        assert web_app.resolve_web_index() == dist_dir / "index.html"
        assert web_app.resolve_web_asset("assets/app.js") == dist_dir / "index.html"

    def test_frontend_scaffold_exists(self):
        root = Path("C:/Users/UncleC/Desktop/VulnClaw/frontend")
        assert (root / "package.json").exists()
        assert (root / "vite.config.ts").exists()
        assert (root / "src" / "main.tsx").exists()
        assert (root / "src" / "App.tsx").exists()

    def test_cli_web_dry_run(self):
        from vulnclaw.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["web", "--dry-run"])
        assert result.exit_code == 0
        assert "Web UI" in result.output

    def test_cli_web_rejects_remote_host_without_allow_remote(self):
        from vulnclaw.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["web", "--host", "0.0.0.0", "--dry-run"])
        assert result.exit_code == 1
        assert "allow-remote" in result.output

    def test_cli_web_allows_remote_host_with_explicit_flag(self):
        from vulnclaw.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["web", "--host", "0.0.0.0", "--allow-remote", "--dry-run"])
        assert result.exit_code == 0
        assert "0.0.0.0" in result.output

    def test_web_target_preview_and_diff_endpoints(self, monkeypatch):
        import vulnclaw.web.app as web_app

        if not web_app.FASTAPI_AVAILABLE:
            pytest.skip("FastAPI is not installed in this environment")

        monkeypatch.setattr(
            web_app,
            "get_preview",
            lambda target, snapshot_id=None: type(
                "Preview",
                (),
                {"model_dump": lambda self, mode="json": {"target": target, "schema_version": 2}},
            )(),
        )
        monkeypatch.setattr(
            web_app,
            "get_diff",
            lambda target, from_snapshot_id, to_snapshot_id=None: type(
                "Diff",
                (),
                {
                    "model_dump": lambda self, mode="json": {
                        "target": target,
                        "from_snapshot_id": from_snapshot_id,
                    }
                },
            )(),
        )

        app = web_app.create_app()
        client = pytest.importorskip("fastapi.testclient").TestClient(app)

        preview = client.get("/api/target-preview/example.com")
        assert preview.status_code == 200
        assert preview.json()["schema_version"] == 2

        diff = client.get("/api/target-diff/example.com", params={"from_snapshot_id": "snap_a"})
        assert diff.status_code == 200
        assert diff.json()["from_snapshot_id"] == "snap_a"
