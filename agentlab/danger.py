"""Dangerous-command protection: classify a shell command before an agent (or a task) runs it.

Levels: ok < warn < confirm < block.
  block    never runs (catastrophic or outside the project: system files, disks, root, credentials)
  confirm  needs your approval even in full-auto mode (destructive to the project, publishes, force-pushes ...)
  warn     runs, but is noted in the log
Hard blocks (wiping / or ~, formatting disks, fork bombs, shutting down) stay blocked even if you turn protection off.

This is a parser-based heuristic, defence in depth: it reads commands the way a shell does (quotes, pipes, `$( )`,
`bash -c`), so `echo "rm -rf /"` is fine and `rm -rf ~` is not. It is NOT a filesystem sandbox: a determined
program can still obfuscate. For untrusted agents use the Docker backend or a VM.
"""

import os
import posixpath
import re
import shlex

ORDER = {"ok": 0, "warn": 1, "confirm": 2, "block": 3}
SYSTEM_DIRS = ("/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/dev", "/sys", "/proc", "/var", "/opt", "/root", "/home", "/mnt", "/srv", "/System", "/Library", "/Applications", "/Users", "/private")
HOME_MARKERS = ("~", "$HOME", "${HOME}")
SECRET_PATHS = (".ssh/", ".aws/credentials", ".aws/config", "/etc/shadow", "/etc/sudoers", ".gnupg", ".kube/config", ".git-credentials", ".netrc", ".docker/config.json", ".config/gcloud", ".azure/", ".npmrc", ".pypirc")
DEVICE_OK = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/tty", "/dev/zero", "/dev/urandom", "/dev/random")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "python", "python3", "perl", "ruby", "node", "php", "pwsh"}
DISK_TOOLS = {"mkfs", "fdisk", "sfdisk", "cfdisk", "parted", "wipefs", "mkswap", "cryptsetup", "losetup", "mount", "umount", "blkdiscard", "hdparm"}
POWER = {"shutdown", "reboot", "halt", "poweroff", "telinit"}
WRAPPERS = {"env", "nohup", "time", "command", "exec", "nice", "ionice", "stdbuf", "timeout", "setsid", "builtin"}
PUBLISH = {("npm", "publish"), ("yarn", "publish"), ("pnpm", "publish"), ("twine", "upload"), ("cargo", "publish"), ("gem", "push"), ("poetry", "publish")}
CLOUD_DELETE = {"aws": ("rm", "delete", "terminate", "destroy"), "gcloud": ("delete",), "az": ("delete",), "kubectl": ("delete",), "terraform": ("destroy",), "gh": ("delete",), "doctl": ("delete",), "heroku": ("destroy",)}


class _R:
    def __init__(self):
        self.level, self.reasons, self.hard = "ok", [], False

    def add(self, level, reason, hard=False):
        self.reasons.append({"level": level, "reason": reason})
        if ORDER[level] > ORDER[self.level]:
            self.level = level
        self.hard = self.hard or (hard and level == "block")


# ---------- shell scanning ----------
def _take(command, i, open_c, close_c):
    depth, j = 1, i
    while j < len(command) and depth:
        if command[j] == open_c:
            depth += 1
        elif command[j] == close_c:
            depth -= 1
        j += 1
    return command[i:j - 1], j


def scan(command):
    """Split into top-level segments [(text, operator_before)] and collect command substitutions (inner texts)."""
    segs, cur, subs, op = [], [], [], ";"
    i, n, quote = 0, len(command), None
    def push(next_op):
        nonlocal cur, op
        text = "".join(cur).strip()
        if text:
            segs.append((text, op))
        cur, op = [], next_op
    while i < n:
        c = command[i]
        if quote == "'":
            cur.append(c)
            quote = None if c == "'" else quote
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            cur.append(command[i:i + 2])
            i += 2
            continue
        if command.startswith("$(", i) or command.startswith("<(", i) or command.startswith(">(", i):
            inner, j = _take(command, i + 2, "(", ")")
            subs.append(inner)
            cur.append(command[i:j])
            i = j
            continue
        if c == "`":
            end = command.find("`", i + 1)
            end = n if end == -1 else end
            subs.append(command[i + 1:end])
            cur.append(command[i:end + 1])
            i = end + 1
            continue
        if quote == '"':
            cur.append(c)
            quote = None if c == '"' else quote
            i += 1
            continue
        if c in "'\"":
            quote = c
            cur.append(c)
        elif c in ";\n":
            push(";")
        elif command.startswith("&&", i):
            push("&&")
            i += 1
        elif command.startswith("||", i):
            push("||")
            i += 1
        elif c == "|":
            push("|")
        elif c == "&" and not (cur and cur[-1] in "><"):
            push("&")
        else:
            cur.append(c)
        i += 1
    push(";")
    return segs, subs


