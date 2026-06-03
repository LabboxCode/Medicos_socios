#!/usr/bin/env python3
"""
Labbox Dashboard — Actualizador diario
Usa linked_items para ligar cada paciente del Diario a su médico en Plan Doctores.
"""

import os, re, json, requests
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

# ── Config ─────────────────────────────────────────────────────────────────────
API_KEY      = os.environ["MONDAY_API_KEY"]
BOARD_2023   = 1587343035
BOARD_2024   = 5691055183
BOARD_2025   = 8104979141
BOARD_2026   = 18391336187
BOARD_PLANES = 4078088806
HEADERS      = {"Authorization": API_KEY, "Content-Type": "application/json", "API-Version": "2023-10"}
ENDPOINT     = "https://api.monday.com/v2"
HTML_FILE    = "index.html"

MN    = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
MN_ES = {m: i+1 for i, m in enumerate(MN)}
NON_SOCIO_SOURCES = {'ventas', 'paciente', ''}

# ── Helpers ────────────────────────────────────────────────────────────────────

def gql(query: str) -> dict:
    r = requests.post(ENDPOINT, json={"query": query}, headers=HEADERS, timeout=90)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]

def fmt(d: date) -> str:
    return f"{d.day:02d}/{MN[d.month-1]}/{str(d.year)[2:]}"

def fmt_iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")

def parse_date_str(s: str) -> date:
    if not s or s in ('—', ''):
        return None
    try:
        if '/' in s and len(s) <= 9:
            parts = s.split('/')
            return date(2000 + int(parts[2]), MN_ES[parts[1]], int(parts[0]))
        return date.fromisoformat(s[:10])
    except:
        return None

def parse_cv(item: dict) -> dict:
    return {cv["id"]: (cv["text"] or "") for cv in item.get("column_values", [])}

