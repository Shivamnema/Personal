"""
PyShell — A Bash-like shell for Windows built in pure Python.
No admin rights required. Features:
  - Tab-completion for commands, paths, and flags
  - Fuzzy folder/file finder
  - AI assistant (via Anthropic API) for command suggestions
  - Built-in Unix-like commands (ls, cat, grep, touch, mkdir, etc.)
  - Command history with arrow-key navigation
  - Piping & redirection support
"""

import os
import sys
import shutil
import subprocess
import readline
import fnmatch
import difflib
import datetime
import platform
import re
import json
import time
import glob
import stat
import hashlib
from pathlib import Path
from typing import Optional

# ─── Optional: AI assistance via Anthropic ──────────────────────────────────
try:
    import anthropic
    _AI_CLIENT = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    _AI_AVAILABLE = True
except ImportError:
    _AI_AVAILABLE = False

# ─── Color helpers ───────────────────────────────────────────────────────────
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    BGBLUE  = "\033[44m"
    BGGRAY  = "\033[100m"

def color(text, *codes):
    return "".join(codes) + text + C.RESET

def enable_ansi_windows():
    """Enable ANSI colors on Windows terminal."""
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

# ─── Completer ───────────────────────────────────────────────────────────────
BUILTIN_COMMANDS = [
    "ls", "ll", "la", "cd", "pwd", "mkdir", "mkdirp", "rm", "rmdir",
    "cp", "mv", "cat", "head", "tail", "touch", "echo", "clear", "cls",
    "env", "export", "unset", "which", "find", "grep", "wc", "sort",
    "uniq", "diff", "tree", "history", "alias", "unalias", "exit", "quit",
    "help", "ai", "ask", "open", "clip", "date", "whoami", "hostname",
    "ps", "kill", "df", "du", "hash", "type", "true", "false"
]

class PyShellCompleter:
    def __init__(self, shell):
        self.shell = shell
        self.matches = []

    def complete(self, text, state):
        if state == 0:
            self.matches = self._get_matches(text)
        try:
            return self.matches[state]
        except IndexError:
            return None

    def _get_matches(self, text):
        line = readline.get_line_buffer()
        tokens = line.split()

        # First token → command completion
        if not tokens or (len(tokens) == 1 and not line.endswith(" ")):
            candidates = BUILTIN_COMMANDS + list(self.shell.aliases.keys())
            # Also add executables from PATH
            candidates += self._path_executables()
            return [c + " " for c in sorted(set(candidates)) if c.startswith(text)]

        # Subsequent tokens → path completion
        return self._path_complete(text)

    def _path_complete(self, text):
        if not text:
            base, prefix = ".", ""
        elif os.path.isdir(text):
            base, prefix = text, text.rstrip("/\\") + os.sep
        else:
            base = os.path.dirname(text) or "."
            prefix = text

        try:
            entries = os.listdir(base)
        except PermissionError:
            return []

        matches = []
        for e in entries:
            full = os.path.join(base, e) if base != "." else e
            # normalise separators
            full = full.replace("\\", "/")
            if full.startswith(prefix.replace("\\", "/")):
                if os.path.isdir(os.path.join(base, e)):
                    matches.append(full + "/")
                else:
                    matches.append(full)
        return sorted(matches)

    def _path_executables(self):
        exes = []
        for p in os.environ.get("PATH", "").split(os.pathsep):
            try:
                for f in os.listdir(p):
                    if f.endswith((".exe", ".cmd", ".bat")):
                        exes.append(f[: f.rfind(".")])
                    else:
                        exes.append(f)
            except Exception:
                pass
        return exes

# ─── Built-in command implementations ────────────────────────────────────────
def fmt_size(n):
    for unit in ["B", "K", "M", "G", "T"]:
        if n < 1024:
            return f"{n:>6.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"

def fmt_perms(path):
    try:
        s = os.stat(path)
        m = s.st_mode
        kinds = "d" if stat.S_ISDIR(m) else ("-" if stat.S_ISREG(m) else "l")
        bits = ""
        for who in [(stat.S_IRUSR, stat.S_IWUSR, stat.S_IXUSR),
                    (stat.S_IRGRP, stat.S_IWGRP, stat.S_IXGRP),
                    (stat.S_IROTH, stat.S_IWOTH, stat.S_IXOTH)]:
            bits += ("r" if m & who[0] else "-")
            bits += ("w" if m & who[1] else "-")
            bits += ("x" if m & who[2] else "-")
        return kinds + bits
    except Exception:
        return "----------"