def _tokens(segment):
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


# ---------- path helpers ----------
def _marker(path):
    return path.startswith(HOME_MARKERS) or path in ("~",)


def _is_system(path):
    p = posixpath.normpath(path) if path.startswith("/") else path
    return p in ("/", "/*") or path.startswith(("/*", "/.")) or any(p == d or p.startswith(d + "/") for d in SYSTEM_DIRS)


def _outside(path, workspace):
    """True if `path` (as written in a command) leaves the project workspace."""
    if _marker(path) or path.startswith("$"):
        return True
    if path.startswith("/"):
        if workspace:
            w = posixpath.normpath(workspace)
            p = posixpath.normpath(path)
            return not (p == w or p.startswith(w + "/"))
        return True
    base = workspace or "/ws"
    joined = posixpath.normpath(posixpath.join(base, path))
    return not (joined == posixpath.normpath(base) or joined.startswith(posixpath.normpath(base) + "/"))


def _secret(path):
    return any(s in path for s in SECRET_PATHS)


def _flags(tokens):
    flags, targets, rest = set(), [], False
    for t in tokens:
        if rest or not t.startswith("-") or t == "-":
            targets.append(t)
        elif t == "--":
            rest = True
        elif t.startswith("--"):
            flags.add(t)
        else:
            flags.update("-" + ch for ch in t[1:])
    return flags, targets


def _redirects(tokens):
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        m = re.fullmatch(r"(\d*|&)>>?(.*)", t)
        if m and not t.startswith(("<", "-")):
            target = m.group(2) or (tokens[i + 1] if i + 1 < len(tokens) else "")
            if target and not target.startswith("&"):
                out.append(target)
        i += 1
    return out


def _check_write(r, path, workspace, what):
    if path in DEVICE_OK or path.startswith("/dev/fd/"):
        return
    if path.startswith("/dev/"):
        r.add("block", f"{what} a device file ({path})", hard=True)
    elif _is_system(path) or _marker(path) and path in ("~", "~/", "$HOME", "$HOME/"):
        r.add("block", f"{what} system or home files ({path})", hard=path in ("/", "~", "$HOME", "~/", "/*"))
    elif _secret(path):
        r.add("block", f"{what} credentials or keys ({path})")
    elif _outside(path, workspace):
        r.add("block", f"{what} a path outside the project ({path})")


# ---------- per-command rules ----------
def _rm(r, cmd, tokens, workspace):
    flags, targets = _flags(tokens)
    recursive = bool({"-r", "-R", "--recursive"} & flags)
    for t in targets:
        if t in ("/", "/*", "~", "~/", "$HOME", "${HOME}", "$HOME/", "~/*", "/home", "/root") or _is_system(t) and t.count("/") <= 2 and recursive:
            r.add("block", f"{cmd} would delete system or home files ({t})", hard=True)
        elif _is_system(t) or _outside(t, workspace):
            r.add("block", f"{cmd} targets a path outside the project ({t})")
        elif recursive and t in (".", "./", "*", "./*", ".*"):
            r.add("confirm", f"{cmd} -r would delete the whole project contents")
        elif _secret(t):
            r.add("block", f"{cmd} targets credentials or keys ({t})")


def _move_like(r, cmd, tokens, workspace):
    flags, targets = _flags(tokens)
    if cmd == "cp" and len(targets) < 2:
        return
    for t in targets:
        if cmd in ("mv", "ln") and (_is_system(t) or _marker(t) and t in ("~", "$HOME")):
            r.add("block", f"{cmd} would move or link system or home files ({t})")
    dest = targets[-1] if targets else ""
    if cmd in ("cp", "mv", "install", "rsync", "ln") and dest:
        if "-t" in flags and len(targets) >= 1:
            dest = targets[0]
        if _is_system(dest) or _outside(dest, workspace) and not dest.startswith("/tmp"):
            r.add("block", f"{cmd} writes outside the project ({dest})")


