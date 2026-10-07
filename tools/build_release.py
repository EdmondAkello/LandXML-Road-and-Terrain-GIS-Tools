"""Build the QGIS plugin repository ZIP and run the repository's upload checks.

Usage (from the repository root):

    python tools/build_release.py            # build dist/<package>-<version>.zip and check it
    python tools/build_release.py --check X  # check an existing ZIP only

The ZIP contains committed files only (``git ls-files``), restricted to the
plugin runtime: tests, developer documents and this tools folder are left
out. The checks mirror plugins.qgis.org: the upload validator (layout,
forbidden files, required metadata, LICENSE, package name), and the security
scan (Bandit with the repository's enabled rules, detect-secrets, flake8 with
the enabled codes, executable and hidden files). Bandit, detect-secrets and
flake8 must be installed (``pip install bandit detect-secrets flake8``).
"""

import argparse
import configparser
import json
import os
import re
import shutil
import subprocess  # nosec B404 - runs local developer tools only
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The folder name QGIS uses to identify the plugin; it must never change between versions.
PACKAGE = "landxml_tin_to_geotiff"

EXCLUDE_PREFIXES = ("tests/", "tools/", "docs/", "examples/", ".")
EXCLUDE_FILES = {"HISTORY.md"}

REQUIRED_METADATA = ("name", "description", "version", "qgisMinimumVersion", "author", "email", "about", "tracker", "repository")

# Rules enabled in the plugins.qgis.org security scan (bandit_rules.json, flake8_rules.json).
BANDIT_TESTS = (
    "B101,B102,B103,B104,B105,B106,B107,B108,B110,B111,B112,B113,B201,B202,B301,B302,B303,B304,B305,B306,B307,"
    "B308,B310,B311,B312,B313,B314,B315,B316,B317,B318,B319,B320,B321,B323,B324,B401,B402,B403,B405,B406,B407,"
    "B408,B409,B412,B413,B501,B502,B503,B504,B505,B506,B507,B508,B509,B601,B602,B603,B604,B605,B606,B607,B608,"
    "B609,B610,B611,B612,B613,B614,B615,B701,B702,B703,B704"
)
FLAKE8_SELECT = "E101,E711,E712,E713,E714,E721,E722,E731,E741,E742,E743,E901,E902,E999,W605,F402,F403,F404,F405,F811,F821,F822,F823,F831,F901"
SUSPICIOUS = (".exe", ".dll", ".so", ".dylib", ".bat", ".sh", ".ps1", ".cmd")


def git_files():
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)  # nosec B603 B607
    return [name for name in output.decode("utf-8").split("\0") if name]


def release_files():
    return sorted(
        name for name in git_files()
        if not name.startswith(EXCLUDE_PREFIXES) and name not in EXCLUDE_FILES and "__pycache__" not in name and not name.endswith(".pyc")
    )


def read_metadata(text):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(text)
    return dict(parser.items("general"))


def build(output_dir):
    with open(os.path.join(ROOT, "metadata.txt"), encoding="utf-8") as stream:
        metadata = read_metadata(stream.read())
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{PACKAGE}-{metadata['version']}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in release_files():
            info = zipfile.ZipInfo(f"{PACKAGE}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16  # regular file, never executable
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(os.path.join(ROOT, name), "rb") as stream:
                archive.writestr(info, stream.read())
    return path


def run(command, cwd=None):
    return subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)  # nosec B603