def do_ls(args, long=False, all_files=False):
    path = args[0] if args else "."
    try:
        entries = sorted(os.listdir(path), key=lambda e: e.lower())
    except FileNotFoundError:
        print(color(f"ls: cannot access '{path}': No such file or directory", C.RED))
        return
    except PermissionError:
        print(color(f"ls: cannot open '{path}': Permission denied", C.RED))
        return

    if not all_files:
        entries = [e for e in entries if not e.startswith(".")]

    if long:
        now = time.time()
        for e in entries:
            full = os.path.join(path, e)
            try:
                st = os.stat(full)
                size = fmt_size(st.st_size)
                mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%b %d %H:%M")
                perms = fmt_perms(full)
                if os.path.isdir(full):
                    name = color(e + "/", C.BLUE, C.BOLD)
                elif os.access(full, os.X_OK):
                    name = color(e, C.GREEN, C.BOLD)
                else:
                    name = e
                print(f"{perms}  {size}  {mtime}  {name}")
            except Exception:
                print(e)
    else:
        cols = []
        for e in entries:
            full = os.path.join(path, e)
            if os.path.isdir(full):
                cols.append(color(e + "/", C.BLUE, C.BOLD))
            elif os.access(full, os.X_OK):
                cols.append(color(e, C.GREEN))
            else:
                cols.append(e)
        # Print in columns
        try:
            width = shutil.get_terminal_size().columns
        except Exception:
            width = 80
        col_width = max((len(e) for e in entries), default=1) + 2
        ncols = max(1, width // col_width)
        for i, name in enumerate(cols):
            end = "\n" if (i + 1) % ncols == 0 or i == len(cols) - 1 else ""
            print(f"{name:<{col_width}}", end=end)

def do_cat(args):
    if not args:
        print(color("cat: missing operand", C.RED)); return
    for path in args:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                print(f.read(), end="")
        except FileNotFoundError:
            print(color(f"cat: {path}: No such file or directory", C.RED))
        except IsADirectoryError:
            print(color(f"cat: {path}: Is a directory", C.RED))

def do_head(args):
    n, files = 10, args
    if args and args[0] == "-n":
        n = int(args[1]); files = args[2:]
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= n: break
                    print(line, end="")
        except Exception as e:
            print(color(str(e), C.RED))

def do_tail(args):
    n, files = 10, args
    if args and args[0] == "-n":
        n = int(args[1]); files = args[2:]
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in lines[-n:]:
                print(line, end="")
        except Exception as e:
            print(color(str(e), C.RED))

def do_grep(args):
    if len(args) < 2:
        print(color("grep: usage: grep <pattern> <file...>", C.RED)); return
    pattern, files = args[0], args[1:]
    try:
        rx = re.compile(pattern)
    except re.error as e:
        print(color(f"grep: invalid pattern: {e}", C.RED)); return
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if rx.search(line):
                        hi = rx.sub(lambda m: color(m.group(), C.RED, C.BOLD), line.rstrip())
                        prefix = color(f"{path}:{i}:", C.MAGENTA) if len(files) > 1 else color(f"{i}:", C.MAGENTA)
                        print(f"{prefix} {hi}")
        except Exception as e:
            print(color(str(e), C.RED))

def do_find(args):
    root = args[0] if args else "."
    pattern = args[1] if len(args) > 1 else "*"
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames + dirnames:
            if fnmatch.fnmatch(name, pattern):
                rel = os.path.relpath(os.path.join(dirpath, name))
                print(rel)

def do_tree(args, shell):
    root = args[0] if args else "."
    max_depth = int(args[args.index("-L") + 1]) if "-L" in args else 4
    def _tree(path, prefix="", depth=0):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            return
        entries = [e for e in entries if not e.startswith(".")]
        for i, e in enumerate(entries):
            connector = "└── " if i == len(entries) - 1 else "├── "
            full = os.path.join(path, e)
            if os.path.isdir(full):
                print(prefix + connector + color(e + "/", C.BLUE, C.BOLD))
                ext = "    " if i == len(entries) - 1 else "│   "
                _tree(full, prefix + ext, depth + 1)
            else:
                print(prefix + connector + e)
    print(color(root, C.BLUE, C.BOLD))
    _tree(root)

def do_wc(args):
    if not args:
        print(color("wc: missing operand", C.RED)); return
    for path in args:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            lines = content.count("\n")
            words = len(content.split())
            chars = len(content)
            print(f"  {lines:>6}  {words:>6}  {chars:>6}  {path}")
        except Exception as e:
            print(color(str(e), C.RED))

def do_diff(args):
    if len(args) < 2:
        print(color("diff: usage: diff <file1> <file2>", C.RED)); return
    try:
        with open(args[0]) as f1, open(args[1]) as f2:
            lines1, lines2 = f1.readlines(), f2.readlines()
        diffs = list(difflib.unified_diff(lines1, lines2, fromfile=args[0], tofile=args[1]))
        for line in diffs:
            if line.startswith("+"):
                print(color(line, C.GREEN), end="")
            elif line.startswith("-"):
                print(color(line, C.RED), end="")
            else:
                print(line, end="")
    except Exception as e:
        print(color(str(e), C.RED))

def do_du(args):
    path = args[0] if args else "."
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except Exception:
                pass
    print(f"{fmt_size(total).strip()}  {path}")

def do_df():
    total, used, free = shutil.disk_usage(os.getcwd())
    print(f"{'Filesystem':<20} {'Size':>8} {'Used':>8} {'Avail':>8} {'Use%':>5}")
    pct = int(used / total * 100) if total else 0
    bar_color = C.RED if pct > 85 else C.YELLOW if pct > 60 else C.GREEN
    print(f"{'(current drive)':<20} {fmt_size(total):>8} {fmt_size(used):>8} {fmt_size(free):>8} {color(str(pct)+'%', bar_color):>5}")

def do_touch(args):
    for path in args:
        Path(path).touch()

def do_mkdir(args, parents=False):
    for path in args:
        try:
            Path(path).mkdir(parents=parents, exist_ok=True)
            print(color(f"mkdir: created '{path}'", C.GREEN))
        except Exception as e:
            print(color(str(e), C.RED))

def do_rm(args, recursive=False):
    for path in args:
        try:
            if os.path.isdir(path):
                if recursive:
                    shutil.rmtree(path)
                    print(color(f"rm: removed '{path}'", C.YELLOW))
                else:
                    print(color(f"rm: cannot remove '{path}': Is a directory (use -r)", C.RED))
            else:
                os.remove(path)
                print(color(f"rm: removed '{path}'", C.YELLOW))
        except Exception as e:
            print(color(str(e), C.RED))

def do_cp(args):
    if len(args) < 2:
        print(color("cp: missing destination", C.RED)); return
    src, dst = args[:-1], args[-1]
    for s in src:
        try:
            if os.path.isdir(s):
                shutil.copytree(s, os.path.join(dst, os.path.basename(s)) if os.path.isdir(dst) else dst)
            else:
                shutil.copy2(s, dst)
            print(color(f"cp: '{s}' → '{dst}'", C.GREEN))
        except Exception as e:
            print(color(str(e), C.RED))

def do_mv(args):
    if len(args) < 2:
        print(color("mv: missing destination", C.RED)); return
    src, dst = args[:-1], args[-1]
    for s in src:
        try:
            shutil.move(s, dst)
            print(color(f"mv: '{s}' → '{dst}'", C.GREEN))
        except Exception as e:
            print(color(str(e), C.RED))

def do_sort(args):
    reverse = "-r" in args
    files = [a for a in args if not a.startswith("-")]
    lines = []
    if files:
        for path in files:
            try:
                with open(path) as f:
                    lines += f.readlines()
            except Exception as e:
                print(color(str(e), C.RED))
    else:
        lines = sys.stdin.readlines()
    for line in sorted(lines, reverse=reverse):
        print(line, end="")

# ─── Fuzzy Folder Finder ─────────────────────────────────────────────────────
def fuzzy_find(query, root=".", max_results=10):
    """Fuzzy-search for folders matching query from root."""
    candidates = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
        for d in dirnames:
            rel = os.path.relpath(os.path.join(dirpath, d), root)
            candidates.append(rel.replace("\\", "/"))

    matches = difflib.get_close_matches(query, candidates, n=max_results, cutoff=0.3)
    # Also prefix-match
    prefix_matches = [c for c in candidates if query.lower() in c.lower()]
    combined = list(dict.fromkeys(matches + prefix_matches))[:max_results]
    return combined

# ─── AI Assistant ─────────────────────────────────────────────────────────────
def ai_assist(question, cwd):
    if not _AI_AVAILABLE:
        print(color("⚠  AI unavailable: install anthropic package (pip install anthropic)", C.YELLOW))
        return
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print(color("⚠  Set ANTHROPIC_API_KEY env variable to use AI features.", C.YELLOW))
        return

    print(color("🤖 Asking AI...", C.CYAN))
    try:
        client = anthropic.Anthropic(api_key=api_key)
        system = (
            "You are PyShell's built-in AI assistant. "
            "The user is in a custom Python shell on Windows. "
            f"Current directory: {cwd}. "
            "Suggest shell commands (using PyShell's built-ins OR native Windows/Python). "
            "Keep answers concise, practical, and show exact commands in backticks."
        )
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": question}]
        )
        answer = response.content[0].text
        print()
        print(color("─" * 60, C.DIM))
        # Highlight code spans
        answer = re.sub(r"`([^`]+)`", lambda m: color(m.group(1), C.GREEN, C.BOLD), answer)
        print(answer)
        print(color("─" * 60, C.DIM))
    except Exception as e:
        print(color(f"AI error: {e}", C.RED))

