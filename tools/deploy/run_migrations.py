#!/usr/bin/env python3
"""run_migrations.py — the ONE reader of the migration manifest.

    run_migrations.py --manifest <path> --db <path> --scripts-dir <abs checkout>
                      --python <abs venv python> [--apply | --dry-run]

Why this exists (2026-09-24/25): the shell loop that used to read the
manifest (`while IFS= read -r MIG`) skipped a final line with no trailing
newline, so a comments-only list looked like a successful run of nothing —
the flip reported success with the migration never executed. A real parser
plus a mandatory paired check per migration makes that failure shape
impossible to report as success.

Manifest format (one migration per line):

    <apply-script> <check-script>      # both relative to --scripts-dir

Blank lines and lines starting with ``#`` are ignored. The final line is
read whether or not the file ends with a newline — a line-oriented parser
does not need one, and aborting a release over a benign file shape would
be its own outage. A manifest with ZERO migrations is refused: "0
applied+checked, 0 failed" must never be the verdict of a run that did
nothing.

Migration contracts (the runner orchestrates; the scripts do the work):

  * apply-script: ``<python> <apply> --db <db>`` is a READ-ONLY dry-run
    report. ``--apply --backup --backup-path <p> --verify`` writes.
  * check-script: ``<python> <check> --db <db>`` opens the DB read-only and
    exits 0 iff the migration's effect is complete. A separate process, so
    it cannot vouch for the code that just wrote.

Verdict format (the release guard, --diagnose and the human gate read
exactly this):

    == run_migrations <iso-ts> manifest=<path> db=<path> mode=apply
    migration <apply-basename>: apply ok; backup ok; check ok
    migrations: <N> applied+checked, <M> failed

Exit codes: 0 = every migration applied and checked; 1 = at least one
migration failed; 2 = refused before running (bad manifest, missing inputs,
insufficient disk). Only 0 means the DB is migrated.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import os
import pwd
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

DESCRIPTION = "Run the manifest's data migrations against one sqlite database."
DEFAULT_HOUSES_ROOT = "/opt/houses"
LOGS_SUBDIR = "logs/releases"
LOG_PREFIX = "run-migrations-"
KEEP_LOGS = 32
# DB + 1 GiB journal/backup headroom — the 2026-09-18 out-of-disk crash:
# the batched apply needs room for the copy, the journal and the WAL.
MIGRATION_HEADROOM_BYTES = 1_073_741_824
MAX_ERROR_CHARS = 200
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2
NOT_ATTEMPTED = "apply FAILED: not attempted — an earlier migration failed"

# A manifest path is relative, inside the checkout, and free of quoting
# metacharacters: the runner rebuilds subprocess argv from it, and a
# charset check is the injection discipline the deploy allowlist uses too.
PATH_TOKEN_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class ManifestError(Exception):
    """The manifest is unusable — refuse the run before touching the DB."""


@dataclasses.dataclass(frozen=True)
class Migration:
    """One manifest line: the script that writes and the script that checks."""

    apply: Path
    check: Path

    @property
    def name(self) -> str:
        """The apply script's basename: the verdict label and backup suffix."""
        return self.apply.name


def _parse_line(lineno: int, line: str, scripts_dir: Path) -> Migration:
    """One manifest line → one Migration, or ManifestError."""
    fields = line.split()
    if len(fields) != 2:
        raise ManifestError(f"line {lineno}: expected '<apply-script> <check-script>', got {line!r}")
    for field in fields:
        if not PATH_TOKEN_RE.match(field) or field.startswith("/") or ".." in Path(field).parts:
            raise ManifestError(f"line {lineno}: {field!r} is not a relative script path")
    apply_rel, check_rel = fields
    return Migration(scripts_dir / apply_rel, scripts_dir / check_rel)


def _reject_duplicates(entries: list[Migration]) -> None:
    """The backup name and the verdict label are keyed on the apply
    basename, and a repeated path could silently mean 'run it twice'."""
    paths = [str(entry.apply) for entry in entries] + [str(entry.check) for entry in entries]
    repeated_paths = sorted(path for path, count in collections.Counter(paths).items() if count > 1)
    if repeated_paths:
        raise ManifestError(f"{repeated_paths[0]} is listed twice")
    names = collections.Counter(entry.name for entry in entries)
    repeated_names = sorted(name for name, count in names.items() if count > 1)
    if repeated_names:
        raise ManifestError(
            f"apply scripts share a basename ({', '.join(repeated_names)}) — "
            "the pre-migration backup name would not be unique"
        )


def parse_manifest(text: str, scripts_dir: Path) -> list[Migration]:
    """Parse the manifest, or raise ManifestError.

    Rejects: a line that is not exactly two paths; an absolute or escaping
    path; an unknown path charset; a path listed twice; two apply scripts
    sharing a basename; zero migrations.
    """
    numbered = ((lineno, raw.strip()) for lineno, raw in enumerate(text.splitlines(), start=1))
    entries = [
        _parse_line(lineno, line, scripts_dir)
        for lineno, line in numbered
        if line and not line.startswith("#")
    ]
    if not entries:
        raise ManifestError(
            "manifest lists no migrations — refusing to report "
            "'0 applied+checked, 0 failed' for a run that did nothing"
        )
    _reject_duplicates(entries)
    return entries


