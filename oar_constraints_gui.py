import json
import os
import math
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

APP_TITLE = "OAR Constraints Browser (JSON-driven) + EQD2"
DEFAULT_JSON = "constraints.json"
AB_MAP_JSON = "alpha_beta_defaults.json"


# -------------------------
# Radiobiology helpers
# -------------------------
def bed(total_dose_gy: float, n_fractions: float, alpha_beta: float) -> float:
    """BED = nd * (1 + d/(a/b)) where d = total/n"""
    if n_fractions <= 0 or alpha_beta <= 0:
        return float("nan")
    d = total_dose_gy / n_fractions
    return total_dose_gy * (1.0 + d / alpha_beta)


def eqd2(total_dose_gy: float, n_fractions: float, alpha_beta: float) -> float:
    """EQD2 = BED / (1 + 2/(a/b))"""
    b = bed(total_dose_gy, n_fractions, alpha_beta)
    if alpha_beta <= 0:
        return float("nan")
    return b / (1.0 + 2.0 / alpha_beta)


def total_dose_for_eqd2(eqd2_gy2: float, n_fractions: float, alpha_beta: float) -> float:
    """
    Solve for total dose D such that EQD2(D, n, a/b) = eqd2_gy2.

    EQD2 = D*(1 + (D/n)/(a/b)) / (1 + 2/(a/b))
    Let k = (1 + 2/(a/b))
    eqd2*k = nd + n d^2/(a/b)  with d = D/n
    => (n/(a/b)) d^2 + n d - eqd2*k = 0
    """
    if n_fractions <= 0 or alpha_beta <= 0:
        return float("nan")

    k = 1.0 + 2.0 / alpha_beta
    A = n_fractions / alpha_beta
    B = n_fractions
    C = -eqd2_gy2 * k

    disc = B * B - 4.0 * A * C
    if disc < 0:
        return float("nan")

    d = (-B + math.sqrt(disc)) / (2.0 * A)  # positive root
    return d * n_fractions


def try_parse_float(x):
    try:
        return float(str(x).strip())
    except Exception:
        return None


def is_dose_metric(metric: str) -> bool:
    m = (metric or "").strip().lower()
    return m.startswith("d")  # Dmax, Dmean, D0.03cc, D(1cc), etc.


def is_volume_metric(metric: str) -> bool:
    m = (metric or "").strip().lower()
    return m.startswith("v")  # V20, V30, etc.


# -------------------------
# IO helpers
# -------------------------
def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_key(x: str) -> str:
    return (x or "").strip()


def norm_text(x: str) -> str:
    """Lowercase + collapse whitespace for matching."""
    s = (x or "").strip().lower()
    s = " ".join(s.split())
    return s


