"""The store of one run: a repository of its own on the configured GraphDB, and the guard around it.

**One repository for each run.** A run creates a repository named `eval-` plus its run id, through
GraphDB's REST API, from jms_kapps' repository config, `graphdb/jms-kapps-repo-config.ttl`: with
SHACL on, the config with only its id changed; with SHACL off, the twin, which also drops the SHACL
layer around the store, so that it differs from jms_kapps' config only in that layer and the id.
Both run the ruleset `rdfsplus-optimized`: a config with another ruleset is refused before it is
sent, and so is a repository for which the store answers another ruleset. The run deletes its
repository at its end, also after a failure, and never any other.

**The guard.** Every request the server sends goes through `permit`, which holds an explicit list
of the requests a run may send, each by its method and its path. A request that names a repository
is one of two kinds. The monitoring view of a repository (`GET rest/monitor/repository/<id>`) may
name any repository of the server, the protected ones included (`monitorable`); it only reads the
counters of queries, transactions and connections. Everything else that names a repository,
reads of its config and of its size and the creation and deletion, takes only `guarded`: `eval-`
plus a run id of letters, digits, `-` and `_`. `guarded` refuses `jms-kapps`, the repository of
the demonstration, `kapps-demo`, the middleware's, and `Tests`, the live suites' of every library,
in any spelling, and every name without `eval-`. So every write, and every read of data (a
query, the statements, an export, the size of a repository), is refused for any repository but the
run's own. A refused request is not sent. The server deletes only a repository that it created
itself in this run.

**What the Workbench shows.** The facts a record holds of the server are the answers of the REST
views that the GraphDB Workbench reads, each kept whole as the server answered it, with its status
(`View`): the version and the edition, the repositories, the resource view and the structures view
(Monitor, System), the license, the run's own repository, its config, its size and its query and
transaction view, and the query and transaction view of every other repository. A view that the
server refuses is kept with its status and why, and the run goes on: the harness invents no fact.

The login is the triple-store interface's, `POST rest/login`, with the connection variables
`GRAPHDB_URL`, `GRAPHDB_USERNAME` and `GRAPHDB_PASSWORD`, which the command reads. Neither the user
nor the password is kept anywhere a record reads.

The store helper (interim names)
`jms-live-store` reuses the login, the guard and the config rewrite for the store chores of a
/land live check. The guard's prefix is a parameter (`guarded`, `permit`, `repository_config`,
`GraphDBServer(prefix=...)`), `eval-` by default, so the harness is unchanged; the helper passes
`ogm-disposable-`. Two exceptions serve the helper alone and are off by default: `size_of_any`
lets the size of any repository be read (the tripwire reads `jms-kapps` and `Tests`), and `adopt`
holds a repository that another process created as one to delete. `login` and `token` are public
for the helper.
"""

from __future__ import annotations

import functools
import re
import time
from collections.abc import Collection
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from rdflib import Graph, Literal, Namespace

# The vocabulary of a GraphDB repository config, by the prefixes its Turtle gives it: `rep:`,
# `sr:`, `sail:` and `graphdb:`.
REP = Namespace("http://www.openrdf.org/config/repository#")
SR = Namespace("http://www.openrdf.org/config/repository/sail#")
SAIL = Namespace("http://www.openrdf.org/config/sail#")
GRAPHDB = Namespace("http://www.ontotext.com/config/graphdb#")

# The sail type of the SHACL layer that wraps the store's own sail in jms_kapps' config.
SHACL_SAIL_TYPE = "rdf4j:ShaclSail"

# The only ruleset a run's repository may run: the ruleset of every repository of jms_kapps.
RULESET = "rdfsplus-optimized"

# Every repository a run creates starts with this, and the run id follows.
REPOSITORY_PREFIX = "eval-"

# The repositories of the demonstration, the middleware and the live suites of every library: never
# a run's, in any spelling.
PROTECTED_REPOSITORIES = ("jms-kapps", "kapps-demo", "Tests")


@functools.cache
def _name_pattern(prefix: str) -> re.Pattern[str]:
    """The names a guard with `prefix` takes: the prefix and an id of letters, digits, `-`, `_`."""
    return re.compile(re.escape(prefix) + r"[A-Za-z0-9][A-Za-z0-9_-]*")


# The id of a repository the monitoring view may name: one segment of a path.
_MONITORED_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# The state of a repository that a monitoring read may name: the others are not read.
RUNNING = "RUNNING"

