#!/usr/bin/env bash
# Check by default. --install is an operator action on the HOSTED instance only.
# These jobs are headless coordinators. --no-agent suppresses the scheduler's
# LLM loop; a guarded daily route can still invoke paid Hermes model stages.
set -euo pipefail
GTM_INSTALL_MODE="${1:---check}"
if [[ "$GTM_INSTALL_MODE" != "--check" && "$GTM_INSTALL_MODE" != "--install" ]]; then
  echo 'Usage: bash scripts/hermes-install.sh [--check|--install]' >&2
  exit 2
fi
command -v hermes >/dev/null || { echo 'Hermes is not installed on this host.' >&2; exit 1; }
GTM_HERMES_VERSION="$(hermes --version | head -n 1)"
GTM_CREATE_HELP="$(hermes cron create --help)"
for GTM_FLAG in --no-agent --script --deliver --name --repeat --workdir; do
  [[ "$GTM_CREATE_HELP" == *"$GTM_FLAG"* ]] || { echo "Unsupported Hermes CLI: missing $GTM_FLAG" >&2; exit 1; }
done
printf 'Checked installed runtime: %s\n' "$GTM_HERMES_VERSION"
if [[ "$GTM_INSTALL_MODE" == "--check" ]]; then
  echo 'Cron CLI capability check passed; model routing and Cloud health remain separately gated.'
  echo 'No files or schedules created. Run installation only on the verified Hermes Cloud instance.'
  exit 0
fi
[[ "${GTM_HOSTED_INSTANCE_CONFIRMED:-}" == "yes" ]] || { echo 'Set GTM_HOSTED_INSTANCE_CONFIRMED=yes only in Hermes Cloud.' >&2; exit 1; }
[[ "${GTM_EXPECTED_HERMES_VERSION:-}" == "$GTM_HERMES_VERSION" ]] || { echo 'Set GTM_EXPECTED_HERMES_VERSION to the reviewed full first line from hermes --version.' >&2; exit 1; }
GTM_REPO="$(cd "$(dirname "$0")/.." && pwd)"
GTM_PYTHON="${GTM_PYTHON:-$GTM_REPO/.venv/bin/python}"
[[ "$GTM_PYTHON" == /* && -x "$GTM_PYTHON" ]] || { echo 'GTM_PYTHON must be an absolute Python 3.12+ executable.' >&2; exit 1; }
"$GTM_PYTHON" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
[[ -f "$GTM_REPO/config.toml" ]] || { echo 'Missing config.toml; initialize and review configuration first.' >&2; exit 1; }
GTM_EXISTING_JOBS="$(hermes cron list)"
[[ "$GTM_EXISTING_JOBS" != *disanchor-gtm-* ]] || { echo 'Existing disanchor-gtm jobs found. Inspect them; installation refuses duplicate schedules.' >&2; exit 1; }
GTM_SCRIPT_DIR="${HERMES_HOME:-$HOME/.hermes}/scripts"
mkdir -p "$GTM_SCRIPT_DIR"
chmod 700 "$GTM_SCRIPT_DIR"
export GTM_REPO GTM_PYTHON GTM_SCRIPT_DIR
"$GTM_PYTHON" - <<'PY'
import os
from pathlib import Path
repo = Path(os.environ['GTM_REPO']).resolve()
python = os.environ['GTM_PYTHON']
scripts = Path(os.environ['GTM_SCRIPT_DIR']).resolve()
allowed = {'UPLOAD_POST_API_KEY', 'CLONE_APP_KEY', 'CLONE_USER_ID',
           'GA4_ACCESS_TOKEN', 'GOOGLE_APPLICATION_CREDENTIALS', 'GA4_PROPERTY_ID', 'GTM_LEADS_AGGREGATE_PATH',
           'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_USER_ID'}
commands = ('daily', 'collect', 'reconcile', 'watchdog', 'report', 'telegram')
for command in commands:
    destination = scripts / ('disanchor-gtm-' + command + '.py')
    if destination.exists() or destination.is_symlink():
        raise SystemExit('Existing wrapper: ' + str(destination))
for command in commands:
    destination = scripts / ('disanchor-gtm-' + command + '.py')
    # A private env file is parsed as literal key=value, never sourced as shell.
    source = f'''import os, pathlib, stat
repo = pathlib.Path({str(repo)!r})
secret_file = repo / 'state' / 'runtime.env'
allowed = {allowed!r}
if secret_file.exists() or secret_file.is_symlink():
    metadata = secret_file.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise SystemExit('runtime.env must be a regular owner-only file owned by the worker user')
    for line in secret_file.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or key not in allowed:
            raise SystemExit('Unsupported runtime.env key or syntax')
        os.environ[key] = value
os.chdir(repo)
os.execv({python!r}, [{python!r}, '-m', 'gtm', '--config', str(repo/'config.toml'),
    '--state', str(repo/'state'/'gtm.sqlite'), '--output', str(repo/'reports'), {command!r}])
'''
    destination.write_text(source)
    destination.chmod(0o700)
PY
# Interval schedules avoid host-zone ambiguity; application slots use IANA zones.
# --repeat limits invocations, not elapsed time or Cloud billing. The application
# end_at guard stops new publication/model work even if a job fires late.
hermes cron create 'every 24h' --no-agent --script disanchor-gtm-daily.py --deliver local --name disanchor-gtm-daily --repeat 7 --workdir "$GTM_REPO"
hermes cron create 'every 8h' --no-agent --script disanchor-gtm-collect.py --deliver local --name disanchor-gtm-collect --repeat 21 --workdir "$GTM_REPO"
hermes cron create 'every 4h' --no-agent --script disanchor-gtm-reconcile.py --deliver local --name disanchor-gtm-reconcile --repeat 42 --workdir "$GTM_REPO"
hermes cron create 'every 1h' --no-agent --script disanchor-gtm-watchdog.py --deliver local --name disanchor-gtm-watchdog --repeat 168 --workdir "$GTM_REPO"
hermes cron create 'every 24h' --no-agent --script disanchor-gtm-report.py --deliver local --name disanchor-gtm-report --repeat 7 --workdir "$GTM_REPO"
# Dedicated Bot API poller: local scheduler output avoids duplicate Telegram
# delivery. Disabled config is a no-op; enabled delivery requires private IDs.
# 2016 five-minute slots cover 168 hours, plus one final status poll.
hermes cron create 'every 5m' --no-agent --script disanchor-gtm-telegram.py --deliver local --name disanchor-gtm-telegram --repeat 2017 --workdir "$GTM_REPO"
hermes cron list
echo 'Save the six returned job IDs; partial installs must be reconciled before retrying.'
echo 'Verify hosted scheduler health and persistent state; run the first jobs after recording experiment start.'
echo 'Telegram is a read-only status channel. Model and publication authorization gates still apply.'
echo 'Job repeat counts and experiment end_at do not stop Cloud billing; stop the instance separately.'
