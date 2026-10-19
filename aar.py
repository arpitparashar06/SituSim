"""
aar.py - the After-Action Review for SituSim

Everything in here is rebuilt from the event log alone, so the review shows
exactly what was recorded and nothing else.

The rubric scores each decision against what that trainee could see at the
moment they decided, not against the hidden truth:
    1. Fit with available information   (did the choice match what you could see?)
    2. Handling of conflicting reports  (did you verify or address a visible conflict?)
    3. Awareness of old information     (did you rely on stale reports without noticing?)
    4. Timeliness                       (did you decide inside the decision window?)
    5. Rationale                        (does your reasoning point to evidence or the intent?)
Each is scored 0, 1 or 2. A dimension that does not apply is left out (N/A).

The page layout follows the US Army's AAR guide (TC 25-20):
what was supposed to happen, what happened, why it happened, how to improve.
"""

import hashlib
import json
import math
import os
import re
import urllib.request

import shadow

ROLE_ORDER = ["GTL", "ISR", "EW", "COMMS"]

# Words are matched as whole words, so "check" does not match "checkpoint".
CONFLICT_WORDS = ["conflict", "conflicts", "conflicting", "contradict", "contradicts", "contradictory",
                  "disagree", "disagrees", "unconfirmed", "verify", "confirm", "spoof", "spoofed", "doubt",
                  "check", "inconsistent", "mismatch", "not sure", "unclear", "suspect"]
AGE_WORDS = ["old", "stale", "outdated", "dated", "age", "earlier", "minutes ago", "min ago", "no longer",
             "out of date", "may have changed", "since then"]
INTENT_WORDS = ["intent", "mission", "commander"]

CHANNEL_NAMES = {"net": "Radio", "relay": "Relay", "runner": "Runner", "flare": "Flare"}

DIMENSION_NAMES = {
    "fit": "Fit with available information",
    "conflict": "Handling of conflicting reports",
    "age": "Awareness of old information",
    "time": "Timeliness",
    "rationale": "Rationale",
}


def fmt(t):
    if t is None:
        return "-"
    t = int(t)
    return "T+" + str(t // 60).zfill(2) + ":" + str(t % 60).zfill(2)


def fmt_span(seconds):
    """65 -> '1 min 5 s'"""
    seconds = int(seconds)
    if seconds < 60:
        return str(seconds) + " s"
    minutes = seconds // 60
    rest = seconds % 60
    if rest == 0:
        return str(minutes) + " min"
    return str(minutes) + " min " + str(rest) + " s"


def plural(n, word):
    if n == 1:
        return "1 " + word
    return str(n) + " " + word + "s"


def base_id(report_id):
    """'R2-B' -> 'R2' (a contradicting variant belongs to the report it contradicts)"""
    return str(report_id).split("-")[0]


def ids_in_text(text):
    found = re.findall(r"\b([RrMm]\d+(?:-[A-Za-z])?)\b", text or "")
    result = []
    for item in found:
        result.append(item.upper())
    return result


def contains_any(text, words):
    for word in words:
        if re.search(r"\b" + re.escape(word) + r"\b", text):
            return True
    return False


# ============================================================
# Scoring one decision
# ============================================================

def rule_matches(when, visible_ids, conflict_pairs):
    for key in when:
        wanted = when[key]
        if key == "visible_any":
            hit = False
            for rid in wanted:
                if rid in visible_ids:
                    hit = True
            if not hit:
                return False
        if key == "visible_all":
            for rid in wanted:
                if rid not in visible_ids:
                    return False
        if key == "not_visible":
            for rid in wanted:
                if rid in visible_ids:
                    return False
        if key == "conflict_visible":
            if (len(conflict_pairs) > 0) != bool(wanted):
                return False
    return True


def score_decision(decision, dp, reports, config):
    items = []
    if dp is None:
        dp = {}
    choice = decision.get("choice")
    rationale = decision.get("rationale", "") or ""
    text = rationale.lower()
    visible = decision.get("visible", [])
    visible_ids = decision.get("visible_ids", [])
    conflict_pairs = decision.get("conflict_pairs", [])
    window = int(decision.get("window", 90))
    latency = int(decision.get("latency", 0))

    labels = {}
    for option in dp.get("options", []):
        labels[option["id"]] = option["label"]

    # "ticked": reports the trainee ticked as the basis for the decision.
    # "cited": ticked reports plus any report named in the rationale (named can
    # also mean "I'm discounting this one", so only ticked ones count as reliance).
    ticked = []
    for rid in decision.get("cited", []):
        if rid not in ticked:
            ticked.append(rid)
    cited = list(ticked)
    for rid in ids_in_text(rationale):
        if rid in visible_ids and rid not in cited:
            cited.append(rid)

    if choice is None:
        items.append({"key": "fit", "score": None, "why": "No decision was made."})
        items.append({"key": "conflict", "score": None, "why": "No decision was made."})
        items.append({"key": "age", "score": None, "why": "No decision was made."})
        items.append({"key": "time", "score": 0, "why": "No decision before the decision point closed."})
        items.append({"key": "rationale", "score": None, "why": "No decision was made."})
        return finish_score(items)

    choice_label = labels.get(choice, choice)
    relevant = dp.get("relevant_reports", [])

    # ---- 1. Fit with available information ----
    rules = dp.get("rules", [])
    if len(rules) == 0:
        items.append({"key": "fit", "score": None,
                      "why": "This decision point has no answer key. Discuss it in the review."})
    else:
        matched = None
        for rule in rules:
            if rule_matches(rule.get("when", {}), visible_ids, conflict_pairs):
                matched = rule
                break
        acceptable = []
        if matched is not None:
            acceptable = matched.get("acceptable", [])
        best = dp.get("best_given_truth", [])

        seen_relevant = []
        missing_relevant = []
        for rid in relevant:
            if rid in visible_ids:
                seen_relevant.append(rid)
            else:
                missing_relevant.append(rid)
        context = ""
        if len(seen_relevant) > 0:
            context = " You had " + ", ".join(seen_relevant) + "."
        if len(missing_relevant) > 0:
            context = context + " " + ", ".join(missing_relevant) + " had not reached you."

        acceptable_names = []
        for option_id in acceptable:
            acceptable_names.append('"' + labels.get(option_id, option_id) + '"')

        if choice in acceptable:
            items.append({"key": "fit", "score": 2, "why": '"' + choice_label + '" fits what you could see.' + context})
        elif choice in best:
            items.append({"key": "fit", "score": 1,
                          "why": "Right in hindsight, but what you could see pointed to " + " or ".join(acceptable_names) + "." + context})
        else:
            items.append({"key": "fit", "score": 0,
                          "why": "What you could see pointed to " + " or ".join(acceptable_names) + "." + context})

    # ---- 2. Handling of conflicting reports ----
    relevant_pairs = []
    for pair in conflict_pairs:
        if len(relevant) == 0:
            relevant_pairs.append(pair)
        else:
            for rid in pair:
                if base_id(rid) in relevant or rid in relevant:
                    if pair not in relevant_pairs:
                        relevant_pairs.append(pair)

    if len(relevant_pairs) == 0:
        items.append({"key": "conflict", "score": None, "why": "No conflicting reports were in front of you."})
    else:
        pair = relevant_pairs[0]
        pair_text = pair[0] + " and " + pair[1]
        both_cited = False
        one_cited = False
        for p in relevant_pairs:
            if p[0] in cited and p[1] in cited:
                both_cited = True
                pair_text = p[0] + " and " + p[1]
            if p[0] in cited or p[1] in cited:
                one_cited = True
        conflict_pair_text = pair_text
        if choice in dp.get("verify_options", []):
            items.append({"key": "conflict", "score": 2,
                          "why": 'You chose "' + choice_label + '" while ' + pair_text + " disagreed."})
        elif both_cited:
            items.append({"key": "conflict", "score": 2,
                          "why": "You weighed " + pair_text + " against each other in your rationale."})
        elif contains_any(text, CONFLICT_WORDS):
            items.append({"key": "conflict", "score": 1,
                          "why": pair_text + " disagreed. You noted doubt, but didn't say which report you trusted or why."})
        elif one_cited:
            items.append({"key": "conflict", "score": 0,
                          "why": pair_text + " disagreed. You relied on one of them without addressing the other."})
        else:
            items.append({"key": "conflict", "score": 0,
                          "why": pair_text + " disagreed and your decision did not address it."})
        items[-1]["pair"] = conflict_pair_text

    # ---- 3. Awareness of old information ----
    stale_after = int(config.get("stale_after", 120))
    relied_on = ticked
    if len(relied_on) == 0:
        relied_on = cited
    cited_entries = []
    for v in visible:
        if v["report_id"] in relied_on:
            cited_entries.append(v)
    if len(cited_entries) == 0:
        items.append({"key": "age", "score": None,
                      "why": "You didn't point to any report, so reliance on old information can't be checked."})
    else:
        stale = []
        for v in cited_entries:
            if v["age"] > stale_after:
                stale.append(v)
        if len(stale) == 0:
            items.append({"key": "age", "score": 2,
                          "why": "Every report you relied on was less than " + fmt_span(stale_after) + " old."})
        else:
            s = stale[0]
            newer = None
            for v in visible:
                if v["report_id"] != s["report_id"] and v.get("grid", "-") != "-" and v.get("grid") == s.get("grid"):
                    if v["source_time"] > s["source_time"]:
                        newer = v
            if contains_any(text, AGE_WORDS):
                items.append({"key": "age", "score": 2,
                              "why": "You relied on " + s["report_id"] + " (" + fmt_span(s["age"]) + " old) and noted its age."})
            elif newer is not None:
                items.append({"key": "age", "score": 0,
                              "why": "You relied on " + s["report_id"] + " (" + fmt_span(s["age"]) + " old) although newer "
                                     + newer["report_id"] + " about the same grid was in front of you."})
            else:
                items.append({"key": "age", "score": 1,
                              "why": s["report_id"] + " was " + fmt_span(s["age"]) + " old and nothing newer had come in. Note its age when you rely on old information."})

    # ---- 4. Timeliness ----
    if latency <= window:
        items.append({"key": "time", "score": 2,
                      "why": "Decided in " + fmt_span(latency) + ", inside the " + fmt_span(window) + " window."})
    else:
        items.append({"key": "time", "score": 1,
                      "why": "Decided in " + fmt_span(latency) + ", " + fmt_span(latency - window) + " after the window closed."})

    # ---- 5. Rationale ----
    if len(rationale.strip()) < 8:
        items.append({"key": "rationale", "score": 0, "why": "No rationale was recorded."})
    elif len(cited) > 0:
        items.append({"key": "rationale", "score": 2, "why": "Your rationale points to evidence: " + ", ".join(cited) + "."})
    elif contains_any(text, INTENT_WORDS):
        items.append({"key": "rationale", "score": 2, "why": "Your rationale ties the choice back to the commander's intent."})
    else:
        items.append({"key": "rationale", "score": 1,
                      "why": "Your rationale doesn't point to a report or to the commander's intent."})

    return finish_score(items)


def finish_score(items):
    total = 0
    maximum = 0
    for item in items:
        item["name"] = DIMENSION_NAMES[item["key"]]
        if item["score"] is not None:
            total = total + item["score"]
            maximum = maximum + 2
    percent = None
    if maximum > 0:
        percent = round(100 * total / maximum)
    return {"items": items, "total": total, "max": maximum, "percent": percent}


def fingerprint(delivery_list):
    """A short code that is identical for two runs with the same seed and the same actions."""
    items = []
    for d in delivery_list:
        if d.get("kind") == "report" and d.get("scripted"):
            items.append([d["report_id"], d["role"], d["delay"], d["status"] == "DROPPED"])
    items.sort()
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:10]


