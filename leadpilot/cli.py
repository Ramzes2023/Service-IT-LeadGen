import argparse
import csv
import json
import os
from pathlib import Path
from .core import initialize, ingest, qualify, get_leads, metrics, suppress, save_ai_analysis
from .bitrix import export_preview
from .finance import sample_finance
from .ai import classify_authorized_signal, config_from_env, AIProvider
from .sources import scan, import_scan, staged_candidates
from .bitrix_readonly import readonly_preflight


def run():
    parser = argparse.ArgumentParser(description="Сервис ИТ — offline LeadGen MVP")
    parser.add_argument("--db", default=os.getenv("LEADPILOT_DB", "data/leads.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("seed")
    command = sub.add_parser("import-csv"); command.add_argument("path")
    command = sub.add_parser("qualify"); command.add_argument("--today", default=None)
    sub.add_parser("queue")
    sub.add_parser("report")
    command = sub.add_parser("suppress"); command.add_argument("inn"); command.add_argument("channel"); command.add_argument("reason")
    command = sub.add_parser("crm-preview"); command.add_argument("--output", default="exports/bitrix_preview.json")
    sub.add_parser("finance-example")
    command = sub.add_parser("ai-preview"); command.add_argument("text")
    sub.add_parser("ai-config-check")
    source = sub.add_parser("source-scan")
    source.add_argument("--region", choices=["77", "50", "06"], default="77")
    source.add_argument("--term", choices=["1С", "системный администратор", "IT-инженер"], default="1С")
    source.add_argument("--limit", type=int, default=15)
    source.add_argument("--offset", type=int, default=0)
    source.add_argument("--import-to-local", action="store_true")
    source.add_argument("--output", default="exports/trudvsem_probe.json")
    sub.add_parser("source-candidates")
    sub.add_parser("crm-readonly-preflight")
    command = sub.add_parser("ai-qualify"); command.add_argument("--max", type=int, default=3)
    command.add_argument("--today", default=None)
    command.add_argument("--include-public-vacancy", action="store_true",
                         help="Send only the public job title to AI, not company or contacts")
    args = parser.parse_args()
    initialize(args.db)
    if args.command == "init":
        print("DB_INITIALIZED=" + args.db)
    elif args.command in ("seed", "import-csv"):
        file = Path("samples/demo_companies.csv") if args.command == "seed" else Path(args.path)
        count = 0
        with file.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                row["existing_customer"] = str(row.get("existing_customer", "")).lower() in ("1", "true", "yes")
                print("INGEST=" + json.dumps(ingest(args.db, row), ensure_ascii=False))
                count += 1
        print(f"IMPORTED_ROWS={count}")
    elif args.command == "qualify":
        print(json.dumps(qualify(args.db, today=args.today), ensure_ascii=False, indent=2))
    elif args.command == "queue":
        for row in get_leads(args.db, only_queue=True):
            print(f'{row["score"]:>3} | {row["name"]} | {row["recommended_service"]} | {row["priority"]}')
    elif args.command == "report":
        print(json.dumps(metrics(args.db), ensure_ascii=False, indent=2))
    elif args.command == "suppress":
        suppress(args.db, args.inn, args.channel, args.reason)
        qualify(args.db)
        print("SUPPRESSION_APPLIED=YES")
    elif args.command == "crm-preview":
        print("PREVIEW_ONLY_NOT_SENT=" + str(export_preview(args.db, args.output)))
        print("FILE=" + args.output)
    elif args.command == "finance-example":
        print(json.dumps(sample_finance(), ensure_ascii=False, indent=2))
    elif args.command == "ai-preview":
        print(json.dumps(classify_authorized_signal(args.text), ensure_ascii=False, indent=2))
    elif args.command == "ai-config-check":
        try:
            cfg = config_from_env()
            print(f"AI_CONFIG=READY; PROVIDER={cfg.provider}; MODEL={cfg.model}; KEY_PRESENT=YES")
        except RuntimeError as exc:
            print("AI_CONFIG=NOT_READY; " + str(exc))
    elif args.command == "source-scan":
        # No contacts, CRM writes or messages. Public business data only.
        try:
            outcome = scan(region=args.region, term=args.term, limit=args.limit, offset=args.offset)
        except Exception as exc:
            print("SOURCE_STATUS=UNAVAILABLE; NO_REAL_RECORDS_CLAIMED; " + type(exc).__name__)
            raise SystemExit(2)
        path = Path(args.output).resolve()
        if (not path.is_relative_to(Path("exports").resolve())
                or path.suffix.lower() != ".json"):
            raise ValueError("Source scan output must be JSON inside exports/")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as out:
            json.dump(outcome, out, ensure_ascii=False, indent=2)
        print(f'SOURCE_STATUS=RESPONSE_OK; FETCHED={outcome["fetched"]}; SIGNALS={outcome["valid_signals"]}; WITH_INN={outcome["with_inn"]}')
        print('OUTPUT=' + str(path))
        if args.import_to_local:
            print('LOCAL_IMPORT=' + json.dumps(import_scan(args.db, outcome), ensure_ascii=False))
        else:
            print('SOURCE_MODE=PREVIEW_ONLY; IMPORT_NOT_PERFORMED')
    elif args.command == "source-candidates":
        print(json.dumps(staged_candidates(args.db), ensure_ascii=False, indent=2))
    elif args.command == "crm-readonly-preflight":
        print(json.dumps(readonly_preflight(), ensure_ascii=False, indent=2))
    elif args.command == "ai-qualify":
        if args.max < 1 or args.max > 10:
            raise ValueError("--max must be between 1 and 10")
        qualify(args.db, today=args.today)
        leads = get_leads(args.db, only_queue=True)
        # The demo explicitly disallows exporting real lead / contact information
        # to personal external AI. Uses only synthetic fixture signals.
        selected = []
        for lead in leads:
            sig_id = lead["explanation"].get("signal_id")
            signal = next((x for x in lead["signals"] if x["id"] == sig_id), None)
            if not signal:
                continue
            is_synthetic = (signal["source_name"] == "synthetic-fixture" and
                            signal["source_url"].startswith("https://example.invalid/"))
            is_public = (signal["source_name"] == "trudvsem-open-vacancies" and
                          signal["source_url"].startswith(("https://trudvsem.ru/", "http://trudvsem.ru/")))
            if not is_synthetic and not (args.include_public_vacancy and is_public):
                print(f'SKIPPED_NOT_ALLOWED_SOURCE={lead["id"]}')
                continue
            selected.append((lead, signal))
            if len(selected) >= args.max:
                break
        cfg = config_from_env()
        errors = 0
        usable = 0
        needs_review = 0
        with AIProvider(cfg) as provider:
            for lead, signal in selected:
                # Intentionally exclude names, INN, phones, emails, domains and URLs.
                # No company name, INN, phone, email or URL is ever sent.
                input_text = ("Публичный сигнал вакансии для аналитики:\n" + signal["evidence"]
                              + "\nТип сигнала: " + signal["signal_type"])
                try:
                    outcome = provider.classify(input_text)
                    # Store as a reviewable hypothesis, never as an approved fact.
                    save_ai_analysis(args.db, lead["id"], signal["id"], outcome)
                    if outcome["quality_status"] == "complete":
                        usable += 1
                    else:
                        needs_review += 1
                    print(json.dumps({"company_id": lead["id"], "ai": outcome}, ensure_ascii=False))
                except (RuntimeError, ValueError) as exc:
                    errors += 1
                    print(f'AI_FAILED_COMPANY_ID={lead["id"]}; RETRY_LATER=YES; DETAILS={type(exc).__name__}')
        print(f"AI_PROCESSED={len(selected)-errors}; AI_USABLE={usable}; "
              f"AI_NEEDS_REVIEW={needs_review}; AI_FAILED={errors}; "
              "CRM_WRITES=NO; SCORE_UNCHANGED=YES")
        if needs_review:
            print("AI_QUALITY_GATE=REVIEW_REQUIRED; EMPTY_AI_IS_NOT_A_QUALIFIED_LEAD")
        elif usable:
            print("AI_QUALITY_GATE=COMPLETE_HYPOTHESES; HUMAN_REVIEW_STILL_REQUIRED")
        if errors:
            raise SystemExit(2)

if __name__ == "__main__":
    run()
