import io
import sys

import pytest

from dbbackup.utils.process import ProcessError, ToolNotFoundError, run_process


def py(code):
    return [sys.executable, "-c", code]


def test_streams_large_stdout():
    sink = io.BytesIO()
    run_process(py("import sys; sys.stdout.buffer.write(b'x' * 3000000)"), stdout_stream=sink)
    assert len(sink.getvalue()) == 3000000


def test_feeds_stdin_and_captures_stdout():
    code = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])"
    assert run_process(py(code), stdin_stream=io.BytesIO(b"abc")) == b"cba"


def test_nonzero_exit_raises_with_stderr():
    with pytest.raises(ProcessError) as exc_info:
        run_process(py("import sys; sys.stderr.write('boom'); sys.exit(3)"))
    assert exc_info.value.returncode == 3
    assert "boom" in exc_info.value.stderr


def test_tool_not_found():
    with pytest.raises(ToolNotFoundError):
        run_process(["outil-qui-nexiste-pas-xyz"])


def test_env_is_passed():
    code = "import os, sys; sys.stdout.write(os.environ['DBB_TEST'])"
    assert run_process(py(code), env={"DBB_TEST": "ok"}) == b"ok"


def test_process_exiting_early_does_not_hang():
    run_process(py("import sys; sys.exit(0)"), stdin_stream=io.BytesIO(b"x" * 5000000))


def test_timeout():
    with pytest.raises(ProcessError, match="délai"):
        run_process(py("import time; time.sleep(30)"), timeout=1)
