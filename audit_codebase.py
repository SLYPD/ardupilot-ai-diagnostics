"""
Codebase Intelligence & Self-Audit Script
==========================================
Runs ruff (linting), vulture (dead-code detection), bandit (security
lint), and pip-audit (dependency vulnerability scan) against the
project root and prints a combined, terminal-friendly report.

Usage:
    python audit_codebase.py          # full audit
    python audit_codebase.py --quiet  # only print when issues found
"""
import subprocess
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _safe_print(text: str) -> None:
    """Print to stdout, replacing characters that the terminal can't encode."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace")
              .decode(sys.stdout.encoding or "utf-8", errors="replace"))


def run_tool(name: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run a tool via python -m and return its CompletedProcess."""
    return subprocess.run(
        [sys.executable, "-m"] + args,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    quiet = "--quiet" in sys.argv
    issues_found = 0

    # ------------------------------------------------------------------
    # 1. ruff check
    # ------------------------------------------------------------------
    _safe_print("=" * 60)
    _safe_print("  ruff check")
    _safe_print("=" * 60)
    ruff = run_tool("ruff", ["ruff", "check", "."])
    if ruff.stdout and ruff.stdout.strip():
        _safe_print(ruff.stdout)
        # Parse "Found N errors" line for an accurate count
        for line in ruff.stdout.splitlines():
            if line.startswith("Found") and "error" in line:
                try:
                    issues_found += int(line.split()[1])
                except (ValueError, IndexError):
                    pass
    if ruff.stderr and ruff.stderr.strip():
        _safe_print(f"[ruff stderr]\n{ruff.stderr}")
    if not (ruff.stdout and ruff.stdout.strip()) and not (ruff.stderr and ruff.stderr.strip()):
        _safe_print("  [OK] No linting issues found.")

    # ------------------------------------------------------------------
    # 2. vulture
    # ------------------------------------------------------------------
    _safe_print("\n" + "=" * 60)
    _safe_print("  vulture (dead-code detection)")
    _safe_print("=" * 60)
    vulture = run_tool("vulture", ["vulture", "."])
    if vulture.stdout and vulture.stdout.strip():
        _safe_print(vulture.stdout)
        vulture_lines = [line for line in vulture.stdout.splitlines() if line.strip()]
        issues_found += len(vulture_lines)
    if vulture.stderr and vulture.stderr.strip():
        _safe_print(f"[vulture stderr]\n{vulture.stderr}")
    if not (vulture.stdout and vulture.stdout.strip()) and not (vulture.stderr and vulture.stderr.strip()):
        _safe_print("  [OK] No dead code found.")

    # ------------------------------------------------------------------
    # 3. bandit (security linting)
    # ------------------------------------------------------------------
    _safe_print("\n" + "=" * 60)
    _safe_print("  bandit (security lint)")
    _safe_print("=" * 60)
    bandit = run_tool("bandit", ["bandit", "-r", ".", "-ll", "-q"])
    if bandit.stdout and bandit.stdout.strip():
        _safe_print(bandit.stdout)
        for line in bandit.stdout.splitlines():
            if "Issue:" in line or "issues" in line.lower():
                try:
                    issues_found += 1
                except (ValueError, IndexError):
                    pass
    if bandit.stderr and bandit.stderr.strip():
        _safe_print(f"[bandit stderr]\n{bandit.stderr}")
    if not (bandit.stdout and bandit.stdout.strip()) and not (bandit.stderr and bandit.stderr.strip()):
        _safe_print("  [OK] No security issues found.")

    # ------------------------------------------------------------------
    # 4. pip-audit (dependency vulnerability scan)
    # ------------------------------------------------------------------
    _safe_print("\n" + "=" * 60)
    _safe_print("  pip-audit (dependency vulnerabilities)")
    _safe_print("=" * 60)
    pip_audit = run_tool(
        "pip_audit", ["pip_audit", "--requirement", "requirements.txt"]
    )
    if pip_audit.returncode != 0:
        out = pip_audit.stdout or pip_audit.stderr or "Vulnerabilities found."
        _safe_print(out)
        vuln_lines = [line for line in (out or "").splitlines() if line.strip()]
        issues_found += max(1, len(vuln_lines))
    else:
        _safe_print("  [OK] No known vulnerabilities in dependencies.")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _safe_print("\n" + "=" * 60)
    if issues_found == 0:
        _safe_print("  Audit clean — zero issues.")
    else:
        _safe_print(f"  {issues_found} issue(s) flagged across ruff + vulture + bandit + pip-audit.")
    _safe_print("=" * 60)

    # In quiet mode, exit non-zero if there are issues (for CI hooks)
    if quiet:
        return 1 if issues_found else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
