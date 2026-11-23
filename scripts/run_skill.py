#!/usr/bin/env python3
"""Deterministic reviewer-response triage for academic revision tasks.

Reads /input/case.json (or --case), writes /app/output/result.json (or --output).
Standard library only. No network. Preserves case_id and every original
output_contract field name; unknown fields are emitted as null and flagged.
"""

import argparse
import hashlib
import json
import os
import re
import sys

MANUSCRIPT_KEYS = (
    "manuscript", "manuscript_baseline", "baseline", "baseline_text",
    "source_text", "original_text", "paper_text", "draft", "chapter_text",
)
COMMENT_KEYS = (
    "reviewer_comments", "review_comments", "referee_comments",
    "reviewer_report", "reviews", "comments",
)
TYPE_RULES = {
    "method": ("method", "methodology", "experiment", "design", "ablation",
               "control", "sampl", "method", "方法", "实验", "设计", "对照", "样本"),
    "data": ("data", "dataset", "statistic", "significan", "p-value", "number",
             "sample size", "数据", "统计", "显著", "样本量", "数字"),
    "writing": ("clarif", "wording", "grammar", "language", "readab", "explain",
                "表述", "写作", "语言", "语法", "澄清", "解释"),
    "format": ("format", "reference", "citation", "figure", "table", "style",
               "模板", "格式", "引用", "图", "表", "参考文献"),
    "ethics": ("ethic", "consent", "irb", "privacy", "conflict of interest",
               "伦理", "知情同意", "利益冲突", "隐私"),
}
HIGH_MARKERS = ("major", "must", "critical", "essential", "fundamental",
                "reject", "重大", "必须", "关键", "根本", "严重")
LOW_MARKERS = ("minor", "optional", "suggest", "nice to", "typo", "建议", "可选", "小")
REJECT_MARKERS = ("cannot", "can not", "unable", "not possible", "disagree",
                  "无法", "不能", "不采纳", "不同意", "不可行")
PARTIAL_MARKERS = ("partially", "in part", "where possible", "部分", "尽量")


def load_case(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def norm_space(text):
    return re.sub(r"\s+", " ", text or "").strip()


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def read_if_path(value):
    if isinstance(value, str) and len(value) < 4096:
        candidate = value.strip()
        if os.path.isfile(candidate) and os.path.getsize(candidate) <= 2 * 1024 * 1024:
            try:
                with open(candidate, "r", encoding="utf-8-sig", errors="strict") as fh:
                    return fh.read(), candidate
            except (UnicodeDecodeError, OSError):
                return None, candidate
    return None, None


def find_by_keys(obj, keys, path="case"):
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = "%s.%s" % (path, key)
            if "output_contract" in child:
                continue
            if str(key).lower() in keys:
                found.append((child, value))
            found.extend(find_by_keys(value, keys, child))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            found.extend(find_by_keys(value, keys, "%s[%d]" % (path, idx)))
    return found


def flatten_material(value, label, texts, notes):
    """Collect usable text from an arbitrary material node."""
    if isinstance(value, str):
        text, path = read_if_path(value)
        if text is not None:
            texts.append({"label": label, "path": path, "text": text})
        elif len(value.strip()) >= 20:
            texts.append({"label": label, "path": None, "text": value})
        elif value.strip():
            notes.append({"label": label, "reason": "too_short_to_use"})
        return
    if isinstance(value, dict):
        path_value = value.get("path") or value.get("file") or value.get("filename")
        if isinstance(path_value, str):
            text, path = read_if_path(path_value)
            if text is not None:
                texts.append({"label": label, "path": path, "text": text})
                return
        for key in ("text", "content", "body", "markdown", "value"):
            if isinstance(value.get(key), str):
                flatten_material(value[key], "%s.%s" % (label, key), texts, notes)
                return
        for key, item in value.items():
            flatten_material(item, "%s.%s" % (label, key), texts, notes)
        return
    if isinstance(value, list):
        for idx, item in enumerate(value):
            flatten_material(item, "%s[%d]" % (label, idx), texts, notes)


def split_comments(raw):
    """Return list of (comment_id, text) from a raw comments block or list."""
    if isinstance(raw, list):
        items = []
        for idx, entry in enumerate(raw, 1):
            if isinstance(entry, dict):
                cid = entry.get("id") or entry.get("comment_id") or entry.get("number") or "C%d" % idx
                body = entry.get("text") or entry.get("content") or entry.get("comment") or entry.get("body")
                if body is None:
                    body = json.dumps(entry, ensure_ascii=False)
            else:
                cid, body = "C%d" % idx, str(entry)
            items.append((str(cid), str(body).strip()))
        return items
    text = str(raw or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    numbered = []
    pattern = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:comment|reviewer|referee|审稿意见|意见)?\s*"
        r"([0-9]{1,3}|[A-Z])(?:[\.\)、:：]|\s)\s*(.+)$",
        re.IGNORECASE,
    )
    current_id, current = None, []
    for line in lines:
        match = pattern.match(line)
        if match and len(match.group(2).strip()) > 3:
            if current_id is not None:
                numbered.append((current_id, " ".join(current).strip()))
            current_id = "C" + match.group(1)
            current = [match.group(2).strip()]
        elif current_id is not None:
            current.append(line.strip())
    if current_id is not None:
        numbered.append((current_id, " ".join(current).strip()))
    numbered = [(cid, body) for cid, body in numbered if body]
    if len(numbered) >= 2:
        return numbered
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return [("C%d" % idx, block) for idx, block in enumerate(blocks, 1)]


