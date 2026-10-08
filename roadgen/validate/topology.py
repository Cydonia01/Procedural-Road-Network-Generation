"""Structural checks on an .xodr file: unique ids and resolvable links."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Union

from lxml import etree

PathLike = Union[str, Path]


def check_topology(xodr_path: PathLike) -> list[str]:
    return check_topology_tree(etree.parse(str(xodr_path)))


def check_topology_tree(tree: etree._ElementTree) -> list[str]:
    """Return a list of errors; empty means ids are unique and links resolve."""
    root = tree.getroot()
    errors: list[str] = []

    road_ids = [r.get("id") for r in root.findall("road")]
    junction_ids = [j.get("id") for j in root.findall("junction")]
    errors += _duplicates("road", road_ids)
    errors += _duplicates("junction", junction_ids)
    known = {"road": set(road_ids), "junction": set(junction_ids)}
    roads = {r.get("id"): r for r in root.findall("road")}

    for road in root.findall("road"):
        rid = road.get("id")
        junction = road.get("junction", "-1")
        if junction != "-1" and junction not in known["junction"]:
            errors.append(f"road {rid}: belongs to missing junction {junction}")
        for link in road.findall("link/predecessor") + road.findall("link/successor"):
            kind, target = link.get("elementType"), link.get("elementId")
            if kind not in known:
                errors.append(f"road {rid}: {link.tag} has unknown elementType {kind!r}")
            elif target not in known[kind]:
                errors.append(f"road {rid}: {link.tag} references missing {kind} {target}")
            elif kind == "road":
                errors += _check_lane_links(road, link, roads[target])

    for junction in root.findall("junction"):
        jid = junction.get("id")
        conns = junction.findall("connection")
        errors += _duplicates(f"junction {jid}: connection", [c.get("id") for c in conns])
        for c in conns:
            cid = f"junction {jid}: connection {c.get('id')}"
            missing = False
            for attr in ("incomingRoad", "connectingRoad"):
                target = c.get(attr)
                if target is not None and target not in known["road"]:
                    errors.append(f"{cid} {attr} references missing road {target}")
                    missing = True
            if missing:
                continue
            incoming, connecting = roads[c.get("incomingRoad")], roads[c.get("connectingRoad")]
            if connecting.get("junction") != jid:
                errors.append(f"{cid}: connecting road {connecting.get('id')} is not in this junction")
            incoming_end = _contact_toward(incoming, jid)
            if not incoming_end:
                errors.append(f"{cid}: incoming road {incoming.get('id')} does not link to junction {jid}")
            for ll in c.findall("laneLink"):
                lane_from, lane_to = int(ll.get("from")), int(ll.get("to"))
                if incoming_end and lane_from not in _lane_ids(incoming, incoming_end):
                    errors.append(f"{cid}: laneLink from lane {lane_from} missing in road {incoming.get('id')}")
                if lane_to not in _lane_ids(connecting, c.get("contactPoint", "start")):
                    errors.append(f"{cid}: laneLink to lane {lane_to} missing in road {connecting.get('id')}")
    return errors


def _lane_ids(road: etree._Element, contact: str) -> set[int]:
    """Lane ids of the first (contact start) or last (contact end) lane section."""
    sections = road.findall("lanes/laneSection")
    if not sections:
        return set()
    section = sections[0] if contact == "start" else sections[-1]
    return {int(l.get("id")) for l in section.iter("lane") if l.get("id") != "0"}


def _contact_toward(road: etree._Element, junction_id: str) -> str:
    """Which end of road touches the junction ('start'/'end'), or '' if unknown."""
    for tag, contact in (("predecessor", "start"), ("successor", "end")):
        link = road.find(f"link/{tag}")
        if link is not None and link.get("elementType") == "junction" and link.get("elementId") == junction_id:
            return contact
    return ""


def _check_lane_links(road: etree._Element, link: etree._Element, other: etree._Element) -> list[str]:
    """Lane predecessor/successor ids must exist at the linked road's contact end."""
    here = "start" if link.tag == "predecessor" else "end"
    there = link.get("contactPoint", "start")
    sections = road.findall("lanes/laneSection")
    if not sections:
        return []
    section = sections[0] if here == "start" else sections[-1]
    errors = []
    available = _lane_ids(other, there)
    for lane in section.iter("lane"):
        lane_link = lane.find(f"link/{link.tag}")
        if lane_link is not None and int(lane_link.get("id")) not in available:
            errors.append(
                f"road {road.get('id')} lane {lane.get('id')}: {link.tag} lane "
                f"{lane_link.get('id')} missing in road {other.get('id')}"
            )
    return errors


def _duplicates(label: str, ids: list[str]) -> list[str]:
    return [f"duplicate {label} id {i}" for i, n in sorted(Counter(ids).items()) if n > 1]
