#!/usr/bin/env python3
"""
Labbox Dashboard — Actualizador diario
Corre cada día. Jala datos de Monday.com y actualiza index.html.
 
Estrategia: usa Plan Doctores como fuente de médicos,
luego busca en cada board Diario por nombre (no por conectar_tableros
que siempre es null).
"""
 
import os, re, json, requests, sys
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
 
# ── Config ────────────────────────────────────────────────────────────────────
API_KEY        = os.environ["MONDAY_API_KEY"]
BOARD_2023     = 1587343035
BOARD_2024     = 5691055183
BOARD_2025     = 8104979141
BOARD_2026     = 18391336187
BOARD_PLANES   = 4078088806
HEADERS        = {"Authorization": API_KEY, "Content-Type": "application/json", "API-Version": "2023-10"}
ENDPOINT       = "https://api.monday.com/v2"
HTML_FILE      = "index.html"
 
MN    = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
MN_ES = {m: i+1 for i, m in enumerate(MN)}
 
NON_SOCIO_SOURCES = {'ventas', 'paciente', ''}
 
# ── Helpers ───────────────────────────────────────────────────────────────────
 
def gql(query: str) -> dict:
    r = requests.post(ENDPOINT, json={"query": query}, headers=HEADERS, timeout=60)
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
 
# ── Fetch Plan Doctores (Ganados) ─────────────────────────────────────────────
 
def get_all_socios() -> list[dict]:
    """Fetch all doctors in Plan Doctores grupo Ganados."""
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
                  column_values(ids: [
                    "estado8", "status5", "pulse_log_mkqpamae",
                    "fecha_Mjj4f1Wb", "numeric_mm3m26jh", "fecha_Mjj4rzUe"
                  ]) {{ id text }}
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
 
# ── Fetch all Doctor Socio items from a Diario board ─────────────────────────
 
def get_diario_doctor_socio(board_id: int) -> list[dict]:
    """
    Fetch all items from a Diario board where Media = Doctor Socio (index 3).
    Returns items with name, date, status, cost.
    Note: we filter client-side since query_params index filter doesn't work reliably.
    """
    items = []
    cursor = None
    while True:
        cursor_clause = f', cursor: "{cursor}"' if cursor else ""
        q = f"""
        query {{
          boards(ids: [{board_id}]) {{
            items_page(limit: 500{cursor_clause}) {{
              cursor
              items {{
                name
                column_values(ids: ["date", "dup__of_reason", "estado_1", "cost"]) {{
                  id text value
                }}
              }}
            }}
          }}
        }}
        """
        data = gql(q)
        page = data["boards"][0]["items_page"]
        # Filter: only Doctor Socio (index 3 in dup__of_reason)
        for item in page["items"]:
            cv = parse_cv(item)
            reason_raw = next(
                (c.get("value","") for c in item["column_values"] if c["id"] == "dup__of_reason"),
                ""
            )
            try:
                reason_obj = json.loads(reason_raw) if reason_raw else {}
                if reason_obj.get("index") == 3:
                    items.append(item)
            except:
                pass
        cursor = page.get("cursor")
        if not cursor:
            break
    print(f"    Board {board_id}: {len(items)} Doctor Socio items")
    return items
 
# ── Build per-doctor stats from Diario items ──────────────────────────────────
 
def build_doctor_stats(diario_items: list[dict], patient_name: str) -> dict:
    """
    Match Diario items to a doctor by patient name similarity.
    Returns {YYYY-MM: {total, done, revenue, cancelado}, last_date: date|None}
    
    Since conectar_tableros is always null, we can only aggregate ALL items
    by month — we can't split by doctor without the link.
    Returns the monthly aggregation for ALL doctor-socio items.
    """
    monthly = defaultdict(lambda: {"total": 0, "done": 0, "revenue": 0, "cancelado": 0})
    last_date = None
 
    for item in diario_items:
        cv      = parse_cv(item)
        date_s  = cv.get("date", "")
        status  = cv.get("estado_1", "")
        cost_s  = cv.get("cost", "0") or "0"
 
        if not date_s:
            continue
        try:
            item_date = date.fromisoformat(date_s[:10])
        except:
            continue
 
        rev = float(re.sub(r"[^\d.]", "", cost_s) or "0")
        ym  = f"{item_date.year}-{item_date.month:02d}"
 
        monthly[ym]["total"] += 1
        if status == "Done":
            monthly[ym]["done"]     += 1
            monthly[ym]["revenue"]  += rev
        elif "cancel" in status.lower():
            monthly[ym]["cancelado"] += rev
 
        if last_date is None or item_date > last_date:
            last_date = item_date
 
    return {"monthly": dict(monthly), "last_date": last_date}
 
 
# ── Alternative approach: aggregate ALL doctor-socio by YYYY-MM ───────────────
 
