from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import sys
import termios


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="exec a command with a controlling PTY")
    parser.add_argument("--slave-fd", required=True, type=int)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    argv = list(args.argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("pty helper requires a command", file=sys.stderr)
        os._exit(127)

    slave_fd = args.slave_fd
    try:
        with contextlib.suppress(OSError):
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.chdir(args.cwd)
        os.execvpe(argv[0], argv, os.environ.copy())
    except OSError as exc:
        print(f"failed to exec {argv[0]}: {exc}", file=sys.stderr)
    os._exit(127)


if __name__ == "__main__":
    main()
