"""Servicio correlacionador: lee los logs (solo lectura), arma el grafo y expone la traza.

Endpoints (todos salvo /healthz exigen `Authorization: Bearer <CORRELATOR_TOKEN>`):
  GET /trace/<cualquier-id>   traza reconstruida
  GET /stats                  conteos y % de trazas completas
  GET /metrics                formato Prometheus
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Dict, List, Union

from obsproto.common import BaseHandler, HttpError, env, serve, token_matches, valid_id
from obsproto.correlator.graph import IdGraph
from obsproto.correlator.parsers import BusLogParser, Event, parse_core_line, parse_gateway_line, parse_span_line

MAX_READ_PER_POLL = 1_048_576
MAX_PARTIAL = 4096


class Tailer:
    """Lee solo líneas completas y nuevas; tolera rotación/truncado del archivo."""

    def __init__(self, path: str, parse: Callable[[str], Union[None, Event, List[Event]]], fmt: str) -> None:
        self.path, self.parse, self.fmt = path, parse, fmt
        self._offset, self._inode, self._partial = 0, None, b""
        self.malformed = 0

    def poll(self, graph: IdGraph, counts: Dict[str, int]) -> None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return
        if st.st_ino != self._inode or st.st_size < self._offset:
            self._offset, self._inode, self._partial = 0, st.st_ino, b""
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            chunk = fh.read(MAX_READ_PER_POLL)
        self._offset += len(chunk)
        data = self._partial + chunk
        *lines, self._partial = data.split(b"\n")
        if len(self._partial) > MAX_PARTIAL:  # línea sin fin: se descarta
            self._partial = b""
            self.malformed += 1
        for raw in lines:
            result = self.parse(raw.decode("utf-8", errors="replace"))
            if result is None:
                self.malformed += 1
                continue
            for ev in [result] if isinstance(result, Event) else result:
                if graph.add(ev):
                    counts[ev.layer] = counts.get(ev.layer, 0) + 1


GRAPH = IdGraph(int(env("MAX_EVENTS", "200000")))
COUNTS: Dict[str, int] = {}
LOG_DIR = env("LOG_DIR", "/logs")
BUS_PARSER = BusLogParser()
# Una fuente por capa, cada una con su propio formato (ver docs/log-formats.md).
TAILERS: List[Tailer] = [
    Tailer(os.path.join(LOG_DIR, "gateway", "gateway.log"), parse_gateway_line, "access-log-kv"),
    Tailer(os.path.join(LOG_DIR, "bus", "bus.log"), BUS_PARSER, "leveled-text"),
    Tailer(os.path.join(LOG_DIR, "core", "core.log"), parse_core_line, "fixed-width"),
    Tailer(os.path.join(LOG_DIR, "modern", "modern.log"), parse_span_line, "otel-span-json"),
]


def _poll_forever() -> None:
    while True:
        for tailer in TAILERS:
            tailer.poll(GRAPH, COUNTS)
        time.sleep(0.5)


class CorrelatorHandler(BaseHandler):
    def route(self, method: str, path: str) -> None:
        if method != "GET":
            raise HttpError(405, "method_not_allowed")
        auth = self.headers.get("Authorization", "")
        if not token_matches(auth[7:] if auth.startswith("Bearer ") else None, env("CORRELATOR_TOKEN")):
            raise HttpError(401, "unauthorized")

        if path.startswith("/trace/"):
            any_id = path[len("/trace/") :]
            if not valid_id(any_id):
                raise HttpError(400, "invalid_id")
            trace = GRAPH.trace(any_id)
            if trace is None:
                raise HttpError(404, "trace_not_found")
            self.send_json(200, trace.to_dict())
        elif path == "/stats":
            self.send_json(200, self._stats())
        elif path == "/metrics":
            self._metrics()
        else:
            raise HttpError(404, "not_found")

    @staticmethod
    def _stats() -> dict:
        comp = GRAPH.completeness()
        return {
            "events": dict(COUNTS),
            "malformedLines": sum(t.malformed for t in TAILERS),
            "sources": {os.path.basename(t.path).split(".")[0]: t.fmt for t in TAILERS},
            "busUnlinkedReplies": BUS_PARSER.unlinked,
            "transactions": comp["total"],
            "completeTraces": comp["complete"],
            "completeness": round(comp["complete"] / comp["total"], 4) if comp["total"] else None,
        }

    def _metrics(self) -> None:
        s = self._stats()
        lines = [f'obsproto_events_total{{layer="{k}"}} {v}' for k, v in sorted(s["events"].items())]
        lines += [
            f"obsproto_malformed_lines_total {s['malformedLines']}",
            f"obsproto_bus_unlinked_replies {s['busUnlinkedReplies']}",
            f"obsproto_transactions_total {s['transactions']}",
            f"obsproto_complete_traces_total {s['completeTraces']}",
        ]
        body = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    env("CORRELATOR_TOKEN", required=True)
    threading.Thread(target=_poll_forever, daemon=True).start()
    serve(CorrelatorHandler, int(env("PORT", "9100")), tls=False)