def _perm(r, cmd, tokens, workspace):
    flags, targets = _flags(tokens)
    recursive = bool({"-R", "-r", "--recursive"} & flags)
    for t in targets[1:] if cmd in ("chmod", "chown", "chgrp") else targets:
        if t.startswith(("+", "=", "0", "1", "2", "3", "4", "5", "6", "7")) and t.lstrip("+=0123456789ugoarwxXst,-") == "":
            continue
        if _is_system(t) or _outside(t, workspace) or t in ("~", "$HOME"):
            r.add("block", f"{cmd} changes permissions or owners outside the project ({t})", hard=recursive and t in ("/", "~", "$HOME"))
    if cmd == "chmod" and any(re.fullmatch(r"(a\+rwx|[0-7]?777|o\+w)", t) for t in tokens) and recursive:
        r.add("confirm", "recursive world-writable permissions")
    elif cmd == "chmod" and any(re.fullmatch(r"[0-7]?777", t) for t in tokens):
        r.add("warn", "makes files world-writable")


def _git(r, tokens):
    sub = next((t for t in tokens[1:] if not t.startswith("-")), "")
    rest = tokens[tokens.index(sub) + 1:] if sub in tokens else []
    if sub == "push":
        if any(t in ("-f", "--force", "--force-with-lease", "--mirror", "--delete") or t.startswith("+") and ":" not in t[:1] for t in rest):
            r.add("confirm", "git push --force can overwrite remote history")
        else:
            r.add("warn", "git push publishes your work")
    elif sub == "reset" and "--hard" in rest:
        r.add("confirm", "git reset --hard discards uncommitted work")
    elif sub == "clean" and any(re.fullmatch(r"-[a-z]*f[a-z]*", t) or t == "--force" for t in rest):
        r.add("confirm", "git clean -f deletes untracked files")
    elif sub in ("checkout", "restore") and any(t in (".", "--", "-f") for t in rest) and sub == "restore" or sub == "checkout" and "." in rest:
        r.add("confirm", f"git {sub} . discards uncommitted changes")
    elif sub == "branch" and any(t in ("-D", "--delete") for t in rest):
        r.add("confirm", "deletes a branch")
    elif sub == "stash" and any(t in ("drop", "clear") for t in rest):
        r.add("confirm", "deletes stashed work")
    elif sub == "filter-branch" or sub == "filter-repo":
        r.add("confirm", "rewrites repository history")


def _docker(r, tokens):
    t = " ".join(tokens)
    if "--privileged" in tokens or re.search(r"(-v|--volume)[ =]+/:", t) or "docker.sock" in t or "--pid=host" in t or "--pid host" in t:
        r.add("block", "docker with privileged access or the host filesystem")
    elif re.search(r"\b(system|volume|container|image|network)\s+prune\b", t) or re.search(r"\brm\s+(-f|--force)", t) or " rmi " in f" {t} ":
        r.add("confirm", "deletes containers, images or volumes")


def _sql(r, tokens):
    text = " ".join(tokens).lower()
    if re.search(r"\bdrop\s+(table|database|schema)\b|\btruncate\b", text):
        r.add("confirm", "SQL that drops or truncates data")
    elif re.search(r"\bdelete\s+from\b(?!.*\bwhere\b)", text):
        r.add("confirm", "SQL delete without a WHERE clause")


def _inline_code(r, tokens):
    code = " ".join(tokens[1:])
    if re.search(r"rmtree\(\s*['\"](/|~|\$HOME)|os\.remove\(\s*['\"]/|unlink\(\s*['\"]/", code):
        r.add("block", "inline code deletes system or home files")
    elif re.search(r"os\.system|subprocess|child_process|\bexec(Sync)?\(|popen", code):
        r.add("warn", "inline code starts other commands")