# ============================================================
# Rebuilding the whole exercise from the event log
# ============================================================

def build_aar(sid, events):
    scenario = {}
    config = {}
    start_event = None
    end_time = None
    reports = {}
    deliveries = {}
    delivery_order = []
    dps = {}
    decisions = []
    link_changes = []
    sensor_changes = []
    probe_answers = []
    probe_events = []
    forwards = []
    chats = []
    acks = []
    config_changes = []
    names = {"GTL": [], "ISR": [], "EW": [], "COMMS": []}
    last_time = 0

    for e in events:
        kind = e["kind"]
        data = e["data"]
        if e["sim_time"] is not None and e["sim_time"] > last_time:
            last_time = e["sim_time"]

        if kind == "SCENARIO_LOADED":
            scenario = data["scenario"]
            config = data["config"]
        elif kind == "SESSION_START":
            config = data["config"]
            start_event = e
        elif kind == "CONFIG_CHANGE":
            if start_event is None:
                config = data["config"]
            else:
                meaningful = False
                for field in data["changed"]:
                    if field != "speed":
                        meaningful = True
                if meaningful:
                    config_changes.append(e)
        elif kind == "REPORT_CREATED":
            report = dict(data)
            report["event_id"] = e["id"]
            reports[report["report_id"]] = report
        elif kind == "CHAT":
            report = dict(data["report"])
            report["event_id"] = e["id"]
            reports[report["report_id"]] = report
            chats.append(e)
        elif kind == "SCHEDULED":
            d = dict(data)
            d["event_id"] = e["id"]
            d["was_held"] = False
            d["held_at"] = None
            deliveries[d["delivery_id"]] = d
            delivery_order.append(d["delivery_id"])
        elif kind == "HELD":
            d = deliveries.get(data["delivery_id"])
            if d is not None:
                d["was_held"] = True
                d["held_at"] = e["sim_time"]
                d["held_event"] = e["id"]
        elif kind == "DELIVERED":
            d = deliveries.get(data["delivery_id"])
            if d is not None:
                d["status"] = "DELIVERED"
                d["delivered_at"] = e["sim_time"]
                d["delivered_event"] = e["id"]
                if d["kind"] == "ack" and d.get("ack_for") in deliveries:
                    deliveries[d["ack_for"]]["ack_received_at"] = e["sim_time"]
        elif kind == "ACK":
            acks.append(e)
            d = deliveries.get(data["delivery_id"])
            if d is not None:
                d["acked_at"] = e["sim_time"]
        elif kind == "DP_OPEN":
            dp = dict(data)
            dp["event_id"] = e["id"]
            dps[dp["dp_id"]] = dp
        elif kind == "DECISION":
            decision = dict(data)
            decision["event_id"] = e["id"]
            decisions.append(decision)
        elif kind == "LINK_CHANGE":
            link_changes.append({"t": e["sim_time"], "role": data["role"], "state": data["state"],
                                 "old": data["old"], "by": data["by"], "event_id": e["id"], "note": data.get("note", "")})
        elif kind == "SENSOR_CHANGE":
            sensor_changes.append({"t": e["sim_time"], "sensor": data["sensor"], "state": data["state"],
                                   "by": data["by"], "event_id": e["id"]})
        elif kind == "PROBE_ANSWER":
            probe_answers.append({"t": e["sim_time"], "role": e["role"], "probe_id": data["probe_id"],
                                  "results": data["results"], "event_id": e["id"]})
        elif kind == "PROBE_START" or kind == "PROBE_END":
            probe_events.append(e)
        elif kind == "FORWARD":
            forwards.append(e)
        elif kind == "JOIN":
            if data["name"] not in names.get(e["role"], []):
                names[e["role"]].append(data["name"])
        elif kind == "SESSION_END":
            end_time = e["sim_time"]

    if scenario == {}:
        scenario = {"name": "Unknown scenario", "roles": {}, "map": {}, "mission": "", "intent": "",
                    "disclaimer": "", "area": ""}

    def title(role):
        if role in scenario.get("roles", {}):
            return scenario["roles"][role]["title"]
        return str(role)

    started = start_event is not None
    if end_time is None:
        end_time = last_time
    span = max(end_time, int(config.get("duration", 900)) if not started else end_time, 1)
    delayed_after = int(config.get("delayed_label_after", 20))
    share_window = int(config.get("share_window", 90))

    def link_state_at(role, t):
        state = "UP"
        for change in link_changes:
            if change["role"] == role and change["t"] <= t:
                state = change["state"]
        return state

    # ---------- re-score every decision from the log ----------
    for decision in decisions:
        decision["score"] = score_decision(decision, dps.get(decision["dp_id"]), reports, config)

    sensor_reports = []
    for rid in reports:
        if reports[rid].get("kind") == "sensor":
            sensor_reports.append(reports[rid])
    sensor_reports.sort(key=lambda r: (r.get("source_time", 0), r["report_id"]))

    # ---------- first time each role received each report ----------
    first_seen = {}   # (report_id, role) -> delivery
    for did in delivery_order:
        d = deliveries[did]
        if d["kind"] == "ack" or d["status"] != "DELIVERED":
            continue
        key = (d["report_id"], d["role"])
        if key not in first_seen or d["delivered_at"] < first_seen[key]["delivered_at"]:
            first_seen[key] = d

    # ---------- communications picture ----------
    comms = {"copies": 0, "delivered": 0, "dropped": 0, "held": 0, "in_transit": 0, "delayed": 0,
             "synthetic": 0, "relayed": 0, "transit_total": 0}
    first_drop_event = None
    first_hold_event = None
    for did in delivery_order:
        d = deliveries[did]
        if d["kind"] == "ack":
            continue
        comms["copies"] = comms["copies"] + 1
        if d["status"] == "DELIVERED":
            comms["delivered"] = comms["delivered"] + 1
            source = reports.get(d["report_id"], {}).get("source_time", d["scheduled_at"])
            comms["transit_total"] = comms["transit_total"] + (d["delivered_at"] - d["scheduled_at"])
            if d["delivered_at"] - source > delayed_after:
                comms["delayed"] = comms["delayed"] + 1
        elif d["status"] == "DROPPED":
            comms["dropped"] = comms["dropped"] + 1
            if first_drop_event is None:
                first_drop_event = d["event_id"]
        else:
            comms["in_transit"] = comms["in_transit"] + 1
        if d["was_held"]:
            comms["held"] = comms["held"] + 1
            if first_hold_event is None:
                first_hold_event = d.get("held_event", d["event_id"])
        if d["via"] == "relay":
            comms["relayed"] = comms["relayed"] + 1
    for r in sensor_reports:
        if r.get("synthetic"):
            comms["synthetic"] = comms["synthetic"] + 1
    comms["avg_transit"] = 0
    if comms["delivered"] > 0:
        comms["avg_transit"] = round(comms["transit_total"] / comms["delivered"])

    # ---------- delivery matrix: which report reached whom, and when ----------
    matrix = []
    for r in sensor_reports:
        cells = []
        for role in ROLE_ORDER:
            mine = []
            for did in delivery_order:
                d = deliveries[did]
                if d["report_id"] == r["report_id"] and d["role"] == role and d["kind"] != "ack":
                    mine.append(d)
            if len(mine) == 0:
                cells.append({"cls": "none", "text": "", "title": "Not sent to " + title(role)})
                continue
            first = first_seen.get((r["report_id"], role))
            if first is not None:
                transit = first["delivered_at"] - r.get("source_time", 0)
                text = fmt(first["delivered_at"])
                detail = "+" + fmt_span(transit)
                cls = "ok"
                if transit > delayed_after:
                    cls = "late"
                if first["was_held"]:
                    detail = detail + ", held"
                if first["from_role"] is not None:
                    detail = detail + ", via " + first["from_role"]
                cells.append({"cls": cls, "text": text, "detail": detail, "title": title(role) + " received it at " + text})
            else:
                held = False
                for d in mine:
                    if d["was_held"]:
                        held = True
                if held:
                    cells.append({"cls": "held", "text": "Held", "detail": "never released", "title": "Held by a link outage"})
                else:
                    pending = False
                    for d in mine:
                        if d["status"] == "PENDING":
                            pending = True
                    if pending:
                        cells.append({"cls": "held", "text": "In transit", "detail": "at end", "title": "Still in transit"})
                    else:
                        cells.append({"cls": "lost", "text": "Lost", "detail": "", "title": "Lost in transit"})
        matrix.append({"report_id": r["report_id"], "sender": r.get("sender", ""), "domain": r.get("domain", ""),
                       "text": r.get("text", ""), "truth": r.get("truth"), "synthetic": r.get("synthetic", False),
                       "critical": r.get("critical", False), "conflicts_with": r.get("conflicts_with"),
                       "source": fmt(r.get("source_time", 0)), "cells": cells, "event_id": r["event_id"],
                       "created_by": r.get("created_by", "script")})

    # ---------- critical reports: did they reach the people who needed them? ----------
    propagation = []
    findings = []
    for r in sensor_reports:
        if not r.get("critical"):
            continue
        for role in r.get("needed_by", []):
            first = first_seen.get((r["report_id"], role))
            row = {"report_id": r["report_id"], "text": r.get("text", ""), "role": role, "role_title": title(role),
                   "observed": fmt(r.get("source_time", 0))}
            if first is not None:
                row["reached"] = fmt(first["delivered_at"])
                row["took"] = fmt_span(first["delivered_at"] - r.get("source_time", 0))
                if first["from_role"] is None:
                    row["route"] = "Direct"
                elif first["via"] == "relay":
                    row["route"] = "Relayed for " + title(first["from_role"])
                elif first["via"] == "runner":
                    row["route"] = "Runner from " + title(first["from_role"])
                else:
                    row["route"] = "Passed on by " + title(first["from_role"])
                row["ok"] = True
                row["event_id"] = first.get("delivered_event", first["event_id"])
            else:
                row["reached"] = "Never"
                row["took"] = ""
                row["ok"] = False
                passed_on = []
                for e in forwards:
                    if e["data"]["report_id"] == r["report_id"] and role in e["data"]["to_roles"]:
                        passed_on.append(e)
                sent_to = []
                for rr in r.get("to_roles", []):
                    sent_to.append(title(rr))
                if len(passed_on) > 0:
                    row["route"] = "Passed on, but the copy didn't arrive"
                    findings.append({
                        "text": r["report_id"] + " (" + r.get("text", "") + ") never reached the " + title(role)
                                + ". " + title(passed_on[0]["role"]) + " passed it on, but that copy was lost or held.",
                        "events": [r["event_id"], passed_on[0]["id"]], "kind": "gap"})
                else:
                    arrived_anywhere = False
                    for did in delivery_order:
                        d = deliveries[did]
                        if d["report_id"] == r["report_id"] and d["status"] == "DELIVERED" and d["kind"] == "report":
                            arrived_anywhere = True
                    if arrived_anywhere:
                        row["route"] = "Nobody passed it on"
                        ending = " and nobody passed it on."
                    else:
                        row["route"] = "Lost on the way"
                        ending = ", and it was lost on the way, so nobody could pass it on."
                    findings.append({
                        "text": r["report_id"] + " (" + r.get("text", "") + ") never reached the " + title(role)
                                + ". It was sent only to the " + " and the ".join(sent_to) + ending,
                        "events": [r["event_id"]], "kind": "gap"})
                row["event_id"] = r["event_id"]
            propagation.append(row)

    # ---------- link outages ----------
    outages = []
    for role in ROLE_ORDER:
        current = None
        for change in link_changes:
            if change["role"] != role:
                continue
            if current is not None:
                current["end"] = change["t"]
                current["end_event"] = change["event_id"]
                outages.append(current)
                current = None
            if change["state"] != "UP":
                current = {"role": role, "state": change["state"], "start": change["t"], "end": None,
                           "start_event": change["event_id"], "end_event": None, "by": change["by"]}
        if current is not None:
            current["end"] = end_time
            outages.append(current)

    for o in outages:
        held_count = 0
        lost_count = 0
        relays = 0
        for did in delivery_order:
            d = deliveries[did]
            if d["kind"] == "ack":
                continue
            if d["role"] == o["role"] and o["start"] <= d["scheduled_at"] <= (o["end"] if o["end"] is not None else end_time):
                if d["status"] == "DROPPED":
                    lost_count = lost_count + 1
                if d["via"] in ["relay", "runner"]:
                    relays = relays + 1
            if (d["role"] == o["role"] or d["from_role"] == o["role"]) and d["was_held"] and d["held_at"] is not None:
                if o["start"] <= d["held_at"] <= (o["end"] if o["end"] is not None else end_time):
                    held_count = held_count + 1
        o["held"] = held_count
        o["lost"] = lost_count
        o["relays"] = relays
        events_cited = [o["start_event"]]
        if o["end_event"] is not None:
            events_cited.append(o["end_event"])
        injected = ""
        if o["by"] == "instructor":
            injected = " (instructor inject)"
        if o["state"] == "DOWN":
            text = title(o["role"]) + "'s link was down from " + fmt(o["start"]) + " to " + fmt(o["end"]) + injected + ". "
            if held_count == 0:
                text = text + "No messages to or from it were waiting"
            elif held_count == 1:
                text = text + "1 message to or from it was held until the link came back"
            else:
                text = text + str(held_count) + " messages to or from it were held until the link came back"
            if relays > 0:
                text = text + ", and " + str(relays) + " reached it by relay or runner."
            else:
                text = text + ", and nobody switched to the relay or a runner."
            findings.append({"text": text, "events": events_cited, "kind": "outage"})
        else:
            hidden = ""
            if not config.get("show_degraded_to_trainee", False):
                hidden = " The trainee was not told."
            lost_text = "none were lost"
            if lost_count == 1:
                lost_text = "1 was lost"
            elif lost_count > 1:
                lost_text = str(lost_count) + " were lost"
            text = (title(o["role"]) + "'s link was degraded from " + fmt(o["start"]) + " to " + fmt(o["end"]) + injected
                    + ": messages to it took twice as long and " + lost_text + "." + hidden)
            findings.append({"text": text, "events": events_cited, "kind": "outage"})

    # ---------- decisions: findings about acting on false or conflicting information ----------
    for decision in decisions:
        if decision["choice"] is None:
            continue
        for rid in decision.get("cited", []):
            report = reports.get(rid)
            if report is not None and report.get("truth") is False and report.get("kind") == "sensor":
                findings.append({
                    "text": title(decision["role"]) + " relied on " + rid + " at " + decision["dp_id"]
                            + ". That report was false" + (" (generated to contradict " + report.get("conflicts_with", "") + ")" if report.get("synthetic") else "") + ".",
                    "events": [decision["event_id"], report["event_id"]], "kind": "false"})
                break
        for item in decision["score"]["items"]:
            if item["key"] == "conflict" and item["score"] == 0:
                findings.append({"text": "At " + decision["dp_id"] + ", " + title(decision["role"]) + " chose \""
                                         + decision["choice_label"] + "\" while " + item.get("pair", "two reports")
                                         + " disagreed, without addressing the conflict.",
                                 "events": [decision["event_id"]], "kind": "conflict"})

    # ---------- per-role picture ----------
    roles_out = []
    for role in ROLE_ORDER:
        received = 0
        delayed = 0
        dropped = 0
        held = 0
        transit_total = 0
        team_messages = 0
        acked = 0
        for did in delivery_order:
            d = deliveries[did]
            if d["role"] != role or d["kind"] == "ack":
                continue
            if d["status"] == "DELIVERED":
                received = received + 1
                source = reports.get(d["report_id"], {}).get("source_time", d["scheduled_at"])
                transit_total = transit_total + (d["delivered_at"] - d["scheduled_at"])
                if d["delivered_at"] - source > delayed_after:
                    delayed = delayed + 1
                if d["from_role"] is not None:
                    team_messages = team_messages + 1
                    if d.get("acked_at") is not None:
                        acked = acked + 1
            if d["status"] == "DROPPED":
                dropped = dropped + 1
            if d["was_held"]:
                held = held + 1

        my_decisions = []
        percents = []
        for decision in decisions:
            if decision["role"] == role:
                my_decisions.append(decision)
                if decision["score"]["percent"] is not None and decision["choice"] is not None:
                    percents.append(decision["score"]["percent"])
                elif decision["choice"] is None:
                    percents.append(0)
        average = None
        if len(percents) > 0:
            average = round(sum(percents) / len(percents))

        # Sharing: critical reports this role had first, that someone else needed.
        opportunities = []
        for r in sensor_reports:
            if not r.get("critical"):
                continue
            mine = first_seen.get((r["report_id"], role))
            if mine is None:
                continue
            needers = []
            for n in r.get("needed_by", []):
                if n == role:
                    continue
                theirs = first_seen.get((r["report_id"], n))
                if theirs is None or theirs["delivered_at"] > mine["delivered_at"]:
                    needers.append(n)
            if len(needers) == 0:
                continue
            shared_at = None
            share_event = None
            for e in forwards:
                if e["role"] == role and e["data"]["report_id"] == r["report_id"] and e["sim_time"] >= mine["delivered_at"]:
                    ok_target = False
                    for target in e["data"]["to_roles"]:
                        if target in needers or target == "COMMS":
                            ok_target = True
                    if ok_target and shared_at is None:
                        shared_at = e["sim_time"]
                        share_event = e["id"]
            opportunities.append({"report_id": r["report_id"], "received_at": mine["delivered_at"],
                                  "needers": needers, "shared_at": shared_at, "share_event": share_event,
                                  "in_time": shared_at is not None and shared_at - mine["delivered_at"] <= share_window,
                                  "event_id": mine.get("delivered_event", mine["event_id"])})
        shared_count = 0
        for o in opportunities:
            if o["in_time"]:
                shared_count = shared_count + 1

        my_probes = []
        for p in probe_answers:
            if p["role"] == role:
                my_probes.append(p)
        probe_right = 0
        probe_total = 0
        attention_gaps = []
        info_gaps = []
        for p in my_probes:
            for res in p["results"]:
                probe_total = probe_total + 1
                if res["correct"]:
                    probe_right = probe_right + 1
                elif res["evidence_visible"]:
                    attention_gaps.append({"probe": p["probe_id"], "text": res["text"], "event_id": p["event_id"]})
                else:
                    info_gaps.append({"probe": p["probe_id"], "text": res["text"], "event_id": p["event_id"]})

        # Sustain and improve, written from the rubric and the log.
        sustain = []
        improve = []
        for decision in my_decisions:
            for item in decision["score"]["items"]:
                where = "At " + decision["dp_id"] + ": "
                if item["key"] == "conflict" and item["score"] == 2:
                    sustain.append(where + item["why"])
                if item["key"] == "conflict" and item["score"] is not None and item["score"] < 2:
                    improve.append(where + item["why"] + " Verify, or say how you resolved it, before acting.")
                if item["key"] == "fit" and item["score"] == 0:
                    improve.append(where + item["why"])
                if item["key"] == "age" and item["score"] is not None and item["score"] < 2:
                    improve.append(where + item["why"])
                if item["key"] == "time" and item["score"] == 0:
                    improve.append(where + "No decision was made. A timely decision on partial information usually beats no decision.")
                if item["key"] == "time" and item["score"] == 1:
                    improve.append(where + item["why"])
                if item["key"] == "rationale" and item["score"] is not None and item["score"] < 2:
                    improve.append(where + item["why"])
        good_rationales = 0
        for decision in my_decisions:
            for item in decision["score"]["items"]:
                if item["key"] == "rationale" and item["score"] == 2:
                    good_rationales = good_rationales + 1
        if good_rationales > 0 and good_rationales == len(my_decisions):
            sustain.append("Every rationale pointed to evidence or to the intent.")
        on_time = 0
        for decision in my_decisions:
            for item in decision["score"]["items"]:
                if item["key"] == "time" and item["score"] == 2:
                    on_time = on_time + 1
        if len(my_decisions) > 0 and on_time == len(my_decisions):
            sustain.append("Every decision was made inside its window.")
        for o in opportunities:
            if o["in_time"]:
                took = o["shared_at"] - o["received_at"]
                if took < 5:
                    sustain.append("You passed " + o["report_id"] + " on as soon as it arrived.")
                else:
                    sustain.append("You passed " + o["report_id"] + " on within " + fmt_span(took) + " of receiving it.")
            elif o["shared_at"] is not None:
                improve.append("You passed " + o["report_id"] + " on, but only after " + fmt_span(o["shared_at"] - o["received_at"]) + ".")
            else:
                needer_names = []
                for n in o["needers"]:
                    needer_names.append(title(n))
                improve.append("You had " + o["report_id"] + " from " + fmt(o["received_at"]) + " but never passed it on. "
                               + " and ".join(needer_names) + " needed it.")
        if team_messages > 0:
            if acked == team_messages:
                sustain.append("You acknowledged every team message you received.")
            elif acked < team_messages:
                improve.append("You acknowledged " + str(acked) + " of " + str(team_messages)
                               + " team messages. Without an ACK the sender can't tell their message got through.")
        for gap in attention_gaps:
            improve.append("In freeze " + gap["probe"] + " you missed \"" + gap["text"] + "\" although the answer was in your feed.")
        if probe_total > 0 and probe_right == probe_total:
            sustain.append("You answered every freeze-probe question correctly.")

        roles_out.append({
            "role": role, "title": title(role), "names": ", ".join(names.get(role, [])) or "Not played",
            "played": len(names.get(role, [])) > 0,
            "received": received, "delayed": delayed, "dropped": dropped, "held": held,
            "avg_transit": round(transit_total / received) if received > 0 else 0,
            "decisions": len(my_decisions), "average": average,
            "opportunities": len(opportunities), "shared": shared_count,
            "team_messages": team_messages, "acked": acked,
            "probe_right": probe_right, "probe_total": probe_total,
            "attention_gaps": len(attention_gaps), "info_gaps": len(info_gaps),
            "sustain": sustain[:6], "improve": improve[:6],
        })

    # ---------- probe findings (information gap versus attention gap) ----------
    for p in probe_answers:
        wrong_with = 0
        wrong_without = 0
        for res in p["results"]:
            if not res["correct"]:
                if res["evidence_visible"]:
                    wrong_with = wrong_with + 1
                else:
                    wrong_without = wrong_without + 1
        if wrong_without > 0:
            findings.append({"text": "In freeze " + p["probe_id"] + ", " + title(p["role"]) + " missed " + plural(wrong_without, "question")
                                     + " with no supporting report in their feed. That is an information gap, not an attention gap.",
                             "events": [p["event_id"]], "kind": "probe"})
        if wrong_with > 0:
            findings.append({"text": "In freeze " + p["probe_id"] + ", " + title(p["role"]) + " missed " + plural(wrong_with, "question")
                                     + " although the answer was in their feed.",
                             "events": [p["event_id"]], "kind": "probe"})

    # ---------- team picture ----------
    team_decisions = []
    for dp_id in dps:
        dp = dps[dp_id]
        if not dp.get("team"):
            continue
        lead = None
        others = []
        for decision in decisions:
            if decision["dp_id"] == dp_id:
                if decision["role"] == "GTL":
                    lead = decision
                else:
                    others.append(decision)
        agree = 0
        answered = 0
        for o in others:
            if o["choice"] is not None:
                answered = answered + 1
                if lead is not None and o["choice"] == lead["choice"]:
                    agree = agree + 1
        team_decisions.append({"dp_id": dp_id, "prompt": dp["prompt"],
                               "lead_choice": lead["choice_label"] if lead else "No decision",
                               "agree": agree, "answered": answered,
                               "others": [{"title": title(o["role"]), "choice": o["choice_label"]} for o in others]})

    probe_team = []
    probe_ids = []
    for p in probe_answers:
        if p["probe_id"] not in probe_ids:
            probe_ids.append(p["probe_id"])
    for pid in probe_ids:
        questions = {}
        for p in probe_answers:
            if p["probe_id"] != pid:
                continue
            for res in p["results"]:
                if res["q_id"] not in questions:
                    questions[res["q_id"]] = {"text": res["text"], "level": res["level"], "correct": 0, "total": 0,
                                              "answers": {}}
                q = questions[res["q_id"]]
                q["total"] = q["total"] + 1
                if res["correct"]:
                    q["correct"] = q["correct"] + 1
                q["answers"][res["answer"]] = q["answers"].get(res["answer"], 0) + 1
        for q_id in questions:
            q = questions[q_id]
            most = 0
            for answer in q["answers"]:
                if q["answers"][answer] > most:
                    most = q["answers"][answer]
            q["shared"] = most
            q["probe_id"] = pid
            probe_team.append(q)

    relays_during_outage = 0
    for did in delivery_order:
        d = deliveries[did]
        if d["via"] == "relay" and link_state_at(d["role"], d["scheduled_at"]) == "DOWN":
            relays_during_outage = relays_during_outage + 1

    team_sustain = []
    team_improve = []
    reached = 0
    for row in propagation:
        if row["ok"]:
            reached = reached + 1
    if len(propagation) > 0:
        if reached == len(propagation):
            team_sustain.append("Every critical report reached the people who needed it.")
        else:
            team_improve.append(str(len(propagation) - reached) + " of " + str(len(propagation))
                                + " critical reports never reached the person who needed them. Push critical information, don't wait to be asked.")
    had_down = False
    for o in outages:
        if o["state"] == "DOWN":
            had_down = True
    if had_down:
        if relays_during_outage > 0:
            team_sustain.append("The team used the alternate channel " + str(relays_during_outage) + " time(s) while a link was down.")
        else:
            team_improve.append("Nobody used the Comms Coordinator's alternate channel while a link was down.")
    for td in team_decisions:
        if td["answered"] > 0:
            if td["agree"] == td["answered"]:
                team_sustain.append("At " + td["dp_id"] + " the whole team backed the Ground Team Lead's call.")
            else:
                team_improve.append("At " + td["dp_id"] + " only " + str(td["agree"]) + " of " + str(td["answered"])
                                    + " teammates agreed with the Ground Team Lead's call. Check that everyone is working from the same picture.")

    # ---------- timeline ----------
    timeline = []
    for e in events:
        kind = e["kind"]
        data = e["data"]
        row = None
        if kind == "SESSION_START":
            row = ("control", "Exercise started (seed " + str(data["config"]["seed"]) + ", " + data["config"]["difficulty"] + ")", None)
        elif kind == "SESSION_PAUSE":
            row = ("control", "Instructor paused the exercise", None)
        elif kind == "SESSION_RESUME":
            row = ("control", "Instructor resumed the exercise", None)
        elif kind == "CONFIG_CHANGE" and e in config_changes:
            parts = []
            for field in data["changed"]:
                if field != "speed":
                    parts.append(field.replace("_", " ") + " " + str(data["changed"][field]))
            row = ("disruption", "Instructor changed settings: " + ", ".join(parts), None)
        elif kind == "REPORT_CREATED":
            by = data.get("created_by", "script")
            prefix = data["report_id"] + " from " + data.get("sender", "")
            if data.get("synthetic"):
                prefix = data["report_id"] + " generated to contradict " + str(data.get("conflicts_with"))
                cat = "disruption"
            else:
                cat = "report"
            if by == "instructor":
                prefix = prefix + " (instructor inject)"
            truth = ""
            if data.get("truth") is False:
                truth = " [false]"
            row = (cat, prefix + ": " + data.get("text", "") + truth, None)
        elif kind == "REPORT_SUPPRESSED":
            row = ("disruption", data["report_id"] + " was never sent because " + data["sender"] + " was offline", None)
        elif kind == "LINK_CHANGE":
            extra = ""
            if data.get("by") == "instructor":
                extra = " (instructor inject)"
            row = ("disruption", title(data["role"]) + "'s link went from " + data["old"].lower() + " to " + data["state"].lower() + extra, data["role"])
        elif kind == "SENSOR_CHANGE":
            extra = ""
            if data.get("by") == "instructor":
                extra = " (instructor inject)"
            row = ("disruption", data["sensor"] + " went " + data["state"].lower() + extra, None)
        elif kind == "DP_OPEN":
            row = ("decision", data["dp_id"] + " opened for " + ", ".join([title(r) for r in data["roles"]]) + ": " + data["prompt"], None)
        elif kind == "DECISION":
            if data["choice"] is None:
                row = ("decision", title(data["role"]) + " made no decision at " + data["dp_id"], data["role"])
            else:
                row = ("decision", title(data["role"]) + " chose \"" + data["choice_label"] + "\" at " + data["dp_id"]
                       + " after " + fmt_span(data["latency"]), data["role"])
        elif kind == "FORWARD":
            via = " by " + CHANNEL_NAMES.get(data.get("via", "net"), "radio").lower()
            row = ("team", title(e["role"]) + " passed " + data["report_id"] + " to " + ", ".join(data["to_roles"]) + via, e["role"])
        elif kind == "CHAT":
            via = " by " + CHANNEL_NAMES.get(data.get("via", "net"), "radio").lower()
            if data["report"].get("kind") == "flare":
                row = ("team", title(e["role"]) + " fired a flare: " + data["report"]["text"].replace("Flare: ", ""), e["role"])
            else:
                row = ("team", title(e["role"]) + " to " + ", ".join(data["to_roles"]) + via + ": \"" + data["report"]["text"] + "\"", e["role"])
        elif kind == "BELIEF":
            q_short = data["q_id"]
            for q in scenario.get("questions", []):
                if q["q_id"] == data["q_id"]:
                    q_short = q["short"]
            row = ("read", title(e["role"]) + " set their read on " + q_short + " to " + str(data["value"]) + "%", e["role"])
        elif kind == "GRADE":
            row = ("read", title(e["role"]) + " graded " + data["report_id"] + " as " + data["reliability"] + data["credibility"], e["role"])
        elif kind == "LOCATED":
            row = ("disruption", title(e["role"]) + " was located by direction finding and went off the net for " + str(data["penalty"]) + " s", e["role"])
        elif kind == "DF_CHANGE":
            row = ("disruption", "Enemy direction finding " + ("became active" if data["active"] else "stopped"), None)
        elif kind == "DIRECTOR" and data["from"] != data["to"]:
            row = ("disruption", "Fog Director moved the fog from level " + str(data["from"]) + " to " + str(data["to"]) + ". " + data["reason"], None)
        elif kind == "ACK":
            row = ("team", title(e["role"]) + " acknowledged " + data["report_id"], e["role"])
        elif kind == "PROBE_START":
            row = ("control", "Freeze probe " + data["probe_id"] + " started; the clock stopped", None)
        elif kind == "PROBE_ANSWER":
            right = 0
            for res in data["results"]:
                if res["correct"]:
                    right = right + 1
            row = ("probe", title(e["role"]) + " answered freeze " + data["probe_id"] + ": " + str(right) + " of " + str(len(data["results"])) + " correct", e["role"])
        elif kind == "PROBE_END":
            row = ("control", "Freeze probe " + data["probe_id"] + " ended", None)
        elif kind == "SESSION_END":
            row = ("control", "Exercise ended", None)
        if row is not None:
            timeline.append({"event_id": e["id"], "t": fmt(e["sim_time"]), "seconds": e["sim_time"],
                             "cat": row[0], "text": row[1], "role": row[2]})

    # ---------- swimlanes (drawn as SVG on the page) ----------
    x0 = 150
    x1 = 860
    lane_height = 52
    top = 34

    def x_of(t):
        return round(x0 + (x1 - x0) * min(max(t, 0), span) / span, 1)

    ticks = []
    step = 60
    if span > 900:
        step = 120
    if span > 1800:
        step = 300
    t = 0
    while t <= span:
        ticks.append({"x": x_of(t), "label": fmt(t)[2:]})
        t = t + step

    lanes = []
    for i in range(len(ROLE_ORDER)):
        role = ROLE_ORDER[i]
        y = top + i * lane_height
        bands = []
        for o in outages:
            if o["role"] == role:
                end = o["end"] if o["end"] is not None else end_time
                bands.append({"x": x_of(o["start"]), "w": max(x_of(end) - x_of(o["start"]), 2), "state": o["state"],
                              "title": "Link " + o["state"].lower() + " " + fmt(o["start"]) + " to " + fmt(end)})
        dots = []
        drops = []
        held_lines = []
        for did in delivery_order:
            d = deliveries[did]
            if d["role"] != role or d["kind"] == "ack":
                continue
            report = reports.get(d["report_id"], {})
            if d["status"] == "DELIVERED":
                source = report.get("source_time", d["scheduled_at"])
                late = d["delivered_at"] - source > delayed_after
                conflict = report.get("conflicts_with") is not None
                for other in reports.values():
                    if other.get("conflicts_with") == d["report_id"]:
                        conflict = True
                cls = "late" if late else "ok"
                if d["from_role"] is not None:
                    cls = cls + " team"
                label = d["report_id"]
                tip = label + " received " + fmt(d["delivered_at"]) + " (" + fmt_span(d["delivered_at"] - source) + " after it was observed)"
                if d["from_role"] is not None:
                    tip = tip + ", from " + title(d["from_role"])
                dots.append({"x": x_of(d["delivered_at"]), "cls": cls, "conflict": conflict, "title": tip})
                if d["was_held"] and d["held_at"] is not None:
                    held_lines.append({"x": x_of(d["held_at"]), "w": max(x_of(d["delivered_at"]) - x_of(d["held_at"]), 1)})
            elif d["status"] == "DROPPED":
                drops.append({"x": x_of(d["deliver_time"]), "title": d["report_id"] + " lost in transit, would have arrived " + fmt(d["deliver_time"])})
        marks = []
        windows = []
        for decision in decisions:
            if decision["role"] == role:
                marks.append({"x": x_of(decision["time"]), "label": decision["dp_id"],
                              "missed": decision["choice"] is None,
                              "title": decision["dp_id"] + ": " + decision["choice_label"] + " at " + fmt(decision["time"])})
        for dp_id in dps:
            dp = dps[dp_id]
            if role in dp["roles"]:
                windows.append({"x": x_of(dp["opened_at"]), "w": max(x_of(dp["deadline"]) - x_of(dp["opened_at"]), 2)})
        lanes.append({"role": role, "title": title(role), "y": y, "mid": y + 22, "bands": bands, "dots": dots,
                      "drops": drops, "held": held_lines, "marks": marks, "windows": windows})
    chart = {"width": 880, "height": top + len(ROLE_ORDER) * lane_height + 8, "x0": x0, "x1": x1, "ticks": ticks,
             "lanes": lanes}

    # ---------- decision cards ----------
    cards = []
    for decision in decisions:
        dp = dps.get(decision["dp_id"], {})
        visible_rows = []
        for v in decision.get("visible", []):
            report = reports.get(v["report_id"], {})
            visible_rows.append({"report_id": v["report_id"], "text": report.get("text", ""),
                                 "age": fmt_span(v["age"]), "truth": report.get("truth"),
                                 "cited": v["report_id"] in decision.get("cited", []),
                                 "relevant": v["report_id"] in dp.get("relevant_reports", []) or base_id(v["report_id"]) in dp.get("relevant_reports", []),
                                 "via": v.get("via", "")})
        visible_rows.sort(key=lambda row: (not row["relevant"], row["report_id"]))
        missing_rows = []
        for m in decision.get("relevant_missing", []):
            report = reports.get(m["report_id"], {})
            if not m.get("exists", True):
                where = "It was never sent."
            elif len(m["had_by"]) > 0:
                where = "Teammates who had it: " + ", ".join([title(r) for r in m["had_by"]]) + "."
            else:
                where = "Nobody on the team had it yet."
            missing_rows.append({"report_id": m["report_id"], "text": report.get("text", ""), "where": where})
        cards.append({
            "event_id": decision["event_id"], "dp_id": decision["dp_id"], "prompt": decision.get("prompt", dp.get("prompt", "")),
            "role": decision["role"], "title": title(decision["role"]), "name": decision.get("name", ""),
            "choice": decision["choice_label"], "missed": decision["choice"] is None,
            "confidence": decision.get("confidence", ""), "time": fmt(decision["time"]), "seconds": decision["time"],
            "latency": fmt_span(decision["latency"]), "window": fmt_span(decision["window"]), "late": decision.get("late", False),
            "rationale": decision.get("rationale", ""), "team": decision.get("team", False),
            "visible": visible_rows, "missing": missing_rows, "conflicts": decision.get("conflict_pairs", []),
            "score": decision["score"],
            "links": [title(r) + " " + decision.get("links", {}).get(r, "UP").lower() for r in ROLE_ORDER if decision.get("links", {}).get(r, "UP") != "UP"],
        })

    # ---------- replay data: what each role knew at any moment ----------
    replay = {"span": span, "roles": [], "reports": {}}
    for role in ROLE_ORDER:
        items = []
        for key in first_seen:
            if key[1] == role:
                d = first_seen[key]
                items.append({"report_id": key[0], "at": d["delivered_at"]})
        items.sort(key=lambda item: item["at"])
        replay["roles"].append({"role": role, "title": title(role), "items": items})
    for rid in reports:
        report = reports[rid]
        replay["reports"][rid] = {"text": report.get("text", ""), "sender": report.get("sender", ""),
                                  "truth": report.get("truth"), "source_time": report.get("source_time", 0),
                                  "conflicts_with": report.get("conflicts_with"), "grid": report.get("grid", "-")}
    replay["decisions"] = [{"role": d["role"], "at": d["time"], "dp_id": d["dp_id"], "choice": d["choice_label"]} for d in decisions]

    # ---------- overall numbers ----------
    all_percents = []
    for decision in decisions:
        if decision["choice"] is None:
            all_percents.append(0)
        elif decision["score"]["percent"] is not None:
            all_percents.append(decision["score"]["percent"])
    average_score = None
    if len(all_percents) > 0:
        average_score = round(sum(all_percents) / len(all_percents))

    participants = []
    for role in ROLE_ORDER:
        participants.append({"role": role, "title": title(role), "names": ", ".join(names.get(role, [])) or "Not played",
                             "brief": scenario.get("roles", {}).get(role, {}).get("brief", "")})

    instructor_injects = 0
    for e in events:
        if e["kind"] == "INJECT" or (e["kind"] in ["LINK_CHANGE", "SENSOR_CHANGE"] and e["data"].get("by") == "instructor"):
            instructor_injects = instructor_injects + 1

    report = {
        "session_id": sid,
        "scenario": {"name": scenario.get("name", ""), "area": scenario.get("area", ""),
                     "mission": scenario.get("mission", ""), "intent": scenario.get("intent", ""),
                     "disclaimer": scenario.get("disclaimer", "")},
        "config": config,
        "started": started,
        "ended": any(e["kind"] == "SESSION_END" for e in events),
        "start_event": start_event["id"] if start_event else (events[0]["id"] if len(events) > 0 else 0),
        "end_time": fmt(end_time),
        "end_seconds": end_time,
        "participants": participants,
        "comms": comms,
        "first_drop_event": first_drop_event,
        "first_hold_event": first_hold_event,
        "matrix": matrix,
        "propagation": propagation,
        "outages": outages,
        "findings": findings,
        "roles": roles_out,
        "team_decisions": team_decisions,
        "probe_team": probe_team,
        "relays_during_outage": relays_during_outage,
        "team_sustain": team_sustain,
        "team_improve": team_improve,
        "timeline": timeline,
        "chart": chart,
        "cards": cards,
        "replay": replay,
        "decision_count": len(decisions),
        "average_score": average_score,
        "event_count": len(events),
        "event_ids": [e["id"] for e in events],
        "instructor_injects": instructor_injects,
        "fingerprint": fingerprint([deliveries[did] for did in delivery_order]),
        "config_changes": len(config_changes),
    }
    build_extras(report, events, scenario, config, reports, deliveries, delivery_order, link_changes, end_time, title)
    report["summary"] = template_summary(report)
    return report


