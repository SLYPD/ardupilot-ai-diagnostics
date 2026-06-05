"""
Codebase Intelligence & Self-Audit Script
==========================================
Runs ruff (linting) and vulture (dead-code detection) against the
project root and prints a combined, terminal-friendly report.

Usage:
    python audit_codebase.py          # full audit
    python audit_codebase.py --quiet  # only print when issues found
"""
import subprocess
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def run_tool(name: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run a tool via python -m and return its CompletedProcess."""
    return subprocess.run(
        [sys.executable, "-m"] + args,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def main() -> int:
    quiet = "--quiet" in sys.argv
    issues_found = 0

    # ------------------------------------------------------------------
    # 1. ruff check
    # ------------------------------------------------------------------
    print("=" * 60)
    print("  ruff check")
    print("=" * 60)
    ruff = run_tool("ruff", ["ruff", "check", "."])
    if ruff.stdout.strip():
        print(ruff.stdout)
        # Parse "Found N errors" line for an accurate count
        for line in ruff.stdout.splitlines():
            if line.startswith("Found") and "error" in line:
                try:
                    issues_found += int(line.split()[1])
                except (ValueError, IndexError):
                    pass
    if ruff.stderr.strip():
        print(f"[ruff stderr]\n{ruff.stderr}")
    if not ruff.stdout.strip() and not ruff.stderr.strip():
        print("  [OK] No linting issues found.")

    # ------------------------------------------------------------------
    # 2. vulture
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("  vulture (dead-code detection)")
    print("=" * 60)
    vulture = run_tool("vulture", ["vulture", "."])
    if vulture.stdout.strip():
        print(vulture.stdout)
        vulture_lines = [line for line in vulture.stdout.splitlines() if line.strip()]
        issues_found += len(vulture_lines)
    if vulture.stderr.strip():
        print(f"[vulture stderr]\n{vulture.stderr}")
    if not vulture.stdout.strip() and not vulture.stderr.strip():
        print("  [OK] No dead code found.")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    if issues_found == 0:
        print("  Audit clean — zero issues.")
    else:
        print(f"  {issues_found} issue(s) flagged across ruff + vulture.")
    print("=" * 60)

    # In quiet mode, exit non-zero if there are issues (for CI hooks)
    if quiet:
        return 1 if issues_found else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
