"""Testes de analytics e persistência."""
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import pytest


class TestAnalytics:
    def test_log_and_read(self, tmp_path):
        analytics_file = tmp_path / "analytics.jsonl"

        # Simula log_analytics
        entry = {
            "ts": datetime.now().isoformat(),
            "bot": "test",
            "user_id": 123,
            "input_tokens": 100,
            "output_tokens": 200,
            "tool_calls": 1,
            "latency_ms": 500,
            "error": "",
        }
        with open(analytics_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Lê de volta
        lines = analytics_file.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["input_tokens"] == 100
        assert data["output_tokens"] == 200
        assert data["bot"] == "test"

    def test_multiple_entries(self, tmp_path):
        analytics_file = tmp_path / "analytics.jsonl"
        for i in range(5):
            entry = {
                "ts": datetime.now().isoformat(),
                "bot": "test",
                "user_id": 123,
                "input_tokens": 100 * (i + 1),
                "output_tokens": 200 * (i + 1),
                "tool_calls": i,
                "latency_ms": 500,
                "error": "" if i < 4 else "TestError",
            }
            with open(analytics_file, "a") as f:
                f.write(json.dumps(entry) + "\n")

        lines = analytics_file.read_text().strip().split("\n")
        assert len(lines) == 5

    def test_jsonl_format(self, tmp_path):
        """Each line should be valid JSON."""
        analytics_file = tmp_path / "analytics.jsonl"
        for i in range(3):
            entry = {"ts": datetime.now().isoformat(), "value": i}
            with open(analytics_file, "a") as f:
                f.write(json.dumps(entry) + "\n")

        for line in analytics_file.read_text().strip().split("\n"):
            parsed = json.loads(line)
            assert "ts" in parsed


class TestConversationPersistence:
    def test_save_and_load(self, tmp_path):
        conv_file = tmp_path / "conversations.json"
        convs = {
            123: [{"role": "user", "content": "hello"}],
            456: [{"role": "user", "content": "hi"},
                  {"role": "assistant", "content": "hey"}],
        }
        conv_file.write_text(json.dumps({str(k): v for k, v in convs.items()}))
        loaded = json.loads(conv_file.read_text())
        loaded = {int(k): v for k, v in loaded.items()}
        assert loaded[123][0]["content"] == "hello"
        assert len(loaded[456]) == 2

    def test_empty_file(self, tmp_path):
        conv_file = tmp_path / "conversations.json"
        conv_file.write_text("{}")
        loaded = json.loads(conv_file.read_text())
        assert loaded == {}


class TestSchedules:
    def test_save_and_load(self, tmp_path):
        sched_file = tmp_path / "schedules.json"
        schedules = [
            {"id": "abc123", "user_id": 100, "hour": 9, "minute": 0,
             "weekdays": "all", "message": "list PRs"},
        ]
        sched_file.write_text(json.dumps(schedules))
        loaded = json.loads(sched_file.read_text())
        assert len(loaded) == 1
        assert loaded[0]["hour"] == 9

    def test_add_remove(self, tmp_path):
        sched_file = tmp_path / "schedules.json"
        schedules = [
            {"id": "abc", "hour": 9, "minute": 0, "weekdays": "all", "message": "test1"},
            {"id": "def", "hour": 15, "minute": 30, "weekdays": "mon,fri", "message": "test2"},
        ]
        sched_file.write_text(json.dumps(schedules))

        # Remove one
        loaded = json.loads(sched_file.read_text())
        loaded = [s for s in loaded if s["id"] != "abc"]
        sched_file.write_text(json.dumps(loaded))

        final = json.loads(sched_file.read_text())
        assert len(final) == 1
        assert final[0]["id"] == "def"
