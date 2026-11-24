"""Workspace (where game files and the editable work tree live) and the one Transaction every edit uses.

    ws = Workspace(levels=..., dll=..., bundles=..., work=...)
    with ws.transaction("squadrons") as tx:
        tx.stage(ws.target("levels", "sharedassets9.assets"), new_bytes, verify=check_fn)
        ...
    # leaving the block commits: every staged file is verified, the files it replaces are backed up into
    # <work>/BSG_Data/.deadlock-backups/<label>-<stamp>/<relative path>, then each target is replaced atomically.
    # Any exception (in the block, a verifier, or a replace) restores every target that was already replaced.

Reads go through ws.source(kind, name, fresh): the work-tree copy when it exists (edits stack), else the configured
original.
"""
import datetime
import os
import shutil

from deadlock_tools import config


class Result:
    """What an operation did: log lines, files it wrote, warnings, plus any data it wants to return."""
    def __init__(self, log=None, outputs=None, warnings=None, **data):
        self.log = list(log or [])
        self.outputs = list(outputs or [])
        self.warnings = list(warnings or [])
        self.data = data

    def add(self, line):
        self.log.append(line)
        return self

    def to_json(self):
        return {"ok": True, "log": self.log, "outputs": self.outputs, "warnings": self.warnings, **self.data}

    def __repr__(self):
        return "Result(log=%r, outputs=%r, warnings=%r)" % (self.log, self.outputs, self.warnings)


class Workspace:
    def __init__(self, levels, dll, work, bundles=None):
        ab = lambda path: os.path.abspath(os.path.expanduser(path)) if path else path
        self.levels, self.dll, self.work, self.bundles = ab(levels), ab(dll), ab(work), ab(bundles)
        self.game_data = os.path.join(self.work, "BSG_Data")
        self.backup_root = os.path.join(self.game_data, ".deadlock-backups")
        self.stage_root = os.path.join(self.work, ".deadlock-tools", "stage")

    @classmethod
    def from_config(cls, levels=None, dll=None, work=None, bundles=None):
        return cls(config.setting("levels", levels), config.setting("dll", dll), config.setting("work", work),
                   config.setting("bundles", bundles, required=False))

    # ---- locations ----
    def original_dir(self, kind):
        base = self.levels if os.path.isdir(self.levels) else os.path.dirname(self.levels)
        return {"levels": base, "dll": os.path.dirname(self.dll), "bundles": self.bundles or os.path.join(base, "StreamingAssets")}[kind]

    def work_dir(self, kind):
        return os.path.join(self.game_data, *config.WORK_PATHS[kind])

    def target(self, kind, name):
        """where an edited file goes in the work tree"""
        return os.path.join(self.work_dir(kind), os.path.basename(name))

    def source(self, kind, name, fresh=False):
        """the file to edit: the work-tree copy unless fresh (edits stack), else the original"""
        original = name if os.path.isabs(name) else os.path.join(self.original_dir(kind), name)
        candidate = self.target(kind, name)
        if not fresh and os.path.isfile(candidate) and not config.same_file(candidate, original):
            return candidate
        return original

    def transaction(self, label):
        return Transaction(self, label)


class Transaction:
    """Stage new file contents, verify them, then back up and atomically replace the targets (all or nothing)."""
    def __init__(self, workspace, label):
        self.ws, self.label = workspace, label
        self.staged = []                 # (target, bytes, verify)
        self.result = Result()
        self.backup = None
        self.committed = False

    def stage(self, target, data, verify=None, original=None):
        """queue bytes for target. verify(bytes) may raise to abort the whole transaction.
        original: the game's own file; refusing to ever overwrite it."""
        target = os.path.abspath(target)
        if original and os.path.exists(target) and config.same_file(target, original):
            raise ValueError("refusing to overwrite the original %s" % original)
        if any(t == target for t, _d, _v in self.staged):
            raise ValueError("%s staged twice in one transaction" % target)
        self.staged.append((target, bytes(data), verify))
        return target

    def log(self, line):
        self.result.add(line)

    def commit(self):
        if self.committed:
            return self.result
        for target, data, verify in self.staged:             # verify everything before touching anything
            if verify is not None:
                verify(data)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_root = os.path.join(self.ws.backup_root, "%s-%s" % (self.label, stamp))
        backups, replaced = {}, []
        try:
            for target, _data, _v in self.staged:
                if os.path.isfile(target):
                    rel = os.path.relpath(target, self.ws.game_data) if target.startswith(self.ws.game_data + os.sep) else os.path.basename(target)
                    backup = os.path.join(backup_root, rel)
                    os.makedirs(os.path.dirname(backup), exist_ok=True)
                    shutil.copy2(target, backup)
                    backups[target] = backup
            for target, data, _v in self.staged:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                temporary = "%s.%s-tmp" % (target, self.label)
                with open(temporary, "wb") as stream:
                    stream.write(data)
                os.replace(temporary, target)
                replaced.append(target)
        except BaseException:
            for target in reversed(replaced):
                if target in backups:
                    shutil.copy2(backups[target], target)
                elif os.path.exists(target):
                    os.unlink(target)
            for target, _d, _v in self.staged:
                temporary = "%s.%s-tmp" % (target, self.label)
                if os.path.exists(temporary):
                    os.unlink(temporary)
            raise
        self.committed = True
        self.backup = backup_root if backups else None
        self.result.outputs += [t for t, _d, _v in self.staged]
        self.result.data["backup"] = self.backup
        return self.result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        return False                                         # never swallow errors; nothing was written on failure


def unity_object_check(original_bytes, allow_removed=()):
    """verifier for rewritten Unity serialized files: same objects, none lost its data (the level3 truncation guard).
    allow_removed: path ids an edit deletes on purpose (e.g. a removed launcher)."""
    from deadlock_tools.repair_shortcuts import _object_sizes
    before = _object_sizes(original_bytes)
    allowed = set(allow_removed)

    def check(data):
        after = _object_sizes(data)
        lost = [pid for pid, size in before.items() if size and not after.get(pid) and pid not in allowed]
        if lost:
            raise ValueError("refusing to save: the rewritten file lost the data of %d of %d objects" % (len(lost), len(before)))
    return check