# ============================================================
# Written summary: a template version that always works, and an optional AI version
# ============================================================

def template_summary(r):
    """A short summary in which every sentence cites the events it is based on."""
    sentences = []
    if not r["started"]:
        return [{"text": "The exercise has not started yet.", "events": []}]
    c = r["config"]
    sentences.append({"text": r["scenario"]["name"] + " ran for " + r["end_time"][2:] + " on " + c.get("difficulty", "").lower()
                      + " difficulty with seed " + str(c.get("seed", "")) + ".", "events": [r["start_event"]]})
    comms_events = [r["start_event"]]
    if r["first_drop_event"] is not None:
        comms_events = [r["first_drop_event"]]
    if r["first_hold_event"] is not None:
        comms_events.append(r["first_hold_event"])
    lost = r["comms"]["dropped"]
    held = r["comms"]["held"]
    lost_text = "none were lost"
    if lost == 1:
        lost_text = "1 was lost"
    elif lost > 1:
        lost_text = str(lost) + " were lost"
    held_text = "none were held by link outages"
    if held == 1:
        held_text = "1 was held by a link outage"
    elif held > 1:
        held_text = str(held) + " were held by link outages"
    sentences.append({"text": str(r["comms"]["delivered"]) + " of " + str(r["comms"]["copies"]) + " message copies arrived, "
                      + lost_text + " and " + held_text + ".", "events": comms_events})
    shown = 0
    for finding in r["findings"]:
        if shown >= 3:
            break
        if finding["kind"] in ["gap", "outage", "false", "conflict"]:
            sentences.append({"text": finding["text"], "events": finding["events"]})
            shown = shown + 1
    sense = r.get("sense")
    if sense and sense.get("any_reads"):
        share = sense["team_share_set"]
        sentences.append({"text": "While reads were set, they were sound " + str(share["sound"]) + "% of the time. "
                          + "For " + str(share["info"]) + "% of the time the information simply hadn't arrived (information gap), and for "
                          + str(share["reasoning"]) + "% the reports pointed the right way but the read didn't follow (reasoning gap).",
                          "events": []})
    if r["decision_count"] > 0:
        ids = [card["event_id"] for card in r["cards"]][:6]
        sentences.append({"text": str(r["decision_count"]) + " decisions were recorded, with an average rubric score of "
                          + str(r["average_score"]) + "%.", "events": ids})
    return sentences


