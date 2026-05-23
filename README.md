# 📊 Dashboard Médicos Socios — Labbox

Dashboard de seguimiento de médicos socios. Se actualiza automáticamente cada lunes.

## 🌐 Ver el dashboard

> **[→ Ver dashboard en vivo](https://TU-USUARIO.github.io/TU-REPO/)**

---

## ⚙️ Setup inicial (una sola vez)

### 1. Crear el repositorio en GitHub
```bash
# Clona este repo o crea uno nuevo y sube estos archivos
git init
git add .
git commit -m "Initial dashboard"
git remote add origin https://github.com/TU-USUARIO/TU-REPO.git
git push -u origin main
```

### 2. Activar GitHub Pages
1. Ve a **Settings → Pages** en tu repo
2. Source: **Deploy from a branch**
3. Branch: `main` / `/ (root)`
4. Guarda → en unos minutos tendrás la URL pública

### 3. Agregar el API Key de Monday.com
1. Ve a **Settings → Secrets and variables → Actions**
2. Clic en **New repository secret**
3. Nombre: `MONDAY_API_KEY`
4. Valor: tu API key de Monday.com
   - En Monday: tu foto → Admin → API → **Copy**

### 4. Verificar que Actions está habilitado
- Ve a **Settings → Actions → General**
- Asegúrate de que Actions está **Enabled**

---

## 🔄 Actualización automática

El dashboard se actualiza **cada lunes a las 8am CST** automáticamente.

### Correr manualmente
1. Ve a **Actions → 📊 Dashboard Semanal Labbox**
2. Clic en **Run workflow**
3. Opcional: especifica la semana a procesar

### Ver el log
En **Actions** puedes ver el resultado de cada ejecución, incluyendo cuántos doctores se actualizaron.

---

## 📁 Archivos

| Archivo | Descripción |
|---|---|
| `index.html` | El dashboard completo (se actualiza automáticamente) |
| `update_dashboard.py` | Script Python que hace las actualizaciones |
| `requirements.txt` | Dependencias Python |
| `.github/workflows/weekly_update.yml` | Configuración del cron job |

---

## 🔍 Qué se actualiza cada semana

| Pestaña | Cambios |
|---|---|
| **Médicos Socios** | Pacientes de la semana, ingresos, último paciente |
| **Recetarios** | Nuevas entregas, mover a "Sin prisa" |
| **Actividad** | Último paciente + alerta recalculada |
| **Resumen** | Totales del mes en curso |
| **Beneficio Médico** | Nuevos usos de beneficio |

---

*Generado por Claude · Labbox*