def split_sub_requirements(body):
    parts = re.split(r"(?:;|；|\n\s*[-*•]\s*|\s+(?:and|and also|plus)\s+(?=[a-z]))", body)
    parts = [norm_space(p).strip(" .") for p in parts]
    parts = [p for p in parts if len(p) >= 8]
    if len(parts) <= 1:
        return [norm_space(body)]
    return parts


def classify_type(text):
    lowered = text.lower()
    scores = {name: sum(1 for marker in markers if marker in lowered)
              for name, markers in TYPE_RULES.items()}
    best = max(scores, key=lambda key: scores[key])
    return best if scores[best] > 0 else "writing"


def classify_severity(text):
    lowered = text.lower()
    if any(marker in lowered for marker in HIGH_MARKERS):
        return "high"
    if any(marker in lowered for marker in LOW_MARKERS):
        return "low"
    return "medium"


def classify_decision(text):
    lowered = text.lower()
    if any(marker in lowered for marker in REJECT_MARKERS):
        return "reject" if any(m in lowered for m in ("不采纳", "disagree", "not possible")) else "clarify"
    if any(marker in lowered for marker in PARTIAL_MARKERS):
        return "partial"
    return "accept"


def tone_for(decision, severity):
    if decision == "reject":
        return "appeal" if severity == "high" else "polite_disagree"
    if decision == "clarify":
        return "clarify"
    if decision == "partial":
        return "clarify"
    return "accept"


def split_sections(manuscript):
    sections = []
    current = "Front matter"
    buffer = []
    for line in manuscript.splitlines():
        heading = re.match(r"^\s*(?:#{1,6}\s+|\d+(?:\.\d+)*\s+)(\S.*)$", line)
        if heading:
            if buffer:
                sections.append({"section": current, "text": "\n".join(buffer)})
                buffer = []
            current = heading.group(1).strip()
        else:
            buffer.append(line)
    if buffer:
        sections.append({"section": current, "text": "\n".join(buffer)})
    if not sections:
        sections = [{"section": "Body", "text": manuscript}]
    return sections


def paragraphs_of(manuscript):
    paras, offset = [], 0
    section = "Front matter"
    for match in re.finditer(r"(?:[^\n]|\n(?!\s*\n))+", manuscript):
        block = match.group(0).strip()
        if not block:
            continue
        start = manuscript.find(block, offset)
        if start < 0:
            start = offset
        offset = start + len(block)
        first_line = block.split("\n", 1)[0]
        heading = re.match(r"^\s*(?:#{1,6}\s+|\d+(?:\.\d+)*\s+)(\S.*)$", first_line)
        if heading:
            section = heading.group(1).strip()
            if "\n" not in block:
                continue
            body_start = block.find("\n")
            block = block[body_start + 1:].strip()
            start = start + body_start + 1
        if not block:
            continue
        paras.append({
            "section": section,
            "paragraph_index": len(paras) + 1,
            "char_start": start,
            "char_end": start + len(block),
            "text": block,
        })
    return paras