# The views of the server that name no repository, each read by GET: the version, the
# repositories, the resource view, the structures view (the global page cache) and the license
# (all as the Workbench reads them; paths of GraphDB 10.8).
SERVER_VIEWS = (
    "rest/info/version",
    "rest/repositories",
    "rest/monitor/infrastructure",
    "rest/monitor/structures",
    "rest/graphdb-settings/license",
)

# jms_kapps' repository config, at the root of a checkout of jms_kapps.
JMS_KAPPS_REPOSITORY_CONFIG = (
    Path(__file__).resolve().parents[3] / "graphdb" / "jms-kapps-repo-config.ttl"
)

# What a view is: the request, the status the server answered, and its answer, or why there is
# none.
View = dict[str, Any]


class StoreRefused(RuntimeError):
    """The guard refused a repository, or the store refused or answered what a run cannot run on.
    The message says which and why, for a person to read."""


# The statuses with which a gateway in front of the store answers in its place: 502 Bad Gateway,
# 503 Service Unavailable and 504 Gateway Timeout. The KIT GraphDB is behind such a proxy. An
# answer to a deletion with one of them is a lost answer, not a refusal (interim name).
GATEWAY_STATUSES = frozenset({502, 503, 504})


class AnswerLost(RuntimeError):
    """The store's answer to a request was lost: a gateway in front of it answered in its place
    (`GATEWAY_STATUSES`), so whether the store did what was asked is not known. The message says
    the status, for a person to read (interim name)."""


def guarded(repository: str, prefix: str = REPOSITORY_PREFIX) -> str:
    """`repository`, if a caller may name it: `prefix` and an id (`eval-` and a run id for the
    harness; the store helper `jms-live-store` passes its own, `ogm-disposable-`; interim name).

    Raises:
        StoreRefused: If it is `jms-kapps`, `kapps-demo` or `Tests` in any spelling, or is not
            `prefix` and an id of letters, digits, `-` and `_`.
    """
    if repository.casefold() in {name.casefold() for name in PROTECTED_REPOSITORIES}:
        raise StoreRefused(
            f"a run never names the repository {repository!r}: it is not the run's own"
        )
    if not _name_pattern(prefix).fullmatch(repository):
        raise StoreRefused(
            f"a run names only its own repository, {prefix!r} and a run id of "
            f"letters, digits, '-' and '_', not {repository!r}"
        )
    return repository


def monitorable(repository: str) -> str:
    """`repository`, if the monitoring view may name it: any id of letters, digits, `.`, `-` and
    `_` that begins with a letter or a digit, so that it is one segment of a path and no more. It
    is the one place where a run names a repository other than its own, and only to read the
    counters of its queries and transactions.

    Raises:
        StoreRefused: If it is no such id.
    """
    if not _MONITORED_REPOSITORY.fullmatch(repository):
        raise StoreRefused(
            f"the monitoring view names a repository by an id of letters, digits, '.', '-' and "
            f"'_', not {repository!r}"
        )
    return repository


def permit(
    method: str,
    path: str,
    own: Collection[str] | None = None,
    *,
    prefix: str = REPOSITORY_PREFIX,
    size_of_any: bool = False,
) -> None:
    """Let a request through: only the requests of the list below, and nothing that a pattern
    could also match a write with.

    - `GET` of a view in `SERVER_VIEWS`;
    - `GET rest/monitor/repository/<id>` of any repository (`monitorable`);
    - `POST rest/repositories`, the creation of a repository (the caller checks its config names
      the run's own);
    - `GET rest/repositories/<id>` and `GET rest/repositories/<id>/size` of the run's own
      repository, and `DELETE rest/repositories/<id>` of the run's own (`guarded`).

    Args:
        own: The repositories that are the run's own, where the caller knows them: a repository
            of the `eval-` form that is not among them, another run's, is refused too.
        prefix: The prefix of the repositories the caller may create and delete (`guarded`).
        size_of_any: Whether `GET rest/repositories/<id>/size` may name any repository
            (`monitorable`), as the store helper's tripwire reads the shared ones.

    Raises:
        StoreRefused: If the request is none of these, or names a repository the list does not
            allow it to.
    """
    parts = path.split("/")
    if method == "GET" and path in SERVER_VIEWS:
        return
    if method == "POST" and path == "rest/repositories":
        return
    if method == "GET" and parts[:3] == ["rest", "monitor", "repository"]:
        if len(parts) == 4:
            monitorable(parts[3])
            return
    elif parts[:2] == ["rest", "repositories"]:
        if len(parts) == 3 and method in ("GET", "DELETE"):
            _own(parts[2], own, prefix)
            return
        if len(parts) == 4 and parts[3] == "size" and method == "GET":
            if size_of_any:
                monitorable(parts[2])
            else:
                _own(parts[2], own, prefix)
            return
    raise StoreRefused(
        f"a run never sends {method} {path}: it is not on the list of its requests"
    )