def build_all_monthly(board_items: list[dict]) -> dict:
    """
    Build {YYYY-MM: {total, done, revenue, cancelado}} for ALL doctor-socio items.
    This is what goes into the global stats (resumen).
    Returns also last_date.
    """
    monthly = defaultdict(lambda: {"total": 0, "done": 0, "revenue": 0, "cancelado": 0})
    last_date = None
 
    for item in board_items:
        cv     = parse_cv(item)
        date_s = cv.get("date", "")
        status = cv.get("estado_1", "")
        cost_s = cv.get("cost", "0") or "0"
        if not date_s:
            continue
        try:
            item_date = date.fromisoformat(date_s[:10])
        except:
            continue
 
        rev = float(re.sub(r"[^\d.]", "", cost_s) or "0")
        ym  = f"{item_date.year}-{item_date.month:02d}"
        monthly[ym]["total"] += 1
        if status == "Done":
            monthly[ym]["done"]    += 1
            monthly[ym]["revenue"] += rev
        elif "cancel" in status.lower():
            monthly[ym]["cancelado"] += rev
 
        if last_date is None or item_date > last_date:
            last_date = item_date
 
    return {"monthly": dict(monthly), "last_date": last_date}
 
# ── Update resumen (m26DATA, r26DATA) ─────────────────────────────────────────
 
def update_resumen(html: str, items_2026: list[dict], today: date) -> str:
    """Replace m26DATA and r26DATA with fresh counts from Diario 2026."""
    monthly = [0]*12
    revenue = [0]*12
 
    for item in items_2026:
        cv     = parse_cv(item)
        date_s = cv.get("date", "")
        status = cv.get("estado_1", "")
        cost_s = cv.get("cost","0") or "0"
        if not date_s:
            continue
        try:
            d = date.fromisoformat(date_s[:10])
        except:
            continue
        if d.year != 2026:
            continue
        monthly[d.month - 1] += 1
        if status == "Done":
            revenue[d.month - 1] += float(re.sub(r"[^\d.]","",cost_s) or "0")
 
    scripts = list(re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL))
    for sc in scripts:
        c = sc.group(1)
        m26_m = re.search(r'var m26DATA=\[([^\]]+)\]', c)
        r26_m = re.search(r'var r26DATA=\[([^\]]+)\]', c)
        if m26_m and r26_m:
            new_c = c.replace(
                m26_m.group(0), f"var m26DATA=[{', '.join(str(x) for x in monthly)}]"
            ).replace(
                r26_m.group(0), f"var r26DATA=[{', '.join(str(int(x)) for x in revenue)}]"
            )
            html = html.replace(c, new_c, 1)
            print(f"  Resumen updated: total 2026 pax = {sum(monthly)}")
            break
    return html
 
# ── Update last-updated timestamp ─────────────────────────────────────────────
 
def update_timestamp(html: str, today: date) -> str:
    ts = f"Actualizado {fmt(today)}"
    html = re.sub(r'Actualizado \d{2}/[A-Z][a-z]{2}/\d{2}', ts, html)
    print(f"  Timestamp: {ts}")
    return html
 
# ── Add new socios ────────────────────────────────────────────────────────────
 
def add_new_socios(html: str, socios: list[dict]) -> str:
    """Add doctors from Plan Doctores that aren't yet in the dashboard."""
    existing = set()
    for m in re.finditer(r'data-name="([^"]+)"', html):
        existing.add(m.group(1).lower().strip())
 
    added = 0
    for item in socios:
        cv     = parse_cv(item)
        source = (cv.get("estado8") or "").strip().lower()
        if source in NON_SOCIO_SOURCES:
            continue
 
        name      = item["name"].strip()
        name_key  = name.lower().strip()
        if name_key in existing:
            continue
 
        ingreso    = item.get("created_at","")[:10] or str(today)
        ing_yr     = ingreso[:4]
        ing_fmt    = fmt(date.fromisoformat(ingreso))
        speciality = cv.get("status5") or ""
        rec_fecha  = cv.get("fecha_Mjj4f1Wb") or ""
        blocks_s   = cv.get("numeric_mm3m26jh") or ""
        blocks     = max(1, int(float(blocks_s)) if blocks_s else 1)
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
 
        # Insert before </tbody>
        tbody_end = html.rfind('</tbody>')
        if tbody_end > 0:
            html = html[:tbody_end] + new_row + '\n' + html[tbody_end:]
            existing.add(name_key)
            added += 1
            print(f"  Added new socio: {name}")
 
    print(f"  New socios added: {added}")
    return html
 
# ── Update data-ld2026 for doctors that have recent patients ──────────────────
 