def check(path):
    problems, notes = [], []
    if os.path.getsize(path) > 25_000_000:
        problems.append("ZIP is larger than 25 MB")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if archive.testzip():
            problems.append("ZIP has a corrupt member")
        parents = {name.split("/")[0] for name in names}
        if parents != {PACKAGE}:
            problems.append(f"top-level folders {sorted(parents)}; expected only {PACKAGE}")
        if not re.match(r"^[a-z_][a-z0-9_]*$", PACKAGE):
            problems.append("package folder is not a PEP 8 name")
        for name in names:
            parts = name.split("/")
            if ".." in name or name.startswith("/"):
                problems.append(f"path information in {name}")
            if ".pyc" in name or {"__MACOSX", ".git", "__pycache__"} & set(parts):
                problems.append(f"forbidden file {name}")
            if os.path.basename(name).startswith(".") and os.path.basename(name) not in (".gitignore", ".gitattributes"):
                problems.append(f"hidden file {name}")
            if name.lower().endswith(SUSPICIOUS):
                problems.append(f"executable or binary file {name}")
        for info in archive.infolist():
            if (info.external_attr >> 16) & 0o111 and info.filename.endswith(".py"):
                problems.append(f"executable permission on {info.filename}")
        for required in ("__init__.py", "metadata.txt", "LICENSE"):
            if f"{PACKAGE}/{required}" not in names:
                problems.append(f"missing {required}")
        metadata = read_metadata(archive.read(f"{PACKAGE}/metadata.txt").decode("utf-8"))
        missing = [key for key in REQUIRED_METADATA if not metadata.get(key, "").strip()]
        if missing:
            problems.append("missing metadata: " + ", ".join(missing))
        if "/" in metadata.get("author", ""):
            problems.append("author contains a slash")
        icon = metadata.get("icon", "")
        if not icon or f"{PACKAGE}/{icon.lstrip('./')}" not in names:
            problems.append("icon missing from the package")
        for key in ("tracker", "repository", "homepage"):
            if metadata.get(key) and not re.match(r"^https?://[^/\s]+", metadata[key]):
                problems.append(f"{key} is not a valid URL")
        notes.append(f"{len(names)} files, {os.path.getsize(path) / 1024:.0f} KB, version {metadata['version']}")
        temp = tempfile.mkdtemp()
        try:
            archive.extractall(temp)  # nosec B202 - our own archive, names checked above
            folder = os.path.join(temp, PACKAGE)
            bandit = run(["bandit", "-r", folder, "-f", "json", "--quiet", "-t", BANDIT_TESTS])
            try:
                results = json.loads(bandit.stdout or "{}").get("results", [])
            except ValueError:
                results = None
                problems.append("Bandit did not run: " + (bandit.stderr or bandit.stdout)[:200])
            for issue in results or []:
                problems.append(f"Bandit {issue['test_id']} {issue['filename'].replace(folder, '')}:{issue['line_number']} {issue['issue_text']}")
            secrets = run(["detect-secrets", "scan", "--all-files", "--exclude-files", r"metadata\.txt", "."], cwd=folder)
            try:
                found = json.loads(secrets.stdout).get("results", {})
            except ValueError:
                found = None
                problems.append("detect-secrets did not run: " + secrets.stderr[:200])
            for filename, items in (found or {}).items():
                for item in items:
                    problems.append(f"secret {item['type']} in {filename}:{item['line_number']}")
            python_files = [os.path.join(r, f) for r, _d, fs in os.walk(folder) for f in fs if f.endswith(".py")]
            flake8 = run(["flake8", "--max-line-length=120", "--select", FLAKE8_SELECT] + python_files)
            if flake8.returncode not in (0, 1):
                problems.append("flake8 did not run: " + flake8.stderr[:200])
            for line in flake8.stdout.splitlines():
                notes.append("flake8 (advisory): " + line.replace(folder, ""))
        finally:
            shutil.rmtree(temp, ignore_errors=True)
    return problems, notes


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", help="check an existing ZIP instead of building one")
    parser.add_argument("--output", default=os.path.join(ROOT, "dist"))
    args = parser.parse_args()
    path = args.check or build(args.output)
    problems, notes = check(path)
    print(path)
    for note in notes:
        print("  " + note)
    for problem in problems:
        print("  FAIL " + problem)
    print("  all checks passed" if not problems else f"  {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
