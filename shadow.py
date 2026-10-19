"""
shadow.py - the "shadow analyst"

For every trainee, the shadow analyst looks at exactly the reports that trainee
had received, and works out what a careful, unbiased reader would believe about
each key question. It is deliberately simple and fully explainable:

    start from the scenario's prior (for example 60% "the bridge is usable")
    for every report the trainee had:
        add the report's evidence for that question
        x how reliable its source usually is
        x how fresh the report is (older reports count for less)
        x 0.1 if another report the trainee had says that source is compromised

The result is a percentage. Comparing it with the trainee's own read separates:
    - an information gap: even the shadow got it wrong, the info never arrived
    - a reasoning gap: the shadow got it right from the same reports, the trainee didn't
    - a lucky read: the trainee was right although their reports pointed the other way

The live instructor console and the After-Action Review both use these
functions, so they always agree.
"""

import math


def truth_value(question, t):
    """The ground truth of a key question at time t (True or False)."""
    value = None
    for step in question.get("truth", []):
        if step["from"] <= t:
            value = step["value"]
    return value


def source_weight(sender, scenario):
    sources = scenario.get("sources", {})
    if sender in sources:
        return float(sources[sender].get("weight", 0.6))
    return 0.6


def shadow_value(question, visible, t, reports, scenario):
    """
    question: one entry of scenario["questions"]
    visible:  list of (report_id, received_at) for one trainee
    t:        the moment to evaluate
    Returns (percent, list of the reports that moved the number).
    """
    q_id = question["q_id"]
    prior = float(question.get("prior", 0.5))
    if prior <= 0.01:
        prior = 0.01
    if prior >= 0.99:
        prior = 0.99
    log_odds = math.log(prior / (1 - prior))
    half_life = float(question.get("half_life", 300))

    # Which sources has this trainee been told are compromised?
    discredited = []
    for item in visible:
        report_id = item[0]
        received_at = item[1]
        if received_at > t:
            continue
        report = reports.get(report_id, {})
        if report.get("discredits") and report["discredits"] not in discredited:
            discredited.append(report["discredits"])

    reasons = []
    for item in visible:
        report_id = item[0]
        received_at = item[1]
        if received_at > t:
            continue
        report = reports.get(report_id)
        if report is None:
            continue
        evidence = report.get("evidence", {}).get(q_id)
        if evidence is None:
            continue
        weight = source_weight(report.get("sender", ""), scenario)
        if report.get("sender") in discredited:
            weight = weight * 0.1
        age = t - report.get("source_time", received_at)
        if age < 0:
            age = 0
        freshness = 0.5 ** (age / half_life)
        push = evidence * weight * freshness
        log_odds = log_odds + push
        reasons.append({"report_id": report_id, "push": round(push, 2)})

    if log_odds > 20:
        log_odds = 20
    if log_odds < -20:
        log_odds = -20
    percent = 100 / (1 + math.exp(-log_odds))
    return round(percent), reasons