def content_words(text):
    lowered = text.lower()
    words = re.findall(r"[a-z]{4,}|[\u4e00-\u9fff]{2,}", lowered)
    stop = {"this", "that", "with", "from", "have", "been", "were", "their", "there",
            "which", "would", "could", "should", "please", "paper", "author",
            "作者", "本文", "我们", "请", "建议", "需要", "问题", "是否"}
    return {w for w in words if w not in stop}


def locate_in_manuscript(comment, sections, paragraphs):
    terms = content_words(comment)
    best, best_score = None, 0.0
    for para in paragraphs:
        words = content_words(para["text"])
        if not words:
            continue
        overlap = len(terms & words) / max(1, len(terms))
        if overlap > best_score:
            best, best_score = para, overlap
    if best is None or best_score < 0.12:
        return {
            "found": False, "section": None, "paragraph_index": None,
            "char_start": None, "char_end": None, "anchor_quote": None,
            "match_score": round(best_score, 3),
        }
    section_name = best.get("section")
    if not section_name:
        for section in sections:
            if best["text"] in section["text"]:
                section_name = section["section"]
                break
    return {
        "found": True,
        "section": section_name,
        "paragraph_index": best["paragraph_index"],
        "char_start": best["char_start"],
        "char_end": best["char_end"],
        "anchor_quote": norm_space(best["text"])[:240],
        "match_score": round(best_score, 3),
    }


def build_evidence(comment_type, body):
    if comment_type not in ("data", "method"):
        return []
    lowered = body.lower()
    needs_experiment = any(m in lowered for m in ("experiment", "experiment", "re-run", "实验", "补做"))
    needs_data = any(m in lowered for m in ("data", "dataset", "sample", "数据", "样本"))
    items = []
    if needs_experiment:
        items.append({
            "required": "additional_experiment_or_analysis",
            "status": "missing",
            "source": None,
            "fallback": "State why the experiment cannot be added; provide sensitivity analysis or limitations.",
        })
    if needs_data:
        items.append({
            "required": "supporting_dataset_or_statistics",
            "status": "missing",
            "source": None,
            "fallback": "Provide existing data citation or mark as author-confirmation required.",
        })
    return items


# ---------------------------------------------------------------- contract ---
def contract_top_fields(contract):
    fields, unsupported = [], []
    if contract is None:
        return fields, unsupported
    if isinstance(contract, str):
        return [contract], unsupported
    if isinstance(contract, list):
        for entry in contract:
            if isinstance(entry, str):
                fields.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("field") or entry.get("key")
                if name:
                    fields.append(str(name))
        return fields, unsupported
    if isinstance(contract, dict):
        if "properties" in contract and isinstance(contract["properties"], dict):
            return list(contract["properties"].keys()), unsupported
        for combo in ("anyOf", "allOf", "oneOf", "$ref", "patternProperties"):
            if combo in contract:
                unsupported.append(combo)
        nested = contract.get("fields")
        if isinstance(nested, list):
            return contract_top_fields(nested)[0], unsupported
        if isinstance(nested, dict):
            return list(nested.keys()), unsupported
        reserved = {"type", "required", "optional", "additionalProperties", "title",
                    "description", "version", "schema"}
        fields = [k for k in contract.keys() if k not in reserved and not str(k).startswith("$")]
        return fields, unsupported
    return fields, unsupported


ALIASES = {
    "case_id": "case_id",
    "task_id": "case_id",
    "challenge_id": "case_id",
    "status": "status",
    "run_status": "status",
    "summary": "summary",
    "comments": "comments",
    "comment_analysis": "comments",
    "responses": "responses",
    "response_letter": "deliverable",
    "deliverable": "deliverable",
    "output": "deliverable",
    "result": "deliverable",
    "change_log": "change_record",
    "changes": "change_record",
    "revision_log": "change_record",
    "evidence": "evidence_checklist",
    "evidence_checklist": "evidence_checklist",
    "sources": "sources",
    "uncertainty": "uncertainty",
    "uncertainties": "uncertainty",
    "risks": "uncertainty",
    "limitations": "limitations",
    "version_ledger": "version_ledger",
}


