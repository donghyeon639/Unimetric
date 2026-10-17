import asyncio
import httpx

from unimetric.api.app import app
from unimetric.api.routes import builder, chat, metrics
from unimetric.api.schemas import AiMetricReq, Ask, ChatReq
from unimetric.ai import conversation


def test_static_page_and_provider_status_without_database():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            root = await client.get("/")
            assert root.status_code == 200
            assert 'id="llmProvider"' in root.text
            assert 'type="module" src="/assets/js/app.js"' in root.text
            assert (await client.get("/assets/app.css")).status_code == 200
            chat_script = await client.get("/assets/js/chat.js")
            assert chat_script.status_code == 200
            assert 'provider:selectedProvider()' in chat_script.text
            status = (await client.get("/api/llm-status")).json()
            assert {"codex_cli", "claude_cli"} <= {p["id"] for p in status["providers"]}
            assert (await client.get("/api/health")).status_code == 200
    asyncio.run(run())


def test_ask_endpoint_forwards_provider(monkeypatch):
    monkeypatch.setattr(metrics, "require_project", lambda *a: {})
    monkeypatch.setattr(metrics.engine, "get_schema", lambda *a: {"orders": []})
    monkeypatch.setattr(metrics.engine, "run_select", lambda *a: [{"value": 1}])
    def sql(question, schema, provider):
        assert provider == "codex_cli"
        return "SELECT 1 AS value"
    monkeypatch.setattr(metrics.text2sql, "question_to_sql", sql)
    assert metrics.ask(Ask(project="p", question="q", provider="codex_cli"))["rows"] == [{"value": 1}]


def test_metric_generation_endpoint_forwards_provider(monkeypatch):
    monkeypatch.setattr(builder, "require_project", lambda *a: {})
    monkeypatch.setattr(builder.engine, "get_schema", lambda *a: {"orders": [{"column": "id", "type": "int"}]})
    def generate(question, table, cols, provider):
        assert provider == "codex_cli"
        return '{"name":"orders_count"}'
    monkeypatch.setattr(builder.text2sql, "question_to_metric", generate)
    assert builder.builder_ai(AiMetricReq(project="p", table="orders", question="q", provider="codex_cli"))["spec"]["name"] == "orders_count"


def test_chat_and_compaction_use_same_request_provider(monkeypatch):
    monkeypatch.setattr(chat, "require_project", lambda *a: {})
    monkeypatch.setattr(conversation, "ensure_session", lambda *a: "session")
    monkeypatch.setattr(conversation, "add_message", lambda *a: None)
    monkeypatch.setattr(conversation, "history", lambda *a: ("", []))
    monkeypatch.setattr(chat.retrieval, "build_context", lambda *a: "context")
    def respond(messages, context, summary, provider):
        assert provider == "codex_cli"
        return '기간을 골라주세요.\n```json\n{"action":"clarify","options":["최근 7일"]}\n```'
    monkeypatch.setattr(chat.text2sql, "chat", respond)
    providers = []
    monkeypatch.setattr(conversation, "maybe_compact", lambda sid, provider: providers.append(provider))
    result = chat.chat(ChatReq(project="p", message="q", provider="codex_cli"))
    assert "clarify" in result
    assert providers == ["codex_cli"]
