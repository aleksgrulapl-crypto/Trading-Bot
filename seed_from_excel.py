# seed_from_excel.py

from trade_log import load_raw_log, save_log
from excel_import import load_excel_trades


def main():
    log = load_raw_log()
    excel_trades = load_excel_trades()

    existing_ids = {e.get("dealId") for e in log}
    added = 0

    for t in excel_trades:
        if t.get("dealId") in existing_ids:
            continue
        log.append(t)
        added += 1

    save_log(log)
    print(f"Seeded {added} trades from Excel into trade_log.json")


if __name__ == "__main__":
    main()