# -------------------------
# App
# -------------------------
class OARConstraintsApp(tk.Tk):
    def __init__(self, constraints_json_path: str, ab_map_path: str):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1300x740")
        self.minsize(980, 600)

        self.json_path = constraints_json_path
        self.ab_map_path = ab_map_path

        self.db = {}
        self.sites = []
        self.techniques = []

        self.ab_map = {}
        self.ab_default = 3.0

        self._build_ui()
        self._load_ab_map(self.ab_map_path)
        self._load_db(self.json_path)

    def _build_ui(self):
        # Top controls
        top = ttk.Frame(self, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Site:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.site_var = tk.StringVar()
        self.site_cb = ttk.Combobox(top, textvariable=self.site_var, state="readonly", width=32)
        self.site_cb.grid(row=0, column=1, sticky="w", padx=(0, 14))
        self.site_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_fractionations())

        ttk.Label(top, text="Technique:").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.tech_var = tk.StringVar()
        self.tech_cb = ttk.Combobox(top, textvariable=self.tech_var, state="readonly", width=16)
        self.tech_cb.grid(row=0, column=3, sticky="w", padx=(0, 14))
        self.tech_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_fractionations())

        ttk.Label(top, text="Fractionation:").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.frac_var = tk.StringVar()
        self.frac_cb = ttk.Combobox(top, textvariable=self.frac_var, state="readonly", width=46)
        self.frac_cb.grid(row=0, column=5, sticky="w", padx=(0, 14))
        self.frac_cb.bind("<<ComboboxSelected>>", lambda e: self._populate_table())

        ttk.Button(top, text="Reload constraints JSON", command=self._reload_constraints).grid(row=0, column=6, sticky="e", padx=(8, 0))
        ttk.Button(top, text="Open constraints JSON…", command=self._open_constraints_json).grid(row=0, column=7, sticky="e", padx=(8, 0))

        # Row 2: filters and toggles
        row2 = ttk.Frame(self, padding=(10, 0, 10, 10))
        row2.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(row2, text="Filter:").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(row2, textvariable=self.filter_var, width=55)
        self.filter_entry.pack(side=tk.LEFT, padx=(8, 10))
        self.filter_entry.bind("<KeyRelease>", lambda e: self._populate_table())

        self.show_only_matching_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row2,
            text="Only constraints matching selected fractionation",
            variable=self.show_only_matching_var,
            command=self._populate_table
        ).pack(side=tk.LEFT, padx=(0, 14))

        self.show_eqd2_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row2,
            text="Show EQD2 + Equivalent Dose",
            variable=self.show_eqd2_var,
            command=self._populate_table
        ).pack(side=tk.LEFT, padx=(0, 14))

        ttk.Button(row2, text="Reload α/β map", command=self._reload_ab_map).pack(side=tk.LEFT)

        # EQD2 calculator panel
        eq = ttk.LabelFrame(self, text="EQD2 / BED Calculator", padding=10)
        eq.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(0, 8))

        self.calc_total_d = tk.StringVar(value="60")
        self.calc_n = tk.StringVar(value="30")
        self.calc_ab = tk.StringVar(value="3")

        ttk.Label(eq, text="Total dose D (Gy):").grid(row=0, column=0, sticky="w")
        ttk.Entry(eq, textvariable=self.calc_total_d, width=10).grid(row=0, column=1, sticky="w", padx=(6, 14))

        ttk.Label(eq, text="Fractions n:").grid(row=0, column=2, sticky="w")
        ttk.Entry(eq, textvariable=self.calc_n, width=10).grid(row=0, column=3, sticky="w", padx=(6, 14))

        ttk.Label(eq, text="α/β (Gy):").grid(row=0, column=4, sticky="w")
        ttk.Entry(eq, textvariable=self.calc_ab, width=10).grid(row=0, column=5, sticky="w", padx=(6, 14))

        ttk.Button(eq, text="Compute", command=self._compute_eqd2_panel).grid(row=0, column=6, sticky="w", padx=(6, 0))

        self.eqd2_out = tk.StringVar(value="EQD2: —   BED: —")
        ttk.Label(eq, textvariable=self.eqd2_out).grid(row=0, column=7, sticky="w", padx=(14, 0))

        ttk.Separator(eq, orient="vertical").grid(row=0, column=8, sticky="ns", padx=14)

        ttk.Label(eq, text="GUI default α/β (fallback):").grid(row=0, column=9, sticky="w")
        self.gui_default_ab = tk.StringVar(value="3")
        ttk.Entry(eq, textvariable=self.gui_default_ab, width=8).grid(row=0, column=10, sticky="w", padx=(6, 0))

        # Table
        table_frame = ttk.Frame(self, padding=10)
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        columns = (
            "Organ", "Metric", "Limit", "Units", "Volume", "Priority",
            "α/β used", "EQD2 (Gy2)", "Eqv Dose @ selected (Gy)",
            "Endpoint/Notes", "Source"
        )
        self.columns = columns

        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=18)
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        widths = {
            "Organ": 170,
            "Metric": 105,
            "Limit": 90,
            "Units": 55,
            "Volume": 105,
            "Priority": 70,
            "α/β used": 70,
            "EQD2 (Gy2)": 95,
            "Eqv Dose @ selected (Gy)": 150,
            "Endpoint/Notes": 320,
            "Source": 280
        }
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths.get(col, 120), anchor="w")

        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_var = tk.StringVar(value="Load a JSON to begin.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Export visible rows (CSV)…", command=self._export_csv).pack(side=tk.RIGHT)

    # -------------------------
    # Loaders
    # -------------------------
    def _load_ab_map(self, path: str):
        self.ab_map = {}
        self.ab_default = 3.0
        if not path or not os.path.exists(path):
            self.status_var.set(f"α/β map not found ({path}). Using GUI default only.")
            return
        try:
            obj = load_json(path)
            self.ab_default = float(obj.get("default_alpha_beta", 3.0))
            organs = obj.get("organs", {})
            # normalize keys
            self.ab_map = {norm_text(k): float(v) for k, v in organs.items()}
            self.status_var.set(f"Loaded α/β map: {path}")
        except Exception as e:
            messagebox.showwarning("α/β map load error", f"Failed to load α/β map:\n{e}\n\nUsing GUI default only.")
            self.ab_map = {}
            self.ab_default = 3.0

    def _reload_ab_map(self):
        self._load_ab_map(self.ab_map_path)
        self._populate_table()

    def _load_db(self, path: str):
        if not os.path.exists(path):
            messagebox.showerror("Missing constraints JSON", f"Could not find JSON file:\n{path}")
            self.status_var.set(f"Missing constraints JSON: {path}")
            return

        try:
            self.db = load_json(path)
        except Exception as e:
            messagebox.showerror("JSON load error", f"Failed to load constraints JSON:\n{e}")
            self.status_var.set("Failed to load constraints JSON.")
            return

        self.sites = sorted(self.db.get("sites", {}).keys())
        self.techniques = self.db.get("techniques", ["3DCRT", "IMRT", "VMAT", "SRS", "SBRT"])

        self.site_cb["values"] = self.sites
        self.tech_cb["values"] = self.techniques

        if self.sites:
            self.site_var.set(self.sites[0])
        if self.techniques:
            self.tech_var.set(self.techniques[0])

        self._refresh_fractionations()
        self._compute_eqd2_panel()
        self.status_var.set(f"Loaded constraints JSON: {path}")

    def _reload_constraints(self):
        self._load_db(self.json_path)

    def _open_constraints_json(self):
        path = filedialog.askopenfilename(
            title="Select constraints JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.json_path = path
            self._load_db(self.json_path)

    # -------------------------
    # Selection helpers
    # -------------------------
    def _get_selected_fractionation(self):
        site = normalize_key(self.site_var.get())
        tech = normalize_key(self.tech_var.get())
        frac_id = normalize_key(self.frac_var.get())

        site_obj = self.db.get("sites", {}).get(site, {})
        fx_obj = None
        for fx in site_obj.get("fractionations", []):
            if fx.get("id") == frac_id:
                valid_techs = fx.get("techniques", [])
                if not valid_techs or tech in valid_techs:
                    fx_obj = fx
                    break
        return fx_obj

    def _refresh_fractionations(self):
        site = normalize_key(self.site_var.get())
        tech = normalize_key(self.tech_var.get())

        fracs = []
        site_obj = self.db.get("sites", {}).get(site, {})
        for fx in site_obj.get("fractionations", []):
            valid_techs = fx.get("techniques", [])
            if not valid_techs or tech in valid_techs:
                fracs.append(fx.get("id", ""))

        self.frac_cb["values"] = fracs
        if fracs and self.frac_var.get() not in fracs:
            self.frac_var.set(fracs[0])
        if not fracs:
            self.frac_var.set("")

        self._populate_table()

    def _constraint_matches_fractionation(self, c: dict, frac_id: str) -> bool:
        ids = c.get("fractionation_ids")
        if ids and frac_id:
            return frac_id in ids
        return True

    # -------------------------
    # EQD2 panel
    # -------------------------
    def _compute_eqd2_panel(self):
        D = try_parse_float(self.calc_total_d.get())
        n = try_parse_float(self.calc_n.get())
        ab = try_parse_float(self.calc_ab.get())
        if D is None or n is None or ab is None or n <= 0 or ab <= 0:
            self.eqd2_out.set("EQD2: —   BED: —")
            return
        e = eqd2(D, n, ab)
        b = bed(D, n, ab)
        self.eqd2_out.set(f"EQD2: {e:.2f} Gy₂   BED: {b:.2f} Gy")
        self._populate_table()

    # -------------------------
    # α/β resolution
    # -------------------------
    def _resolve_alpha_beta(self, organ_name: str, constraint_alpha_beta):
        """
        Priority:
          1) constraint alpha_beta
          2) alpha_beta_defaults.json mapping (exact key)
          3) mapping via partial match (contains)
          4) alpha_beta_defaults.json default_alpha_beta
          5) GUI default alpha/beta entry
        Returns (alpha_beta_value, alpha_beta_source_label)
        """
        ab = try_parse_float(constraint_alpha_beta)
        if ab is not None and ab > 0:
            return ab, "constraint"

        o = norm_text(organ_name)

        # exact match
        if o in self.ab_map:
            return self.ab_map[o], "map"

        # partial match: if any known key is contained in organ string
        for key, val in self.ab_map.items():
            if key and key in o:
                return val, "map*"

        # map default
        if self.ab_default and self.ab_default > 0:
            return self.ab_default, "map_default"

        # GUI fallback
        gui_ab = try_parse_float(self.gui_default_ab.get())
        if gui_ab is not None and gui_ab > 0:
            return gui_ab, "gui"

        return None, "none"

    # -------------------------
    # Constraint typing
    # -------------------------
    def _infer_limit_type(self, c: dict) -> str:
        lt = (c.get("limit_type") or "").strip().lower()
        if lt in ("dose", "volume"):
            return lt
        metric = c.get("metric", "")
        if is_dose_metric(metric):
            return "dose"
        if is_volume_metric(metric):
            return "volume"
        return "other"

    # -------------------------
    # EQD2 per-row computation
    # -------------------------
    def _compute_eqd2_for_constraint(self, c: dict, selected_fx: dict):
        """
        Returns (ab_used_str, eqd2_str, eqv_totaldose_str).
        Only for dose-based constraints with numeric limits and known basis fractionation.
        """
        if not self.show_eqd2_var.get():
            return ("", "", "")

        lt = self._infer_limit_type(c)
        if lt != "dose":
            return ("—", "—", "—")

        limit_val = try_parse_float(c.get("limit"))
        if limit_val is None:
            return ("—", "—", "—")

        ab, ab_src = self._resolve_alpha_beta(c.get("organ", ""), c.get("alpha_beta"))
        if ab is None:
            return ("—", "—", "—")

        # Need basis fractionation for that constraint (n or d)
        basis = c.get("basis_fractionation") or {}
        n_basis = try_parse_float(basis.get("n"))
        d_basis = try_parse_float(basis.get("d"))

        # Infer missing piece if possible
        if n_basis is None and d_basis is not None and d_basis > 0:
            n_basis = limit_val / d_basis
        if d_basis is None and n_basis is not None and n_basis > 0:
            d_basis = limit_val / n_basis

        if n_basis is None or n_basis <= 0:
            return (f"{ab:.2f}", "(need basis n)", "—")

        e = eqd2(limit_val, n_basis, ab)

        # Equivalent total dose at selected fractionation (if selected has n)
        if not selected_fx:
            return (f"{ab:.2f}", f"{e:.2f}", "—")

        n_sel = try_parse_float(selected_fx.get("n_fractions"))
        if n_sel is None or n_sel <= 0:
            return (f"{ab:.2f}", f"{e:.2f}", "—")

        D_eqv = total_dose_for_eqd2(e, n_sel, ab)
        if math.isnan(D_eqv):
            return (f"{ab:.2f}", f"{e:.2f}", "—")

        return (f"{ab:.2f} ({ab_src})", f"{e:.2f}", f"{D_eqv:.2f}")

    # -------------------------
    # Table population
    # -------------------------
    def _populate_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        site = normalize_key(self.site_var.get())
        tech = normalize_key(self.tech_var.get())
        frac_id = normalize_key(self.frac_var.get())
        flt = normalize_key(self.filter_var.get()).lower()

        site_obj = self.db.get("sites", {}).get(site, {})
        if not site_obj:
            self.status_var.set("No site selected / site not found.")
            return

        fx_obj = self._get_selected_fractionation()

        constraints = list(site_obj.get("constraints", []))
        if fx_obj:
            constraints += fx_obj.get("constraints", [])

        visible = 0
        for c in constraints:
            # gate by technique if present
            techs = c.get("techniques")
            if techs and tech not in techs:
                continue

            # gate by fractionation ids if present
            if self.show_only_matching_var.get() and frac_id:
                if not self._constraint_matches_fractionation(c, frac_id):
                    continue

            organ = c.get("organ", "")
            metric = c.get("metric", "")
            limit = c.get("limit", "")
            units = c.get("units", "")
            volume = c.get("volume", "")
            priority = c.get("priority", "")
            notes = c.get("notes", "")
            source = c.get("source", "")

            hay = f"{organ} {metric} {limit} {units} {volume} {notes} {source}".lower()
            if flt and flt not in hay:
                continue

            ab_used, eqd2_str, eqv_str = self._compute_eqd2_for_constraint(c, fx_obj)
            if not self.show_eqd2_var.get():
                ab_used, eqd2_str, eqv_str = ("", "", "")

            self.tree.insert(
                "",
                "end",
                values=(organ, metric, limit, units, volume, priority, ab_used, eqd2_str, eqv_str, notes, source)
            )
            visible += 1

        fx_label = fx_obj.get("label") if fx_obj else ""
        fx_desc = f"{frac_id} {('- ' + fx_label) if fx_label else ''}".strip()
        self.status_var.set(f"Site={site} | Technique={tech} | Fractionation={fx_desc or 'N/A'} | Rows={visible}")

    # -------------------------
    # Export
    # -------------------------
    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            title="Export CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")]
        )
        if not path:
            return

        cols = self.tree["columns"]
        try:
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for iid in self.tree.get_children():
                    w.writerow(self.tree.item(iid, "values"))
        except Exception as e:
            messagebox.showerror("Export error", f"Failed to export CSV:\n{e}")
            return

        messagebox.showinfo("Exported", f"Saved:\n{path}")


def main():
    app = OARConstraintsApp(constraints_json_path=DEFAULT_JSON, ab_map_path=AB_MAP_JSON)
    app.mainloop()


if __name__ == "__main__":
    main()
