import hashlib
import json

from verify_and_repair.repair_context import compile_context


def test_context_selects_failure_dependencies_and_caller_not_unrelated_code(tmp_path):
    source = (
        "from decimal import Decimal\n\n"
        "RATE = Decimal('0.2')\n\n"
        "def total(value: Decimal) -> Decimal:\n"
        "    return value + RATE\n\n"
        "def invoice():\n"
        "    return total(Decimal('0.1'))\n\n"
        "def unrelated():\n"
        "    return 'unrelated secret is not context'\n"
    )
    (tmp_path / "money.py").write_text(source, encoding="utf-8")
    (tmp_path / "private.py").write_text("PRIVATE_EVALUATOR_SENTINEL")
    packet = compile_context(
        tmp_path,
        ("money.py",),
        (),
        "assert total(Decimal('0.1')) == Decimal('0.30')",
        12000,
    )
    snippets = packet["snippets"]
    text = json.dumps(packet)
    assert "failure_target" in text and "dependency" in text and "caller" in text
    assert "unrelated secret" not in text and "PRIVATE_EVALUATOR" not in text
    assert snippets[0]["symbol"] == "total"
    assert (
        snippets[0]["sha256"]
        == hashlib.sha256((tmp_path / "money.py").read_bytes()).hexdigest()
    )


def test_context_is_bounded_and_reports_omitted_symbols(tmp_path):
    (tmp_path / "large.py").write_text(
        "def target():\n" + "    value = 'a long line of irrelevant data'\n" * 1000
    )
    packet = compile_context(tmp_path, ("large.py",), (), "assert target() == 1", 512)
    assert len(json.dumps(packet, ensure_ascii=True).encode("utf-8")) <= 512
    assert packet["needs_more_context"] and packet["omitted_snippets"]
    assert not packet["snippets"]


def test_non_python_context_uses_diagnostic_location_not_entire_file(tmp_path):
    (tmp_path / "logic.ts").write_text(
        "".join(f"const value{i} = {i};\n" for i in range(200))
    )
    packet = compile_context(tmp_path, ("logic.ts",), (), "logic.ts:100:2 error", 12000)
    snippet = packet["snippets"][0]
    assert snippet["start_line"] == 95 and snippet["end_line"] == 134
    assert "value99" in snippet["code"] and "value1 =" not in snippet["code"]


def test_public_context_files_are_marked_read_only(tmp_path):
    (tmp_path / "logic.py").write_text("def inc(x):\n    return x - 1\n")
    (tmp_path / "test_logic.py").write_text("def test_inc():\n    assert inc(0) == 1\n")
    packet = compile_context(
        tmp_path, ("logic.py",), ("test_logic.py",), "inc(0)", 12000
    )
    assert any(not item["editable"] for item in packet["snippets"])