def llm_provider():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "Claude (" + os.environ.get("LLM_MODEL", "claude-haiku-5-5") + ")"
    if os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_MODEL"):
        return os.environ.get("LLM_MODEL")
    return None


def call_llm(prompt):
    if os.environ.get("ANTHROPIC_API_KEY"):
        body = {"model": os.environ.get("LLM_MODEL", "claude-haiku-5-5"), "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}]}
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read())
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text = text + block["text"]
        return text

    # Any OpenAI-compatible service (OpenAI, Gemini, Groq, a local Ollama server...)
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    body = {"model": os.environ["LLM_MODEL"], "max_tokens": 600, "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}]}
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"], "content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.loads(response.read())
    return data["choices"][0]["message"]["content"]


def build_facts(r):
    facts = {
        "exercise": r["scenario"]["name"],
        "length": r["end_time"],
        "difficulty": r["config"].get("difficulty"),
        "seed": r["config"].get("seed"),
        "start_event": "E" + str(r["start_event"]),
        "participants": [p["title"] + ": " + p["names"] for p in r["participants"]],
        "communications": {"copies_sent": r["comms"]["copies"], "arrived": r["comms"]["delivered"],
                           "lost": r["comms"]["dropped"], "held_by_outages": r["comms"]["held"],
                           "average_transit_seconds": r["comms"]["avg_transit"]},
        "findings": [],
        "decisions": [],
        "team": {"sustain": r["team_sustain"], "improve": r["team_improve"]},
    }
    for finding in r["findings"]:
        facts["findings"].append({"text": finding["text"], "events": ["E" + str(x) for x in finding["events"]]})
    for card in r["cards"]:
        facts["decisions"].append({"event": "E" + str(card["event_id"]), "role": card["title"], "point": card["dp_id"],
                                   "choice": card["choice"], "time": card["time"], "took": card["latency"],
                                   "rubric_percent": card["score"]["percent"]})
    return facts


def check_summary(text, facts_text, valid_ids):
    """Reject the AI text unless every sentence cites real events and every number exists in the facts."""
    clean = text.strip()
    if clean == "":
        return "The AI returned nothing."
    if len(clean.split()) > 220:
        return "The AI summary was too long."
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", clean)
    allowed_numbers = set(re.findall(r"\d+", facts_text))
    for sentence in sentences:
        sentence = sentence.strip()
        if sentence == "":
            continue
        cited = re.findall(r"\[([^\]]*)\]", sentence)
        ids = []
        for group in cited:
            ids = ids + re.findall(r"E(\d+)", group)
        if len(ids) == 0:
            return "A sentence had no event citation: \"" + sentence[:80] + "\""
        for event_id in ids:
            if int(event_id) not in valid_ids:
                return "The AI cited an event that does not exist: E" + event_id
        without_citations = re.sub(r"\[[^\]]*\]", "", sentence)
        for number in re.findall(r"\d+", without_citations):
            if number not in allowed_numbers:
                return "The AI used a number that is not in the log: " + number
    return ""


def llm_summary(r):
    provider = llm_provider()
    if provider is None:
        return {"ok": False, "reason": "No AI key is set. See the README to turn this on."}
    facts = build_facts(r)
    facts_text = json.dumps(facts, indent=1)
    prompt = (
        "You are helping an instructor at a military staff college write an after-action review of a fictional "
        "training exercise about decision-making when communications are degraded.\n\n"
        "Write a summary of 4 to 6 sentences (under 150 words) for the instructor. Use ONLY the facts below. "
        "End every sentence with the event IDs it relies on in square brackets, for example [E12, E40]. "
        "Do not mention any event, time, name or number that is not in the facts. Plain prose, no lists, no headings.\n\n"
        "FACTS:\n" + facts_text
    )
    try:
        text = call_llm(prompt)
    except Exception as error:
        return {"ok": False, "reason": "The AI request failed: " + str(error)[:200]}
    problem = check_summary(text, facts_text, set(r["event_ids"]))
    if problem != "":
        return {"ok": False, "reason": "The AI summary was rejected and the template summary is shown instead. " + problem}
    return {"ok": True, "text": text.strip(), "provider": provider}


# ============================================================
# The picture over time: reads, the shadow analyst, calibration, detectability
# ============================================================

GAP_NAMES = {
    "sound": "Sound read",
    "info": "Information gap",
    "reasoning": "Reasoning gap",
    "lucky": "Ahead of the evidence",
    "unset": "No read yet",
}


def classify(read, shadow_value, truth):
    """Put one moment of one trainee's read into a category."""
    if read is None or truth is None:
        return "unset"
    if read == 50:
        trainee_right = False
    else:
        trainee_right = (read > 50) == truth
    shadow_right = (shadow_value > 50) == truth
    if trainee_right and shadow_right:
        return "sound"
    if (not trainee_right) and (not shadow_right):
        return "info"
    if (not trainee_right) and shadow_right:
        return "reasoning"
    return "lucky"


def value_at(steps, t):
    """steps: list of (time, value) in time order. The latest value at or before t."""
    value = None
    for item in steps:
        if item[0] <= t:
            value = item[1]
    return value


def build_extras(report, events, scenario, config, reports, deliveries, delivery_order, link_changes, end_time, title):
    questions = scenario.get("questions", [])
    end_time = max(int(end_time), 1)

    # ---------- collect the new kinds of events ----------
    beliefs = {}
    for role in ROLE_ORDER:
        beliefs[role] = {}
        for q in questions:
            beliefs[role][q["q_id"]] = []
    belief_events = {}
    grade_list = []
    emits = []
    located = []
    df_changes = []
    director = []
    for e in events:
        kind = e["kind"]
        data = e["data"]
        if kind == "BELIEF" and e["role"] in beliefs and data["q_id"] in beliefs[e["role"]]:
            beliefs[e["role"]][data["q_id"]].append((e["sim_time"], data["value"]))
            belief_events[(e["role"], data["q_id"], e["sim_time"])] = e["id"]
        elif kind == "GRADE":
            grade_list.append({"t": e["sim_time"], "role": e["role"], "report_id": data["report_id"],
                               "grade": data["reliability"] + data["credibility"], "credibility": int(data["credibility"]),
                               "reliability": data["reliability"], "truth": data.get("truth"),
                               "sender": data.get("sender", ""), "event_id": e["id"]})
        elif kind == "EMIT":
            emits.append({"t": e["sim_time"], "role": e["role"], "exposure": data["exposure"], "reason": data["reason"]})
        elif kind == "LOCATED":
            located.append({"t": e["sim_time"], "role": e["role"], "penalty": data["penalty"], "event_id": e["id"]})
        elif kind == "DF_CHANGE":
            df_changes.append({"t": e["sim_time"], "active": data["active"], "event_id": e["id"]})
        elif kind == "DIRECTOR":
            director.append({"t": e["sim_time"], "from": data["from"], "to": data["to"], "reason": data["reason"],
                             "tracking": data.get("tracking"), "event_id": e["id"]})

    # ---------- what each role had, and when ----------
    first = {}
    for did in delivery_order:
        d = deliveries[did]
        if d.get("status") != "DELIVERED" or d["kind"] == "ack":
            continue
        key = (d["report_id"], d["role"])
        if key not in first or d["delivered_at"] < first[key]:
            first[key] = d["delivered_at"]
    visible = {}
    for role in ROLE_ORDER:
        visible[role] = []
    for key in first:
        if key[1] in visible:
            visible[key[1]].append((key[0], first[key]))

    step = 5
    times = list(range(0, end_time + 1, step))
    if times[-1] != end_time:
        times.append(end_time)

    # Chart geometry (drawn as SVG by the template)
    cw = 300
    ch = 92
    pad_l = 4
    pad_r = 4

    def cx(t):
        return round(pad_l + (cw - pad_l - pad_r) * t / end_time, 1)

    def cy(v):
        return round(6 + (ch - 12) * (1 - v / 100.0), 1)

    pictures = []
    findings = []
    totals = {"sound": 0, "info": 0, "reasoning": 0, "lucky": 0, "unset": 0}
    role_summaries = []
    samples = []   # for calibration: (role, q_id, read, shadow, truth)

    for role in ROLE_ORDER:
        role_counts = {"sound": 0, "info": 0, "reasoning": 0, "lucky": 0, "unset": 0}
        panels = []
        for q in questions:
            q_id = q["q_id"]
            steps = beliefs[role][q_id]
            shadow_series = []
            truth_series = []
            read_series = []
            cats = []
            for t in times:
                value, reasons = shadow.shadow_value(q, visible[role], t, reports, scenario)
                shadow_series.append(value)
                truth = shadow.truth_value(q, t)
                truth_series.append(truth)
                read = value_at(steps, t)
                read_series.append(read)
                category = classify(read, value, truth)
                cats.append(category)
                role_counts[category] = role_counts[category] + 1
                totals[category] = totals[category] + 1
                if read is not None and t % 30 == 0:
                    samples.append((role, q_id, read, value, truth))

            # SVG paths
            shadow_points = []
            for i in range(len(times)):
                shadow_points.append(str(cx(times[i])) + "," + str(cy(shadow_series[i])))
            truth_points = []
            for i in range(len(times)):
                v = 100 if truth_series[i] else 0
                if i > 0:
                    prev = 100 if truth_series[i - 1] else 0
                    if prev != v:
                        truth_points.append(str(cx(times[i])) + "," + str(cy(prev)))
                truth_points.append(str(cx(times[i])) + "," + str(cy(v)))
            read_path = ""
            dots = []
            last_value = None
            for item in steps:
                x = cx(item[0])
                if last_value is None:
                    read_path = read_path + "M" + str(x) + "," + str(cy(item[1]))
                else:
                    read_path = read_path + " L" + str(x) + "," + str(cy(last_value)) + " L" + str(x) + "," + str(cy(item[1]))
                last_value = item[1]
                dots.append({"x": x, "y": cy(item[1]), "value": item[1], "t": fmt(item[0])})
            if last_value is not None:
                read_path = read_path + " L" + str(cx(end_time)) + "," + str(cy(last_value))

            # The strip under the chart: one coloured segment per run of the same category
            strip = []
            start = 0
            for i in range(1, len(times) + 1):
                if i == len(times) or cats[i] != cats[start]:
                    t0 = times[start]
                    t1 = times[i] if i < len(times) else end_time
                    if t1 > t0:
                        strip.append({"x": cx(t0), "w": max(cx(t1) - cx(t0), 1), "cat": cats[start],
                                      "label": GAP_NAMES[cats[start]] + ", " + fmt(t0) + " to " + fmt(t1)})
                    start = i

            # Longest reasoning gap becomes a finding
            run_start = None
            longest = (0, None, None)
            for i in range(len(times)):
                if cats[i] == "reasoning":
                    if run_start is None:
                        run_start = times[i]
                    length = times[i] - run_start + step
                    if length > longest[0]:
                        longest = (length, run_start, times[i])
                else:
                    run_start = None
            if longest[0] >= 60:
                t_mid = longest[1]
                read = value_at(steps, t_mid)
                value, reasons = shadow.shadow_value(q, visible[role], t_mid, reports, scenario)
                cite = []
                for item in steps:
                    if item[0] <= t_mid:
                        cite = [belief_events.get((role, q_id, item[0]))]
                for rid, at in visible[role]:
                    if at <= t_mid and reports.get(rid, {}).get("evidence", {}).get(q_id) is not None:
                        if reports[rid].get("event_id") is not None and len(cite) < 3:
                            cite.append(reports[rid]["event_id"])
                cite = [c for c in cite if c is not None]
                findings.append({
                    "kind": "reasoning",
                    "text": title(role) + " spent " + fmt_span(longest[0]) + " in a reasoning gap on " + q["short"]
                            + " from " + fmt(longest[1]) + ": the reports they had pointed to " + str(value)
                            + "%, but their read stayed at " + str(read) + "%.",
                    "events": cite,
                })

            # How did their read respond to strong evidence?
            responses = []
            for rid, at in sorted(visible[role], key=lambda item: item[1]):
                r = reports.get(rid, {})
                evidence = r.get("evidence", {}).get(q_id)
                if evidence is None:
                    continue
                strength = evidence * shadow.source_weight(r.get("sender", ""), scenario)
                if abs(strength) < 1.0:
                    continue
                before = value_at(steps, at)
                if before is None:
                    responses.append({"report_id": rid, "at": fmt(at), "result": "No read set yet", "lag": None, "ok": None,
                                      "direction": "yes" if evidence > 0 else "no"})
                    continue
                moved = None
                for item in steps:
                    if item[0] < at:
                        continue
                    if evidence > 0 and item[1] - before >= 10:
                        moved = ("toward", item[0])
                        break
                    if evidence < 0 and before - item[1] >= 10:
                        moved = ("toward", item[0])
                        break
                    if evidence > 0 and before - item[1] >= 10:
                        moved = ("away", item[0])
                        break
                    if evidence < 0 and item[1] - before >= 10:
                        moved = ("away", item[0])
                        break
                if moved is None:
                    result_text = "Read did not move"
                    responses.append({"report_id": rid, "at": fmt(at), "result": result_text, "lag": None, "ok": False,
                                      "direction": "yes" if evidence > 0 else "no"})
                elif moved[0] == "toward":
                    lag = moved[1] - at
                    responses.append({"report_id": rid, "at": fmt(at), "result": "Updated after " + fmt_span(lag),
                                      "lag": lag, "ok": True, "direction": "yes" if evidence > 0 else "no"})
                else:
                    responses.append({"report_id": rid, "at": fmt(at), "result": "Moved the other way", "lag": None,
                                      "ok": False, "direction": "yes" if evidence > 0 else "no"})

            share = {}
            n = len(cats)
            for key in ["sound", "info", "reasoning", "lucky", "unset"]:
                share[key] = round(100 * cats.count(key) / n) if n > 0 else 0

            panels.append({
                "q_id": q_id, "short": q["short"], "text": q["text"],
                "shadow_points": " ".join(shadow_points), "truth_points": " ".join(truth_points),
                "read_path": read_path, "dots": dots, "strip": strip, "share": share,
                "responses": responses, "has_read": len(steps) > 0,
                "final_read": steps[-1][1] if len(steps) > 0 else None,
                "series": {"read": read_series, "shadow": shadow_series,
                           "truth": [1 if v else 0 for v in truth_series]},
            })
        total = sum(role_counts.values())
        role_summaries.append({"role": role, "title": title(role),
                               "share": {k: (round(100 * role_counts[k] / total) if total else 0) for k in role_counts}})
        pictures.append({"role": role, "title": title(role), "panels": panels})

    grand = sum(totals.values())
    team_share = {}
    for key in totals:
        team_share[key] = round(100 * totals[key] / grand) if grand else 0
    # The same shares, counting only the moments when a read was set.
    set_total = grand - totals["unset"]
    team_share_set = {}
    for key in ["sound", "info", "reasoning", "lucky"]:
        team_share_set[key] = round(100 * totals[key] / set_total) if set_total else 0

    # ---------- calibration ----------
    buckets = []
    edges = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    for low, high in edges:
        buckets.append({"label": str(low) + "–" + str(min(high, 100)) + "%", "low": low, "high": high, "n": 0, "right": 0})
    brier_total = 0.0
    shadow_brier_total = 0.0
    counted = 0
    per_role = {}
    for role, q_id, read, shadow_value, truth in samples:
        if truth is None:
            continue
        outcome = 1.0 if truth else 0.0
        brier_total = brier_total + (read / 100.0 - outcome) ** 2
        shadow_brier_total = shadow_brier_total + (shadow_value / 100.0 - outcome) ** 2
        counted = counted + 1
        per_role.setdefault(role, {"n": 0, "conf": 0.0, "right": 0, "brier": 0.0})
        per_role[role]["n"] = per_role[role]["n"] + 1
        per_role[role]["brier"] = per_role[role]["brier"] + (read / 100.0 - outcome) ** 2
        if read == 50:
            continue
        confidence = max(read, 100 - read)
        right = (read > 50) == truth
        per_role[role]["conf"] = per_role[role]["conf"] + confidence
        if right:
            per_role[role]["right"] = per_role[role]["right"] + 1
        for b in buckets:
            if b["low"] <= confidence < b["high"]:
                b["n"] = b["n"] + 1
                if right:
                    b["right"] = b["right"] + 1
    for b in buckets:
        b["rate"] = round(100 * b["right"] / b["n"]) if b["n"] > 0 else None
        b["mid"] = (b["low"] + min(b["high"], 100)) / 2
    calibration = {
        "buckets": buckets,
        "brier": round(brier_total / counted, 3) if counted else None,
        "shadow_brier": round(shadow_brier_total / counted, 3) if counted else None,
        "samples": counted,
        "roles": [],
    }
    for role in ROLE_ORDER:
        if role not in per_role:
            continue
        item = per_role[role]
        avg_conf = round(item["conf"] / item["n"]) if item["n"] else None
        hit = round(100 * item["right"] / item["n"]) if item["n"] else None
        calibration["roles"].append({"role": role, "title": title(role), "confidence": avg_conf, "hit": hit,
                                     "gap": (avg_conf - hit) if avg_conf is not None and hit is not None else None,
                                     "brier": round(item["brier"] / item["n"], 3)})
    # calibration chart points (x = stated confidence, y = how often right)
    cal_w = 260
    cal_h = 200
    for b in buckets:
        if b["rate"] is not None:
            b["x"] = round(30 + (cal_w - 40) * (b["mid"] - 50) / 50, 1)
            b["y"] = round(10 + (cal_h - 40) * (1 - b["rate"] / 100.0), 1)
            b["r"] = round(4 + min(b["n"], 40) / 5, 1)
    calibration["chart"] = {"w": cal_w, "h": cal_h,
                            "diag": [round(30, 1), round(10 + (cal_h - 40) * 0.5, 1), round(cal_w - 10, 1), 10]}

    # ---------- detectability ----------
    decay = float(config.get("emission_decay", 0.3))
    threshold = float(config.get("located_threshold", 100))
    emission_panels = []
    for role in ROLE_ORDER:
        mine = [e for e in emits if e["role"] == role]
        my_located = [l for l in located if l["role"] == role]
        value = 0.0
        series = []
        idx_e = 0
        idx_l = 0
        peak = 0.0
        for t in range(0, end_time + 1):
            if t > 0:
                value = max(0.0, value - decay)
            while idx_e < len(mine) and mine[idx_e]["t"] <= t:
                value = mine[idx_e]["exposure"]
                idx_e = idx_e + 1
            while idx_l < len(my_located) and my_located[idx_l]["t"] <= t:
                value = 30.0
                idx_l = idx_l + 1
            if value > peak:
                peak = value
            if t % step == 0 or t == end_time:
                series.append((t, value))
        top = max(threshold * 1.25, peak + 10)
        points = []
        for t, v in series:
            points.append(str(cx(t)) + "," + str(round(6 + (ch - 12) * (1 - v / top), 1)))
        area = "M" + str(cx(0)) + "," + str(ch - 6) + " L" + " L".join(points) + " L" + str(cx(end_time)) + "," + str(ch - 6) + " Z"
        channels = {"net": 0, "relay": 0, "runner": 0, "flare": 0}
        for e in events:
            if e["role"] != role:
                continue
            if e["kind"] in ["FORWARD", "CHAT"]:
                via = e["data"].get("via", "net")
                channels[via] = channels.get(via, 0) + 1
            if e["kind"] == "ACK":
                via = e["data"].get("via", "net")
                channels[via] = channels.get(via, 0) + 1
        emission_panels.append({
            "role": role, "title": title(role), "area": area, "line": " ".join(points),
            "threshold_y": round(6 + (ch - 12) * (1 - threshold / top), 1),
            "located": [{"x": cx(l["t"]), "t": fmt(l["t"]), "event_id": l["event_id"]} for l in my_located],
            "peak": round(peak), "channels": channels,
            "channel_list": [{"name": CHANNEL_NAMES[k], "n": channels.get(k, 0)} for k in ["net", "relay", "runner", "flare"]],
        })
    df_bands = []
    active_from = None
    for change in df_changes:
        if change["active"] and active_from is None:
            active_from = change["t"]
        if (not change["active"]) and active_from is not None:
            df_bands.append({"x": cx(active_from), "w": max(cx(change["t"]) - cx(active_from), 1)})
            active_from = None
    if active_from is not None:
        df_bands.append({"x": cx(active_from), "w": max(cx(end_time) - cx(active_from), 1)})
    for l in located:
        findings.append({"kind": "located", "text": title(l["role"]) + " was located by direction finding at " + fmt(l["t"])
                         + " after heavy radio use, and was off the net for " + fmt_span(l["penalty"]) + ".",
                         "events": [l["event_id"]]})

    # ---------- switching channels when a link went down (PACE) ----------
    pace = []
    for i in range(len(link_changes)):
        change = link_changes[i]
        if change["state"] != "DOWN":
            continue
        role = change["role"]
        start = change["t"]
        end = end_time
        for later in link_changes[i + 1:]:
            if later["role"] == role:
                end = later["t"]
                break
        switched = None
        for e in events:
            if e["sim_time"] is None or e["sim_time"] < start or e["sim_time"] > end:
                continue
            if e["kind"] not in ["FORWARD", "CHAT", "ACK"]:
                continue
            via = e["data"].get("via", "net")
            if via == "net":
                continue
            to_roles = e["data"].get("to_roles", [])
            if e["kind"] == "ACK":
                to_roles = [e["data"].get("to_role")]
            if e["role"] == role or role in to_roles:
                switched = {"t": e["sim_time"], "by": title(e["role"]), "via": CHANNEL_NAMES.get(via, via), "event_id": e["id"]}
                break
        item = {"role": role, "title": title(role), "start": fmt(start), "end": fmt(end), "length": fmt_span(end - start),
                "by_engine": change["by"] == "engine", "event_id": change["event_id"]}
        if switched is not None:
            item["result"] = switched["by"] + " switched to " + switched["via"].lower() + " after " + fmt_span(switched["t"] - start)
            item["ok"] = True
        else:
            item["result"] = "Nobody switched channels during the outage"
            item["ok"] = False
        pace.append(item)

    # ---------- source grading ----------
    graded = []
    trust_errors = 0
    for g in grade_list:
        credibility = g["credibility"]
        if credibility == 6:
            verdict = "Couldn't judge"
            ok = None
        elif g["truth"] is False and credibility <= 3:
            verdict = "Trusted a false report"
            ok = False
            trust_errors = trust_errors + 1
        elif g["truth"] is False:
            verdict = "Doubted a false report"
            ok = True
        elif g["truth"] is True and credibility <= 3:
            verdict = "Trusted a true report"
            ok = True
        else:
            verdict = "Doubted a true report"
            ok = False
        graded.append({"role": g["role"], "title": title(g["role"]), "report_id": g["report_id"], "grade": g["grade"],
                       "sender": g["sender"], "truth": g["truth"], "verdict": verdict, "ok": ok, "t": fmt(g["t"]),
                       "text": reports.get(g["report_id"], {}).get("text", ""), "event_id": g["event_id"]})
        if ok is False and g["truth"] is False:
            findings.append({"kind": "trust", "text": title(g["role"]) + " graded " + g["report_id"] + " from " + g["sender"]
                             + " as " + g["grade"] + ", but that report was false.", "events": [g["event_id"]]})

    # ---------- the Fog Director ----------
    level_names = ["Clear", "Light fog", "Normal fog", "Heavy fog", "Dense fog"]
    director_points = []
    level = director[0]["from"] if len(director) > 0 else int(config.get("fog_level", 2))
    director_points.append(str(cx(0)) + "," + str(round(6 + (60 - 12) * (1 - level / 4.0), 1)))
    for d in director:
        y_old = round(6 + (60 - 12) * (1 - d["from"] / 4.0), 1)
        y_new = round(6 + (60 - 12) * (1 - d["to"] / 4.0), 1)
        director_points.append(str(cx(d["t"])) + "," + str(y_old))
        director_points.append(str(cx(d["t"])) + "," + str(y_new))
        level = d["to"]
    director_points.append(str(cx(end_time)) + "," + str(round(6 + (60 - 12) * (1 - level / 4.0), 1)))
    director_out = {"used": len(director) > 0, "points": " ".join(director_points),
                    "steps": [{"t": fmt(d["t"]), "from": level_names[d["from"]], "to": level_names[d["to"]],
                               "reason": d["reason"], "changed": d["from"] != d["to"]} for d in director]}

    # ---------- decision cards: what they believed at that moment ----------
    for card in report["cards"]:
        decision = None
        for e in events:
            if e["id"] == card["event_id"]:
                decision = e["data"]
        card["picture"] = None
        if decision is None:
            continue
        q_id = decision.get("question")
        if q_id is None:
            continue
        question = None
        for q in questions:
            if q["q_id"] == q_id:
                question = q
        if question is None:
            continue
        read = decision.get("reads", {}).get(q_id)
        shadow_value = decision.get("shadow", {}).get(q_id)
        truth = decision.get("truth", {}).get(q_id)
        if shadow_value is None:
            continue
        category = classify(read, shadow_value, truth)
        card["picture"] = {"short": question["short"], "read": read, "shadow": shadow_value, "truth_yes": bool(truth),
                           "truth": question["yes"] if truth else question["no"], "category": category,
                           "category_name": GAP_NAMES[category]}

    # ---------- replay: rebuild each role's screen at any moment ----------
    replay_roles = []
    for role in ROLE_ORDER:
        items = []
        for did in delivery_order:
            d = deliveries[did]
            if d["role"] != role or d.get("status") != "DELIVERED" or d["kind"] == "ack":
                continue
            r = reports.get(d["report_id"], {})
            items.append({"rid": d["report_id"], "at": d["delivered_at"], "kind": d["kind"], "via": d.get("via", "net"),
                          "from": title(d["from_role"]) if d.get("from_role") else "", "sender": r.get("sender", ""),
                          "text": r.get("text", ""), "source_time": r.get("source_time", 0), "truth": r.get("truth"),
                          "grid": r.get("grid", "-")})
        items.sort(key=lambda item: item["at"])
        reads = {}
        for q in questions:
            reads[q["q_id"]] = [[t, v] for t, v in beliefs[role][q["q_id"]]]
        link_steps = [[0, "UP"]]
        for change in link_changes:
            if change["role"] == role:
                link_steps.append([change["t"], change["state"]])
        replay_roles.append({"role": role, "title": title(role), "items": items, "reads": reads, "links": link_steps,
                             "located": [l["t"] for l in located if l["role"] == role]})
    shadow_grid = {}
    for p in pictures:
        shadow_grid[p["role"]] = {}
        for panel in p["panels"]:
            shadow_grid[p["role"]][panel["q_id"]] = panel["series"]["shadow"]
    report["replay2"] = {
        "end": end_time, "step": step, "times": times,
        "questions": [{"q_id": q["q_id"], "short": q["short"]} for q in questions],
        "roles": replay_roles, "shadow": shadow_grid,
        "truth": {q["q_id"]: [1 if shadow.truth_value(q, t) else 0 for t in times] for q in questions},
        "decisions": [{"role": c["role"], "dp_id": c["dp_id"], "choice": c["choice"], "at": c["seconds"],
                       "missed": c.get("missed", False)} for c in report["cards"]],
    }

    ticks = []
    t = 0
    tick_step = 120 if end_time > 600 else 60
    while t <= end_time:
        ticks.append({"x": cx(t), "label": fmt(t)[2:]})
        t = t + tick_step

    report["sense"] = {
        "questions": [{"q_id": q["q_id"], "short": q["short"], "text": q["text"]} for q in questions],
        "pictures": pictures, "team_share": team_share, "team_share_set": team_share_set, "role_share": role_summaries,
        "calibration": calibration, "chart": {"w": cw, "h": ch, "ticks": ticks},
        "gap_names": GAP_NAMES, "any_reads": any(len(beliefs[r][q["q_id"]]) > 0 for r in ROLE_ORDER for q in questions),
    }
    report["emissions"] = {"panels": emission_panels, "df_bands": df_bands, "threshold": threshold,
                           "located": len(located), "chart": {"w": cw, "h": ch}}
    report["pace"] = pace
    report["grading"] = {"items": graded, "trust_errors": trust_errors}
    report["director"] = director_out
    report["findings"] = findings + report["findings"]
