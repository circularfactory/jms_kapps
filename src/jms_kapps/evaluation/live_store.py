"""`jms-live-store`: the store chores of a /land live check on the KIT GraphDB (jms_kapps 121;
interim name, listed on jms_kapps 83). Development tooling, not part of the paper's artifact: the
release allowlist decision (jms_kapps 13) says whether it ships.

Four subcommands, one line of output each, exit code 0 on success and 1 on a refusal, so that a
/land script reads them:

- `login`: the token login (a plain username and password on a request gets 401 from the KIT
  GraphDB); one line, the success or the refusal.
- `create <name>`: the repository `<name>` from jms_kapps' repository config, the one the compose
  file mounts, with its SHACL layer and the ruleset `rdfsplus-optimized`, only the id changed.
- `size <name>`: the statement counts of any repository (explicit, inferred, total), read-only:
  the tripwire reads `jms-kapps` and `Tests` before and after a live check.
- `delete <name>`: the repository `<name>`, deleted.

`create` and `delete` accept only a name that starts with `ogm-disposable-`; any other name, and
`jms-kapps`, `Tests` and `kapps-demo` in any spelling, is refused before a request is sent.

The store and the identity come only from the environment: `TRIPLESTORE_URL`,
`TRIPLESTORE_USERNAME` and `TRIPLESTORE_PASSWORD`, with the deprecated `GRAPHDB_*` names as
fallbacks. No argument takes a credential, and nothing prints the password or the token.

The login, the guard and the config rewrite are the measurement harness's (`evaluation.store`),
reused with the prefix as a parameter: the harness keeps `eval-`.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from jms_kapps.evaluation.store import (
    AnswerLost,
    GraphDBServer,
    StoreRefused,
    create_run_repository,
    guarded,
    monitorable,
    public_url,
)

PROGRAM = "jms-live-store"

# The prefix of the disposable repositories on the KIT GraphDB: the only names `create` and
# `delete` take.
DISPOSABLE_PREFIX = "ogm-disposable-"

# The variables of the store, each with its deprecated fallback.
_VARIABLES = (
    ("TRIPLESTORE_URL", "GRAPHDB_URL"),
    ("TRIPLESTORE_USERNAME", "GRAPHDB_USERNAME"),
    ("TRIPLESTORE_PASSWORD", "GRAPHDB_PASSWORD"),
)


class Refused(RuntimeError):
    """A refusal, in one line for a person to read."""


def _environment(env: Mapping[str, str]) -> tuple[str, str, str]:
    """The URL, the username and the password, from `env`.

    Raises:
        Refused: If a variable is missing, naming it and its deprecated fallback.
    """
    values: list[str] = []
    missing: list[str] = []
    for name, deprecated in _VARIABLES:
        value = env.get(name) or env.get(deprecated)
        if value:
            values.append(value)
        else:
            missing.append(f"{name} (or the deprecated {deprecated})")
    if missing:
        raise Refused(f"{' and '.join(missing)} not set")
    url, username, password = values
    return url, username, password


def _scrub(text: str, secrets: Sequence[str]) -> str:
    """`text` on one line, without any of `secrets`."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return " ".join(text.split())


def _disposable(name: str) -> None:
    """Check that `create` and `delete` may take `name`.

    Raises:
        Refused: If it is a shared repository in any spelling or lacks the prefix.
    """
    try:
        guarded(name, DISPOSABLE_PREFIX)
    except StoreRefused as refused:
        raise Refused(
            f"{PROGRAM} creates and deletes only repositories named {DISPOSABLE_PREFIX}<id>, "
            f"never jms-kapps, Tests or kapps-demo: {refused}"
        ) from None


def _any_name(name: str) -> None:
    """Check that `name` is one segment of a path, as `size` takes it.

    Raises:
        Refused: If it is not.
    """
    try:
        monitorable(name)
    except StoreRefused as refused:
        raise Refused(str(refused)) from None


def _login(server: GraphDBServer, args: argparse.Namespace) -> str:
    server.login()
    return f"logged in at {public_url(server.url)}"


def _create(server: GraphDBServer, args: argparse.Namespace) -> str:
    created = create_run_repository(server, args.name, shacl=True)
    return f"created {args.name} (SHACL on, ruleset {created['ruleset']})"


def _size(server: GraphDBServer, args: argparse.Namespace) -> str:
    view = server.statement_counts(args.name)
    answer = view.get("answer")
    if view.get("status") != 200 or not isinstance(answer, dict):
        why = " ".join(
            str(part) for part in (view.get("status"), view.get("error")) if part
        )
        raise Refused(f"the store gave no size for {args.name}: {why or 'no answer'}")
    counts = " ".join(
        f"{key}={answer.get(key)}" for key in ("explicit", "inferred", "total")
    )
    return f"{args.name}: {counts}"


def _delete(server: GraphDBServer, args: argparse.Namespace) -> str:
    server.adopt(args.name)
    try:
        server.delete_repository(args.name)
    except AnswerLost as lost:
        if not server.deleted_after_a_lost_answer(args.name):
            raise Refused(f"the answer to the deletion was lost ({lost})") from None
    return f"deleted {args.name}"


# Each subcommand: its help, whether it takes a name, how the name is checked before any request,
# and what it does.
_Command = tuple[str, bool, Callable[[str], None] | None, Callable[..., str]]
_COMMANDS: dict[str, _Command] = {
    "login": ("Log in with the token login", False, None, _login),
    "create": (
        f"Create the repository {DISPOSABLE_PREFIX}<id> from jms_kapps' config",
        True,
        _disposable,
        _create,
    ),
    "size": ("Print the statement counts of any repository", True, _any_name, _size),
    "delete": (
        f"Delete the repository {DISPOSABLE_PREFIX}<id>",
        True,
        _disposable,
        _delete,
    ),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="The store chores of a live check: the store and the identity come from "
        "TRIPLESTORE_URL, TRIPLESTORE_USERNAME and TRIPLESTORE_PASSWORD.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, (help_, takes_name, _, _) in _COMMANDS.items():
        sub = commands.add_parser(name, help=help_, description=help_)
        if takes_name:
            sub.add_argument("name", help="The repository")
    return parser


def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    client: httpx.Client | None = None,
) -> tuple[int, str]:
    """Run one command; return the exit code and its one line of output.

    Args:
        argv: The command line without the program.
        env: The environment the store and the identity come from.
        client: The HTTP client, one of its own if None: a test's stand-in of the store.
    """
    args = _parser().parse_args(argv)
    _, _, check, action = _COMMANDS[args.command]
    secrets: list[str] = []
    try:
        if check is not None:
            check(args.name)
        url, username, password = _environment(env)
        secrets.extend((password, urlsplit(url).password or ""))
        server = GraphDBServer(
            url,
            username=username,
            password=password,
            client=client,
            prefix=DISPOSABLE_PREFIX,
            size_of_any=True,
        )
        try:
            line = action(server, args)
        finally:
            secrets.append(server.token)
            server.close()
        return 0, line
    except (Refused, StoreRefused, AnswerLost) as refused:
        return 1, _scrub(f"{PROGRAM}: {refused}", secrets)
    except httpx.HTTPError as error:
        return 1, _scrub(
            f"{PROGRAM}: the store did not answer: {type(error).__name__}", secrets
        )
    except Exception as error:  # noqa: BLE001 - no traceback may carry a secret
        return 1, f"{PROGRAM}: failed with {type(error).__name__}"


def main(argv: list[str] | None = None) -> int:
    code, line = run(sys.argv[1:] if argv is None else argv, os.environ)
    print(line, file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
