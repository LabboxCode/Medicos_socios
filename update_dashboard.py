#!/usr/bin/env python3
"""
Labbox Dashboard — Actualizador semanal
Corre cada lunes. Jala datos de Monday.com y actualiza index.html.
"""

import os, re, json, requests, sys
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY       = os.environ["MONDAY_API_KEY"]
BOARD_DIARIO  = 18391336187   # Diario 2026
BOARD_PLANES  = 4078088806    # Plan Doctores
HEADERS       = {"Authorization": API_KEY, "Content-Type": "application/json", "API-Version": "2023-10"}
ENDPOINT      = "https://api.monday.com/v2"
HTML_FILE     = "index.html"

MN = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
MN_ES = {m: i+1 for i, m in enumerate(MN)}

# Reflejo exclusions: estos sources NO son socios
NON_SOCIO_SOURCES = {'ventas', 'paciente', ''}

# ── Helpers ───────────────────────────────────────────────────────────────────

def gql(query: str, variables: dict = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(ENDPOINT, json=payload, headers=HEADERS, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]

def fmt(d: date) -> str:
    """date → '15/May/26'"""
    return f"{d.day:02d}/{MN[d.month-1]}/{str(d.year)[2:]}"

def fmt_iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")

def week_key(dt: datetime) -> str:
    """datetime → '2026-05-W3' using Mon-based weeks"""
    d = dt.date()
    first = date(d.year, d.month, 1)
    first_mon_offset = (7 - first.weekday()) % 7
    first_mon = first if first_mon_offset == 0 else first + timedelta(days=first_mon_offset)
    if d < first_mon:
        wn = 1
    else:
        wn = (d - first_mon).days // 7 + 2
    return f"{d.year}-{d.month:02d}-W{wn}"

def ts_to_date(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()

def parse_date_str(s: str) -> date:
    """Parse '15/May/26' or '2026-05-15'"""
    if not s or s in ('—', ''):
        return None
    try:
        if '/' in s and len(s) <= 9:
            parts = s.split('/')
            return date(2000 + int(parts[2]), MN_ES[parts[1]], int(parts[0]))
        return date.fromisoformat(s[:10])
    except:
        return None

# ── Week boundaries ───────────────────────────────────────────────────────────

def get_closed_week() -> tuple[date, date]:
    """
    Returns (week_start, week_end) of the LAST fully closed Mon-Sun week.
    Script runs on Monday: last closed week = Mon-Sun 8 days ago to yesterday.
    """
    today = date.today()
    # today is Monday; last Sun = today-1, last Mon = today-7
    week_end   = today - timedelta(days=1)   # last Sunday
    week_start = today - timedelta(days=7)   # last Monday
    return week_start, week_end

# ── Monday API calls ──────────────────────────────────────────────────────────

def get_diario_week(week_start: date, week_end: date) -> list[dict]:
    """Fetch all Diario 2026 items with date in [week_start, week_end]."""
    items = []
    cursor = None
    while True:
        cursor_clause = f', cursor: "{cursor}"' if cursor else ""
        pq = f"""
    query {{
      boards(ids: [{BOARD_DIARIO}]) {{
        items_page(limit: 500{cursor_clause}, query_params: {{
          rules: [
            {{column_id: "date", compare_value: ["{fmt_iso(week_start)}", "{fmt_iso(week_end)}"], operator: between}}
          ]
        }}) {{
          cursor
          items {{
            id
            column_values(ids: ["conectar_tableros", "date", "estado_1", "cost"]) {{
              id text
            }}
          }}
        }}
      }}
    }}
    """
        data = gql(pq)
        page = data["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break
    return items

def get_new_recetarios(since: date) -> list[dict]:
    """Plan Doctores items where fecha_Mjj4f1Wb >= since."""
    q = f"""
    query {{
      boards(ids: [{BOARD_PLANES}]) {{
        items_page(limit: 500, query_params: {{
          rules: [
            {{column_id: "fecha_Mjj4f1Wb", compare_value: "{fmt_iso(since)}", operator: greater_than_or_equals}}
            {{column_id: "estado4", compare_value: ["Ganados"], operator: any_of}}
          ]
        }}) {{
          items {{
            id name
            column_values(ids: [
              "fecha_Mjj4f1Wb", "estado8", "numeric_mm3m26jh",
              "men__desplegable", "pulse_log_mkqpamae", "status5"
            ]) {{ id text }}
          }}
        }}
      }}
    }}
    """
    data = gql(q)
    return data["boards"][0]["items_page"]["items"]

def get_new_socios(since: date) -> list[dict]:
    """Plan Doctores Ganados items created >= since."""
    q = f"""
    query {{
      boards(ids: [{BOARD_PLANES}]) {{
        groups(ids: ["grupo_nuevo"]) {{
          items_page(limit: 200) {{
            items {{
              id name created_at
              column_values(ids: ["estado8", "pulse_log_mkqpamae", "status5",
                                   "fecha_Mjj4f1Wb", "numeric_mm3m26jh"]) {{ id text }}
            }}
          }}
        }}
      }}
    }}
    """
    data = gql(q)
    items = data["boards"][0]["groups"][0]["items_page"]["items"]
    return [
        item for item in items
        if date.fromisoformat(item["created_at"][:10]) >= since
    ]

def get_beneficio_week(week_start: date, week_end: date) -> list[dict]:
    """Diario items with forma_de_pago = Beneficio Médico in the week."""
    # Note: adjust column IDs as needed for your Diario board
    try:
        q = f"""
    query {{
      boards(ids: [{BOARD_DIARIO}]) {{
        items_page(limit: 200, query_params: {{
          rules: [
            {{column_id: "date", compare_value: ["{fmt_iso(week_start)}", "{fmt_iso(week_end)}"], operator: between}}
            {{column_id: "estado_1", compare_value: ["Beneficio Medico"], operator: any_of}}
          ]
        }}) {{
          items {{
            id name
            column_values(ids: ["conectar_tableros", "date", "cost", "estado_1"]) {{
              id text
            }}
          }}
        }}
      }}
    }}
    """
        data = gql(q)
        return data["boards"][0]["items_page"]["items"]
    except:
        return []  # If no beneficio column, skip silently

# ── HTML helpers ──────────────────────────────────────────────────────────────

def load_html(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()

def save_html(path: str, html: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

def parse_cv(item: dict) -> dict:
    """column_values list → {id: text} dict"""
    return {cv["id"]: (cv["text"] or "") for cv in item["column_values"]}

def get_doc_id_from_row(row_html: str) -> str | None:
    """Extract the Monday.com board_id embedded in a doctor-row (doctorList key)."""
    m = re.search(r'data-monday-id="([^"]+)"', row_html)
    return m.group(1) if m else None

# ── Update 1: allWeeklyData ───────────────────────────────────────────────────

def update_weekly_data(html: str, diario_items: list, week_start: date) -> str:
    """Add the closed week to allWeeklyData for each doctor that had activity."""

    # Build week aggregation: {str(board_id): {wk: {total, done, revenue, cancelado}}}
    week_agg: dict[str, dict] = defaultdict(lambda: defaultdict(
        lambda: {"total": 0, "done": 0, "revenue": 0, "cancelado": 0}
    ))

    for item in diario_items:
        cv = parse_cv(item)
        doc_id  = cv.get("conectar_tableros", "")
        status  = cv.get("estado_1", "")
        cost_s  = cv.get("cost", "0") or "0"
        date_s  = cv.get("date", "")

        if not doc_id or not date_s:
            continue

        try:
            item_date = date.fromisoformat(date_s[:10])
        except:
            continue

        wk  = week_key(datetime(item_date.year, item_date.month, item_date.day))
        rev = float(re.sub(r"[^\d.]", "", cost_s) or "0")

        week_agg[doc_id][wk]["total"] += 1
        if status == "Done":
            week_agg[doc_id][wk]["done"]    += 1
            week_agg[doc_id][wk]["revenue"] += rev
        elif status == "Cancelacion":
            week_agg[doc_id][wk]["cancelado"] += rev

    if not week_agg:
        print("  No new weekly data")
        return html

    # Load and update allWeeklyData in the JS
    m = re.search(r'const allWeeklyData = (\{.*?\});', html, re.DOTALL)
    if not m:
        print("  WARNING: allWeeklyData not found")
        return html

    wd = json.loads(m.group(1))
    added = 0
    for doc_id, weeks in week_agg.items():
        if doc_id not in wd:
            wd[doc_id] = {}
        for wk, vals in weeks.items():
            if wk not in wd[doc_id]:
                wd[doc_id][wk] = vals
                added += 1
            else:
                # Accumulate if week already partially exists
                for k in vals:
                    wd[doc_id][wk][k] = wd[doc_id][wk].get(k, 0) + vals[k]

    new_js = f"const allWeeklyData = {json.dumps(wd, separators=(',',':'), ensure_ascii=False)};"
    html = html.replace(m.group(0), new_js)
    print(f"  allWeeklyData: +{added} new week entries for {len(week_agg)} doctors")
    return html

# ── Update 2: data-myr2026 + data-ld2026 per doctor row ──────────────────────

def update_doctor_monthly(html: str, diario_items: list) -> str:
    """Update data-myr2026, data-ld2026, data-monthly in doctor rows."""

    # Build monthly + last-date per doc_id
    monthly_agg: dict[str, dict] = defaultdict(lambda: defaultdict(
        lambda: {"total": 0, "done": 0, "revenue": 0, "cancelado": 0}
    ))
    last_date: dict[str, date] = {}

    for item in diario_items:
        cv      = parse_cv(item)
        doc_id  = cv.get("conectar_tableros", "")
        status  = cv.get("estado_1", "")
        cost_s  = cv.get("cost", "0") or "0"
        date_s  = cv.get("date", "")
        if not doc_id or not date_s:
            continue
        try:
            item_date = date.fromisoformat(date_s[:10])
        except:
            continue

        rev = float(re.sub(r"[^\d.]", "", cost_s) or "0")
        ym  = f"{item_date.year}-{item_date.month:02d}"
        monthly_agg[doc_id][ym]["total"] += 1
        if status == "Done":
            monthly_agg[doc_id][ym]["done"]    += 1
            monthly_agg[doc_id][ym]["revenue"] += rev
        elif status == "Cancelacion":
            monthly_agg[doc_id][ym]["cancelado"] += rev

        # Track last patient date (any status = patient showed up)
        if doc_id not in last_date or item_date > last_date[doc_id]:
            last_date[doc_id] = item_date

    if not monthly_agg:
        return html

    # Build lookup: name_lower → doctor_row html
    rows = re.findall(r'<tr class="doctor-row"[^>]+>', html)
    doc_id_to_row_key: dict[str, str] = {}

    # Try to map doc_id → data-name from doctorList
    dl_m = re.search(r'const doctorList = (\[.*?\]);', html, re.DOTALL)
    if dl_m:
        dl = json.loads(dl_m.group(1))
        for d in dl:
            doc_id_to_row_key[str(d["id"])] = d["name"].lower().strip()

    updated_rows = 0

    def patch_row(row_html: str, doc_id: str) -> str:
        nonlocal updated_rows
        name_key = doc_id_to_row_key.get(doc_id, "")
        if not name_key:
            return row_html

        new_row = row_html

        # Update data-myr2026
        if doc_id in monthly_agg:
            myr26_m = re.search(r"data-myr2026='([^']*)'", new_row)
            if myr26_m:
                try:
                    existing = json.loads(myr26_m.group(1) or '{}')
                except:
                    existing = {}
                for ym, vals in monthly_agg[doc_id].items():
                    if not ym.startswith("2026"):
                        continue
                    if ym not in existing:
                        existing[ym] = vals
                    else:
                        for k in vals:
                            existing[ym][k] = existing[ym].get(k, 0) + vals[k]
                new_row = new_row.replace(
                    myr26_m.group(0),
                    f"data-myr2026='{json.dumps(existing, separators=(',',':'))}'"
                )

            # Update data-monthly (all key)
            monthly_m = re.search(r"data-monthly='([^']+)'", new_row)
            if monthly_m:
                try:
                    existing_m = json.loads(monthly_m.group(1))
                except:
                    existing_m = {}
                for ym, vals in monthly_agg[doc_id].items():
                    if ym not in existing_m:
                        existing_m[ym] = vals
                    else:
                        for k in vals:
                            existing_m[ym][k] = existing_m[ym].get(k, 0) + vals[k]
                # Rebuild 'all' aggregate
                all_agg = {"total": 0, "done": 0, "revenue": 0, "cancelado": 0}
                for ym, v in existing_m.items():
                    if ym != "all" and isinstance(v, dict):
                        for k in all_agg:
                            all_agg[k] += v.get(k, 0)
                existing_m["all"] = all_agg
                new_row = new_row.replace(
                    monthly_m.group(0),
                    f"data-monthly='{json.dumps(existing_m, separators=(',',':'))}'"
                )

        # Update data-ld2026
        if doc_id in last_date:
            new_date_str = fmt(last_date[doc_id])
            ld_m = re.search(r'data-ld2026="([^"]*)"', new_row)
            if ld_m:
                try:
                    old_date = parse_date_str(ld_m.group(1))
                except:
                    old_date = None
                if old_date is None or last_date[doc_id] > old_date:
                    new_row = new_row.replace(ld_m.group(0), f'data-ld2026="{new_date_str}"')
            else:
                new_row = new_row.replace('<tr class="doctor-row"',
                                          f'<tr class="doctor-row" data-ld2026="{new_date_str}"', 1)
            updated_rows += 1

        return new_row

    # Apply patches using doctorList ID mapping
    def replace_row(m):
        row = m.group(0)
        nm = re.search(r'data-name="([^"]+)"', row)
        if not nm:
            return row
        name_lower = nm.group(1).lower().strip()
        # Find doc_id for this name
        doc_id = None
        for did, nk in doc_id_to_row_key.items():
            if nk == name_lower:
                doc_id = did
                break
        if not doc_id:
            return row
        return patch_row(row, doc_id)

    html = re.sub(r'<tr class="doctor-row"[^>]+>', replace_row, html)
    print(f"  Doctor rows updated: {updated_rows} with new last-date")
    return html

# ── Update 3: Actividad alerts ────────────────────────────────────────────────

def update_actividad_alerts(html: str, last_dates: dict[str, date], today: date) -> str:
    """
    Update 'Último pax' and recalculate alert for doctors in risk-row divs.
    last_dates: {name_lower: latest_date_from_diario}
    """
    if not last_dates:
        return html

    riesgo_s = html.find('id="page-riesgo"')
    riesgo_e = html.find('id="page-beneficio"')
    rc = html[riesgo_s:riesgo_e]

    row_starts = list(re.finditer(r'<div class="risk-row">', rc))
    new_rc = rc
    offset = 0
    updated = 0

    for rs_match in row_starts:
        start = rs_match.start() + offset
        next_pos = rc.find('<div class="risk-row">', rs_match.start() + 10)
        end = (next_pos + offset) if next_pos > 0 else len(new_rc)

        row = new_rc[start:end]
        name_m = re.search(r'class="risk-row-name">([^<]+)', row)
        if not name_m:
            continue

        name_key = name_m.group(1).strip().lower()
        if name_key not in last_dates:
            continue

        new_last = last_dates[name_key]
        dates_in_row = re.findall(r'\d{2}/[A-Z][a-z]{2}/\d{2}', row)
        if not dates_in_row:
            continue

        old_date_str = dates_in_row[0]
        old_date = parse_date_str(old_date_str)
        if old_date and new_last <= old_date:
            continue  # No update needed

        # Update the date
        new_date_str = fmt(new_last)
        new_row = row.replace(old_date_str, new_date_str, 1)

        # Recalculate alert
        cad_m = re.search(r'c/(\d+)d', new_row)
        if cad_m:
            cad = int(cad_m.group(1))
            days_since = (today - new_last).days
            if days_since <= 0:
                alert = '<span style="color:#22c55e;font-size:11px;font-weight:700">🟢 Al día</span>'
            elif days_since <= cad:
                alert = f'<span style="color:#22c55e;font-size:11px;font-weight:700">🟢 +{days_since}d</span>'
            elif days_since <= cad * 1.5:
                alert = f'<span style="color:#f59e0b;font-size:11px;font-weight:700">🟡 +{days_since}d</span>'
            else:
                alert = f'<span style="color:#ef4444;font-size:11px;font-weight:700">🔴 +{days_since}d</span>'

            old_alert = re.search(r'<span[^>]*>(?:🔴|🟡|🟢)[^<]+</span>', new_row)
            if old_alert:
                new_row = new_row.replace(old_alert.group(0), alert, 1)

        new_rc = new_rc[:start] + new_row + new_rc[end:]
        offset += len(new_row) - len(row)
        updated += 1

    html = html[:riesgo_s] + new_rc + html[riesgo_e:]
    print(f"  Actividad: {updated} doctors updated")
    return html

# ── Update 4: Recetarios ──────────────────────────────────────────────────────

def update_recetarios(html: str, recetario_items: list) -> str:
    """Update rec-fecha, rec-meses, rec-restantes and move to Sin prisa."""
    if not recetario_items:
        print("  No recetario updates")
        return html

    for item in recetario_items:
        cv = parse_cv(item)

        source  = (cv.get("estado8") or "").strip().lower()
        if source in NON_SOCIO_SOURCES:
            continue

        name = item["name"].strip()
        fecha_str = cv.get("fecha_Mjj4f1Wb") or ""
        blocks_s  = cv.get("numeric_mm3m26jh") or ""
        status    = cv.get("men__desplegable") or ""

        if not fecha_str:
            continue

        try:
            fecha_date = date.fromisoformat(fecha_str[:10])
        except:
            continue

        blocks = max(1, int(float(blocks_s)) if blocks_s else 1)
        fecha_disp = fmt(fecha_date)
        name_lower = name.lower().strip()

        # Update data-rec-fecha, data-rec-meses, data-rec-restantes in TR row
        def patch_rec_row(m):
            row = m.group(0)
            nm = re.search(r'data-name="([^"]+)"', row)
            if not nm or nm.group(1).lower().strip() != name_lower:
                return row

            row = re.sub(r'data-rec-fecha="[^"]*"', f'data-rec-fecha="{fecha_str}"', row)

            # Get rec_rate from existing rec-row HTML for this doctor
            rec_rate = get_doctor_rec_rate(html, name_lower)
            if rec_rate > 0:
                meses = round((blocks * 100) / rec_rate, 1)
            else:
                meses = 4.0 * blocks  # fallback

            row = re.sub(r'data-rec-meses="[^"]*"',      f'data-rec-meses="{meses}"', row)
            row = re.sub(r'data-rec-restantes="[^"]*"',  f'data-rec-restantes="{int(meses * rec_rate / 100) if rec_rate > 0 else blocks * 100}"', row)
            return row

        html = re.sub(r'<tr class="doctor-row"[^>]+>', patch_rec_row, html)

        # Move doctor in recetario tab → Sin prisa
        html = move_to_sinprisa(html, name, name_lower, fecha_disp, blocks, fecha_str)

    print(f"  Recetarios: {len(recetario_items)} processed")
    return html

def get_doctor_rec_rate(html: str, name_lower: str) -> float:
    """Extract rec_rate (rec/mes) from the existing hardcoded rec-row for a doctor."""
    rec_s = html.find('id="page-recetario"')
    if rec_s < 0:
        return 0
    pos = html.lower().find(name_lower, rec_s)
    if pos < 0:
        return 0
    chunk = html[pos:pos + 600]
    m = re.search(r'([\d.]+)/mes', chunk)
    return float(m.group(1)) if m else 0

def move_to_sinprisa(html: str, name: str, name_lower: str, fecha_disp: str,
                     blocks: int, fecha_iso: str) -> str:
    """Remove doctor from atencion/proximo and add/update in sinprisa."""
    rec_s = html.find('id="page-recetario"')
    rec_e = html.find('id="page-riesgo"')
    rc = html[rec_s:rec_e]

    # Find existing rec-row for this doctor (any section)
    doc_pos = rc.lower().find(name_lower)
    if doc_pos < 0:
        return html  # not in recetario tab at all

    row_start_abs = rc.rfind('<div class="rec-row">', 0, doc_pos)
    if row_start_abs < 0:
        return html

    row_end_abs = rc.find('<div class="rec-row">', doc_pos)
    if row_end_abs < 0:
        row_end_abs = rc.find('</div>\n  </div>', doc_pos) + 15

    old_row_html = rc[row_start_abs:row_end_abs]

    # Get rec_rate
    rec_rate = get_doctor_rec_rate(html, name_lower)
    meses = round((blocks * 100) / rec_rate, 1) if rec_rate > 0 else 4.0 * blocks
    used_pct = 0  # just delivered

    # Build new row
    new_row = f'''<div class="rec-row">
    <div><div class="rec-row-name">{name}</div></div>
    <div><div class="rec-bar-wrap"><div class="rec-bar-fill" style="width:0%;background:#22c55e"></div></div><div style="font-size:10px;color:#94a3b8;margin-top:3px">0/{int(blocks*100)} usados</div></div>
    <div class="rec-col"><span style="color:#94a3b8">{fecha_disp}</span></div>
    <div class="rec-col" style="text-align:right"><span style="color:#22c55e">+0 mes</span></div>
    <div class="rec-col"><strong style="color:#22c55e">~{int(meses)}</strong> · Entregado</div>
    <div class="rec-col" style="text-align:right"><span style="color:#64748b">{int(rec_rate) if rec_rate > 0 else "—"}/mes</span></div>
    <div><span class="rec-badge" style="background:#22c55e22;color:#22c55e;border:1px solid #22c55e44;border-radius:20px;padding:3px 9px;font-size:11px;font-weight:700">🟢 Sin prisa</span></div>
  </div>
'''

    # Determine which section the doctor is currently in
    sinprisa_body_pos = rc.find('id="rbody-sinprisa"')
    in_sinprisa = sinprisa_body_pos > 0 and row_start_abs > sinprisa_body_pos

    # Remove from current section
    rc_new = rc.replace(old_row_html, '', 1)

    if not in_sinprisa:
        # Add to beginning of sinprisa
        sinpr_pos = rc_new.find('id="rbody-sinprisa"')
        first_row = rc_new.find('<div class="rec-row">', sinpr_pos)
        if first_row > 0:
            rc_new = rc_new[:first_row] + new_row + rc_new[first_row:]
    else:
        # Replace in sinprisa with updated version
        sinpr_pos = rc_new.find('id="rbody-sinprisa"')
        new_doc_pos = rc_new.lower().find(name_lower, sinpr_pos)
        if new_doc_pos < 0:
            first_row = rc_new.find('<div class="rec-row">', sinpr_pos)
            rc_new = rc_new[:first_row] + new_row + rc_new[first_row:]

    # Update section counts
    rc_new = update_section_counts(rc_new)

    return html[:rec_s] + rc_new + html[rec_e:]

def update_section_counts(rc: str) -> str:
    """Recount doctors in each section and update the badge."""
    for sec_id, chev_id in [('rbody-atencion','rchev-atencion'),
                              ('rbody-proximo','rchev-proximo'),
                              ('rbody-sinprisa','rchev-sinprisa')]:
        s = rc.find(f'id="{sec_id}"')
        e_kw = [rc.find(f'id="{x}"', s) for x in
                ['rchev-proximo','rchev-sinprisa','id="page-riesgo"'] if rc.find(f'id="{x}"', s) > s]
        e = min(e_kw) if e_kw else len(rc)
        section = rc[s:e]
        count = len(re.findall(r'<div class="rec-row-name">', section))

        # Update badge in header
        chev_pos = rc.find(f'id="{chev_id}"')
        header_s = rc.rfind('rec-section-count', 0, chev_pos)
        if header_s > 0:
            old_cnt = re.search(r'>(\d+)</span>', rc[header_s:header_s+50])
            if old_cnt:
                rc = rc[:header_s] + rc[header_s:header_s+50].replace(
                    f'>{old_cnt.group(1)}</span>', f'>{count}</span>', 1
                ) + rc[header_s+50:]
    return rc

# ── Update 5: New socios ──────────────────────────────────────────────────────

def add_new_socios(html: str, new_socios: list) -> str:
    """Add brand-new socios to doctorTableBody and update badge counts."""
    if not new_socios:
        print("  No new socios")
        return html

    # Get existing names
    existing = set()
    for m in re.finditer(r'data-name="([^"]+)"', html):
        existing.add(m.group(1).lower().strip())

    dl_m = re.search(r'const doctorList = (\[.*?\]);', html, re.DOTALL)
    dl = json.loads(dl_m.group(1)) if dl_m else []
    dl_ids = {str(d["id"]) for d in dl}

    added = 0
    for item in new_socios:
        cv     = parse_cv(item)
        source = (cv.get("estado8") or "").strip().lower()
        if source in NON_SOCIO_SOURCES:
            continue

        name     = item["name"].strip()
        name_key = name.lower().strip()
        item_id  = str(item["id"])

        if name_key in existing or item_id in dl_ids:
            continue  # already in dashboard

        ingreso  = item.get("created_at", "")[:10] or str(date.today())
        ing_yr   = ingreso[:4]
        ing_fmt  = fmt(date.fromisoformat(ingreso))
        rec_fecha = cv.get("fecha_Mjj4f1Wb") or ""
        blocks_s  = cv.get("numeric_mm3m26jh") or ""
        speciality = cv.get("status5") or ""

        blocks   = max(1, int(float(blocks_s)) if blocks_s else 1)
        rec_disp = fmt(date.fromisoformat(rec_fecha)) if rec_fecha else "—"
        rec_meses_val = "0.0" if not rec_fecha else "4.0"

        new_row = (
            f'<tr class="doctor-row" data-ingreso="{ingreso}" data-joinyear="{ing_yr}" '
            f'data-myr2023=\'{{}}\'  data-myr2024=\'{{}}\'  data-myr2025=\'{{}}\'  '
            f'data-monthly=\'{{\"all\":{{\"total\":0,\"done\":0,\"revenue\":0,\"cancelado\":0}}}}\' '
            f'data-name="{name_key}" data-reflejo="{source}" '
            f'data-rec-fecha="{rec_fecha}" data-rec-spec="" '
            f'data-rec-restantes="0" data-rec-meses="{rec_meses_val}">'
            f'<td style="padding:8px 10px"><div style="font-weight:600;font-size:13px;color:#e2e8f0">{name}</div>'
            f'<div style="font-size:11px;color:#475569">{speciality}</div></td>'
            f'<td style="padding:8px 10px;text-align:center">'
            f'<span style="background:#1e293b;color:#475569;font-size:11px;padding:4px 10px;border-radius:12px">⚪ Sin actividad</span></td>'
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

        tbody_end = html.rfind('</tbody>', 0, html.find('</table>'))
        html = html[:tbody_end] + new_row + '\n' + html[tbody_end:]
        existing.add(name_key)

        # Add to doctorList
        dl.append({"id": item_id, "name": name, "reflejo": source})
        added += 1

        # Add to recetario tab (Sin prisa if has recetario, else skip)
        if rec_fecha:
            html = move_to_sinprisa(html, name, name_key, rec_disp, blocks, rec_fecha)

    # Save updated doctorList
    if added > 0 and dl_m:
        new_dl = json.dumps(dl, separators=(',',':'), ensure_ascii=False)
        html = re.sub(r'const doctorList = \[.*?\];', f'const doctorList = {new_dl};', html, flags=re.DOTALL)

    print(f"  New socios added: {added}")
    return html

# ── Update 6: Resumen — m26DATA current month ─────────────────────────────────

def update_resumen_month(html: str, diario_items: list, today: date) -> str:
    """Update only the current month's index in m26DATA and r26DATA."""
    if not diario_items:
        return html

    month_idx = today.month - 1  # 0-based

    # Count totals for current month
    total_done = 0
    total_rev  = 0
    for item in diario_items:
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
        if item_date.year != 2026 or item_date.month != today.month:
            continue
        total_done += 1
        if status == "Done":
            total_rev += float(re.sub(r"[^\d.]", "", cost_s) or "0")

    if total_done == 0:
        return html

    scripts = list(re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL))
    for sc in scripts:
        c = sc.group(1)
        m26_m = re.search(r'var m26DATA=\[([^\]]+)\]', c)
        r26_m = re.search(r'var r26DATA=\[([^\]]+)\]', c)
        if m26_m and r26_m:
            m26 = [int(x.strip()) for x in m26_m.group(1).split(',')]
            r26 = [int(x.strip()) for x in r26_m.group(1).split(',')]
            m26[month_idx] = total_done
            r26[month_idx] = int(total_rev)
            new_c = c.replace(
                m26_m.group(0), f"var m26DATA=[{', '.join(str(x) for x in m26)}]"
            ).replace(
                r26_m.group(0), f"var r26DATA=[{', '.join(str(x) for x in r26)}]"
            )
            html = html.replace(c, new_c, 1)
            print(f"  Resumen: m26[{month_idx}]={total_done}, r26[{month_idx}]={int(total_rev)}")
            break

    return html

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today = date.today()
    week_start, week_end = get_closed_week()

    print(f"=== Labbox Dashboard Update ===")
    print(f"Date: {today}  |  Closed week: {week_start} → {week_end}")
    print()

    # ── Load HTML
    html = load_html(HTML_FILE)
    print(f"Loaded {HTML_FILE} ({len(html):,} chars)")

    # ── Fetch data
    print("\n[1/3] Fetching Diario 2026...")
    diario_items = get_diario_week(week_start, week_end)
    print(f"  {len(diario_items)} items in closed week")

    print("\n[2/3] Fetching Plan Doctores (recetarios + new socios)...")
    recetario_items = get_new_recetarios(week_start)
    new_socios      = get_new_socios(week_start)
    print(f"  Recetarios: {len(recetario_items)} | New socios: {len(new_socios)}")

    print("\n[3/3] Fetching Beneficio Médico...")
    beneficio_items = get_beneficio_week(week_start, week_end)
    print(f"  Beneficio items: {len(beneficio_items)}")

    # ── Build last-date lookup for Actividad
    last_dates: dict[str, date] = {}
    for item in diario_items:
        cv     = parse_cv(item)
        doc_id = cv.get("conectar_tableros", "")
        date_s = cv.get("date", "")
        if not doc_id or not date_s:
            continue
        dl_m = re.search(r'const doctorList = (\[.*?\]);', html, re.DOTALL)
        if dl_m:
            for d in json.loads(dl_m.group(1)):
                if str(d["id"]) == doc_id:
                    name_key = d["name"].lower().strip()
                    try:
                        item_d = date.fromisoformat(date_s[:10])
                        if name_key not in last_dates or item_d > last_dates[name_key]:
                            last_dates[name_key] = item_d
                    except:
                        pass

    # ── Apply updates
    print("\n── Applying updates ──")

    print("\n[A] Weekly data + doctor rows...")
    html = update_weekly_data(html, diario_items, week_start)
    html = update_doctor_monthly(html, diario_items)

    print("\n[B] Actividad alerts...")
    html = update_actividad_alerts(html, last_dates, today)

    print("\n[C] Recetarios...")
    html = update_recetarios(html, recetario_items)

    print("\n[D] New socios...")
    html = add_new_socios(html, new_socios)

    print("\n[E] Resumen General (current month)...")
    html = update_resumen_month(html, diario_items, today)

    # ── Save
    save_html(HTML_FILE, html)
    print(f"\n✅ Done — {HTML_FILE} updated ({len(html):,} chars)")
    print(f"   Week: {week_start} to {week_end}")

if __name__ == "__main__":
    main()