def _segment(r, text, workspace, depth, prev_cmd=None):
    tokens = _tokens(text)
    for t in _redirects(tokens):
        _check_write(r, t, workspace, "redirects output to")
    # drop redirections, leading VAR=value assignments and transparent wrappers
    tokens = [t for t in tokens if not re.fullmatch(r"(\d*|&)>>?.*", t)]
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens = tokens[1:]
    if tokens and posixpath.basename(tokens[0]) in ("env", "printenv") and len(tokens) == 1:
        r.add("warn", "prints environment variables")  # may expose keys shared with this project
        return None
    while tokens and posixpath.basename(tokens[0]) in WRAPPERS:
        tokens = [t for t in tokens[1:] if not (t.startswith("-") or re.fullmatch(r"[A-Za-z_]\w*=.*|\d+[smhd]?", t))] if posixpath.basename(tokens[0]) in ("env", "timeout", "nice", "ionice") else tokens[1:]
    if not tokens:
        return None
    cmd = posixpath.basename(tokens[0])
    rest = tokens[1:]
    for t in rest:  # reading credentials is blocked wherever it appears as an argument
        if _secret(t) and cmd not in ("echo", "printf"):
            r.add("block", f"touches credentials or private keys ({t})")
    if cmd in ("sudo", "su", "doas", "pkexec", "runuser"):
        r.add("block", "root access is not available to agents")
    elif cmd in ("rm", "rmdir", "unlink", "shred", "srm", "trash"):
        _rm(r, cmd, tokens, workspace)
    elif cmd in ("mv", "cp", "install", "rsync", "ln"):
        _move_like(r, cmd, tokens, workspace)
    elif cmd in ("chmod", "chown", "chgrp"):
        _perm(r, cmd, tokens, workspace)
    elif cmd == "dd":
        for t in rest:
            if t.startswith("of="):
                _check_write(r, t[3:], workspace, "dd writes to")
    elif cmd == "truncate" or cmd == "tee":
        for t in _flags(rest)[1]:
            if cmd == "tee" or t not in ("0",):
                _check_write(r, t, workspace, f"{cmd} writes to")
    elif cmd.startswith("mkfs") or cmd in DISK_TOOLS:
        r.add("block", f"{cmd} formats or modifies disks", hard=True)
    elif cmd in POWER or cmd == "init" and rest and rest[0] in ("0", "6"):
        r.add("block", f"{cmd} would shut down or restart the machine", hard=True)
    elif cmd == "systemctl" and any(t in ("poweroff", "reboot", "halt", "suspend", "hibernate", "kexec") for t in rest):
        r.add("block", "systemctl would power off or restart the machine", hard=True)
    elif cmd == "systemctl" and any(t in ("stop", "disable", "mask", "restart") for t in rest):
        r.add("confirm", "systemctl changes running services")
    elif cmd == "kill":
        ids = [t for t in rest if re.fullmatch(r"-?\d+", t)]
        if any(t in ("1", "-1") for t in ids[-1:]):
            r.add("block", "kill PID 1 / -1 would take down the system", hard=True)
        else:
            r.add("warn", "stops a process")
    elif cmd in ("killall", "pkill"):
        r.add("confirm", f"{cmd} can stop processes that are not yours to stop")
    elif cmd in ("iptables", "ip6tables", "nft", "ufw", "firewall-cmd"):
        r.add("block", "changes the firewall")
    elif cmd == "crontab":
        r.add("confirm", "changes scheduled jobs")
    elif cmd == "git":
        _git(r, tokens)
    elif cmd in ("docker", "podman"):
        _docker(r, tokens)
    elif cmd in ("psql", "mysql", "sqlite3", "mongosh", "mongo", "redis-cli"):
        _sql(r, tokens)
    elif cmd in ("nc", "ncat", "netcat", "socat"):
        r.add("block", f"{cmd} can open reverse shells or listeners") if any(t in ("-e", "-l", "-c", "EXEC", "-lvp") or t.startswith("EXEC:") for t in rest) else r.add("confirm", f"{cmd} opens raw network connections")
    elif cmd in ("scp", "sftp", "ssh", "telnet", "ftp"):
        r.add("confirm", f"{cmd} connects to another machine")
    elif cmd in ("curl", "wget"):
        if any(t in ("-T", "--upload-file", "--data-binary", "-F", "--form") or t.startswith(("-d@", "--data=@")) for t in rest) or any(re.search(r"@\S", t) and t.startswith(("@", "-d", "--data")) for t in rest) or ("-d" in rest and any(t.startswith("@") for t in rest)):
            r.add("confirm", f"{cmd} uploads a file")
        for i, t in enumerate(rest):
            if t in ("-o", "-O", "--output", "-P") and i + 1 < len(rest):
                _check_write(r, rest[i + 1], workspace, f"{cmd} saves to")
    elif cmd in ("history",) and "-c" in rest or cmd == "unset" and "HISTFILE" in rest:
        r.add("confirm", "erases shell history")
    elif cmd in ("env", "printenv", "set") and not rest:
        r.add("warn", "prints environment variables")
    elif cmd == "find":
        if "-delete" in rest or any(t in ("rm", "shred") for t in rest) and "-exec" in rest:
            start = next((t for t in rest if not t.startswith("-") and t not in ("(", "!")), ".")
            if _is_system(start) or _outside(start, workspace):
                r.add("block", f"find would delete files outside the project ({start})")
            else:
                r.add("confirm", "find ... -delete removes files in bulk")
    elif cmd == "xargs" and any(posixpath.basename(t) in ("rm", "shred", "mv", "chmod", "chown") for t in rest):
        r.add("confirm", "xargs applies a destructive command to a computed list")
    elif cmd in ("npm", "yarn", "pnpm", "pip", "pip3", "pipx", "brew", "apt", "apt-get", "dnf", "yum", "apk"):
        if cmd in ("apt", "apt-get", "dnf", "yum", "apk", "brew") and any(t in ("install", "remove", "purge", "upgrade", "add", "del", "uninstall") for t in rest):
            r.add("confirm", f"{cmd} changes system packages")
        elif "-g" in rest or "--global" in rest or "--break-system-packages" in rest:
            r.add("confirm", f"{cmd} changes global packages")
        elif (cmd, rest[0] if rest else "") in PUBLISH:
            r.add("confirm", f"{cmd} publish sends your code to a public registry")
    elif (cmd, rest[0] if rest else "") in PUBLISH:
        r.add("confirm", f"{cmd} {rest[0]} sends your code to a public registry")
    elif cmd in CLOUD_DELETE and any(t in CLOUD_DELETE[cmd] or (cmd == "terraform" and t == "apply" and "-auto-approve" in rest) for t in rest):
        r.add("confirm", f"{cmd} changes or deletes cloud resources")
    elif cmd == "terraform" and "apply" in rest and "-auto-approve" in rest:
        r.add("confirm", "terraform apply without review")
    elif cmd in ("python", "python3", "node", "perl", "ruby", "php") and any(t in ("-c", "-e", "-E") for t in rest):
        _inline_code(r, rest)
    # nested shells: sh -c "...", eval "..."
    if cmd in ("sh", "bash", "zsh", "dash", "ksh", "eval") and depth < 3:
        inner = None
        if cmd == "eval":
            inner = " ".join(rest)
        elif "-c" in rest and rest.index("-c") + 1 < len(rest):
            inner = rest[rest.index("-c") + 1]
        if inner:
            sub = classify(inner, workspace, depth + 1)
            for x in sub["reasons"]:
                r.add(x["level"], "inside a nested shell: " + x["reason"], hard=sub["hard"] and x["level"] == "block")
    return cmd


