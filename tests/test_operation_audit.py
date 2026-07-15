#!/usr/bin/env python3
"""Executable acceptance tests for unified operation audit and diagnostics."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DOUYIN_SCRIPTS = PROJECT_ROOT / "deps" / "douyin" / "scripts"
if str(DOUYIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(DOUYIN_SCRIPTS))

from server.operation_audit import (  # noqa: E402
    AUDIT_COVERAGE_MATRIX,
    OperationAuditStore,
    OperationWebSocket,
)
from server.github_tasks import GitHubTaskStore  # noqa: E402
from server.websocket_server import LibrarianServer  # noqa: E402
from status_writer import StatusWriter  # noqa: E402


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, value: str) -> None:
        self.messages.append(value)


class OperationAuditStoreTests(unittest.TestCase):
    def test_stage_order_and_failure_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = OperationAuditStore(raw)
            store.ensure_operation(
                "op-order",
                operation_type="task.ingest",
                task_id="task-1",
                stage="accepted",
            )
            store.record_event("op-order", stage="queued", state="started")
            store.record_event("op-order", stage="worker_started", state="started")
            store.finish(
                "op-order",
                stage="download_failed",
                state="failed",
                error={
                    "code": "network_error",
                    "type": "NetworkError",
                    "stage": "download",
                    "message": "connection reset",
                    "retryable": True,
                },
                error_code="network_error",
                retryable=True,
            )

            restored = OperationAuditStore(raw).get("op-order")
            self.assertIsNotNone(restored)
            assert restored is not None
            events = restored["events"]
            self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
            self.assertEqual(
                [event["stage"] for event in events],
                ["accepted", "queued", "worker_started", "download_failed"],
            )
            self.assertEqual(restored["summary"]["state"], "failed")
            self.assertEqual(restored["summary"]["error"]["code"], "network_error")
            self.assertTrue(restored["summary"]["error"]["retryable"])
            self.assertTrue(Path(restored["diagnostics"]["index"]).is_file())

    def test_restart_recovery_keeps_operation_queryable_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            first = OperationAuditStore(raw)
            first.ensure_operation(
                "op-restart",
                operation_type="task.ingest",
                task_id="task-restart",
                stage="subprocess_started",
            )
            recovered = OperationAuditStore(raw)
            self.assertEqual(recovered.recover_incomplete(), ["op-restart"])
            self.assertEqual(recovered.recover_incomplete(), [])
            payload = recovered.get("op-restart")
            assert payload is not None
            self.assertEqual(payload["summary"]["state"], "started")
            self.assertEqual(payload["events"][-1]["stage"], "service_restart_recovery")
            self.assertTrue(payload["events"][-1]["result"]["recovered"])

    def test_success_after_retry_clears_current_error_summary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = OperationAuditStore(raw)
            store.ensure_operation("op-retry-success", operation_type="github.auth")
            store.record_event(
                "op-retry-success",
                stage="github_auth_poll_retrying",
                state="started",
                error={"code": "network_error", "message": "temporary", "retryable": True},
                error_code="network_error",
                retryable=True,
            )
            store.finish("op-retry-success", stage="github_auth_ready", state="succeeded")
            payload = store.get("op-retry-success")
            assert payload is not None
            self.assertEqual(payload["summary"]["state"], "succeeded")
            self.assertEqual(payload["summary"]["error"], {})

    def test_concurrent_operations_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = OperationAuditStore(raw)
            operation_ids = [f"op-concurrent-{index}" for index in range(4)]
            for operation_id in operation_ids:
                store.ensure_operation(operation_id, operation_type="test.concurrent", stage="accepted")

            def write_events(operation_id: str) -> None:
                local = OperationAuditStore(raw)
                for index in range(20):
                    local.record_event(
                        operation_id,
                        stage=f"step_{index:02d}",
                        result={"owner": operation_id, "index": index},
                    )
                local.finish(operation_id, stage="done", state="succeeded")

            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(write_events, operation_ids))

            for operation_id in operation_ids:
                payload = store.get(operation_id)
                assert payload is not None
                events = payload["events"]
                self.assertEqual(len(events), 22)
                self.assertEqual({event["operationId"] for event in events}, {operation_id})
                self.assertEqual([event["sequence"] for event in events], list(range(1, 23)))
                owners = {event.get("result", {}).get("owner") for event in events if event.get("result", {}).get("owner")}
                self.assertEqual(owners, {operation_id})

    def test_secrets_are_redacted_from_summary_timeline_and_index(self) -> None:
        cookie_secret = "session" + "id=raw-cookie-secret"
        authorization_secret = "Bear" + "er raw-authorization-secret"
        github_secret = "ghp" + "_rawgithubsecret1234567890"
        secrets = {
            "api_key": "ark-secret-raw-value",
            "Cookie": cookie_secret,
            "Authorization": authorization_secret,
            "github_token": github_secret,
            "device_code": "raw-device-code",
            "user_code": "RAW-USER-CODE",
            "auth_response": {"access_token": "raw-access-token"},
            "response_id": "resp-raw-model-id",
        }
        with tempfile.TemporaryDirectory() as raw:
            store = OperationAuditStore(raw)
            store.ensure_operation(
                "op-secret",
                operation_type="control.settings_save",
                params={
                    **secrets,
                    "url": "https://example.test/path?token=query-secret&safe=ok",
                },
            )
            store.finish(
                "op-secret",
                stage="failed",
                state="failed",
                error={
                    "code": "auth_failed",
                    "message": "Authorization: Bearer error-secret",
                    "retryable": False,
                },
                result=secrets,
            )
            persisted = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in Path(raw).rglob("*")
                if path.is_file()
            )
            for secret in (
                "ark-secret-raw-value",
                "raw-cookie-secret",
                "raw-authorization-secret",
                "rawgithubsecret",
                "raw-device-code",
                "RAW-USER-CODE",
                "raw-access-token",
                "raw-model-id",
                "query-secret",
                "error-secret",
            ):
                self.assertNotIn(secret, persisted)
            self.assertIn("[REDACTED]", persisted)
            self.assertIn("safe=ok", persisted)

    def test_artifacts_are_referenced_without_copying_large_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            artifact = runtime / "run-artifacts" / "task-1" / "prompt.md"
            artifact.parent.mkdir(parents=True)
            artifact_content = "FULL PROMPT AND RESPONSE " * 1000
            artifact.write_text(artifact_content, encoding="utf-8")
            store = OperationAuditStore(runtime)
            store.ensure_operation("op-artifact", operation_type="task.ingest")
            store.finish(
                "op-artifact",
                stage="model_completed",
                state="succeeded",
                result={"responseChars": len(artifact_content)},
                artifacts=[{"kind": "model_prompt", "ref": str(artifact), "bytes": artifact.stat().st_size}],
            )
            diagnostics_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (runtime / "operations").rglob("*")
                if path.is_file()
            )
            self.assertIn(str(artifact), diagnostics_text)
            self.assertNotIn(artifact_content, diagnostics_text)
            self.assertNotIn("FULL PROMPT AND RESPONSE FULL PROMPT", diagnostics_text)

    def test_status_writer_projects_task_lifecycle_and_artifact_reference(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            writer = StatusWriter(
                "task-status",
                runtime / "status",
                operation_id="op-status",
                operation_type="task.ingest",
            )
            writer.update(stage="source_identified", source_type="douyin")
            writer.progress("download", {"pct": 50})
            writer.update(
                stage="done",
                ok=True,
                vault_path="/tmp/vault/asset.md",
                audit_artifacts={"dir": "run-artifacts/task-status"},
            )
            payload = OperationAuditStore(runtime).get("op-status")
            assert payload is not None
            stages = [event["stage"] for event in payload["events"]]
            self.assertLess(stages.index("source_identified"), stages.index("download"))
            self.assertLess(stages.index("download"), stages.index("done"))
            self.assertEqual(payload["summary"]["state"], "succeeded")
            self.assertEqual(payload["summary"]["taskId"], "task-status")
            self.assertTrue(any(ref["ref"] == "run-artifacts/task-status" for ref in payload["summary"]["artifacts"]))

    def test_websocket_reply_inherits_operation_context(self) -> None:
        async def scenario() -> dict[str, object]:
            raw = FakeWebSocket()
            audited = OperationWebSocket(
                raw,
                operation_id="op-wire",
                task_id="task-wire",
                parent_id="op-parent",
            )
            await audited.send(json.dumps({"type": "reply"}))
            return json.loads(raw.messages[0])

        payload = asyncio.run(scenario())
        self.assertEqual(payload["operationId"], "op-wire")
        self.assertEqual(payload["taskId"], "task-wire")
        self.assertEqual(payload["parentId"], "op-parent")


class ControlPlaneAuditTests(unittest.TestCase):
    def test_github_batch_and_failed_child_keep_operation_relationship_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "github" / "tasks"
            store = GitHubTaskStore(root)
            batch, created = store.create_batch(
                [{"id": 42, "fullName": "example/project"}],
                request_key="request-1",
                operation_id="op-github-batch",
                parent_id="op-github-parent",
            )
            self.assertTrue(created)
            item = batch["items"][0]
            store.begin_batch(batch["id"])
            store.begin_item(batch["id"], item["taskId"])
            store.fail_item(
                batch["id"],
                item["taskId"],
                code="mock_failure",
                message="mock child failure",
            )
            store.finalize_batch(batch["id"])

            restored = GitHubTaskStore(root).get_batch(batch["id"])
            assert restored is not None
            self.assertEqual(restored["operationId"], "op-github-batch")
            self.assertEqual(restored["parentId"], "op-github-parent")
            self.assertEqual(restored["items"][0]["operationId"], item["operationId"])
            self.assertEqual(restored["items"][0]["parentId"], "op-github-batch")
            self.assertEqual(restored["items"][0]["state"], "failed")
            self.assertEqual(restored["items"][0]["error"]["code"], "mock_failure")

    def test_cookie_transport_payload_is_never_written_to_operation_logs(self) -> None:
        async def scenario(runtime: Path) -> None:
            with mock.patch.dict(os.environ, {"AGENT_WIKI_HOME": str(runtime)}):
                server = LibrarianServer(enable_task_runner=False)
                await server.handle_message(FakeWebSocket(), {
                "type": "cookie_update",
                    "operationId": "op-cookie",
                    "platform": "douyin",
                    "data": "session" + "id=raw-cookie-transport-secret; sid_guard=another-secret",
                })

        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            asyncio.run(scenario(runtime))
            operations_text = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in (runtime / "operations").rglob("*")
                if path.is_file()
            )
            self.assertNotIn("raw-cookie-transport-secret", operations_text)
            self.assertNotIn("another-secret", operations_text)
            self.assertIn('"characters"', operations_text)

    def test_task_projection_cancel_retry_and_parent_link_are_persistent(self) -> None:
        async def scenario(runtime: Path) -> tuple[dict, dict, dict]:
            with mock.patch.dict(os.environ, {"AGENT_WIKI_HOME": str(runtime)}):
                server = LibrarianServer(enable_task_runner=False)
                accepted = await server.handle_task_request({
                    "type": "task_request",
                    "operationId": "op-submit",
                    "requestId": "request-submit",
                    "url": "https://www.douyin.com/video/123456789",
                    "source": "extension_popup",
                })
                task_id = accepted["task"]["id"]
                public = server.task_status_snapshot()["items"][0]
                cancelled = await server.handle_task_control({
                    "type": "task_cancel",
                    "operationId": "op-cancel",
                    "taskId": task_id,
                })
                await server.run_task_file(runtime / "inbox" / f"{task_id}.json")
                retried = await server.handle_task_control({
                    "type": "task_retry",
                    "operationId": "op-retry",
                    "taskId": task_id,
                })
                return public, cancelled, retried

        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            public, cancelled, retried = asyncio.run(scenario(runtime))
            self.assertEqual(public["operationId"], "op-submit")
            self.assertEqual(public["diagnostics"]["operationId"], "op-submit")
            self.assertEqual(cancelled["parentId"], "op-submit")
            self.assertFalse((runtime / "inbox" / f"{cancelled['taskId']}.json").exists())
            self.assertTrue((runtime / "failed" / f"{cancelled['taskId']}.json").exists())
            self.assertEqual(retried["operationId"], "op-retry")
            self.assertEqual(retried["parentId"], "op-submit")
            retry_payload = OperationAuditStore(runtime).get("op-retry")
            assert retry_payload is not None
            self.assertEqual(retry_payload["summary"]["parentId"], "op-submit")
            self.assertEqual(retry_payload["summary"]["taskId"], retried["taskId"])
            submit_payload = OperationAuditStore(runtime).get("op-submit")
            assert submit_payload is not None
            self.assertEqual(submit_payload["summary"]["state"], "cancelled")

    def test_derived_child_gets_distinct_operation_and_parent(self) -> None:
        async def scenario(runtime: Path) -> tuple[dict, dict]:
            with mock.patch.dict(os.environ, {"AGENT_WIKI_HOME": str(runtime)}):
                server = LibrarianServer(enable_task_runner=False)
                parent_asset = runtime / "vault" / "parent.md"
                parent_asset.parent.mkdir(parents=True)
                parent_asset.write_text("# parent\n", encoding="utf-8")
                parent_status = {
                    "id": "parent-task",
                    "operation_id": "op-parent",
                    "ok": True,
                    "vault_path": str(parent_asset),
                    "source_url": "https://www.douyin.com/video/1",
                }
                server.audit_store.ensure_operation(
                    "op-parent",
                    operation_type="task.ingest",
                    task_id="parent-task",
                    stage="done",
                )
                child = await server.enqueue_derived_candidate(
                    "parent-task",
                    parent_status,
                    {
                        "id": "candidate-1",
                        "name": "Example Project",
                        "targetType": "github_project",
                        "targetUrl": "https://github.com/example/project",
                    },
                )
                child_status = json.loads(
                    (runtime / "status" / f"{child['id']}.json").read_text(encoding="utf-8")
                )
                return child, child_status

        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            child, status = asyncio.run(scenario(runtime))
            self.assertNotEqual(status["operation_id"], "op-parent")
            payload = OperationAuditStore(runtime).get(status["operation_id"])
            assert payload is not None
            self.assertEqual(payload["summary"]["parentId"], "op-parent")
            self.assertEqual(payload["summary"]["taskId"], child["id"])


class CoverageMatrixTests(unittest.TestCase):
    def test_acceptance_matrix_keeps_all_nine_categories_verbatim(self) -> None:
        self.assertEqual(list(AUDIT_COVERAGE_MATRIX), list(range(1, 10)))
        expected_titles = (
            "扩展与控制面：扩展启动、握手、版本兼容、状态刷新、设置保存、Cookie 同步、用户提交/取消/重试以及服务回复。",
            "通用任务系统：任务接收、入队、排队、并发调度、worker 启动、阶段切换、超时、重试、取消、完成、失败和服务重启恢复。",
            "来源获取：URL/来源识别、元数据读取、下载、Cookie 可用性结果、ffprobe/媒体探测、文件校验；不记录 Cookie 本身。",
            "视频拆解：预扫描、画面变化、自动/固定 FPS 决策、分段、实际抽帧、上传/模型请求、响应元数据、Token 与成本、汇总和失败兜底。",
            "图文/网页/GitHub 等其他来源：来源抓取、清洗、统一模型处理和结果验证。",
            "知识资产生成：简洁概括、完整整理、AI 分析的生成阶段，结构解析/校验，标题、标签、文件命名、文件写入、索引更新；大型 prompt/完整响应继续放现有 run-artifacts，只在统一时间线保存摘要与引用。",
            "派生策略全流程：候选产生、证据、筛选/保留/忽略原因、GitHub 官方 API 目标解析、歧义待确认、子任务创建、派生执行、父子关系、已有资产去重、成功/失败。",
            "GitHub 登录、Stars、资产、自动 Star、刷新。",
            "知识库扫描、新建、切换、迁移和回退。",
        )
        self.assertEqual(
            tuple(AUDIT_COVERAGE_MATRIX[index]["title"] for index in range(1, 10)),
            expected_titles,
        )

    def test_every_matrix_stage_is_wired_in_executable_source(self) -> None:
        for index, category in AUDIT_COVERAGE_MATRIX.items():
            source = "\n".join(
                (PROJECT_ROOT / relative).read_text(encoding="utf-8")
                for relative in category["modules"]
            )
            for stage in category["stages"]:
                with self.subTest(category=index, stage=stage):
                    if stage not in source:
                        self.fail(f"category {index} is missing executable marker: {stage}")


if __name__ == "__main__":
    unittest.main()
