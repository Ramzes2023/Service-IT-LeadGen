"""Read-only local audit of stored AI hypotheses; no company details printed."""
import json
import sqlite3
import sys
from pathlib import Path

def audit_db(db: Path):
    with sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True) as c:
        rows = c.execute("""SELECT q.ai_suggestion,s.evidence
                          FROM qualifications q LEFT JOIN signals s
                          ON s.id=json_extract(q.explanation,'$.signal_id')
                          WHERE q.ai_suggestion!=''""").fetchall()
    total, complete, review = len(rows), 0, 0
    for saved, evidence in rows:
        try:
            r = json.loads(saved)
            reason = []
            if not r.get('possible_need', '').strip(): reason.append('missing_possible_need')
            if r.get('service') == 'Требуется проверка': reason.append('model_abstained')
            elif r.get('confidence', 0) == 0: reason.append('zero_confidence')
            quote = r.get('evidence_quote', '')
            if r.get('service') != 'Требуется проверка' and (not quote or quote not in (evidence or '')):
                reason.append('missing_verified_evidence')
            if reason: review += 1
            else: complete += 1
        except (ValueError, KeyError, AttributeError, TypeError):
            review += 1
    return {'saved_ai_hypotheses': total, 'usable_hypotheses': complete,
            'needs_review': review, 'crm_writes': 'NO', 'network_calls': 'NO'}

if __name__ == '__main__':
    print('AI_LOCAL_AUDIT=' + json.dumps(audit_db(Path(sys.argv[1])), ensure_ascii=False))