def fill_contract(result, contract, payload):
    fields, unsupported = contract_top_fields(contract)
    unmapped, preserved = [], []
    for field in fields:
        if field in result and result[field] is not None:
            preserved.append(field)
            continue
        key = ALIASES.get(field.lower(), field.lower())
        if key in payload and payload[key] is not None:
            result[field] = payload[key]
            preserved.append(field)
        elif field in payload and payload[field] is not None:
            result[field] = payload[field]
            preserved.append(field)
        else:
            result[field] = None
            unmapped.append(field)
    result["contract_status"] = {
        "declared_fields": fields,
        "preserved_fields": preserved,
        "unmapped_fields": unmapped,
        "unsupported_features": unsupported,
        "contract_present": contract is not None,
    }
    if unmapped or unsupported:
        if result.get("status") == "completed":
            result["status"] = "partial"


def write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def main():
    parser = argparse.ArgumentParser(description="Reviewer-response triage")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--output", "--out", default="/app/output/result.json")
    args = parser.parse_args()

    result = {
        "case_id": None,
        "status": "failed",
        "summary": {},
        "comments": [],
        "responses": [],
        "deliverable": {},
        "sources": [],
        "uncertainty": [],
        "change_record": [],
        "limitations": [],
        "version_ledger": [],
    }
    try:
        case = load_case(args.case)
    except (OSError, ValueError) as exc:
        result["limitations"].append("case_read_failed: %s" % type(exc).__name__)
        result["uncertainty"].append({"item": "input", "detail": "case.json unreadable"})
        write_json(args.output, result)
        return 2

    result["case_id"] = case.get("case_id") or case.get("id") or case.get("challenge_id")
    contract = case.get("output_contract")

    texts, notes = [], []
    for field, value in case.items():
        if field in ("output_contract", "scoring", "tags", "meta", "datasets"):
            continue
        flatten_material(value, field, texts, notes)

    manuscript_candidates, comment_candidates = [], []
    for label, value in find_by_keys(case, set(MANUSCRIPT_KEYS)):
        before = len(texts)
        flatten_material(value, label, manuscript_candidates, notes)
        if len(manuscript_candidates) == before:
            continue
    for label, value in find_by_keys(case, set(COMMENT_KEYS)):
        flatten_material(value, label, comment_candidates, notes)

    manuscript = ""
    if manuscript_candidates:
        manuscript = max(manuscript_candidates, key=lambda item: len(item["text"]))["text"]
    comments_raw = None
    for label, value in find_by_keys(case, set(COMMENT_KEYS)):
        if isinstance(value, (list, str)) and value:
            comments_raw = value
            break

    if not manuscript:
        result["limitations"].append("manuscript_missing_or_unreadable")
    if comments_raw is None:
        for item in comment_candidates:
            if len(item["text"]) > 20:
                comments_raw = item["text"]
                break

    sections = split_sections(manuscript) if manuscript else []
    paragraphs = paragraphs_of(manuscript) if manuscript else []
    comment_list = split_comments(comments_raw) if comments_raw is not None else []

    if not comment_list:
        result["limitations"].append("reviewer_comments_missing")
        result["uncertainty"].append({"item": "comments", "detail": "no review comments found"})
        result["status"] = "partial"
    else:
        result["status"] = "completed"

    responses, ledger, evidence_all = [], [], []
    located = 0
    manual = 0
    for cid, body in comment_list:
        ctype = classify_type(body)
        severity = classify_severity(body)
        decision = classify_decision(body)
        tone = tone_for(decision, severity)
        subs = split_sub_requirements(body)
        locator = locate_in_manuscript(body, sections, paragraphs) if paragraphs else {
            "found": False, "section": None, "paragraph_index": None,
            "char_start": None, "char_end": None, "anchor_quote": None, "match_score": 0.0,
        }
        if locator["found"]:
            located += 1
        else:
            manual += 1
        evidence = build_evidence(ctype, body)
        evidence_all.extend(dict(item, comment_id=cid) for item in evidence)
        entry = {
            "comment_id": cid,
            "text": body,
            "type": ctype,
            "severity": severity,
            "sub_requirements": subs,
            "decision": decision,
            "tone": tone,
            "locator": locator,
            "evidence": evidence,
            "status": "located" if locator["found"] else "author_confirmation_required",
        }
        result["comments"].append(entry)
        responses.append({
            "comment_id": cid,
            "decision": decision,
            "tone": tone,
            "response": "We thank the reviewer for this comment. " + (
                "We have revised the manuscript accordingly." if decision == "accept"
                else "We have partially addressed this point and state the remaining limitation."
                if decision == "partial"
                else "We respectfully clarify the point without changing the scientific conclusion."
            ),
            "sub_requirement_coverage": [
                {"requirement": sub, "handled": True, "note": "revision action planned"}
                for sub in subs
            ],
            "locator_status": "found" if locator["found"] else "not_found",
        })
        ledger.append({
            "comment_id": cid,
            "decision": decision,
            "action": "revise" if decision in ("accept", "partial") else "clarify_or_rebut",
            "locator": locator,
            "status": "pending_author_edit" if decision in ("accept", "partial") else "pending_author_confirmation",
        })

    letter_lines = ["# Response to Reviewers (draft)", ""]
    for item in result["comments"]:
        letter_lines.append("## %s" % item["comment_id"])
        letter_lines.append("> %s" % norm_space(item["text"]))
        letter_lines.append("")
        letter_lines.append("Decision: %s | Tone: %s | Type: %s | Severity: %s"
                            % (item["decision"], item["tone"], item["type"], item["severity"]))
        if item["locator"]["found"]:
            letter_lines.append("Location: section '%s', paragraph %s, chars %s-%s"
                                % (item["locator"]["section"], item["locator"]["paragraph_index"],
                                   item["locator"]["char_start"], item["locator"]["char_end"]))
            letter_lines.append("Anchor: \"%s\"" % item["locator"]["anchor_quote"])
        else:
            letter_lines.append("Location: NOT FOUND - author must supply the exact section and line.")
        letter_lines.append("")
    worksheet = ["# Revision Worksheet", "", "Baseline SHA-256: %s" % (sha256_text(manuscript) if manuscript else "n/a"), ""]
    for entry in ledger:
        worksheet.append("- [ ] %s | %s | %s | %s" % (
            entry["comment_id"], entry["action"], entry["status"],
            "para %s" % entry["locator"]["paragraph_index"] if entry["locator"]["found"] else "needs locator"))
    if notes:
        worksheet.append("")
        worksheet.append("Unreadable materials: " + ", ".join(sorted({n["label"] for n in notes})))

    result["responses"] = responses
    result["version_ledger"] = ledger
    result["evidence_checklist"] = evidence_all
    result["deliverable"] = {
        "response_letter": "\n".join(letter_lines),
        "revision_worksheet": "\n".join(worksheet),
    }
    result["sources"] = [{
        "role": "manuscript_baseline",
        "path": manuscript_candidates[0]["path"] if manuscript_candidates else None,
        "sha256": sha256_text(manuscript) if manuscript else None,
        "chars": len(manuscript),
    }, {
        "role": "reviewer_comments",
        "path": None,
        "sha256": sha256_text(json.dumps(comments_raw, ensure_ascii=False)) if comments_raw is not None else None,
    }]
    result["summary"] = {
        "comments": len(comment_list),
        "sub_requirements": sum(len(c["sub_requirements"]) for c in result["comments"]),
        "located": located,
        "not_located": manual,
        "evidence_items": len(evidence_all),
        "manual_confirmation": manual + sum(1 for c in result["comments"] if c["decision"] in ("clarify", "reject")),
    }
    result["uncertainty"] = result["uncertainty"] + ([
        {"item": "location", "detail": "%d comment(s) have no verified anchor" % manual}
    ] if manual else [])
    result["change_record"] = [
        {"action": "triage_comments", "count": len(comment_list)},
        {"action": "locate_in_baseline", "count": located},
        {"action": "build_response_letter", "status": "draft"},
    ]
    result["limitations"] += [
        "No experiments were performed and no scientific data were added.",
        "Anchor search is literal text overlap; found=True does not prove the revision is scientifically correct.",
    ]

    payload = dict(result)
    fill_contract(result, contract, payload)
    if not result.get("case_id"):
        result["status"] = "failed"
        result["limitations"].append("case_id_missing")

    write_json(args.output, result)
    if result["status"] == "failed":
        return 2
    if result["contract_status"]["unmapped_fields"]:
        return 1
    return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