class Report:
    """The run transcript: stdout (streamed to the caller) plus one log file.

    Both carry the same lines; the log is the evidence trail the workflow,
    ``switch.sh --diagnose`` and a human read after the fact.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = path.open("w", encoding="utf-8")

    def say(self, line: str) -> None:
        print(line, flush=True)
        self._handle.write(line + "\n")
        self._handle.flush()

    def echo(self, label: str, text: str) -> None:
        """Record a subprocess's output, indented so verdict lines stay unique."""
        for line in text.splitlines():
            if line.strip():
                self.say(f"    | {label}: {line}")

    def close(self) -> None:
        self._handle.close()


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return " ".join(line.split())[:MAX_ERROR_CHARS]
    return ""


def _failure(proc: subprocess.CompletedProcess[str]) -> str:
    """One line naming why a subprocess failed — the verdict must stay one line."""
    return _last_line(proc.stderr) or _last_line(proc.stdout) or f"exit {proc.returncode}"


def _stage_verdict(label: str, proc: subprocess.CompletedProcess[str]) -> str | None:
    """A verdict clause for one stage: None when the stage succeeded."""
    return None if proc.returncode == 0 else f"{label}: {_failure(proc)}"


@dataclasses.dataclass
class Run:
    """One invocation: the DB, the interpreter, the checkout, the transcript."""

    db: Path
    python: Path
    scripts_dir: Path
    report: Report

    def _invoke(
        self, script: Path, *, extra: list[str], may_write: bool
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        if may_write:
            env["HOUSES_SCRIPTS_MAY_WRITE"] = "1"
        return subprocess.run(
            [str(self.python), str(script), "--db", str(self.db), *extra],
            cwd=str(self.scripts_dir),
            env=env,
            capture_output=True,
            text=True,
        )

    def _dry_run_report(self, migration: Migration) -> subprocess.CompletedProcess[str]:
        proc = self._invoke(migration.apply, extra=[], may_write=False)
        self.report.echo(migration.name, proc.stdout + proc.stderr)
        return proc

    def _apply_and_backup(self, migration: Migration, backup: Path) -> subprocess.CompletedProcess[str]:
        proc = self._invoke(
            migration.apply,
            extra=["--apply", "--backup", "--backup-path", str(backup), "--verify"],
            may_write=True,
        )
        self.report.echo(migration.name, proc.stdout + proc.stderr)
        return proc

    def _independent_check(self, migration: Migration) -> subprocess.CompletedProcess[str]:
        proc = self._invoke(migration.check, extra=[], may_write=False)
        self.report.echo(migration.check.name, proc.stdout + proc.stderr)
        return proc

    def _hand_back(self) -> None:
        """The app runs as ubuntu; the runner runs as root. A root-owned 600 DB
        is unopenable by the service, so the apply must hand ownership back."""
        if os.geteuid() != 0:
            return
        try:
            ubuntu = pwd.getpwnam("ubuntu")
        except KeyError:
            return
        os.chown(self.db, ubuntu.pw_uid, ubuntu.pw_gid)
        os.chmod(self.db, 0o600)

    def dry_run_verdict(self, migration: Migration) -> str | None:
        """None when the apply script's own read-only report succeeded."""
        return _stage_verdict("dry-run FAILED", self._dry_run_report(migration))

    def apply_verdict(self, migration: Migration) -> str | None:
        """The full verdict clause: None when applied, backed up and checked."""
        backup = self.db.with_name(self.db.name + f".pre-{migration.name}")
        if reason := _stage_verdict("apply FAILED: dry-run failed", self._dry_run_report(migration)):
            return reason
        if reason := _stage_verdict("apply FAILED", self._apply_and_backup(migration, backup)):
            return reason
        if not backup.is_file() or backup.stat().st_size == 0:
            return f"apply FAILED: no backup written at {backup}"
        self._hand_back()
        return _stage_verdict("check FAILED", self._independent_check(migration))

    def dry_run(self, entries: list[Migration]) -> int:
        """Read-only report: every apply script's own dry-run mode. No writes."""
        verdicts = [(entry.name, self.dry_run_verdict(entry)) for entry in entries]
        for name, reason in verdicts:
            self.report.say(f"migration {name}: {reason or 'dry-run ok'}")
        failures = sum(reason is not None for _, reason in verdicts)
        self.report.say(f"migrations: {len(entries) - failures} dry-run ok, {failures} failed")
        return failures

    def _verdicts(self, entries: list[Migration]) -> Iterator[tuple[str, str | None]]:
        """Per-migration verdict clauses, in manifest order.

        A failure stops the chain — later migrations are reported as not
        attempted rather than applied over a half-migrated database — but
        every manifest entry still gets exactly one verdict clause.
        """
        stopped = False
        for migration in entries:
            reason = NOT_ATTEMPTED if stopped else self.apply_verdict(migration)
            stopped = stopped or reason is not None
            yield migration.name, reason

    def apply(self, entries: list[Migration]) -> int:
        """Per migration: dry-run report, apply + backup, independent check."""
        verdicts = list(self._verdicts(entries))
        for name, reason in verdicts:
            self.report.say(f"migration {name}: {reason or 'apply ok; backup ok; check ok'}")
        failures = sum(reason is not None for _, reason in verdicts)
        self.report.say(f"migrations: {len(entries) - failures} applied+checked, {failures} failed")
        return failures


def _log_dir() -> Path:
    root = Path(os.environ.get("HOUSES_ROOT", DEFAULT_HOUSES_ROOT))
    return Path(os.environ.get("HOUSES_LOG_DIR", root / LOGS_SUBDIR))


def _prune_logs(log_dir: Path) -> None:
    """Keep the newest KEEP_LOGS transcripts — the box must not fill up."""
    for stale in sorted(log_dir.glob(f"{LOG_PREFIX}*.log"), reverse=True)[KEEP_LOGS:]:
        stale.unlink(missing_ok=True)


def _space_gate(db: Path, report: Report) -> str | None:
    """The 2026-09-18 out-of-disk crash: the batched apply needs DB +
    headroom free beside it. Returns an error line, or None when there is
    room. HOUSES_MIGRATION_MIN_FREE_BYTES pins the threshold in tests."""
    need = int(
        os.environ.get("HOUSES_MIGRATION_MIN_FREE_BYTES", db.stat().st_size + MIGRATION_HEADROOM_BYTES)
    )
    free = shutil.disk_usage(db.parent).free
    if free < need:
        return (
            f"refusing: need {need} bytes free beside the DB, have {free} "
            "(2026-09-18 out-of-disk crash)"
        )
    report.say(f"== space gate ok ({free} bytes free beside the DB, need {need})")
    return None


def _validate_inputs(args: argparse.Namespace) -> str | None:
    """Every caller input must be a real absolute path — an error line, or None."""
    checks = (
        (args.scripts_dir.is_dir(), f"scripts-dir is not a directory: {args.scripts_dir}"),
        (args.db.is_file(), f"database not found: {args.db}"),
        (
            args.python.is_file() and os.access(args.python, os.X_OK),
            f"python is not an executable file: {args.python}",
        ),
        (args.manifest.is_file(), f"manifest not found: {args.manifest}"),
    )
    return next((message for ok, message in checks if not ok), None)


def _missing_script(entries: list[Migration]) -> str | None:
    """The ref must ship every script it lists — silence here is drift."""
    for entry in entries:
        for script in (entry.apply, entry.check):
            if not script.is_file():
                return f"manifest lists a missing script: {script}"
    return None


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--manifest", required=True, type=Path, help="migration manifest path")
    parser.add_argument("--db", required=True, type=Path, help="sqlite database to migrate")
    parser.add_argument(
        "--scripts-dir",
        required=True,
        type=Path,
        help="absolute checkout the manifest paths resolve against",
    )
    parser.add_argument(
        "--python",
        required=True,
        type=Path,
        help="absolute interpreter for the migration + check scripts",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write: dry-run, apply + backup, check")
    mode.add_argument("--dry-run", action="store_true", help="read-only report (the default)")
    args = parser.parse_args(argv)
    args.mode = "apply" if args.apply else "dry-run"
    return args


def _open_report() -> Report:
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    report = Report(log_dir / f"{LOG_PREFIX}{stamp}.log")
    _prune_logs(log_dir)
    return report


def _preflight(args: argparse.Namespace, report: Report) -> list[Migration] | str:
    """Everything that must hold before the DB is touched: a parsed manifest,
    present scripts, and disk for the journal and the backup."""
    problem = _validate_inputs(args)
    if problem is not None:
        return problem
    try:
        entries = parse_manifest(args.manifest.read_text(), args.scripts_dir)
    except ManifestError as exc:
        return f"bad manifest: {exc}"
    problem = _missing_script(entries) or _space_gate(args.db, report)
    if problem is not None:
        return problem
    report.say(f"== {len(entries)} migration(s) from {args.manifest}")
    return entries


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    report = _open_report()
    try:
        report.say(
            f"== run_migrations {dt.datetime.now(dt.UTC).isoformat(timespec='seconds')} "
            f"manifest={args.manifest} db={args.db} mode={args.mode}"
        )
        prepared = _preflight(args, report)
        if isinstance(prepared, str):
            report.say(f"run_migrations: {prepared}")
            return EXIT_REFUSED
        run = Run(db=args.db, python=args.python, scripts_dir=args.scripts_dir, report=report)
        if args.mode != "apply":
            return EXIT_OK if run.dry_run(prepared) == 0 else EXIT_FAILED
        failures = run.apply(prepared)
        return EXIT_OK if failures == 0 else EXIT_FAILED
    finally:
        report.close()


if __name__ == "__main__":
    raise SystemExit(main())
