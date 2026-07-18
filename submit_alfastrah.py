#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Подача заявлений на возмещение ДМС в med.alfastrah.ru + учёт номеров.

ПЕРСОНАЛЬНЫХ ДАННЫХ В ЭТОМ ФАЙЛЕ НЕТ. Они берутся из ~/.alfastrah_profile.json
(см. alfastrah_profile.template.json). Сессия — из ~/.alfastrah_cookies.

Поток (2 запроса, JSON, авторизация только куками, CSRF-заголовок НЕ нужен):
  POST /api/policy/dms/{policy}/claim       -> создаёт заявление, возвращает data.document.id (= № заявления)
  PUT  /api/policy/dms/{policy}/claim/{id}  -> финализирует; files[] = [{name, body(сырой base64)}]
accessToken (кука, JWT) живёт ~5 мин и не продлевается — снимай свежие куки перед запуском.

Использование:
  python3 submit_alfastrah.py <claims.json> [--dry-run]

claims.json — список заявлений к подаче:
  [{"folder": "<имя папки в ~/Downloads/chaika>",
    "date": "ГГГГ-ММ-ДД",            # дата страхового события
    "description": "Консультация ...",
    "sum": 1500,                      # руб.
    "reason": "<опц., по умолчанию profile.defaults.reason>",
    "doctor": "<опц., для таблицы>",
    "visit":  "<опц., для таблицы>",
    "files":  ["Протокол приёма.pdf", {"path": "<UUID>.pdf", "name": "Кассовый чек.pdf"}],
                                      # опц.; если нет — автоподбор; {path,name} = грузить path под именем name
    "insured": {"insuredName": "...", "birthdate": "ГГГГ-ММ-ДД", "policyNumber": "..."}
                                      # опц.; подача за родственника (claimOption RELATIVE):
                                      # его данные в claimInfo, паспорт/банк/контакты — из профиля.
                                      # Без блока — profile.insured (подача за себя).
  }]