def load_html(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()

def save_html(path: str, html: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

# ── Fetch Doctor Socio items WITH linked doctor name ───────────────────────────

def get_diario_items(board_id: int, year: int) -> list[dict]:
    """
    Fetch all Doctor Socio items from a Diario board.
    Returns list of {doctor_name, date, status, cost, year, month}.
    Uses linked_items to get the doctor name per patient.
    """
    results = []
    cursor = None
    page_num = 0

    while True:
        cursor_clause = f', cursor: "{cursor}"' if cursor else ""
        q = f"""
        query {{
          boards(ids: [{board_id}]) {{
            items_page(limit: 200{cursor_clause}) {{
              cursor
              items {{
                column_values(ids: ["date", "dup__of_reason", "estado_1", "cost"]) {{
                  id text value
                }}
                linked_items(link_to_item_column_id: "conectar_tableros",
                             linked_board_id: {BOARD_PLANES}) {{
                  name
                }}
              }}
            }}
          }}
        }}
        """
        data = gql(q)
        page = data["boards"][0]["items_page"]
        page_num += 1

        for item in page["items"]:
            # Filter Doctor Socio (index 3)
            reason_raw = next(
                (c.get("value","") for c in item["column_values"] if c["id"] == "dup__of_reason"),
                ""
            )
            try:
                if json.loads(reason_raw or "{}").get("index") != 3:
                    continue
            except:
                continue

            # Get doctor name from linked_items
            linked = item.get("linked_items", [])
            if not linked:
                continue
            doctor_name = linked[0]["name"].strip().lower()

            cv = parse_cv(item)
            date_s  = cv.get("date", "")
            status  = cv.get("estado_1", "")
            cost_s  = cv.get("cost", "0") or "0"

            if not date_s:
                continue
            try:
                item_date = date.fromisoformat(date_s[:10])
            except:
                continue

            rev  = float(re.sub(r"[^\d.]", "", cost_s) or "0")
            canc = rev if "cancel" in status.lower() else 0
            done = rev if status == "Done" else 0

            results.append({
                "doctor":  doctor_name,
                "date":    item_date,
                "year":    item_date.year,
                "month":   item_date.month,
                "ym":      f"{item_date.year}-{item_date.month:02d}",
                "total":   1,
                "done":    1 if status == "Done" else 0,
                "revenue": done,
                "cancelado": canc,
            })

        cursor = page.get("cursor")
        if not cursor:
            break

    print(f"    Board {board_id} ({year}): {len(results)} Doctor Socio items with doctor link")
    return results

# ── Build per-doctor aggregation ───────────────────────────────────────────────

def build_doctor_monthly(all_items: list[dict]) -> dict:
    """
    Returns {doctor_name_lower: {
        monthly: {YYYY-MM: {total,done,revenue,cancelado}},
        last_date: date
    }}
    """
    doctors = defaultdict(lambda: {"monthly": defaultdict(
        lambda: {"total":0,"done":0,"revenue":0,"cancelado":0}
    ), "last_date": None})

    for item in all_items:
        doc  = item["doctor"]
        ym   = item["ym"]
        d    = doctors[doc]
        d["monthly"][ym]["total"]     += item["total"]
        d["monthly"][ym]["done"]      += item["done"]
        d["monthly"][ym]["revenue"]   += item["revenue"]
        d["monthly"][ym]["cancelado"] += item["cancelado"]

        if d["last_date"] is None or item["date"] > d["last_date"]:
            d["last_date"] = item["date"]

    # Convert defaultdicts to plain dicts
    return {k: {"monthly": dict(v["monthly"]), "last_date": v["last_date"]}
            for k, v in doctors.items()}

# ── Update doctor rows in HTML ─────────────────────────────────────────────────

def update_doctor_rows(html: str, doctor_stats: dict, today: date) -> str:
    """
    For each doctor row, REPLACE (not accumulate) data-monthly, data-ld2026,
    data-myr2026, data-ld2025, data-myr2025, etc. with fresh data.
    """
    updated = 0

    def patch_row(m):
        nonlocal updated
        row = m.group(0)
        nm  = re.search(r'data-name="([^"]+)"', row)
        if not nm:
            return row

        name_key = nm.group(1).lower().strip()
        if name_key not in doctor_stats:
            return row

        stats = doctor_stats[name_key]
        monthly_all = stats["monthly"]  # {YYYY-MM: {...}}
        last_date   = stats["last_date"]

        # Split by year
        def year_data(yr):
            d = {ym: v for ym, v in monthly_all.items() if ym.startswith(str(yr))}
            if d:
                all_agg = {"total":0,"done":0,"revenue":0,"cancelado":0}
                for v in d.values():
                    for k in all_agg:
                        all_agg[k] += v.get(k,0)
                d["all"] = all_agg
            return d

        m2026 = year_data(2026)
        m2025 = year_data(2025)
        m2024 = year_data(2024)
        m2023 = year_data(2023)

        # Build data-monthly (all years combined, keyed by YYYY-MM + "all")
        monthly_combined = {ym: v for ym, v in monthly_all.items()}
        all_agg = {"total":0,"done":0,"revenue":0,"cancelado":0}
        for v in monthly_all.values():
            for k in all_agg:
                all_agg[k] += v.get(k,0)
        monthly_combined["all"] = all_agg

        # Replace attributes
        row = re.sub(r"data-myr2026='[^']*'",
                     f"data-myr2026='{json.dumps(m2026, separators=(',',':'))}'", row)
        row = re.sub(r"data-myr2025='[^']*'",
                     f"data-myr2025='{json.dumps(m2025, separators=(',',':'))}'", row)
        row = re.sub(r"data-myr2024='[^']*'",
                     f"data-myr2024='{json.dumps(m2024, separators=(',',':'))}'", row)
        row = re.sub(r"data-myr2023='[^']*'",
                     f"data-myr2023='{json.dumps(m2023, separators=(',',':'))}'", row)
        row = re.sub(r"data-monthly='[^']+'",
                     f"data-monthly='{json.dumps(monthly_combined, separators=(',',':'))}'", row)

        # Update last date per year
        if last_date:
            last_str = fmt_iso(last_date)
            if last_date.year == 2026:
                row = re.sub(r'data-ld2026="[^"]*"', f'data-ld2026="{last_str}"', row)
            elif last_date.year == 2025:
                row = re.sub(r'data-ld2025="[^"]*"', f'data-ld2025="{last_str}"', row)

        updated += 1
        return row

    html = re.sub(r'<tr class="doctor-row"[^>]+>', patch_row, html)
    print(f"  Doctor rows updated: {updated}")
    return html

# ── Update resumen (m26DATA, r26DATA) ──────────────────────────────────────────

def update_resumen(html: str, items_2026: list[dict], today: date) -> str:
    """Replace m26DATA and r26DATA with fresh counts from all 2026 Doctor Socio."""
    monthly_total = [0]*12
    monthly_rev   = [0]*12

    for item in items_2026:
        monthly_total[item["month"] - 1] += 1
        monthly_rev[item["month"] - 1]   += item["revenue"]

    scripts = list(re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL))
    for sc in scripts:
        c = sc.group(1)
        m26_m = re.search(r'var m26DATA=\[([^\]]+)\]', c)
        r26_m = re.search(r'var r26DATA=\[([^\]]+)\]', c)
        if m26_m and r26_m:
            new_c = c.replace(
                m26_m.group(0),
                f"var m26DATA=[{', '.join(str(x) for x in monthly_total)}]"
            ).replace(
                r26_m.group(0),
                f"var r26DATA=[{', '.join(str(int(x)) for x in monthly_rev)}]"
            )
            html = html.replace(c, new_c, 1)
            print(f"  Resumen: {sum(monthly_total)} total pax 2026")
            break
    return html

# ── Update timestamp ───────────────────────────────────────────────────────────

def update_timestamp(html: str, today: date) -> str:
    ts = f"Actualizado {fmt(today)}"
    html = re.sub(r'Actualizado \d{2}/[A-Z][a-z]{2}/\d{2}', ts, html)
    print(f"  Timestamp: {ts}")
    return html

# ── Add new socios ─────────────────────────────────────────────────────────────

def get_all_socios() -> list[dict]:
    items = []
    cursor = None
    while True:
        cursor_clause = f', cursor: "{cursor}"' if cursor else ""
        q = f"""
        query {{
          boards(ids: [{BOARD_PLANES}]) {{
            groups(ids: ["grupo_nuevo"]) {{
              items_page(limit: 200{cursor_clause}) {{
                cursor
                items {{
                  id name created_at
                  column_values(ids: ["estado8","status5","fecha_Mjj4f1Wb","numeric_mm3m26jh"]) {{
                    id text
                  }}
                }}
              }}
            }}
          }}
        }}
        """
        data = gql(q)
        page = data["boards"][0]["groups"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
    return items

def add_new_socios(html: str, socios: list[dict]) -> str:
    existing = set()
    for m in re.finditer(r'data-name="([^"]+)"', html):
        existing.add(m.group(1).lower().strip())

    added = 0
    for item in socios:
        cv     = parse_cv(item)
        source = (cv.get("estado8") or "").strip().lower()
        if source in NON_SOCIO_SOURCES:
            continue

        name     = item["name"].strip()
        name_key = name.lower().strip()
        if name_key in existing:
            continue

        ingreso    = item.get("created_at","")[:10] or str(date.today())
        ing_yr     = ingreso[:4]
        ing_fmt    = fmt(date.fromisoformat(ingreso))
        speciality = cv.get("status5") or ""
        rec_fecha  = cv.get("fecha_Mjj4f1Wb") or ""
        rec_disp   = fmt(date.fromisoformat(rec_fecha)) if rec_fecha else "—"

        new_row = (
            f'<tr class="doctor-row" data-ingreso="{ingreso}" data-joinyear="{ing_yr}" '
            f'data-myr2023=\'{{}}\' data-myr2024=\'{{}}\' data-myr2025=\'{{}}\' '
            f'data-monthly=\'{{"all":{{"total":0,"done":0,"revenue":0,"cancelado":0}}}}\' '
            f'data-name="{name_key}" data-reflejo="{source}" '
            f'data-rec-fecha="{rec_fecha}" data-rec-spec="{speciality}" '
            f'data-rec-restantes="0" data-rec-meses="0.0" data-ld2025="" data-ld2026="">\n'
            f'<td style="padding:8px 10px"><div style="font-weight:600;font-size:13px;color:#e2e8f0">{name}</div>'
            f'<div style="font-size:11px;color:#475569">{speciality}</div></td>'
            f'<td style="padding:8px 10px;text-align:center">'
            f'<span style="background:#1e293b;color:#475569;font-size:11px;padding:4px 10px;border-radius:12px">⬜ Sin ritmo</span></td>'
            f'<td style="padding:8px 10px;text-align:center"><span class="cell-num" style="font-size:20px;font-weight:800;color:#e2e8f0">—</span></td>'
            f'<td style="padding:8px 10px;text-align:right"><span class="cell-rev" style="font-size:14px;font-weight:700;color:#22c55e">—</span></td>'
            f'<td style="padding:8px 10px;text-align:right"><span class="cell-canc" style="font-size:13px;font-weight:600;color:#ef4444">—</span></td>'
            f'<td style="padding:8px 10px;text-align:center"><span style="font-size:12px;color:#94a3b8">—</span></td>'
            f'<td style="padding:8px 10px;text-align:center"><span style="font-size:12px;color:#94a3b8">—</span></td>'
            f'<td style="padding:8px 10px;text-align:center"><span style="color:#94a3b8;font-size:12px">{rec_disp}</span></td>'
            f'<td style="padding:8px 10px;text-align:center"><span style="color:#64748b;font-size:11px">Sin datos</span></td>'
            f'<td style="padding:8px 10px;text-align:center"><span style="font-size:12px;color:#475569">—</span></td>'
            f'<td style="padding:8px 10px;text-align:center"><span style="font-size:11px;color:#475569">{ing_fmt}</span></td>'
            f'</tr>'
        )

        tbody_end = html.rfind('</tbody>')
        if tbody_end > 0:
            html = html[:tbody_end] + new_row + '\n' + html[tbody_end:]
            existing.add(name_key)
            added += 1
            print(f"  + New socio: {name}")

    print(f"  New socios added: {added}")
    return html

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    today = date.today()
    print(f"=== Labbox Dashboard Update — {today} ===\n")

    html = load_html(HTML_FILE)
    print(f"Loaded {HTML_FILE} ({len(html):,} chars)\n")

    # Fetch all Doctor Socio items from all years
    print("[1/3] Fetching Doctor Socio items from all Diario boards...")
    all_items = []
    for board_id, year in [(BOARD_2023,2023),(BOARD_2024,2024),(BOARD_2025,2025),(BOARD_2026,2026)]:
        items = get_diario_items(board_id, year)
        all_items.extend(items)
    print(f"  Total: {len(all_items)} items across all years\n")

    print("[2/3] Fetching Plan Doctores (new socios)...")
    socios = get_all_socios()
    print(f"  {len(socios)} doctors in Ganados\n")

    # Build per-doctor stats
    doctor_stats = build_doctor_monthly(all_items)
    print(f"  Doctors with data: {len(doctor_stats)}\n")

    # Apply updates
    print("── Applying updates ──\n")

    print("[A] New socios...")
    html = add_new_socios(html, socios)

    print("\n[B] Doctor rows (monthly data + last dates)...")
    html = update_doctor_rows(html, doctor_stats, today)

    print("\n[C] Resumen general...")
    items_2026 = [i for i in all_items if i["year"] == 2026]
    html = update_resumen(html, items_2026, today)

    print("\n[D] Timestamp...")
    html = update_timestamp(html, today)

    save_html(HTML_FILE, html)
    print(f"\n✅ Done — {HTML_FILE} updated ({len(html):,} chars)")

if __name__ == "__main__":
    main()