# ─── Help ─────────────────────────────────────────────────────────────────────
HELP_TEXT = f"""
{color('PyShell — Python-powered Bash for Windows', C.CYAN, C.BOLD)}

{color('Navigation', C.YELLOW, C.BOLD)}
  cd <path>            Change directory (supports ~, -, ..)
  pwd                  Print working directory
  ls [-la] [path]      List files   (ll = long, la = all)
  tree [-L n] [path]   Directory tree
  find [root] [pat]    Find files matching pattern

{color('File Operations', C.YELLOW, C.BOLD)}
  cat / head / tail    View file content
  touch <file>         Create empty file
  mkdir / mkdirp       Create directory (mkdirp = with parents)
  rm [-r] <path>       Remove file/dir   (-r for recursive)
  cp / mv              Copy / Move
  diff <f1> <f2>       Compare two files
  wc <file>            Word/line/char count

{color('Search & Filter', C.YELLOW, C.BOLD)}
  grep <pattern> <f>   Search in files (regex)
  sort [-r] [file]     Sort lines
  uniq [file]          Remove duplicates (sorted input)
  fz <query>           Fuzzy folder finder → cd into it

{color('System Info', C.YELLOW, C.BOLD)}
  df                   Disk usage
  du [path]            Directory size
  env / export K=V     Show / set env variables
  unset <key>          Remove env variable
  date                 Current date & time
  whoami               Current user
  hostname             Machine name
  ps                   Running processes (via tasklist)
  which <cmd>          Locate a command

{color('Shell Features', C.YELLOW, C.BOLD)}
  history              Show command history
  alias k=cmd          Set alias   (alias with no args → list)
  unalias <key>        Remove alias
  echo <text>          Print text
  clear / cls          Clear screen
  open <path>          Open file/folder in Explorer
  clip <file>          Copy file content to clipboard

{color('AI Assistant', C.CYAN, C.BOLD)}
  ai <question>        Ask AI for shell help
  ask <question>       Alias for ai

{color('Pipes & Redirection', C.YELLOW, C.BOLD)}
  cmd | cmd2           Pipe output (via Python subprocess)
  cmd > file           Redirect stdout to file
  cmd >> file          Append stdout to file

{color('Misc', C.YELLOW, C.BOLD)}
  help                 Show this help
  exit / quit          Exit PyShell

{color('Tips', C.DIM)}
  • Tab         → autocomplete commands, paths, and flags
  • ↑ / ↓       → navigate command history
  • fz mydir    → fuzzy-find and cd into a folder
  • ai "how do I list large files?"  → AI explains the command
"""

# ─── Shell Core ──────────────────────────────────────────────────────────────
class PyShell:
    def __init__(self):
        self.cwd = os.getcwd()
        self.prev_dir = self.cwd
        self.aliases: dict[str, str] = {}
        self.env = dict(os.environ)
        self.history: list[str] = []
        self._setup_readline()

    def _setup_readline(self):
        completer = PyShellCompleter(self)
        readline.set_completer(completer.complete)
        readline.set_completer_delims(" \t\n;|&><")
        readline.parse_and_bind("tab: complete")
        # Load persistent history
        hist_file = Pa