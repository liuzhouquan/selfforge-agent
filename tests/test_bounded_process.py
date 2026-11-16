"""The shared capture must bound producer execution, including child processes."""

import sys
import time

import pytest

from selfforge_agent.core.process import run_bounded


def test_timeout_reaps_process_group_before_delayed_child_write(tmp_path) -> None:
    marker = tmp_path / "child-finished"
    child = "import time,sys;from pathlib import Path;time.sleep(.6);Path(sys.argv[1]).write_text('ran')"
    parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);time.sleep(10)"
    with pytest.raises(TimeoutError, match="timed out"):
        run_bounded([sys.executable, "-c", parent, child, str(marker)], timeout=.3, max_bytes=1024)
    time.sleep(.7)
    assert not marker.exists()
