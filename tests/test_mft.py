"""Eslabón MFT (tipo GoAnywhere): el archivo plano se ubica por el rango de horas de su nombre."""

import pytest

from obsproto.correlator.filelink import TransferIndex
from obsproto.correlator.graph import IdGraph
from obsproto.correlator.parsers import Event, MftLogParser, MftTransfer
from obsproto.wire import novedad_filename, parse_novedad_filename

NAME = "NOV_CORE01_20261119_072059670-072104512.txt"
CTX = "[MFT] job=NOVEDADES_CORE01 run=RUN-00000000000a session=SSH-00000000000b"


def test_filename_says_node_date_and_time_range():
    assert novedad_filename("CORE01", "20261119072059670", "20261119072104512") == NAME
    node, start, end = parse_novedad_filename(NAME)
    assert node == "CORE01" and round(end - start, 3) == 4.842


@pytest.mark.parametrize(
    "bad", ["NOV_CORE01_20261119_072104512-072059670.txt", "otro.txt", "NOV_core01_20261119_1-2.txt"]
)
def test_filename_rejects_bad_names(bad):
    assert parse_novedad_filename(bad) is None


def test_mft_parser_builds_a_transfer_from_copy_and_move():
    p = MftLogParser()
    assert p(f"2026-11-19T07:21:06.100Z INFO  {CTX} event=ssh.connect host=ftp user=mft") == []
    copy = (
        f"2026-11-19T07:21:06.110Z INFO  {CTX} transfer=MFT-00000000000c event=file.copy "
        f"src=/outbox/{NAME} dst=/inbox/{NAME} bytes=190 sha256=0123456789abcdef"
    )
    assert p(copy) == []
    (t,) = p(
        f"2026-11-19T07:21:06.130Z INFO  {CTX} transfer=MFT-00000000000c event=file.move src=/outbox/{NAME} status=OK"
    )
    assert (t.transfer_id, t.file, t.node, t.session) == ("MFT-00000000000c", NAME, "CORE01", "SSH-00000000000b")
    assert round(t.moved_ts - t.copied_ts, 3) == 0.02


def test_mft_parser_rejects_garbage():
    assert MftLogParser()("[MFT] cualquier cosa") is None


def transfer(tid, start, end, node="CORE01"):
    return MftTransfer(tid, f"NOV_{node}_x.txt", node, start, end, end + 2, end + 2.02, "SSH-00000000000b")


def test_index_finds_the_single_file_whose_range_contains_the_movement():
    idx = TransferIndex()
    idx.add(transfer("MFT-000000000001", 100.0, 104.8))
    idx.add(transfer("MFT-000000000002", 105.0, 109.9))
    assert idx.find(103.0).transfer_id == "MFT-000000000001"
    assert idx.find(105.0).transfer_id == "MFT-000000000002"
    assert idx.find(110.5) is None  # ese lote todavía no se ha movido


def test_overlapping_ranges_are_ambiguous_and_not_linked():
    idx = TransferIndex()
    idx.add(transfer("MFT-000000000001", 100.0, 104.8, "CORE01"))
    idx.add(transfer("MFT-000000000002", 102.0, 106.0, "CORE02"))  # otro nodo, rango solapado
    assert idx.find(103.0) is None and idx.ambiguous == 1


def chain(t=103.0, core_status="00"):
    s = "00000000000a"
    return [
        Event("gateway", f"GW-{s}", f"CLI-{s}", t + 0.02, 22, "200"),
        Event("bus", f"BUS-{s}", f"GW-{s}", t + 0.01, 18, "00"),
        Event("core", f"CORE-{s}", f"BUS-{s}", t, 16, core_status),
        Event("modern", f"MOD-{s}", f"CORE-{s}", t + 6, 0, "OK"),
    ]


def test_trace_includes_mft_and_requires_it_when_enabled():
    idx = TransferIndex()
    g = IdGraph(layers=("gateway", "bus", "core", "mft", "modern"), side=idx.side_event)
    for ev in chain():
        g.add(ev)
    assert g.trace("CLI-00000000000a").missing_layers == ["mft"]  # el MFT aún no movió el archivo
    idx.add(transfer("MFT-000000000001", 100.0, 104.8))
    tr = g.trace("CLI-00000000000a")
    assert tr.complete and [e.layer for e in tr.events] == ["gateway", "bus", "core", "mft", "modern"]
    span = next(s for s in tr.to_dict()["spans"] if s["layer"] == "mft")
    assert (
        span["id"] == "MFT-000000000001"
        and span["parentId"] == "CORE-00000000000a"
        and span["file"] == "NOV_CORE01_x.txt"
    )


def test_core_failure_expects_neither_mft_nor_modern():
    g = IdGraph(layers=("gateway", "bus", "core", "mft", "modern"), side=TransferIndex().side_event)
    for ev in chain(core_status="91")[:3]:
        g.add(ev)
    assert g.trace("CLI-00000000000a").complete
