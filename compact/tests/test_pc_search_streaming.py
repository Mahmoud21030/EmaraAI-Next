"""PC search must not read complete files into memory."""
from pathlib import Path

from emaraai_hub.pc_native.runtime import NativePcRuntime


def test_pc_workspace_search_streams_lines_without_read_text(tmp_path, monkeypatch):
    path = tmp_path / "large-log.txt"
    path.write_text(("ordinary item\n" * 30000) + "TARGET_ONLY_AT_END\n", encoding="utf-8")
    original = Path.read_text

    def guarded_read_text(self, *args, **kwargs):
        if self == path:
            raise AssertionError("whole-file Path.read_text would risk a MemoryError")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    runtime = NativePcRuntime.__new__(NativePcRuntime)
    found = runtime._fs_search(tmp_path, query="TARGET_ONLY_AT_END")
    assert found["ok"], found
    assert len(found["result"]["matches"]) == 1
    assert found["result"]["matches"][0]["line"] == 30001


def test_pc_workspace_search_limit_and_regex_compatibility(tmp_path):
    path = tmp_path / "results.txt"
    path.write_text("alpha\nneedle one\nneedle two\nneedle three\n", encoding="utf-8")
    runtime = NativePcRuntime.__new__(NativePcRuntime)
    hits = runtime._fs_search(path, query="needle", limit=2)
    assert hits["ok"] and hits["result"]["truncated"] is True
    assert [h["line"] for h in hits["result"]["matches"]] == [2, 3]
    malformed = runtime._fs_search(path, query="needle|alpha|(", regex=True)
    assert malformed["ok"], malformed
    assert "alternatives" in malformed["summary"]
    assert [h["line"] for h in malformed["result"]["matches"]] == [1, 2, 3, 4]      # searched as plain text: "needle" or "alpha" is in every line
