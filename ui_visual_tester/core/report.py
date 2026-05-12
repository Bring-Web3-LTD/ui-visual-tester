import json
import re
from datetime import datetime
from config import REPORTS_DIR


def generate_report(results: list):
    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS_DIR / f"report_{timestamp}.json"

    report = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platforms": {},
    }

    for r in results:
        cr = r["ai_result"]
        raw_name = r["name"]
        platform_name = raw_name.split("_", 1)[1] if "_" in raw_name else raw_name

        if r.get("mode") == "responsive" and isinstance(cr, str):
            # Responsive: parse plain-text AI response
            lines = cr.strip().splitlines()
            pass_items = []
            fail_items = []
            severity_counts = {"high": 0, "medium": 0, "low": 0}
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.upper().startswith("OVERALL"):
                    continue
                if ": PASS" in stripped:
                    pass_items.append(stripped)
                elif ": FAIL" in stripped:
                    sev = "medium"  # default
                    low_line = stripped.lower()
                    if "(high)" in low_line:
                        sev = "high"
                    elif "(low)" in low_line:
                        sev = "low"
                    elif "(medium)" in low_line:
                        sev = "medium"
                    severity_counts[sev] += 1
                    fail_items.append({"issue": stripped, "severity": sev})

            overall_match = re.search(r"OVERALL:\s*(PASS|FAIL)", cr, re.IGNORECASE)
            verdict = overall_match.group(1).upper() if overall_match else "FAIL"
            total = len(pass_items) + len(fail_items)

            platform_entry = {
                "verdict": verdict,
                "total_checks": total,
                "passed": len(pass_items),
                "failed": len(fail_items),
                "severity": severity_counts,
                "PASS": pass_items,
                "FAIL": fail_items,
            }

            ds = r.get("diff_stats")
            if ds:
                platform_entry["pixel_diff"] = {
                    "mismatch_pct": ds.get("pct"),
                    "resolution": f"{ds['size'][0]}x{ds['size'][1]}" if ds.get("size") else None,
                }
        else:
            # Style: structured dict with checks / summary
            checks = cr.get("checks", []) if isinstance(cr, dict) else []
            summary = cr.get("summary", {}) if isinstance(cr, dict) else {}
            visual_issues = cr.get("visual_issues", []) if isinstance(cr, dict) else []

            passed_checks = [c for c in checks if c.get("status") == "PASS"]
            failed_checks = [c for c in checks if c.get("status") == "FAIL"]

            pass_list = []
            for c in passed_checks:
                pass_list.append(f"[{c.get('state', '?')}] {c.get('element', '')} > {c.get('property', '')}: {c.get('dom', '')}")

            fail_list = []
            severity_counts = {"high": 0, "medium": 0, "low": 0}
            for c in failed_checks:
                sev = c.get("severity", "medium")
                severity_counts[sev] = severity_counts.get(sev, 0) + 1
                fail_list.append({
                    "state": c.get("state", "?"),
                    "element": c.get("element", ""),
                    "property": c.get("property", ""),
                    "expected (figma)": c.get("figma", ""),
                    "actual (dom)": c.get("dom", ""),
                    "severity": sev,
                })

            platform_entry = {
                "verdict": summary.get("verdict", "FAIL"),
                "total_checks": summary.get("total", len(checks)),
                "passed": summary.get("passed", len(passed_checks)),
                "failed": summary.get("failed", len(failed_checks)),
                "severity": severity_counts,
                "PASS": pass_list,
                "FAIL": fail_list,
            }

            if visual_issues:
                platform_entry["visual_issues"] = visual_issues

        report["platforms"][platform_name] = platform_entry

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON report: {json_path}")

    return json_path