def update_last_dates(html: str, items_2026: list[dict], socios: list[dict]) -> str:
    """
    Since we can't match Diario items to doctors (conectar_tableros is null),
    we use Plan Doctores fecha_Mjj4rzUe (Último paciente) as the source of truth
    for data-ld2026. This field IS maintained per doctor in Plan Doctores.
    """
    # Build name → last_patient_date from Plan Doctores
    last_dates = {}
    for item in socios:
        cv = parse_cv(item)
        ld_str = cv.get("fecha_Mjj4rzUe", "")
        if not ld_str:
            continue
        try:
            ld = date.fromisoformat(ld_str[:10])
            name_key = item["name"].strip().lower()
            last_dates[name_key] = ld
        except:
            continue
 
    if not last_dates:
        print("  No last-date updates from Plan Doctores")
        return html
 
    updated = 0
 
    def patch_ld(m):
        nonlocal updated
        row = m.group(0)
        nm = re.search(r'data-name="([^"]+)"', row)
        if not nm:
            return row
        name_key = nm.group(1).lower().strip()
        if name_key not in last_dates:
            return row
 
        new_date = last_dates[name_key]
        new_date_str = fmt_iso(new_date)
 
        # Check existing ld2026
        ld_m = re.search(r'data-ld2026="([^"]*)"', row)
        if ld_m:
            old = parse_date_str(ld_m.group(1))
            if old and new_date <= old:
                return row  # no update needed
            row = row.replace(ld_m.group(0), f'data-ld2026="{new_date_str}"')
        else:
            row = row.replace('<tr class="doctor-row"',
                              f'<tr class="doctor-row" data-ld2026="{new_date_str}"', 1)
        updated += 1
        return row
 
    html = re.sub(r'<tr class="doctor-row"[^>]+>', patch_ld, html)
    print(f"  Last dates updated: {updated} doctors")
    return html
 
# ── Update stat counters ──────────────────────────────────────────────────────
 
def update_stat_counters(html: str, items_2026: list[dict], socios: list[dict], today: date) -> str:
    """Update the top stat cards: total activos 2026, total socios."""
 
    # Count doctors active in 2026 — use socios with ld2026 in 2026
    active_2026 = sum(
        1 for item in socios
        if parse_date_str((parse_cv(item).get("fecha_Mjj4rzUe") or "")) and
           (lambda d: d and d.year == 2026)(parse_date_str(parse_cv(item).get("fecha_Mjj4rzUe") or ""))
    )
 
    total_socios = len([
        item for item in socios
        if (parse_cv(item).get("estado8") or "").strip().lower() not in NON_SOCIO_SOURCES
    ])
 
    # Update stat-activos26
    html = re.sub(
        r'(<span[^>]*id="stat-activos26"[^>]*>)\d+',
        lambda m: m.group(1) + str(active_2026),
        html
    )
 
    # Update sub text
    html = re.sub(
        r'Han enviado ≥1 paciente',
        f'Han enviado ≥1 paciente · {total_socios} en red',
        html, count=1
    )
    # Reset if already has "· X en red" to avoid duplicating
    html = re.sub(
        r'Han enviado ≥1 paciente · \d+ en red',
        f'Han enviado ≥1 paciente · {total_socios} en red',
        html
    )
 
    print(f"  Stats: {active_2026} activos 2026, {total_socios} total red")
    return html
 
# ── Main ──────────────────────────────────────────────────────────────────────
 
def main():
    today = date.today()
    print(f"=== Labbox Dashboard Update ===")
    print(f"Date: {today}")
    print()
 
    # ── Load HTML
    html = load_html(HTML_FILE)
    print(f"Loaded {HTML_FILE} ({len(html):,} chars)")
 
    # ── Fetch data
    print("\n[1/3] Fetching Plan Doctores (Ganados)...")
    socios = get_all_socios()
    print(f"  {len(socios)} doctors in Plan Doctores Ganados")
 
    print("\n[2/3] Fetching Diario 2026 (Doctor Socio items)...")
    items_2026 = get_diario_doctor_socio(BOARD_2026)
 
    print("\n[3/3] Checking for new socios...")
    # (already have socios from step 1)
 
    # ── Apply updates
    print("\n── Applying updates ──")
 
    print("\n[A] New socios...")
    html = add_new_socios(html, socios)
 
    print("\n[B] Last patient dates (from Plan Doctores)...")
    html = update_last_dates(html, items_2026, socios)
 
    print("\n[C] Resumen general (2026 monthly counts)...")
    html = update_resumen(html, items_2026, today)
 
    print("\n[D] Stat counters...")
    html = update_stat_counters(html, items_2026, socios, today)
 
    print("\n[E] Timestamp...")
    html = update_timestamp(html, today)
 
    # ── Save
    save_html(HTML_FILE, html)
    print(f"\n✅ Done — {HTML_FILE} updated ({len(html):,} chars)")
 
if __name__ == "__main__":
    main()