def _own(
    repository: str, own: Collection[str] | None, prefix: str = REPOSITORY_PREFIX
) -> None:
    """Let `repository` through as the run's own: `guarded`, and among `own` where it is given."""
    guarded(repository, prefix)
    if own is not None and repository not in own:
        raise StoreRefused(
            f"{repository!r} is not this run's own repository: a run reads, sizes and deletes "
            "only the repository it creates"
        )


def repository_name(run_id: str) -> str:
    """The repository of the run `run_id`: `eval-` and the run id.

    Raises:
        StoreRefused: If the run id makes no name the guard takes.
    """
    return guarded(REPOSITORY_PREFIX + run_id)


def jms_kapps_repository_config() -> str:
    """jms_kapps' repository config, as Turtle.

    Raises:
        StoreRefused: If it is not there, as in an installed copy without the checkout.
    """
    try:
        return JMS_KAPPS_REPOSITORY_CONFIG.read_text(encoding="utf-8")
    except OSError as error:
        raise StoreRefused(
            f"jms_kapps' repository config cannot be read at "
            f"{str(JMS_KAPPS_REPOSITORY_CONFIG)!r}: {error}. The harness runs from a checkout "
            "of jms_kapps"
        ) from None


def repository_config(
    repository: str,
    *,
    shacl: bool,
    source: str | None = None,
    prefix: str = REPOSITORY_PREFIX,
) -> str:
    """The config of the run's repository `repository`, as Turtle: jms_kapps' config with its id
    changed, and without its SHACL layer unless `shacl`.

    The SHACL layer is the `rdf4j:ShaclSail` that jms_kapps' config puts around the store's own
    sail, with every setting of its own. The twin points the repository at the store's sail
    directly; nothing else changes, not even the label.

    Args:
        source: The config to start from, jms_kapps' if None.
        prefix: The prefix `repository` must have (`guarded`).

    Raises:
        StoreRefused: If the guard refuses `repository`, if the config does not name one
            repository with a store's sail in it, or if its ruleset is not `rdfsplus-optimized`.
    """
    guarded(repository, prefix)
    graph = Graph().parse(
        data=jms_kapps_repository_config() if source is None else source,
        format="turtle",
    )
    named = list(graph.subjects(REP.repositoryID, None))
    if len(named) != 1:
        raise StoreRefused(
            f"a repository config names one repository, this one names {len(named)}"
        )
    [node] = named
    graph.set((node, REP.repositoryID, Literal(repository)))
    implementation = graph.value(node, REP.repositoryImpl)
    sail = graph.value(implementation, SR.sailImpl)
    if implementation is None or sail is None:
        raise StoreRefused("the repository config has no sail")
    if str(graph.value(sail, SAIL.sailType)) == SHACL_SAIL_TYPE:
        store = graph.value(sail, SAIL.delegate)
        if store is None:
            raise StoreRefused(
                "the SHACL layer of the repository config wraps no store"
            )
        if not shacl:
            graph.remove((sail, None, None))
            graph.set((implementation, SR.sailImpl, store))
    elif shacl:
        raise StoreRefused("the repository config has no SHACL layer to keep")
    else:
        store = sail
    ruleset = graph.value(store, GRAPHDB.ruleset)
    if str(ruleset) != RULESET:
        raise StoreRefused(
            f"a run's repository runs the ruleset {RULESET}, and the config names {ruleset}"
        )
    return graph.serialize(format="turtle")


