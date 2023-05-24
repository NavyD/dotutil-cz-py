import subprocess as sp

from dotutil import elevate


def test_py_run():
    s = "pyrun"
    p = elevate.py_run(f"print('{s}', end='')", text=True, stdout=sp.PIPE)
    assert p.returncode == 0
    assert p.stdout == s
    # pass


def test_py_popen():
    data = "pyrun\nnextline\nthenline"
    # for line in s.splitlines():
    with elevate.py_popen(
        # escape
        f"""s = {repr(data)}
for line in s.splitlines():
    print(line, flush=True)""",
        stdout=sp.PIPE,
        text=True,
    ) as p:
        res = p.stdout.readlines()
        assert list(map(lambda s: s.rstrip(), res)) == data.splitlines()
        assert p.wait() == 0