def classify(command, workspace=None, depth=0):
    """-> {"level", "reasons": [{"level","reason"}], "hard": bool}. `workspace` is the project directory the command runs in."""
    r = _R()
    command = str(command or "")
    if len(command) > 20000:
        r.add("confirm", "very long command")
    if re.search(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", command):
        r.add("block", "fork bomb", hard=True)
    if "/dev/tcp/" in command or "/dev/udp/" in command:
        r.add("block", "opens a raw network socket (reverse shell pattern)")
    segs, subs = scan(command)
    prev = None
    for text, op in segs:
        cmd = _segment(r, text, workspace, depth)
        if op == "|" and prev and cmd and posixpath.basename(cmd) in SHELLS and prev in ("curl", "wget", "base64", "openssl", "xxd", "echo", "cat", "printf") and (prev in ("curl", "wget") or "-d" in text or True):
            if prev in ("curl", "wget", "base64", "openssl", "xxd"):
                r.add("confirm", f"pipes downloaded or decoded content into {cmd}")
        prev = cmd
    if depth < 3:
        for inner in subs:
            sub = classify(inner, workspace, depth + 1)
            for x in sub["reasons"]:
                r.add(x["level"], "inside $( ): " + x["reason"], hard=sub["hard"] and x["level"] == "block")
    return {"level": r.level, "reasons": r.reasons, "hard": r.hard}


def decide(command, workspace=None, protection="standard"):
    """What the runtime should do: ("allow" | "warn" | "confirm" | "block", reasons). `protection`: standard | strict | off."""
    c = classify(command, workspace)
    level = c["level"]
    if protection == "off":
        level = "block" if c["hard"] else "ok"
    elif protection == "strict" and level == "warn":
        level = "confirm"
    return ("allow" if level == "ok" else level), c["reasons"]