def public_url(url: str) -> str:
    """`url` without a user or a password in it."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


class GraphDBServer:
    """GraphDB's REST API, as a run uses it: create, read and delete the run's own repository, and
    read the views the Workbench shows.

    Every request it sends is kept in `requests`, by its method and its path, so that a record
    shows that no request named a repository other than the run's own.

    Args:
        url: The server, `GRAPHDB_URL`.
        username: The login's user, `GRAPHDB_USERNAME`.
        password: The login's password, `GRAPHDB_PASSWORD`.
        client: The HTTP client, one of its own if None: a test's stand-in of the store.
        timeout: How long a request may take, in seconds.
        prefix: The prefix of the repositories it creates and deletes (`guarded`).
        size_of_any: Whether it reads the size of any repository, not only its own.
    """

    def __init__(
        self,
        url: str,
        *,
        username: str,
        password: str,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
        prefix: str = REPOSITORY_PREFIX,
        size_of_any: bool = False,
    ) -> None:
        self.prefix = prefix
        self._size_of_any = size_of_any
        self.url = url.rstrip("/")
        self._client = (
            client
            if client is not None
            else httpx.Client(base_url=self.url, timeout=timeout)
        )
        self._login = {"username": username, "password": password}
        self._authorization: str | None = None
        self._created: set[str] = set()
        # The repositories this server has set out to create: the run's own.
        self._own: set[str] = set()
        self.requests: list[str] = []

    @property
    def created(self) -> frozenset[str]:
        """The repositories this server created and has not deleted yet."""
        return frozenset(self._created)

    def _send(self, method: str, path: str, **options: Any) -> httpx.Response:
        """Send one request, logged in first, and keep its method and path.

        Raises:
            StoreRefused: If the request is not on the guard's list (`permit`), and nothing is sent.
        """
        permit(
            method,
            path,
            self._own,
            prefix=self.prefix,
            size_of_any=self._size_of_any,
        )
        self.login()
        self.requests.append(f"{method} {path}")
        headers = {"Accept": "application/json"} | options.pop("headers", {})
        if self._authorization:
            headers["Authorization"] = self._authorization
        return self._client.request(method, "/" + path, headers=headers, **options)

    @property
    def token(self) -> str:
        """The token the login gave, empty before it: for a caller that keeps it out of its
        output."""
        return self._authorization or ""

    def login(self) -> None:
        """Log in with the token login (a plain username and password on a request gets 401 from
        the KIT GraphDB), once: later calls do nothing.

        Raises:
            StoreRefused: If the store refuses the login.
        """
        if self._authorization is not None:
            return
        self.requests.append("POST rest/login")
        login = self._client.post("/rest/login", json=self._login)
        if login.status_code != 200:
            raise StoreRefused(
                f"the store at {public_url(self.url)} refused the login: {login.status_code}"
            )
        self._authorization = login.headers.get("Authorization", "")

    def adopt(self, repository: str) -> None:
        """Hold `repository`, which this server did not create, as its own to delete: the store
        helper deletes in a process of its own, apart from the one that created.

        Raises:
            StoreRefused: If the guard refuses `repository`.
        """
        guarded(repository, self.prefix)
        self._own.add(repository)
        self._created.add(repository)

    def _view(self, path: str) -> View:
        """The view at `path`, as the server answered it: its status and its answer, or why there
        is none. It never raises: a view the server refuses or does not answer is kept as such."""
        request = f"GET {path}"
        try:
            response = self._send("GET", path)
        except httpx.HTTPError as error:
            return {"request": request, "error": str(error) or type(error).__name__}
        if response.status_code != 200:
            return {
                "request": request,
                "status": response.status_code,
                "error": response.text,
            }
        try:
            answer = response.json()
        except ValueError:
            answer = response.text
        return {"request": request, "status": 200, "answer": answer}

    def version(self) -> View:
        """The server's version and edition (the Workbench's About)."""
        return self._view("rest/info/version")

    def repositories(self) -> View:
        """Every repository of the server, with its state (the Workbench's repositories)."""
        return self._view("rest/repositories")

    def resources(self) -> View:
        """The server's CPU load, memory, threads and disks (Monitor, System, Resources)."""
        return self._view("rest/monitor/infrastructure")

    def repository(self, repository: str) -> View:
        """The config of the run's repository, as the server holds it.

        Raises:
            StoreRefused: If the guard refuses `repository`. Nothing is sent then.
        """
        return self._view(f"rest/repositories/{guarded(repository, self.prefix)}")

    def structures(self) -> View:
        """The hits and misses of the global page cache, and the entity pool (Monitor, System,
        Structures): `rest/monitor/structures`, documented in GraphDB 10.8's system monitoring."""
        return self._view("rest/monitor/structures")

    def license(self) -> View:
        """The license: its edition and the CPU cores it licenses (Setup, License):
        `rest/graphdb-settings/license`, the path the Workbench reads. A login that may not read
        it is refused, and the refusal is kept."""
        return self._view("rest/graphdb-settings/license")

    def statement_counts(self, repository: str) -> View:
        """The statements of the run's repository, explicit, inferred and in all (the
        Workbench's repository size): `rest/repositories/<id>/size`. 10.8.5 answers a wrong size
        inside an open transaction (GDB-12163), so the caller reads it where no participant
        writes.

        Raises:
            StoreRefused: If the guard refuses `repository`. Nothing is sent then.
        """
        name = (
            monitorable(repository)
            if self._size_of_any
            else guarded(repository, self.prefix)
        )
        return self._view(f"rest/repositories/{name}/size")

    def repository_monitor(self, repository: str) -> View:
        """The queries, transactions and connections of a repository (Monitor, System,
        Performance): the one view that may name any repository of the server (`monitorable`).

        Raises:
            StoreRefused: If `repository` is no id a path takes. Nothing is sent then.
        """
        return self._view(f"rest/monitor/repository/{monitorable(repository)}")

    def repository_monitors(self, repositories: View) -> dict[str, View]:
        """The monitoring view of every repository that the view `repositories` (the server's
        list) names as `RUNNING`, by its id, the protected ones included. A repository in any
        other state, or in none, is kept with its state and marked as not read, and no request
        names it: the view of an inactive repository might initialise it, which would load
        another user's repository into the shared server, and an inactive repository has no
        queries, transactions or connections to count. Empty where the list was refused: the
        refusal is in the list's own view."""
        answer = repositories.get("answer")
        monitors: dict[str, View] = {}
        for entry in answer if isinstance(answer, list) else []:
            name = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(name, str) or name in monitors:
                continue
            state = entry.get("state")
            if state != RUNNING:
                monitors[name] = {
                    "request": None,
                    "state": state,
                    "read": False,
                    "reason": f"not read: the repository is {state}, not {RUNNING}",
                }
                continue
            try:
                monitors[name] = self.repository_monitor(name)
            except StoreRefused as refused:
                monitors[name] = {
                    "request": f"GET rest/monitor/repository/{name}",
                    "error": str(refused),
                }
        return monitors

    def round_trip(self) -> View:
        """One round trip: how long the server takes to answer its version, in milliseconds, with
        the status it answered. It never raises: a refused or lost read is kept as such."""
        request = "GET rest/info/version"
        started = time.perf_counter()
        view = self.version()
        taken = {
            "request": request,
            "milliseconds": round((time.perf_counter() - started) * 1000, 3),
        }
        if "status" in view:
            taken["status"] = view["status"]
        if "error" in view:
            taken["error"] = view["error"]
        return taken

    def round_trip_ms(self, times: int = 5) -> list[float]:
        """How long the server takes to answer its version, `times` times one after the other,
        in milliseconds: the network's round trip and the server's least work. The login comes
        before the first, so that no time counts it."""
        if self._authorization is None:
            self.version()
        taken = []
        for _ in range(times):
            started = time.perf_counter()
            self.version()
            taken.append(round((time.perf_counter() - started) * 1000, 3))
        return taken

    def create_repository(self, repository: str, config: str) -> None:
        """Create the run's repository `repository` from `config`, a repository config in
        Turtle.

        A repository of that name that exists already is not this run's, and is refused. Where the
        store's answer is lost, the repository is this run's if the store lists it then, so that
        the run deletes it.

        Raises:
            StoreRefused: If the guard refuses `repository`, or `config` names another
                repository, and nothing is sent; if a repository of that name exists already; or
                if the store did not create it.
        """
        guarded(repository, self.prefix)
        named = [
            str(name)
            for name in Graph()
            .parse(data=config, format="turtle")
            .objects(None, REP.repositoryID)
        ]
        if named != [repository]:
            raise StoreRefused(
                f"the config of {repository} names the repository {', '.join(named) or 'none'}"
            )
        if self._listed(repository):
            raise StoreRefused(
                f"the store holds a repository {repository} already, which this run did not "
                "create"
            )
        self._own.add(repository)
        try:
            response = self._send(
                "POST",
                "rest/repositories",
                files={"config": (f"{repository}.ttl", config.encode(), "text/turtle")},
            )
        except httpx.HTTPError as error:
            if self._listed(repository):
                self._created.add(repository)
            raise StoreRefused(
                f"the store's answer to the creation of {repository} was lost: {error}"
            ) from error
        if response.status_code not in (200, 201, 204):
            raise StoreRefused(
                f"the store did not create {repository}: {response.status_code} "
                f"{response.text}"
            )
        self._created.add(repository)

    def delete_repository(self, repository: str) -> None:
        """Delete the run's repository `repository`, which this server created.

        Raises:
            StoreRefused: If the guard refuses `repository`, or this server did not create it,
                and nothing is sent; or if the store did not delete it.
            AnswerLost: If a gateway in front of the store answered 502, 503 or 504 in its place

        """
        guarded(repository, self.prefix)
        if repository not in self._created:
            raise StoreRefused(
                f"a run deletes only its own repository, and it did not create {repository}"
            )
        response = self._send("DELETE", f"rest/repositories/{repository}")
        if response.status_code not in (200, 204):
            raise _not_deleted(repository, response)
        self._created.discard(repository)

    def deleted_after_a_lost_answer(self, repository: str) -> bool:
        """Whether the store deleted the run's repository `repository` although the deletion got
        no answer (the request did not reach the store, no answer came in the request's time, the
        connection dropped, or a gateway answered in the store's place, `AnswerLost`): one list
        of the server's repositories, by a request that names none, as the creation does after a
        lost answer. A repository that the store lists no more is deleted, and this server holds
        it as created no more; one that it still lists is not (interim name).

        Raises:
            StoreRefused: If the list gets no answer or is refused, so that whether the store
                holds the repository is not known; the message says why.
        """
        guarded(repository, self.prefix)
        listed = self.repositories()
        if not isinstance(listed.get("answer"), list):
            why = " ".join(
                str(part)
                for part in (listed.get("status"), listed.get("error"))
                if part is not None
            )
            raise StoreRefused(f"the server's repositories could not be listed: {why}")
        if _names(listed, repository):
            return False
        self._created.discard(repository)
        return True

    def _listed(self, repository: str) -> bool:
        """Whether the store lists a repository named `repository`, by a request that names
        none."""
        return _names(self.repositories(), repository)

    def close(self) -> None:
        self._client.close()


def _names(listed: View, repository: str) -> bool:
    """Whether `listed`, the view of the server's repositories, names `repository`: False where
    the store refused the list or did not answer it."""
    answer = listed.get("answer")
    return isinstance(answer, list) and any(
        isinstance(entry, dict) and entry.get("id") == repository for entry in answer
    )


def _not_deleted(repository: str, response: httpx.Response) -> Exception:
    """Why the store did not delete `repository`, from its answer `response`: a lost answer
    where a gateway answered in the store's place (`GATEWAY_STATUSES`), else a refusal."""
    if response.status_code in GATEWAY_STATUSES:
        return AnswerLost(
            f"the gateway in front of the store answered {response.status_code} "
            f"{response.text}"
        )
    return StoreRefused(
        f"the store did not delete {repository}: {response.status_code} {response.text}"
    )


def create_run_repository(
    server: GraphDBServer, repository: str, *, shacl: bool
) -> dict[str, Any]:
    """Create the run's repository from jms_kapps' config, with SHACL on or as the twin, and check
    the ruleset the store answers for it. Returns what a record keeps: the config `sent`, the view
    of the repository the store `answered`, and the `ruleset` in it.

    Raises:
        StoreRefused: If the config is refused, if the store did not create the repository, or if
            it answers a ruleset other than `rdfsplus-optimized` for it. The repository stays
            then, created by this server, which deletes it on request.
    """
    config = repository_config(repository, shacl=shacl, prefix=server.prefix)
    server.create_repository(repository, config)
    answered = server.repository(repository)
    ruleset = _ruleset(answered)
    if ruleset != RULESET:
        raise StoreRefused(
            f"the store answers the ruleset {ruleset} for {repository}, and a run runs on "
            f"{RULESET} only"
        )
    return {"sent": config, "answered": answered, "ruleset": ruleset}


def _ruleset(view: View) -> str | None:
    """The ruleset in the view of a repository's config, as the store answered it, or None
    where it answered none."""
    answer = view.get("answer")
    if not isinstance(answer, dict):
        return None
    ruleset = answer.get("params", {}).get("ruleset", {})
    value = ruleset.get("value") if isinstance(ruleset, dict) else None
    return value if isinstance(value, str) else None
