"""deploy.sh reuses the stack's alert email and refuses to change it.

Changing the alert address makes CloudFormation replace the two budgets, which
fails because they have fixed names. The "alert email" section of deploy.sh runs
here under bash, against a fake `aws` that reports the stack's parameters."""
import os
import stat
import subprocess

from conftest import ROOT

START = "# ------------------------------------------------------------------ alert email\n"
END = "# ------------------------------------------------------------------ custom domain\n"


def run_section(tmp_path, stack_email, alert_email=None):
    """Run the alert-email section. stack_email: the stack's AlertEmail value, or
    None when the stack doesn't exist. Returns (exit code, output, ALERT_EMAIL)."""
    with open(os.path.join(ROOT, "deploy.sh"), encoding="utf-8") as f:
        script = f.read()
    section = script[script.index(START):script.index(END)]

    fake = tmp_path / "aws"
    if stack_email is None:
        fake.write_text("#!/bin/sh\necho 'Stack does not exist' >&2\nexit 254\n")
    else:
        fake.write_text(f"#!/bin/sh\necho '{stack_email or 'None'}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -euo pipefail\n"
        'fail() { printf "\\nERROR: %s\\n" "$*" >&2; exit 1; }\n'
        'REGION=us-east-2; STACK_NAME=arrl-10ghz-web\n'
        + section +
        'echo "RESULT=${ALERT_EMAIL:-}"\n')
    env = {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
    if alert_email is not None:
        env["ALERT_EMAIL"] = alert_email
    done = subprocess.run(["bash", str(harness)], env=env, capture_output=True, text=True)
    result = next((l[len("RESULT="):] for l in done.stdout.splitlines() if l.startswith("RESULT=")), None)
    return done.returncode, done.stdout + done.stderr, result


def test_reuses_the_stacks_email_when_none_given(tmp_path):
    code, out, email = run_section(tmp_path, "rushealy@gmail.com")
    assert code == 0 and email == "rushealy@gmail.com"
    assert "reusing the stack's alert email" in out


def test_empty_alert_email_also_reuses_it(tmp_path):
    code, _, email = run_section(tmp_path, "rushealy@gmail.com", alert_email="")
    assert code == 0 and email == "rushealy@gmail.com"


def test_same_email_in_any_case_is_accepted(tmp_path):
    code, _, email = run_section(tmp_path, "rushealy@gmail.com", alert_email="RusHealy@Gmail.com")
    assert code == 0 and email == "RusHealy@Gmail.com"


def test_a_different_email_stops_the_deploy_early(tmp_path):
    # rus.healy@ and rushealy@ are the same Gmail inbox, but not the same text.
    code, out, email = run_section(tmp_path, "rushealy@gmail.com", alert_email="rus.healy@gmail.com")
    assert code != 0 and email is None
    assert "rus.healy@gmail.com" in out and "rushealy@gmail.com" in out
    assert "replaces the budgets" in out


def test_new_stack_takes_the_given_email(tmp_path):
    code, _, email = run_section(tmp_path, None, alert_email="me@example.com")
    assert code == 0 and email == "me@example.com"


def test_new_stack_without_an_email_stays_without_budgets(tmp_path):
    code, _, email = run_section(tmp_path, None)
    assert code == 0 and email == ""


def test_stack_without_budgets_can_add_them(tmp_path):
    code, _, email = run_section(tmp_path, "", alert_email="me@example.com")
    assert code == 0 and email == "me@example.com"