Скан паспорта/полиса (profile.passportScanPath — строка или СПИСОК путей; за родственника
клади оба паспорта) прикладывается ко всем заявлениям автоматически.
"""
import json, base64, os, re, sys, csv, time, datetime, urllib.request, urllib.error

HOME = os.path.expanduser("~")
PROFILE_FILE = f"{HOME}/.alfastrah_profile.json"
COOKIES_FILE = f"{HOME}/.alfastrah_cookies"
CHAIKA = f"{HOME}/Downloads/chaika"
TABLE  = f"{CHAIKA}/Заявления.csv"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
AUTO_DOCS = ["Протокол приёма.pdf", "Акт оказанных услуг.pdf", "Кассовый чек.pdf"]

def load_profile():
    if not os.path.exists(PROFILE_FILE):
        sys.exit(f"!! нет {PROFILE_FILE} — создай из alfastrah_profile.template.json (chmod 600)")
    return json.load(open(PROFILE_FILE))

def read_cookies():
    if not os.path.exists(COOKIES_FILE):
        sys.exit(f"!! нет {COOKIES_FILE} — сними свежий cURL с med.alfastrah.ru")
    return open(COOKIES_FILE).read().strip()

def token_seconds_left(cookies):
    tok = next((c.split("=",1)[1] for c in cookies.split("; ") if c.startswith("accessToken=")), None)
    if not tok: return None
    pl = tok.split(".")[1]; pl += "=" * (-len(pl) % 4)
    return int(json.loads(base64.urlsafe_b64decode(pl))["exp"] - time.time())

def b64file(path):
    with open(path, "rb") as f: return base64.b64encode(f.read()).decode()

def api_base(prof):
    return f"https://med.alfastrah.ru/api/policy/dms/{prof['policy']['number']}/claim"

def referer(prof):
    n = prof["policy"]["number"]
    return f"https://med.alfastrah.ru/policies/dms/{n}/return-dms?policyId={n}&step=application"

def http(method, url, cookies, prof, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/json, text/plain, */*", "Content-Type": "application/json",
        "Origin": "https://med.alfastrah.ru", "Referer": referer(prof), "User-Agent": UA, "Cookie": cookies})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode("utf-8", "replace")[:500]}

def claim_insured(prof, c):
    """Застрахованный по этому заявлению: c["insured"] (подача за родственника,
    портальная опция RELATIVE) или profile.insured. Паспорт/банк/контакты в payload
    всегда остаются заявителя из профиля."""
    return c.get("insured") or prof["insured"]

def passport_scans(prof):
    p = prof["passportScanPath"]
    return [os.path.expanduser(x) for x in (p if isinstance(p, list) else [p])]

def file_entry(f):
    """Элемент files: строка (имя в папке визита) или {"path", "name"} — грузим path под именем name."""
    return (f["path"], f["name"]) if isinstance(f, dict) else (f, f)

def base_payload(prof, c):
    ins = claim_insured(prof, c)
    info = {"insuredName": ins["insuredName"], "birthdate": ins["birthdate"],
            "policyNumber": ins.get("policyNumber") or prof["policy"]["number"], "country": ins.get("country", "РФ"),
            "date": c["date"], "description": c["description"],
            "reason": c.get("reason") or prof["defaults"]["reason"], "otherReason": "",
            "sum": c["sum"], "currency": prof["defaults"].get("currency", "RUR"), "otherCurrency": ""}
    return {"email": prof["email"], "phone": prof["phone"], "passport": prof["passport"],
            "bank": prof["bank"], "documents": prof["documents"], "claimInfo": info}

def files_payload(prof, c):
    fdir = os.path.join(CHAIKA, c["folder"])
    files = []
    for f in c["_files"]:
        path, name = file_entry(f)
        files.append({"name": name, "body": b64file(os.path.join(fdir, path))})
    for scan in passport_scans(prof):
        files.append({"name": os.path.basename(scan), "body": b64file(scan)})
    return files

def resolve_files(prof, c):
    fdir = os.path.join(CHAIKA, c["folder"])
    want = c.get("files") or [fn for fn in AUTO_DOCS if os.path.exists(os.path.join(fdir, fn))]
    miss = [file_entry(f)[0] for f in want if not os.path.exists(os.path.join(fdir, file_entry(f)[0]))]
    for scan in passport_scans(prof):
        if not os.path.exists(scan): miss.append(f"скан паспорта: {scan}")
    c["_files"] = want
    return miss

def already_submitted(folder):
    d = os.path.join(CHAIKA, folder)
    return os.path.isdir(d) and any(fn.startswith("Заявление №") and fn.endswith(".txt") for fn in os.listdir(d))

def record_marker(prof, folder, no, c, status_msg):
    today = datetime.date.today().isoformat()
    reason = (c.get("reason") or prof["defaults"]["reason"]).split("/")[0]
    ins = claim_insured(prof, c)
    txt = (f"Заявление № {no}\n"
           f"Полис: {ins.get('policyNumber') or prof['policy']['number']} (ДМС, АльфаСтрахование)\n"
           f"Застрахованный: {ins['insuredName']}\n"
           f"Дата страхового события: {c['date']}\n"
           f"Причина: {reason}\n"
           f"Описание: {c['description']}\n"
           f"Сумма к возмещению: {c['sum']} ₽\n"
           f"Дата подачи: {today}\n"
           f"Статус: {status_msg}\n"
           f"Документы: {', '.join([file_entry(f)[1] for f in c['_files']] + ['скан паспорта/полиса'])}\n")
    open(os.path.join(CHAIKA, folder, f"Заявление №{no}.txt"), "w").write(txt)

def append_table(c, no, status_msg):
    today = datetime.date.today().isoformat()
    new = not os.path.exists(TABLE)
    with open(TABLE, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["Дата события","Визит","Врач","Сумма ₽","№ заявления","Дата подачи","Статус","Папка"])
        w.writerow([c["date"], c.get("visit",""), c.get("doctor",""), c["sum"], no, today, status_msg, c["folder"]])

def fetch_claims(prof, cookies):
    """Список заявлений возмещения с портала (statusText — живой статус)."""
    url = (f"https://med.alfastrah.ru/api/policy/dms/{prof['policy']['claimsListId']}"
           f"/claims?count=ALL&type=REFUND")
    req = urllib.request.Request(url, headers={
        "Accept": "application/json, text/plain, */*", "User-Agent": UA,
        "Referer": "https://med.alfastrah.ru/policies", "Cookie": cookies})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8")).get("data", [])

def sync_table(prof, cookies):
    """Обновить статусы в Заявления.csv до актуальных с портала (Отправлено→На рассмотрении→Оплачено)."""
    if (token_seconds_left(cookies) or 0) < 5:
        sys.exit("!! токен протух — сними свежие куки в ~/.alfastrah_cookies")
    by_no = {}
    for c in fetch_claims(prof, cookies):
        m = re.search(r"№\s*(\d+)", c.get("title", ""))
        if m: by_no[m.group(1)] = c.get("statusText", "")
    if not os.path.exists(TABLE): sys.exit(f"нет таблицы {TABLE}")
    rows = list(csv.reader(open(TABLE))); h = rows[0]
    ni, si = h.index("№ заявления"), h.index("Статус")
    for r in rows[1:]:
        if r[ni] in by_no: r[si] = by_no[r[ni]]
    with open(TABLE, "w", newline="") as f: csv.writer(f).writerows(rows)
    print("=== Таблица заявлений (live-статус) ===")
    for r in rows: print(" | ".join(map(str, r)))

def main():
    if "--sync" in sys.argv:
        sync_table(load_profile(), read_cookies()); return
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args: sys.exit("использование: submit_alfastrah.py <claims.json> [--dry-run] | --sync")
    claims = json.load(open(args[0]))
    prof = load_profile()
    print(f"{'=== DRY RUN ===' if dry else '=== ПОДАЧА ==='}\n")
    if not dry:
        cookies = read_cookies()
        left = token_seconds_left(cookies)
        print(f"accessToken: осталось ~{left} сек")
        if left is None or left < 30:
            sys.exit("!! Токен протух/почти истёк. Сними свежие куки в ~/.alfastrah_cookies.")
    for c in claims:
        folder = c["folder"]
        print(f"--- {folder} | {c['sum']} ₽ | событие {c['date']} ---")
        miss = resolve_files(prof, c)
        if miss: print("  !! нет файлов:", miss); continue
        if already_submitted(folder):
            print("  !! в папке уже есть 'Заявление №*.txt' — пропускаю (защита от дубля)."); continue
        bp = base_payload(prof, c)
        print("  claimInfo:", json.dumps(bp["claimInfo"], ensure_ascii=False))
        print("  файлы:", [file_entry(f)[1] for f in c["_files"]] + [os.path.basename(s) for s in passport_scans(prof)])
        if dry: print("  (dry-run: не отправляю)\n"); continue
        st, resp = http("POST", api_base(prof), cookies, prof, bp)
        if st != 200 or resp.get("status") != "success":
            print("  !! POST не удался:", st, str(resp)[:300]); continue
        no = resp["data"]["document"]["id"]
        print(f"  POST ok -> Заявление №{no}")
        bp["files"] = files_payload(prof, c)
        st2, resp2 = http("PUT", f"{api_base(prof)}/{no}", cookies, prof, bp)
        if st2 != 200 or resp2.get("status") != "success":
            print(f"  !! PUT не удался: {st2} {str(resp2)[:300]} (№{no} создано, не финализировано)"); continue
        print(f"  PUT ok -> {resp2['data'].get('message', '')}")
        record_marker(prof, folder, no, c, "Отправлено")  # канонический статус на момент подачи
        append_table(c, no, "Отправлено")
        print(f"  записал: Заявление №{no}.txt + строка в {os.path.basename(TABLE)}\n")
    if os.path.exists(TABLE):
        print("=== Таблица заявлений ===")
        for row in csv.reader(open(TABLE)): print(" | ".join(map(str, row)))

if __name__ == "__main__":
    main()
